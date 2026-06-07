"""
RiskBird 核心采集逻辑
支持：Cookie登录、命令行参数、GitHub Actions
"""
import asyncio
import json
import os
import re
import sys
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
from playwright.async_api import async_playwright

RISKBIRD_BASE = "https://riskbird.com"
OUTPUT_DIR = Path(__file__).parent / "results"
OUTPUT_DIR.mkdir(exist_ok=True)

STEALTH_JS = """
delete navigator.__proto__.webdriver;
Object.defineProperty(navigator, 'webdriver', {get: () => false});
window.speechSynthesis = undefined;
window.navigator.chrome = {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
"""


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)


def load_excel(excel_path: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name="Sheet1")
    def extract_company(addr):
        if pd.isna(addr):
            return None
        parts = str(addr).split("-")
        name = parts[-1].strip()
        name = re.sub(r"[（(][^）)]*[）)]", "", name).strip()
        return name or None
    df["企业名称"] = df["Unnamed: 2"].apply(extract_company)
    if "注册资本" not in df.columns:
        df["注册资本"] = ""
    if "联系方式" not in df.columns:
        df["联系方式"] = ""
    df["注册资本"] = df["注册资本"].astype(str)
    df["联系方式"] = df["联系方式"].astype(str)
    return df


async def load_cookies(context, cookie_file: str) -> bool:
    """从文件加载 Cookie"""
    try:
        if not os.path.exists(cookie_file):
            return False
        with open(cookie_file, 'r') as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        log(f"  🍪 已从 {cookie_file} 加载 Cookie")
        return True
    except Exception as e:
        log(f"  ⚠️ 加载 Cookie 失败: {e}")
        return False


async def save_cookies(context, cookie_file: str):
    """保存 Cookie 到文件"""
    try:
        cookies = await context.cookies()
        with open(cookie_file, 'w') as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        log(f"  💾 Cookie 已保存到 {cookie_file}")
    except Exception as e:
        log(f"  ⚠️ 保存 Cookie 失败: {e}")


async def detect_and_wait_login(page, context, cookie_file: str, qr_callback=None, max_wait=300) -> bool:
    """
    检测是否需要登录
    - 如果已登录，返回 True
    - 如果需要登录，截取二维码并调用 qr_callback(qr_path)
    - 登录成功后保存 Cookie
    """
    log("  🔐 检测登录状态...")
    start = datetime.now()
    
    while True:
        body = await page.evaluate("() => document.body.innerText")
        url = page.url
        
        # 已登录特征
        if "导出记录" in body or "个人中心" in body or "退出登录" in body:
            log("  ✅ 已登录")
            await save_cookies(context, cookie_file)
            return True
        
        # 需要登录
        if "登录" in body and "注册" in body and "导出记录" not in body:
            log("  📸 需要登录，尝试截取二维码...")
            
            # 尝试截取二维码
            if qr_callback:
                try:
                    qr_path = str(OUTPUT_DIR.parent / "qrcodes" / f"qr_{datetime.now().strftime('%H%M%S')}.png")
                    os.makedirs(os.path.dirname(qr_path), exist_ok=True)
                    
                    # 尝试多种选择器找二维码
                    for selector in ['img[alt*="二维码"]', 'img[src*="qr"]', 'canvas', '.qrcode img', '#qrcode img']:
                        el = await page.query_selector(selector)
                        if el:
                            await el.screenshot(path=qr_path)
                            log(f"  📸 二维码已保存: {qr_path}")
                            qr_callback(qr_path)
                            break
                except Exception as e:
                    log(f"  ⚠️ 截取二维码失败: {e}")
            
            # 等待登录完成
            log("  ⏳ 等待登录（最多5分钟）...")
            for _ in range(60):  # 5分钟
                await asyncio.sleep(5)
                body = await page.evaluate("() => document.body.innerText")
                if "导出记录" in body or "个人中心" in body:
                    log("  ✅ 登录成功！")
                    await save_cookies(context, cookie_file)
                    return True
            return False
        
        # 超时检查
        elapsed = (datetime.now() - start).seconds
        if elapsed > max_wait:
            log("  ❌ 登录超时")
            return False
        
        await asyncio.sleep(3)
        await page.reload(wait_until="domcontentloaded", timeout=20000)


