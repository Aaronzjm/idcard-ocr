#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# 身份证 OCR 服务一键部署脚本（Linux）
# 支持：Ubuntu 20.04+ / Debian 11+ / CentOS 7+ / Rocky / AlmaLinux / TencentOS
# 用法：chmod +x deploy.sh && sudo ./deploy.sh
# ============================================================

# -------------------------- 可配置项 --------------------------
SERVICE_PORT="${OCR_PORT:-8000}"            # 监听端口
SERVICE_USER="ocr_service"                   # 运行服务的系统用户（非 root 更安全）
INSTALL_DIR="/opt/idcard_ocr"                # 安装目录
MEMORY_LIMIT="3G"                            # 内存上限，超限自动重启（防 Paddle 内存泄漏）
PYPI_MIRROR="https://mirrors.aliyun.com/pypi/simple/"  # 国内加速源，海外环境可改为官方源
# -----------------------------------------------------------

# 需要随代码一起部署的核心文件（video_check.py 是 server.py 的依赖，绝不能漏）
CORE_FILES=(ocr_core.py server.py video_check.py requirements.txt)

log() { echo -e "\n\033[1;32m[部署]\033[0m $*"; }
warn() { echo -e "\033[1;33m[提示]\033[0m $*"; }
fail() { echo -e "\033[1;31m[失败]\033[0m $*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    fail "请用 root 权限执行：sudo ./deploy.sh"
fi

# -------------------------- 1. 检测系统发行版 --------------------------
log "检测系统发行版..."
if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VERSION="${VERSION_ID:-0}"
else
    fail "不支持的操作系统（缺少 /etc/os-release）"
fi
echo "系统：${OS_ID} ${OS_VERSION}"

# -------------------------- 2. 安装系统依赖 + 确定 Python --------------------------
log "安装系统依赖..."
PYTHON_BIN=""
case "${OS_ID}" in
    ubuntu|debian)
        apt-get update -y
        apt-get install -y python3 python3-venv python3-pip \
            libgomp1 libglib2.0-0 libsm6 libxrender1 libxext6 wget curl
        PYTHON_BIN="$(command -v python3)"
        ;;
    centos|rhel|rocky|almalinux|tencentos|anolis|openeuler)
        yum install -y python3 python3-pip gcc libgomp libGL libSM libXrender libXext wget curl || true
        PYTHON_BIN="$(command -v python3)"
        ;;
    *)
        fail "不支持的系统发行版：${OS_ID}（当前仅支持 Ubuntu/Debian/CentOS/RHEL 系）"
        ;;
esac

# 校验 Python 版本 >= 3.8
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail "未找到 python3，请先手动安装 Python 3.8+"
fi
PY_VER="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Python 版本：${PY_VER}"
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    fail "Python 版本过低（${PY_VER}），需要 >= 3.8"
fi

# -------------------------- 3. 创建运行用户 --------------------------
log "创建系统运行用户..."
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd -r -s /sbin/nologin "$SERVICE_USER"
    echo "用户 $SERVICE_USER 创建完成"
else
    echo "用户 $SERVICE_USER 已存在"
fi

# -------------------------- 4. 部署代码 --------------------------
log "部署代码到 ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"
for f in "${CORE_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        fail "缺少文件：${f}（请确认在项目根目录执行本脚本）"
    fi
    cp "$f" "$INSTALL_DIR/"
done
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"
echo "代码部署完成"

# -------------------------- 5. 虚拟环境 + 安装依赖 --------------------------
log "创建虚拟环境并安装 Python 依赖（约 2-3 分钟）..."
cd "$INSTALL_DIR"
"$PYTHON_BIN" -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip -i "$PYPI_MIRROR"
python -m pip install -i "$PYPI_MIRROR" -r requirements.txt
python -c "import paddle; print('PaddlePaddle 安装成功，版本：', paddle.__version__)"
deactivate
echo "Python 依赖安装完成"

# -------------------------- 6. 预下载 OCR 模型 --------------------------
log "预下载 OCR 模型（避免首个请求超时）..."
su -s /bin/bash "$SERVICE_USER" -c \
    "cd $INSTALL_DIR && source venv/bin/activate && python -c \"from ocr_core import warmup; warmup()\""

# -------------------------- 7. 配置 systemd 服务 --------------------------
log "配置 systemd 服务（开机自启、异常自动重启、内存上限）..."
cat > /etc/systemd/system/idcard-ocr.service << EOF
[Unit]
Description=身份证 OCR 识别服务
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
Environment=OCR_HOST=0.0.0.0
Environment=OCR_PORT=${SERVICE_PORT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/server.py
Restart=always
RestartSec=5
MemoryMax=${MEMORY_LIMIT}
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable idcard-ocr
systemctl start idcard-ocr
sleep 5

# -------------------------- 8. 验证 --------------------------
log "验证服务状态..."
if ! systemctl is-active --quiet idcard-ocr; then
    fail "服务启动失败，请查看日志：journalctl -u idcard-ocr -n 50"
fi
echo "服务已启动"

HEALTH="$(curl -s "http://127.0.0.1:${SERVICE_PORT}/health" || true)"
if echo "$HEALTH" | grep -q '"code":0'; then
    echo "健康检查通过：${HEALTH}"
else
    warn "服务已启动但健康检查未通过（可能是模型仍在加载）。日志：journalctl -u idcard-ocr -f"
fi

# -------------------------- 9. 完成提示 --------------------------
log "部署完成！"
echo "============================================================"
echo "  监听地址：0.0.0.0:${SERVICE_PORT}"
echo "  健康检查：GET http://服务器IP:${SERVICE_PORT}/health"
echo "  识别接口：POST http://服务器IP:${SERVICE_PORT}/api/ocr/idcard"
echo ""
echo "  常用命令："
echo "    systemctl status idcard-ocr   查看状态"
echo "    journalctl -u idcard-ocr -f   实时日志"
echo "    systemctl restart idcard-ocr  重启"
echo ""
echo "  注意事项："
echo "    1. 请在云安全组/防火墙放行 TCP ${SERVICE_PORT} 端口"
echo "    2. 生产建议加 Nginx 反代 + HTTPS + 鉴权"
echo "============================================================"
