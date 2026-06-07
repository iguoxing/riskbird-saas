# RiskBird SAAS 部署指南

## 架构

```
用户浏览器 → ailink.zhaoguoxing.com:443
  → Nginx (Reverse Proxy + SSL)
    → /static/ → 前端 HTML
    → /api/    → FastAPI :8000
    → /ws/     → WebSocket (实时进度 + 二维码)
      → Playwright + Chromium → riskbird.com
```

## 准备

### 1. 购买云服务器

推荐腾讯云轻量应用服务器：
- 配置：2核2G 内存（最低要求）
- 系统：Ubuntu 22.04 LTS
- 价格：约 ¥68/月
- 购买链接：https://cloud.tencent.com/product/lighthouse

> ⚠️ Chromium 浏览器需要 ~500MB 内存，所以至少需要 2GB 内存的服务器。

### 2. 配置 DNS

将 `ailink.zhaoguoxing.com` 的 A 记录指向新服务器的公网 IP。

当前 DNS 指向 GitHub Pages，需要改为新服务器 IP。

### 3. 开放端口

在云服务器控制台的防火墙规则中开放：
- 22 (SSH)
- 80 (HTTP)
- 443 (HTTPS)

## 部署

### 一键部署（推荐）

SSH 登录服务器后运行：

```bash
bash <(curl -sSL https://raw.githubusercontent.com/iguoxing/riskbird-saas/main/cloud/deploy.sh)
```

这个脚本会自动：
1. 安装 Docker + Docker Compose
2. 克隆项目代码
3. 构建 Docker 镜像（含 Chromium）
4. 启动服务

### 手动部署

```bash
# 1. 安装 Docker
curl -fsSL https://get.docker.com | sh

# 2. 克隆项目
git clone https://github.com/iguoxing/riskbird-saas.git /opt/riskbird-saas
cd /opt/riskbird-saas

# 3. 构建镜像（首次约5-10分钟）
docker compose -f cloud/docker-compose.yml build app

# 4. 启动
docker compose -f cloud/docker-compose.yml up -d

# 5. 验证
curl http://localhost:80/
```

## 配置 SSL

```bash
# 1. 安装 certbot
sudo apt install certbot -y

# 2. 获取证书（先停止 nginx）
docker compose -f /opt/riskbird-saas/cloud/docker-compose.yml stop nginx

# 3. 申请证书
sudo certbot certonly --standalone -d ailink.zhaoguoxing.com

# 4. 复制证书
sudo cp /etc/letsencrypt/live/ailink.zhaoguoxing.com/fullchain.pem \
  /var/lib/docker/volumes/riskbird-saas_riskbird_certs/_data/
sudo cp /etc/letsencrypt/live/ailink.zhaoguoxing.com/privkey.pem \
  /var/lib/docker/volumes/riskbird-saas_riskbird_certs/_data/

# 5. 替换为 SSL 配置
cp cloud/nginx/default-ssl.conf cloud/nginx/default.conf

# 6. 重启服务
docker compose -f cloud/docker-compose.yml up -d
```

### 自动续期

```bash
# 设置 cron 定时任务
echo "0 0 1 * * /usr/bin/certbot renew --quiet && cp /etc/letsencrypt/live/ailink.zhaoguoxing.com/fullchain.pem /var/lib/docker/volumes/riskbird-saas_riskbird_certs/_data/ && cp /etc/letsencrypt/live/ailink.zhaoguoxing.com/privkey.pem /var/lib/docker/volumes/riskbird-saas_riskbird_certs/_data/ && docker compose -f /opt/riskbird-saas/cloud/docker-compose.yml restart nginx" | sudo crontab -
```

## 日常维护

```bash
# 查看日志
docker compose -f /opt/riskbird-saas/cloud/docker-compose.yml logs -f app

# 重启服务
docker compose -f /opt/riskbird-saas/cloud/docker-compose.yml restart

# 停止服务
docker compose -f /opt/riskbird-saas/cloud/docker-compose.yml down

# 更新代码
cd /opt/riskbird-saas && git pull
docker compose -f cloud/docker-compose.yml build app
docker compose -f cloud/docker-compose.yml up -d
```
