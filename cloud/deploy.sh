#!/bin/bash
# ==================================================
#  RiskBird SAAS 一键部署脚本
#  适用：Ubuntu 20.04+ / Debian 11+
#  要求：最少 2核2G 内存，端口 80/443 开放
#  运行：bash deploy.sh
# ==================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  RiskBird SAAS 一键部署${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# ---------- 1. Check system ----------
echo -e "${GREEN}[1/6] 检查系统环境...${NC}"
if ! command -v apt-get &>/dev/null; then
    echo -e "${RED}❌ 仅支持 Ubuntu/Debian 系统${NC}"
    exit 1
fi

MEM_TOTAL=$(free -m | awk '/Mem:/{print $2}')
if [ "$MEM_TOTAL" -lt 1500 ]; then
    echo -e "${RED}❌ 内存不足 (${MEM_TOTAL}MB)，建议至少 2GB${NC}"
    echo "   Chromium 浏览器需要 ~500MB 内存"
    exit 1
fi
echo "  内存: ${MEM_TOTAL}MB ✓"

# ---------- 2. Install Docker ----------
echo -e "${GREEN}[2/6] 安装 Docker...${NC}"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo "  Docker 安装完成 ✓"
else
    echo "  Docker 已安装: $(docker --version) ✓"
fi

if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    echo "  Docker Compose 安装完成 ✓"
else
    echo "  Docker Compose 已安装 ✓"
fi

# ---------- 3. Setup project ----------
echo -e "${GREEN}[3/6] 设置项目文件...${NC}"
PROJECT_DIR="/opt/riskbird-saas"
if [ ! -d "$PROJECT_DIR" ]; then
    git clone https://github.com/iguoxing/riskbird-saas.git "$PROJECT_DIR" 2>/dev/null || {
        echo -e "${RED}❌ 无法克隆仓库，请确认仓库地址正确${NC}"
        echo "   手动克隆: git clone https://github.com/iguoxing/riskbird-saas.git $PROJECT_DIR"
        exit 1
    }
fi
cd "$PROJECT_DIR"
echo "  项目目录: $PROJECT_DIR ✓"

# ---------- 4. Build and start ----------
echo -e "${GREEN}[4/6] 构建 Docker 镜像（约5-10分钟）...${NC}"
docker compose -f cloud/docker-compose.yml build app
echo "  镜像构建完成 ✓"

echo -e "${GREEN}[5/6] 启动服务...${NC}"
docker compose -f cloud/docker-compose.yml up -d
echo "  服务启动中，等待就绪..."
sleep 10

# ---------- 5. Verify ----------
echo -e "${GREEN}[6/6] 验证服务...${NC}"
if curl -s -o /dev/null -w "%{http_code}" http://localhost:80/ | grep -q 200; then
    echo -e "${GREEN}✅ 服务运行正常！${NC}"
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}  RiskBird SAAS 已部署成功！${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "  访问地址: http://ailink.zhaoguoxing.com"
    echo ""
    echo -e "${BLUE}设置 SSL（推荐）：${NC}"
    echo "  1. 确保域名 DNS 已指向本服务器 IP"
    echo "  2. 运行: sudo apt install certbot -y"
    echo "  3. 运行: sudo certbot --nginx -d ailink.zhaoguoxing.com"
    echo ""
    echo -e "${BLUE}常用命令：${NC}"
    echo "  查看日志: docker compose -f $PROJECT_DIR/cloud/docker-compose.yml logs -f app"
    echo "  重启服务: docker compose -f $PROJECT_DIR/cloud/docker-compose.yml restart"
    echo "  停止服务: docker compose -f $PROJECT_DIR/cloud/docker-compose.yml down"
else
    echo -e "${RED}❌ 服务未正常启动，请检查日志：${NC}"
    echo "  docker compose -f $PROJECT_DIR/cloud/docker-compose.yml logs"
fi
