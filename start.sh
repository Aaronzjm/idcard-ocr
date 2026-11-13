#!/usr/bin/env bash
# 身份证 OCR 服务启动脚本（Linux / macOS）
# 用法：./start.sh  或  bash start.sh
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[提示] 未找到虚拟环境，正在创建..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c "import fastapi, paddleocr" 2>/dev/null; then
    echo "[提示] 依赖未安装，正在安装（首次约 2-3 分钟）..."
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
fi

exec python server.py