async def search_and_collect(page, company_name: str) -> dict:
    result = {"注册资本": "", "联系方式": []}
    try:
        log(f"  🔍 搜索: {company_name}")
        
        await page.goto(RISKBIRD_BASE, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        
        search_input = await page.query_selector('input[placeholder*="请输入企业名"]')
        if not search_input:
            log("  ❌ 未找到搜索框")
            return result
        
        await search_input.click()
        await search_input.fill(company_name)
        await page.wait_for_timeout(500)
        await search_input.press("Enter")
        await page.wait_for_timeout(4000)
        
        # 检查登录状态
        body = await page.evaluate("() => document.body.innerText")
        if "登录/注册" in body and "导出记录" not in body:
            log("  ⚠️ 登录已过期")
            return result
        
        # 找详情链接
        detail_url = await find_company_detail_link(page, company_name)
        if not detail_url:
            log("  ℹ️ 搜索无结果")
            return result
        
        log(f"  🔗 进入详情页...")
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3500)
        
        # 点击"更多"按钮
        try:
            more_btns = page.locator('text="更多"')
            count = await more_btns.count()
            for i in range(min(count, 5)):
                btn = more_btns.nth(i)
                if await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(800)
            await page.wait_for_timeout(1500)
        except Exception:
            pass
        
        result["注册资本"] = await collect_capital(page)
        result["联系方式"] = await collect_phones(page)
        
        cap = result["注册资本"] or "—"
        phones = result["联系方式"]
        log(f"  ✅ 注册资本={cap} | 电话={phones if phones else '—'}")
        
    except Exception as e:
        log(f"  ⚠️ 采集出错: {e}")
    return result


async def find_company_detail_link(page, company_name: str) -> Optional[str]:
    import json
    js = """
    (() => {
        const name = NAME_PLACEHOLDER;
        const shortName = name.slice(0, 6);
        const links = Array.from(document.querySelectorAll('a[href]'));
        const scored = [];
        for (const a of links) {
            const href = a.href;
            const text = (a.textContent || '').trim();
            const title = a.getAttribute('title') || '';
            if (!href || href.includes('javascript') || href === location.href) continue;
            if (href.includes('/login') || href.includes('/register')) continue;
            if (!href.includes('riskbird.com') && !href.startsWith('/')) continue;
            let score = 0;
            if (href.includes('/ent/')) score += 50;
            if (text === name || title === name) score += 100;
            else if (text.includes(name) || title.includes(name)) score += 90;
            else if (text.includes(shortName) && text.length < 60) score += 70;
            else if (title.includes(shortName)) score += 65;
            if (score > 50) {
                scored.push({href, text: text.slice(0, 60), score});
            }
        }
        scored.sort((a, b) => b.score - a.score);
        return scored.length > 0 ? scored[0] : null;
    })()
    """.replace("NAME_PLACEHOLDER", json.dumps(company_name))
    
    match = await page.evaluate(js)
    if match and match.get("href"):
        log(f"  📎 匹配: {match['text'][:30]} ({match['score']}分)")
        return match["href"]
    return None


async def collect_capital(page) -> str:
    js = r"""
    () => {
        const labels = Array.from(document.querySelectorAll('*')).filter(
            el => (el.textContent || '').trim() === '注册资本'
        );
        for (const label of labels) {
            let el = label;
            for (let i = 0; i < 5; i++) {
                el = el.nextElementSibling;
                if (!el) break;
                const txt = (el.textContent || '').trim();
                if (txt && /^[\d,.]+/.test(txt)) return txt;
            }
        }
        const body = document.body.innerText;
        const m = body.match(/注册资本[：:\s]*([\d,.]+[万千百]?元?)/);
        return m ? m[1] : '';
    }
    """
    try:
        return await page.evaluate(js) or ""
    except Exception:
        return ""


async def collect_phones(page) -> list:
    js = r"""
    (() => {
        const phones = new Set();
        const bodyText = document.body.innerText;
        const mobiles = bodyText.match(/(?<!\d)1[3-9]\d{9}(?!\d)/g) || [];
        mobiles.forEach(p => phones.add(p));
        const tels = bodyText.match(/(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)/g) || [];
        tels.forEach(p => phones.add(p));
        document.querySelectorAll('a[href^="tel:"]').forEach(a => {
            const num = a.href.replace('tel:', '').replace(/\s/g, '');
            if (num.length >= 7) phones.add(num);
        });
        const footerPhones = new Set();
        const footerEls = document.querySelectorAll('footer, [class*="footer"], [class*="bottom"]');
        footerEls.forEach(el => {
            const text = el.textContent || '';
            const matches = text.match(/(?<!\d)1[3-9]\d{9}(?!\d)/g) || [];
            matches.forEach(p => footerPhones.add(p));
        });
        const headerPhones = new Set();
        const headerEls = document.querySelectorAll('header, [class*="header"], [class*="userinfo"], nav');
        headerEls.forEach(el => {
            const text = el.textContent || '';
            const matches = text.match(/(?<!\d)1[3-9]\d{9}(?!\d)/g) || [];
            matches.forEach(p => headerPhones.add(p));
        });
        const mainContent = (() => {
            const clone = document.body.cloneNode(true);
            const removeEls = clone.querySelectorAll('footer, header, nav, [class*="footer"], [class*="header"], [class*="userinfo"]');
            removeEls.forEach(el => el.remove());
            return clone.textContent || '';
        })();
        const filteredPhones = [];
        phones.forEach(p => {
            const n = p.replace(/\D/g, '');
            if (!(/^1[3-9]\d{9}$/.test(n) || /^0\d{9,11}$/.test(n))) return;
            if (footerPhones.has(p) && !mainContent.includes(p)) return;
            if (headerPhones.has(p) && !mainContent.includes(p)) return;
            filteredPhones.push(n);
        });
        return filteredPhones;
    })()
    """
    try:
        raw = await page.evaluate(js)
        seen = set()
        clean = []
        for p in raw:
            p = re.sub(r"\D", "", p)
            if p not in seen and len(p) >= 7:
                seen.add(p)
                clean.append(p)
        return clean[:15]
    except Exception:
        return []


