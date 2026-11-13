@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   ID Card OCR Service Launcher
echo ============================================================

REM Check virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Virtual environment not found, creating...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Please install Python 3.8~3.12 and add it to PATH.
        pause
        exit /b 1
    )
)

REM Check dependencies
.venv\Scripts\python.exe -c "import fastapi, paddleocr" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Dependencies not installed, installing now - first time takes 2-3 min...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo.
echo   Keep this window open while the service is running.
echo   Health check : http://127.0.0.1:8000/health
echo   OCR API      : POST http://127.0.0.1:8000/api/ocr/idcard
echo ============================================================

.venv\Scripts\python.exe server.py
pause
