# 身份证 OCR 识别服务

基于 **PaddleOCR** 的身份证识别服务，同时内置**视频清晰度 / 拉伸检测**能力。提供 HTTP 接口，开箱即用。

## 功能

- 🔍 **身份证识别**：上传正反面照片，自动判断正反面并结构化提取字段
  - 正面（人像面）：姓名、性别、民族、出生日期、住址、身份证号（含 GB 11643-1999 校验码合法性校验）
  - 反面（国徽面）：签发机关、有效期限
- 🎬 **视频质量检测**：传入公网视频 URL，检测画面清晰度（拉普拉斯方差法）与是否被非等比例拉伸
- 🛡️ **安全设计**：SSRF 防护、上传大小限制、身份证号校验、生产默认关闭接口文档

## 技术栈

- Python 3.8 ~ 3.12（**不支持 3.13**，paddlepaddle 2.6.2 无对应 wheel）
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) + PaddlePaddle（CPU 即可，无需 GPU）
- FastAPI + Uvicorn
- OpenCV + NumPy

## 快速开始

### 1. 安装依赖

```bash
# 建议使用 Python 3.10 / 3.11 虚拟环境
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
# 国内加速：
# pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 启动服务

```bash
python server.py
```

首次启动会在首次调用识别接口时自动下载 OCR 模型（约 20MB），请耐心等待。

启动后：

| 接口 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `POST /api/ocr/idcard` | 身份证识别 |
| `POST /api/video/check` | 视频清晰度 / 拉伸检测 |

### 3. 测试

```bash
# 健康检查
curl http://127.0.0.1:8000/health

# 身份证识别（替换为你的图片路径）
curl -X POST -F "file=@idcard.jpg" http://127.0.0.1:8000/api/ocr/idcard

# 视频质量检测
curl -X POST http://127.0.0.1:8000/api/video/check \
  -H "Content-Type: application/json" \
  -d '{"video_url": "https://example.com/video.mp4"}'
```

也可以命令行直接测试识别核心：

```bash
python ocr_core.py 身份证照片.jpg
```

## 接口返回示例

身份证识别（正面）：

```json
{
  "code": 0,
  "msg": "识别成功",
  "data": {
    "side": "front",
    "name": "张三",
    "gender": "男",
    "nation": "汉族",
    "birth": "1990年1月1日",
    "address": "某省某市某区某街道",
    "id_number": "110101199001011234",
    "id_valid": true
  },
  "cost_ms": 1200
}
```

`code` 含义：`0` 成功；`400` 参数错误；`404` 未识别到身份证；`500` 识别失败/关键字段缺失。

## 配置（环境变量）

复制 `.env.example` 并按需修改，或直接在启动前导出环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OCR_HOST` | `127.0.0.1` | 监听地址，对外服务设 `0.0.0.0` |
| `OCR_PORT` | `8000` | 监听端口 |
| `OCR_CORS_ORIGINS` | 空 | 允许跨域来源，逗号分隔 |
| `OCR_ENABLE_DOCS` | `false` | 是否开启 `/docs` 交互式文档 |
| `OCR_ALLOW_PRIVATE_NETWORK` | `false` | 是否允许视频检测访问内网地址（默认禁止防 SSRF） |
| `OCR_MAX_FILE_SIZE` | `5242880` | 上传大小上限（字节） |

## 部署到 Linux 服务器

一键部署脚本 `deploy.sh` 已备好，支持 Ubuntu / Debian / CentOS / Rocky / AlmaLinux / TencentOS。

```bash
chmod +x deploy.sh
sudo ./deploy.sh
```

详见 [DEPLOY.md](DEPLOY.md)。

## 项目结构

```
.
├── server.py          # FastAPI 服务入口
├── ocr_core.py        # 身份证 OCR 核心逻辑（正反面识别、字段提取、号码校验）
├── video_check.py     # 视频清晰度 / 拉伸检测
├── test_video.py      # 视频检测接口测试脚本
├── requirements.txt   # 依赖清单
├── deploy.sh          # Linux 一键部署脚本
├── DEPLOY.md          # 部署文档
├── 启动服务.bat        # Windows 启动脚本
└── .env.example       # 环境变量示例
```

## 许可证

[MIT](LICENSE)
