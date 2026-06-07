"""
RiskBird Cloud SAAS - FastAPI + WebSocket
==========================================
实时推送：进度 / 二维码 / 结果下载
部署：docker-compose up -d
"""
import asyncio
import base64
import json
import os
import uuid
import traceback
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import aiofiles

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper_core import load_excel

app = FastAPI(title="RiskBird SAAS")

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/app/data/uploads"))
RESULT_DIR = Path(os.environ.get("RESULT_DIR", "/app/data/results"))
COOKIE_FILE = os.environ.get("COOKIE_FILE", "/app/data/cookies/cookie.json")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)
Path(COOKIE_FILE).parent.mkdir(parents=True, exist_ok=True)

jobs = {}
jobs_lock = asyncio.Lock()


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


@app.get("/")
async def root():
    static_root = Path(__file__).parent / "static" / "index.html"
    if static_root.exists():
        return HTMLResponse(static_root.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>RiskBird SAAS</h1><p>Static files not found</p>")


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...)):
    job_id = uuid.uuid4().hex[:12]
    safe_name = Path(file.filename).name
    filepath = UPLOAD_DIR / f"{job_id}_{safe_name}"
    async with aiofiles.open(filepath, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)
    log(f"Upload: {filepath}")

    try:
        df = load_excel(str(filepath))
        total = len(df)
    except Exception as e:
        return {"error": f"读取Excel失败: {e}", "job_id": job_id}

    async with jobs_lock:
        jobs[job_id] = {
            "status": "uploaded",
            "total": total,
            "progress": 0,
            "excel_path": str(filepath),
            "result_path": None,
            "message": "",
            "ws": None,
            "qr_base64": None,
            "login_event": asyncio.Event(),
        }

    return {"job_id": job_id, "total": total, "status": "ok"}


@app.post("/api/start/{job_id}")
async def start_job(job_id: str):
    async with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job["status"] == "running":
            return {"status": "already_running"}
        job["status"] = "running"

    asyncio.create_task(run_scraper_async(job_id))
    return {"status": "started", "job_id": job_id}


@app.websocket("/ws/{job_id}")
async def websocket_endpoint(ws: WebSocket, job_id: str):
    await ws.accept()
    async with jobs_lock:
        if job_id not in jobs:
            await ws.close(code=4004, reason="Job not found")
            return
        jobs[job_id]["ws"] = ws

    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "login_ok":
                async with jobs_lock:
                    if job_id in jobs:
                        jobs[job_id]["login_event"].set()
            elif data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/api/qr/{job_id}")
async def get_qr(job_id: str):
    """Polling fallback: get QR code as base64 image"""
    async with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        qr = job.get("qr_base64")
    if qr:
        return {"qr_base64": qr, "status": "need_login"}
    return {"qr_base64": None, "status": "no_qr_yet"}


@app.post("/api/login_ok/{job_id}")
async def login_ok(job_id: str):
    """User clicked button after scanning QR"""
    async with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job["login_event"].set()
    return {"status": "ok"}


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    async with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return {
            "status": job["status"],
            "progress": job["progress"],
            "total": job["total"],
            "message": job["message"],
            "result_id": job["result_path"],
        }


