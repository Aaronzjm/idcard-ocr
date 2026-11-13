# -*- coding: utf-8 -*-
"""
FastAPI 服务入口
提供身份证 OCR 识别、视频清晰度/拉伸检测 HTTP 接口

配置项全部通过环境变量注入（见 .env.example），无需改代码即可适配不同部署环境。
"""
import os
import time

import uvicorn
from fastapi import Body, FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ocr_core import recognize_id_card
from video_check import check_video_quality

# -------------------------- 配置（环境变量，均有安全默认值） --------------------------
HOST = os.getenv("OCR_HOST", "127.0.0.1")
PORT = int(os.getenv("OCR_PORT", "8000"))
# 允许跨域的来源，逗号分隔。留空表示不启用 CORS 中间件（同源调用无需跨域）。
# 注意：不要把 "*" 和 allow_credentials=True 同时使用，该组合不被 CORS 规范支持，
# 浏览器会直接拒绝携带凭据的跨域响应。
CORS_ORIGINS = [o.strip() for o in os.getenv("OCR_CORS_ORIGINS", "").split(",") if o.strip()]
MAX_FILE_SIZE = int(os.getenv("OCR_MAX_FILE_SIZE", str(5 * 1024 * 1024)))  # 默认 5MB
# 生产环境默认关闭交互式文档，避免接口结构被公开探测
ENABLE_DOCS = os.getenv("OCR_ENABLE_DOCS", "false").lower() in ("1", "true", "yes")

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/jpg", "image/webp"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# 视频检测接口单次请求的下载超时（秒）
VIDEO_TIMEOUT = int(os.getenv("OCR_VIDEO_TIMEOUT", "15"))


class VideoCheckRequest(BaseModel):
    video_url: str = Field(..., description="公网可访问的 mp4 视频 URL")


# -------------------------- 服务初始化 --------------------------
app = FastAPI(
    title="身份证 OCR 识别服务",
    description="提供身份证 OCR 识别、视频清晰度/拉伸检测接口",
    version="1.2.0",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

# -------------------------- 静态资源（可视化操作页） --------------------------
# 挂载 static 目录，访问根路径 "/" 返回可视化页面，方便直接在浏览器里上传图片 / 填视频 URL 使用
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    """返回可视化操作页（若存在），否则返回简单接口说明"""
    index_path = os.path.join(_STATIC_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse(
        {
            "code": 0,
            "msg": "身份证 OCR 识别服务运行中",
            "endpoints": {
                "health": "GET /health",
                "ocr_idcard": "POST /api/ocr/idcard (multipart/form-data, 字段名 file)",
                "video_check": "POST /api/video/check (application/json, 字段 video_url)",
            },
        }
    )


def _error(status_code: int, msg: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "msg": msg, "data": {}},
    )


async def _read_upload_with_limit(file: UploadFile) -> bytes:
    """
    分块读取上传内容，一旦超过上限立即中止。
    不能先 read() 再判断长度——那样超限文件会先被完整读进内存，容易被大文件打爆。
    """
    chunks = []
    received = 0
    while True:
        chunk = await file.read(1024 * 256)
        if not chunk:
            break
        received += len(chunk)
        if received > MAX_FILE_SIZE:
            raise ValueError(
                f"文件大小超过限制，最大允许 {round(MAX_FILE_SIZE / 1024 / 1024, 2)}MB"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _is_allowed_image(filename: str, content_type: str) -> bool:
    """content_type 优先；部分客户端只给 application/octet-stream，此时回退看扩展名。"""
    if content_type in ALLOWED_CONTENT_TYPES:
        return True
    if content_type == "application/octet-stream" and filename:
        ext = os.path.splitext(filename)[1].lower()
        return ext in ALLOWED_EXTENSIONS
    return False


# -------------------------- 接口定义 --------------------------
@app.get("/health", summary="健康检查")
async def health_check():
    """服务健康检查，供监控探活使用"""
    return {"code": 0, "msg": "服务运行正常", "timestamp": int(time.time())}


@app.post("/api/video/check", summary="视频清晰度 + 拉伸检测")
async def video_check(
    req: VideoCheckRequest = Body(..., description='{"video_url": "https://example.com/a.mp4"}'),
):
    """
    传入公网视频 URL，检测画面清晰度和是否被非等比例拉伸。

    返回字段：
    - clarity_score: 清晰度分数 0-100，≥60 清晰，30-60 一般，<30 模糊
    - is_clear: 是否清晰
    - is_stretched: 是否被拉伸变形
    - stretch_desc: 拉伸说明
    """
    url = req.video_url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return _error(400, "参数错误，请传入合法的 http/https 视频 URL")

    code, msg, data = check_video_quality(url, timeout=VIDEO_TIMEOUT)
    return {"code": code, "msg": msg, "data": data, "cost_ms": data.get("cost_ms", 0)}


@app.post("/api/ocr/idcard", summary="身份证识别")
async def ocr_idcard(
    file: UploadFile = File(..., description="身份证图片文件（jpg/png/webp，最大 5MB）"),
):
    """
    上传身份证图片，返回结构化识别结果。
    - 正面（人像面）：side, name, gender, nation, birth, address, id_number, id_valid
    - 反面（国徽面）：side, authority, valid_date
    """
    if not _is_allowed_image(file.filename or "", file.content_type or ""):
        return _error(400, f"不支持的文件类型，仅支持 {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    try:
        file_content = await _read_upload_with_limit(file)
    except ValueError as e:
        return _error(400, str(e))
    except Exception as e:
        return _error(400, f"文件读取失败: {e}")

    if not file_content:
        return _error(400, "上传内容为空")

    start_time = time.time()
    code, msg, data = recognize_id_card(file_content)
    cost_ms = round((time.time() - start_time) * 1000)

    return {"code": code, "msg": msg, "data": data, "cost_ms": cost_ms}


# -------------------------- 启动入口 --------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("[启动] 身份证 OCR 识别服务")
    print(f"[页面] http://{HOST}:{PORT}          （可视化操作页）")
    print("[健康] GET  /health")
    print("[接口] POST /api/ocr/idcard")
    print("[接口] POST /api/video/check")
    print(f"[文档] {'已开启 ' + HOST + ':' + str(PORT) + '/docs' if ENABLE_DOCS else '已关闭（设 OCR_ENABLE_DOCS=true 开启）'}")
    print("[提示] 首次启动会自动下载 OCR 模型（约 20MB），请耐心等待")
    print("=" * 60)
    # workers 固定为 1：多 worker 会重复加载 OCR 模型，内存成倍上涨
    uvicorn.run(app, host=HOST, port=PORT, workers=1, log_level="info")
