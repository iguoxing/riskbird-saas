# RiskBird 企业信息采集 SAAS

基于 Playwright 的 RiskBird.com 企业信息采集 Web 应用，支持：
- 📤 上传 Excel 批量采集
- 📱 登录二维码展示
- 📊 实时进度显示
- 💾 结果 Excel 下载
- ⏱️ 每日50次查询限制

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

## 启动

```bash
python main.py
# 访问 http://localhost:8000
```

## 部署到服务器

### 1. 安装依赖
```bash
pip install -r requirements.txt
playwright install --with-deps chromium
```

### 2. 使用 systemd 管理（推荐）
创建 `/etc/systemd/system/riskbird.service`：

```ini
[Unit]
Description=RiskBird SAAS
After=network.target

[Service]
User=your_user
WorkingDirectory=/path/to/riskbird_saas
ExecStart=/usr/bin/python3 /path/to/riskbird_saas/main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

启动：
```bash
sudo systemctl daemon-reload
sudo systemctl enable riskbird
sudo systemctl start riskbird
```

### 3. 使用 nginx 反向代理（可选）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 4. 使用 Docker（推荐）

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000
CMD ["python", "main.py"]
```

构建运行：
```bash
docker build -t riskbird-saas .
docker run -d -p 8000:8000 --name riskbird riskbird-saas
```

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 前端页面 |
| `/api/upload` | POST | 上传Excel文件 |
| `/api/task/{id}/start` | POST | 启动采集任务 |
| `/api/task/{id}` | GET | 查询任务状态 |
| `/api/task/{id}/qr` | GET | 获取登录二维码 |
| `/api/task/{id}/download` | GET | 下载结果 |
| `/api/quota` | GET | 查询今日剩余额度 |

## 目录结构

```
riskbird_saas/
├── main.py           # FastAPI 后端
├── scraper_core.py   # 核心采集逻辑
├── static/
│   └── index.html   # 前端页面
├── uploads/         # 上传文件临时目录
├── results/         # 结果文件目录
├── qrcodes/        # 二维码临时目录
└── requirements.txt # 依赖
```

## 注意事项

1. **每日查询限制**：RiskBird 每天限制50次查询，应用会自动计数并在达到上限后拒绝新任务
2. **登录状态**：应用使用 headless 浏览器，检测到需要登录时会通过API返回二维码
3. **并发限制**：建议同一时间只运行一个采集任务，避免触发风控
4. **数据隐私**：上传的Excel和结果文件存储在服务器本地，请定期清理
