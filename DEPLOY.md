# 身份证 OCR 服务 Linux 部署说明

## 一、服务器配置要求

| 项目 | 要求 |
|------|------|
| CPU | 2 核及以上（纯 CPU 即可，无需 GPU） |
| 内存 | 最低 4G（模型加载后常驻约 1.2-1.8G） |
| 系统 | Ubuntu 20.04+ / Debian 11+ / CentOS 7+ / Rocky / AlmaLinux / TencentOS |
| 网络 | 部署时需联网下载依赖与模型，部署完成后可离线运行 |
| 磁盘 | 预留 2G 以上（Python 环境 + 依赖 + 模型约 1.2G） |

## 二、快速部署（5 分钟）

### 步骤 1：上传文件

把仓库里这 4 个文件传到服务器（如 `/root`）：

```
ocr_core.py  server.py  video_check.py  requirements.txt  deploy.sh
```

> ⚠️ 不要上传 `.venv`（Windows 本地虚拟环境，对 Linux 无用）。

```bash
scp ocr_core.py server.py video_check.py requirements.txt deploy.sh root@服务器IP:/root/
```

### 步骤 2：执行一键部署

```bash
cd /root
chmod +x deploy.sh
sudo ./deploy.sh
```

脚本会自动完成：安装系统依赖 → 创建虚拟环境 → 安装 Python 依赖 → 预下载 OCR 模型 → 配置 systemd 服务（开机自启、异常自动重启、内存超限重启）。

看到「部署完成！」即成功。

## 三、部署验证

```bash
# 1. 服务状态（active (running) 为正常）
systemctl status idcard-ocr

# 2. 本地健康检查
curl http://127.0.0.1:8000/health
# 返回 {"code":0,"msg":"服务运行正常",...}

# 3. 放行端口
#    Ubuntu/Debian:  ufw allow 8000/tcp
#    CentOS/RHEL:    firewall-cmd --add-port=8000/tcp --permanent && firewall-cmd --reload
#    云服务器还需在控制台「安全组」入方向放行 TCP 8000

# 4. 外部验证：浏览器访问 http://服务器公网IP:8000/health
```

## 四、接口说明

### 身份证识别

```
POST http://服务器IP:8000/api/ocr/idcard
Content-Type: multipart/form-data
参数：file（jpg/png/webp，最大 5MB）
```

返回：

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

### 视频清晰度 / 拉伸检测

```
POST http://服务器IP:8000/api/video/check
Content-Type: application/json
参数：{"video_url": "https://example.com/video.mp4"}
```

返回 `clarity_score`（0-100）、`is_clear`、`is_stretched`、`stretch_desc` 等字段。

## 五、常用运维命令

```bash
systemctl status idcard-ocr      # 状态
journalctl -u idcard-ocr -f      # 实时日志
systemctl restart idcard-ocr     # 重启
systemctl stop idcard-ocr        # 停止
systemctl enable idcard-ocr      # 开机自启
```

## 六、纯内网部署（不能连公网）

1. 在能联网的机器上，把依赖与模型提前下载好：

```bash
# 下载依赖包
pip download -d packages -r requirements.txt

# 预热模型，让其落入 ~/.paddleocr 缓存目录（约 20MB）
python -c "from ocr_core import warmup; warmup()"
```

2. 把 `packages/` 和 `~/.paddleocr/whl` 一起传到内网服务器，`pip install` 改为本地安装即可。

## 七、常见问题

| 现象 | 原因 / 处理 |
|------|------------|
| 服务起不来 | `journalctl -u idcard-ocr -n 50` 看最后 50 行日志 |
| 外部访问不了 | 先 `curl 127.0.0.1:8000/health` 验证本地通；通则查安全组/防火墙 |
| 首次请求慢 | 首次会加载模型（约 10s），之后单张 1-2s；可用 `warmup()` 预热 |
| 内存占用高 | 正常常驻 1.2-1.8G，已配置 3G 上限自动重启 |
| 视频检测报「禁止访问内网地址」 | 这是 SSRF 防护，确需内网视频请设 `OCR_ALLOW_PRIVATE_NETWORK=true` |

## 八、环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OCR_HOST` | `127.0.0.1` | 监听地址，部署时设 `0.0.0.0` |
| `OCR_PORT` | `8000` | 监听端口 |
| `OCR_CORS_ORIGINS` | 空 | 允许跨域来源，逗号分隔 |
| `OCR_ENABLE_DOCS` | `false` | 是否开启 /docs 交互式文档 |
| `OCR_ALLOW_PRIVATE_NETWORK` | `false` | 是否允许视频检测访问内网地址 |
| `OCR_MAX_FILE_SIZE` | `5242880` | 上传大小上限（字节） |