async def run_scraper(
    excel_path: str,
    limit: int = 50,
    cookie_file: str = "cookie.json",
    progress_callback=None,
    qr_callback=None,
    quota_callback=None
) -> str:
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    log("📂 加载Excel...")
    df = load_excel(excel_path)
    total = len(df)
    log(f"  共 {total} 条")
    
    if limit and limit > 0:
        df = df.head(limit)
        total = len(df)
        log(f"  ⚠️ 限制处理前 {total} 条")
    
    if total == 0:
        raise ValueError("没有可处理的数据")
    
    progress_file = Path(excel_path).parent / "progress.json"
    done_names = set()
    if progress_file.exists():
        try:
            done_names = set(json.loads(progress_file.read_text()).get("done", []))
            log(f"  📌 已有进度：{len(done_names)} 条已完成")
        except Exception:
            done_names = set()
    
    results = []
    processed = 0
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.add_init_script(STEALTH_JS)
        
        # 尝试加载 Cookie
        cookie_loaded = await load_cookies(context, cookie_file)
        if cookie_loaded:
            log("🍪 已加载 Cookie，跳过登录")
        
        # 首次访问，检测登录
        log("🌐 打开 RiskBird...")
        await page.goto(RISKBIRD_BASE, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        
        logged_in = await detect_and_wait_login(page, context, cookie_file, qr_callback)
        if not logged_in:
            raise RuntimeError("登录超时，请刷新页面重试")
        
        for idx, row in df.iterrows():
            company = row["企业名称"]
            if pd.isna(company):
                continue
            company = str(company).strip()
            if not company:
                continue
            if company in done_names:
                log(f"⏭️ 跳过已完成: {company}")
                processed += 1
                if progress_callback:
                    progress_callback(processed, total, f"跳过已完成: {company}")
                continue
            
            # 防风控延迟
            if processed > 0:
                delay = 4 + (hash(company) % 6)
                log(f"  ⏱️ 等待 {delay}s 防风控...")
                await asyncio.sleep(delay)
            
            if processed > 0 and processed % 25 == 0:
                rest = 25 + (hash(company) % 26)
                log(f"  💤 已处理 {processed} 条，长休息 {rest}s...")
                await asyncio.sleep(rest)
            
            data = await search_and_collect(page, company)
            processed += 1
            
            if quota_callback:
                quota_callback()
            
            results.append({
                "企业名称": company,
                "注册资本": data["注册资本"],
                "联系方式": ",".join(data["联系方式"])
            })
            done_names.add(company)
            
            progress_file.write_text(json.dumps({"done": list(done_names)}, ensure_ascii=False))
            
            if progress_callback:
                progress_callback(processed, total, f"已处理 {processed}/{total}: {company}")
            
            # 检查登录状态
            if processed % 10 == 0:
                body = await page.evaluate("() => document.body.innerText")
                if "登录/注册" in body and "导出记录" not in body:
                    log("  ⚠️ 登录已过期，重新登录...")
                    await page.goto(RISKBIRD_BASE, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                    await detect_and_wait_login(page, context, cookie_file, qr_callback)
        
        await browser.close()
    
    result_df = pd.DataFrame(results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = str(OUTPUT_DIR / f"采集结果_{timestamp}.xlsx")
    result_df.to_excel(result_path, index=False)
    log(f"💾 结果已保存: {result_path}")
    
    return result_path


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='RiskBird 企业信息采集')
    parser.add_argument('--excel', type=str, default=None, help='Excel 文件路径')
    parser.add_argument('--limit', type=int, default=50, help='采集数量限制（默认50）')
    parser.add_argument('--cookie-file', type=str, default='cookie.json', help='Cookie 文件路径')
    args = parser.parse_args()
    
    excel = args.excel
    if not excel:
        # 自动查找 Excel 文件
        for f in os.listdir('.'):
            if f.endswith('.xlsx') and '需求' in f:
                excel = f
                break
        if not excel:
            print("❌ 未找到 Excel 文件，请使用 --excel 参数指定")
            sys.exit(1)
    
    async def test_progress(current, total, msg):
        print(f"[进度] {current}/{total} - {msg}")
    
    async def test_qr(qr_path):
        print(f"[二维码] {qr_path}")
    
    async def test_quota():
        print("[额度] 使用1次")
    
    print(f"测试模式：处理 {args.limit} 条")
    print(f"Excel 文件: {excel}")
    result = asyncio.run(run_scraper(
        excel,
        limit=args.limit,
        cookie_file=args.cookie_file,
        progress_callback=test_progress,
        qr_callback=test_qr,
        quota_callback=test_quota
    ))
    print(f"结果文件: {result}")
