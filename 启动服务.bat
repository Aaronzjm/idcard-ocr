@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo 身份证 OCR 服务启动脚本
echo ============================================================

REM 检查虚拟环境，不存在则自动创建
if not exist ".venv\Scripts\python.exe" (
    echo [提示] 未找到虚拟环境，正在创建...
    python -m venv .venv
    if errorlevel 1 (
        echo [失败] 创建虚拟环境失败，请确认已安装 Python 3.8~3.12 并加入 PATH
        pause
        exit /b 1
    )
)

REM 检查依赖，未安装则自动安装
.venv\Scripts\python.exe -c "import fastapi, paddleocr" >nul 2>&1
if errorlevel 1 (
    echo [提示] 依赖未安装，正在安装（首次约 2-3 分钟）...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
)

echo.
echo 服务启动后请勿关闭本窗口，关闭即停止服务
echo 健康检查：http://127.0.0.1:8000/health
echo ============================================================

.venv\Scripts\python.exe server.py
pause
