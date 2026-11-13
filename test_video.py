# -*- coding: utf-8 -*-
"""
视频检测接口测试脚本

用法：
    python test_video.py "https://example.com/video.mp4"
    或
    python test_video.py    # 使用 TEST_VIDEO_URL 环境变量

注意：请勿在代码里硬编码带签名密钥的真实视频地址，否则会泄露访问凭证。
"""
import json
import os
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = os.getenv("OCR_BASE_URL", "http://127.0.0.1:8000")
test_url = ""

if len(sys.argv) > 1:
    test_url = sys.argv[1]
else:
    test_url = os.getenv("TEST_VIDEO_URL", "")
    if not test_url:
        print("用法：python test_video.py \"https://example.com/video.mp4\"")
        print("或设置环境变量 TEST_VIDEO_URL")
        sys.exit(1)

print(f"正在测试视频检测接口：{BASE_URL}/api/video/check")
print(f"视频 URL：{test_url}")
print("检测中，请稍候...")

try:
    resp = requests.post(
        f"{BASE_URL}/api/video/check",
        json={"video_url": test_url},
        timeout=30,
    )
    print("状态码：", resp.status_code)
    print("返回结果：")
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
except Exception as e:
    print("请求失败：", str(e))