@app.get("/api/download/{job_id}")
async def download_result(job_id: str):
    async with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        path = job.get("result_path")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Result not ready")
    return FileResponse(path, filename=f"RiskBird采集结果_{job_id}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


async def send_ws(job_id: str, msg: dict):
    async with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        ws = job.get("ws")
    if ws:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def run_scraper_async(job_id: str):
    """在后台运行 Playwright 采集，通过 WebSocket 推送进度"""
    from playwright.async_api import async_playwright

    async with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return

    excel_path = job["excel_path"]
    await send_ws(job_id, {"type": "status", "status": "loading"})

    try:
        df = load_excel(excel_path)
    except Exception as e:
        await send_ws(job_id, {"type": "error", "message": f"读取Excel失败: {e}"})
        return

    total = len(df)
    if total > 50:
        df = df.head(50)
        total = 50
        await send_ws(job_id, {"type": "status", "status": "limit_notice",
                               "message": f"每日限制50条，本次处理前{total}条"})

    async with jobs_lock:
        job["total"] = total
        job["progress"] = 0

    await send_ws(job_id, {"type": "status", "status": "starting",
                           "message": f"开始采集 {total} 条", "total": total})

    results = []
    processed = 0

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-gpu",
                      "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            page = await context.new_page()

            stealth_js = """
            delete navigator.__proto__.webdriver;
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            """
            await page.add_init_script(stealth_js)

            await send_ws(job_id, {"type": "status", "status": "login_check",
                                   "message": "检测登录状态..."})

            await page.goto("https://riskbird.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            body = await page.evaluate("() => document.body.innerText")
            need_login = ("登录" in body and "注册" in body and "导出记录" not in body)

            if need_login:
                await send_ws(job_id, {"type": "status", "status": "need_login",
                                       "message": "需要扫码登录，正在获取二维码..."})

                qr_found = False
                for attempt in range(30):
                    for selector in ['img[alt*="二维码"]', 'img[src*="qr"]', 'canvas',
                                     '.qrcode img', '#qrcode img', 'img[src*="QR"]',
                                     'img[src*="qrcode"]', '.login-qrcode img']:
                        el = await page.query_selector(selector)
                        if el:
                            ss = await el.screenshot(type="png")
                            qr_b64 = base64.b64encode(ss).decode()
                            async with jobs_lock:
                                if job_id in jobs:
                                    jobs[job_id]["qr_base64"] = qr_b64
                            await send_ws(job_id, {"type": "qr", "image": qr_b64,
                                                   "message": "请用微信/浏览器扫描二维码登录"})
                            qr_found = True
                            break
                    if qr_found:
                        break
                    await page.wait_for_timeout(2000)
                    await page.reload(wait_until="domcontentloaded", timeout=20000)

                if not qr_found:
                    screenshot = await page.screenshot(type="png")
                    qr_b64 = base64.b64encode(screenshot).decode()
                    async with jobs_lock:
                        if job_id in jobs:
                            jobs[job_id]["qr_base64"] = qr_b64
                    await send_ws(job_id, {"type": "qr_fullpage", "image": qr_b64,
                                           "message": "未找到二维码，请查看页面截图手动登录"})

                await send_ws(job_id, {"type": "status", "status": "waiting_login",
                                       "message": "等待扫码登录（最多5分钟）..."})

                login_event = jobs.get(job_id, {}).get("login_event") if job_id in jobs else None
                logged_in = False
                for _ in range(120):
                    if login_event:
                        try:
                            await asyncio.wait_for(login_event.wait(), timeout=3)
                            if login_event.is_set():
                                await asyncio.sleep(3)
                                body = await page.evaluate("() => document.body.innerText")
                                if "导出记录" in body or "个人中心" in body:
                                    logged_in = True
                                    break
                                login_event.clear()
                        except asyncio.TimeoutError:
                            pass
                    body = await page.evaluate("() => document.body.innerText")
                    if "导出记录" in body or "个人中心" in body:
                        logged_in = True
                        break
                    await page.wait_for_timeout(3000)

                if not logged_in:
                    await send_ws(job_id, {"type": "error", "message": "登录超时，请刷新页面重试"})
                    await browser.close()
                    return

                cookies = await context.cookies()
                Path(COOKIE_FILE).parent.mkdir(parents=True, exist_ok=True)
                with open(COOKIE_FILE, 'w') as f:
                    json.dump(cookies, f, ensure_ascii=False)

                await send_ws(job_id, {"type": "status", "status": "logged_in",
                                       "message": "登录成功，开始采集..."})

            for idx, row in df.iterrows():
                company = row.get("企业名称")
                if not company or (hasattr(company, '__iter__') and not isinstance(company, str)):
                    company = str(row.get("企业名称", "")).strip()
                if not company or company == "nan" or company == "None":
                    continue

                company = str(company).strip()
                if not company:
                    continue

                if processed > 0:
                    delay = 4 + (hash(company) % 6)
                    await asyncio.sleep(delay)

                if processed > 0 and processed % 25 == 0:
                    await asyncio.sleep(25)

                try:
                    page_url = f"https://riskbird.com/search/company?keyword={company}"
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(3000)

                    body = await page.evaluate("() => document.body.innerText")
                    if "登录/注册" in body and "导出记录" not in body:
                        await send_ws(job_id, {"type": "error", "message": "登录已过期，请刷新重试"})
                        break

                    detail_url = None
                    links = await page.evaluate("""() => {
                        const links = document.querySelectorAll('a[href*="/ent/"]');
                        for (const a of links) {
                            const txt = (a.textContent || '').trim();
                            if (txt.length > 2) return a.href;
                        }
                        return null;
                    }""")

                    capital = ""
                    phones = []

                    if links:
                        await page.goto(links, wait_until="domcontentloaded", timeout=30000)
                        await page.wait_for_timeout(3500)

                        try:
                            more_btns = page.locator('text="更多"')
                            count = await more_btns.count()
                            for i in range(min(count, 5)):
                                if await more_btns.nth(i).is_visible():
                                    await more_btns.nth(i).click()
                                    await page.wait_for_timeout(800)
                            await page.wait_for_timeout(1500)
                        except Exception:
                            pass

                        capital = await page.evaluate("""() => {
                            const body = document.body.innerText;
                            const m = body.match(/注册资本[：:\\s]*([\\d,.]+[万千百]?元?)/);
                            return m ? m[1] : '';
                        }""")

                        phones_raw = await page.evaluate("""() => {
                            const seen = new Set();
                            const text = document.body.innerText;
                            const matches = text.match(/(?<!\\d)1[3-9]\\d{9}(?!\\d)/g) || [];
                            matches.forEach(p => {
                                const n = p.replace(/\\D/g, '');
                                if (n.length === 11) seen.add(n);
                            });
                            const tels = text.match(/(?<!\\d)0\\d{2,3}[-\\s]?\\d{7,8}(?!\\d)/g) || [];
                            tels.forEach(p => {
                                const n = p.replace(/\\D/g, '');
                                if (n.length >= 10) seen.add(n);
                            });
                            return Array.from(seen).slice(0, 10);
                        }""")
                        phones = phones_raw or []

                    processed += 1
                    results.append({
                        "企业名称": company,
                        "注册资本": capital or "",
                        "联系方式": ",".join(phones) if phones else "",
                    })

                    async with jobs_lock:
                        if job_id in jobs:
                            jobs[job_id]["progress"] = processed
                            jobs[job_id]["message"] = f"已处理 {processed}/{total}: {company}"

                    await send_ws(job_id, {
                        "type": "progress",
                        "current": processed,
                        "total": total,
                        "message": f"[{processed}/{total}] {company}",
                        "capital": capital or "—",
                        "phones": phones,
                    })

                except Exception as e:
                    processed += 1
                    results.append({
                        "企业名称": company,
                        "注册资本": f"ERROR: {e}",
                        "联系方式": "",
                    })
                    await send_ws(job_id, {
                        "type": "progress",
                        "current": processed,
                        "total": total,
                        "message": f"[{processed}/{total}] {company} — 错误",
                        "error": str(e)[:200],
                    })

            await browser.close()

    except Exception as e:
        traceback.print_exc()
        await send_ws(job_id, {"type": "error", "message": f"采集异常: {e}"})
        return

    import pandas as pd
    result_df = pd.DataFrame(results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = RESULT_DIR / f"采集结果_{job_id}_{timestamp}.xlsx"
    result_df.to_excel(result_path, index=False, engine="openpyxl")

    async with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["status"] = "complete"
            jobs[job_id]["result_path"] = str(result_path)
            jobs[job_id]["progress"] = processed

    await send_ws(job_id, {
        "type": "complete",
        "total_success": len(results),
        "result_id": job_id,
        "message": f"采集完成！成功 {len(results)} 条",
    })

    log(f"Job {job_id}: 完成，结果 {result_path}")
