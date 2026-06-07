"""
RiskBird 企业信息采集 SAAS 应用
- 上传Excel，批量采集企业联系方式和注册资本
- 每天限制50次查询
- 需要登录时展示二维码
- 支持结果下载
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import asyncio
import json
import os
import uuid
from datetime import datetime, date
import pandas as pd
from pathlib import Path

# ── 目录设置 ──
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
QR_DIR = BASE_DIR / "qrcodes"
UPLOAD_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)
QR_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RiskBird 企业信息采集 SAAS")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ── 内存状态（生产环境建议用Redis）──
daily_quota = {"date": None, "count": 0}  # 每日查询计数
tasks: Dict[str, dict] = {}  # task_id -> task状态


# ── 数据模型 ──
class TaskStatus(BaseModel):
    task_id: str
    status: str  # pending, running, done, error, waiting_login
    total: int = 0
    processed: int = 0
    result_file: Optional[str] = None
    qr_code: Optional[str] = None
    message: Optional[str] = None


# ── 工具函数 ──
def check_daily_quota() -> bool:
    """检查今日是否还有查询额度"""
    today = str(date.today())
    if daily_quota["date"] != today:
        daily_quota["date"] = today
        daily_quota["count"] = 0
    return daily_quota["count"] < 50


def incr_quota():
    today = str(date.today())
    if daily_quota["date"] != today:
        daily_quota["date"] = today
        daily_quota["count"] = 0
    daily_quota["count"] += 1


# ── 路由 ──
@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    html_file = BASE_DIR / "static" / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>前端文件不存在，请检查 static/index.html</h1>")


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...)):
    """
    上传Excel文件，创建采集任务
    返回 task_id
    """
    # 检查文件类型
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="只支持 .xlsx 或 .xls 文件")

    # 检查每日额度
    if not check_daily_quota():
        raise HTTPException(status_code=429, detail="今日查询额度已用完（50次/天），请明天再试")

    # 保存上传的文件
    task_id = str(uuid.uuid4())[:8]
    file_path = UPLOAD_DIR / f"{task_id}_{file.filename}"
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 读取企业数量
    try:
        df = pd.read_excel(file_path, sheet_name="Sheet1")
        total = len(df)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Excel读取失败: {e}")

    # 创建任务
    tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "total": min(total, 50 - daily_quota["count"]),  # 最多处理剩余额度
        "processed": 0,
        "result_file": None,
        "qr_code": None,
        "message": "任务已创建，等待处理...",
        "file_path": str(file_path),
        "filename": file.filename
    }

    return {"task_id": task_id, "total": tasks[task_id]["total"], "message": "任务已创建"}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return tasks[task_id]


@app.post("/api/task/{task_id}/start")
async def start_task(task_id: str, background_tasks: BackgroundTasks):
    """
    启动采集任务（后台执行）
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    if tasks[task_id]["status"] in ["running", "waiting_login"]:
        return {"message": "任务正在运行中"}

    tasks[task_id]["status"] = "running"
    tasks[task_id]["message"] = "正在启动浏览器..."

    # 后台执行采集
    background_tasks.add_task(run_scraper, task_id)

    return {"message": "任务已启动"}


@app.get("/api/task/{task_id}/qr")
async def get_qr_code(task_id: str):
    """获取登录二维码（如果需要）"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    qr_path = tasks[task_id].get("qr_code")
    if qr_path and os.path.exists(qr_path):
        return FileResponse(qr_path)
    return {"message": "暂无二维码"}


@app.get("/api/task/{task_id}/download")
async def download_result(task_id: str):
    """下载采集结果"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    result_file = tasks[task_id].get("result_file")
    if not result_file or not os.path.exists(result_file):
        raise HTTPException(status_code=404, detail="结果文件不存在")
    return FileResponse(
        result_file,
        filename=os.path.basename(result_file),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.get("/api/quota")
async def get_quota():
    """查询今日剩余额度"""
    today = str(date.today())
    if daily_quota["date"] != today:
        daily_quota["date"] = today
        daily_quota["count"] = 0
    return {
        "date": today,
        "used": daily_quota["count"],
        "remaining": max(0, 50 - daily_quota["count"]),
        "limit": 50
    }


# ── 后台采集任务 ──
async def run_scraper(task_id: str):
    """
    后台执行采集任务
    这里调用 Playwright 采集逻辑
    """
    import sys
    sys.path.insert(0, str(BASE_DIR))
    from scraper_core import run_scraper as core_run

    task = tasks[task_id]
    file_path = task["file_path"]

    def progress_callback(current, total, message):
        """进度回调"""
        task["processed"] = current
        task["total"] = total
        task["message"] = message
        if "需要登录" in message or "二维码" in message:
            task["status"] = "waiting_login"

    def qr_callback(qr_path: str):
        """二维码回调"""
        task["qr_code"] = qr_path
        task["status"] = "waiting_login"
        task["message"] = "请扫描二维码登录"

    def quota_callback():
        """使用一次查询额度"""
        incr_quota()

    try:
        result_path = await core_run(
            excel_path=file_path,
            limit=task["total"],
            progress_callback=progress_callback,
            qr_callback=qr_callback,
            quota_callback=quota_callback
        )
        task["status"] = "done"
        task["result_file"] = result_path
        task["message"] = f"采集完成！共处理 {task['processed']} 条"
    except Exception as e:
        task["status"] = "error"
        task["message"] = f"采集失败: {str(e)}"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
