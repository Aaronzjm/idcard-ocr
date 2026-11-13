# -*- coding: utf-8 -*-
"""
视频清晰度 + 拉伸检测核心模块
功能：传入公网视频 URL，检测画面清晰度，判断是否被非等比例拉伸变形

安全说明：
本模块会由服务端主动下载用户提供的 URL，存在 SSRF（服务端请求伪造）风险——
攻击者可借此探测内网服务或云厂商元数据接口（如 169.254.169.254）。
因此默认拦截私网/回环/链路本地地址，确需检测内网视频时设 OCR_ALLOW_PRIVATE_NETWORK=true。
"""
import ipaddress
import os
import socket
import tempfile
import time
from typing import Dict, Tuple
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

# 单次最多下载的字节数：只需读到中间帧即可判定，无需下完整个视频
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
# 是否允许访问内网地址（默认禁止，防止 SSRF 探测内网）
ALLOW_PRIVATE_NETWORK = os.getenv("OCR_ALLOW_PRIVATE_NETWORK", "false").lower() in ("1", "true", "yes")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _assert_public_url(url: str) -> Tuple[bool, str]:
    """
    SSRF 防护：校验目标主机解析出的 IP 是否为公网地址。
    :return: (是否通过, 拒绝原因)
    """
    if ALLOW_PRIVATE_NETWORK:
        return True, ""
    try:
        host = urlparse(url).hostname
        if not host:
            return False, "URL 中缺少有效主机名"
        # 解析域名为 IP，逐个校验（一个域名可能解析到多个地址）
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                return False, f"出于安全考虑，禁止访问内网地址（{ip}）。确需检测内网视频请设置 OCR_ALLOW_PRIVATE_NETWORK=true"
        return True, ""
    except socket.gaierror:
        return False, "域名解析失败，请检查 URL 是否正确"
    except ValueError as e:
        return False, f"URL 格式非法：{e}"


def calculate_clarity_score(frame: np.ndarray) -> Tuple[float, str]:
    """
    计算画面清晰度分数（拉普拉斯方差法，业界通用无参考清晰度评估）
    :return: (分数 0-100, 等级：清晰/一般/模糊)
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 拉普拉斯算子求边缘方差：方差越大边缘越锐利，画面越清晰
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    # 经验归一化：<20 极模糊，20-60 一般，60+ 清晰
    score = min(100.0, max(0.0, laplacian_var / 8.0 * 10))
    level = "清晰" if score >= 60 else ("一般" if score >= 30 else "模糊")
    return round(score, 1), level


def detect_stretch(frame: np.ndarray) -> Tuple[bool, str, float]:
    """
    检测画面是否被非等比例拉伸变形
    原理：正常画面水平/垂直方向的 Sobel 梯度能量比值落在固定区间，拉伸后比值异常偏移
    :return: (是否拉伸, 说明文字, 拉伸系数)
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    energy_x = float(np.mean(np.abs(grad_x)))
    energy_y = float(np.mean(np.abs(grad_y)))
    # 正常画面梯度比值约在 0.8~1.2，超出判定为拉伸
    ratio = energy_x / (energy_y + 1e-6)

    stretch_ratio = round(abs(ratio - 1.0) * 100, 1)
    if ratio < 0.75:
        return True, f"画面被垂直拉长（人/物体变瘦高），拉伸程度约 {stretch_ratio}%", stretch_ratio
    if ratio > 1.25:
        return True, f"画面被水平拉长（人/物体变矮胖），拉伸程度约 {stretch_ratio}%", stretch_ratio
    return False, "画面比例正常，无拉伸变形", stretch_ratio


def _download_head(url: str, timeout: int) -> Tuple[str, None]:
    """
    流式下载视频前 MAX_DOWNLOAD_BYTES 到临时文件。
    不直接把 URL 交给 OpenCV：其 FFmpeg 后端对带签名 query 参数的 HTTPS 流支持很差，
    先落盘再读可 100% 兼容各种地址。
    """
    with requests.get(url, headers={"User-Agent": _UA}, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            temp_path = f.name
            downloaded = 0
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if downloaded >= MAX_DOWNLOAD_BYTES:
                    break
    return temp_path, None


def check_video_quality(video_url: str, timeout: int = 15) -> Tuple[int, str, Dict]:
    """
    主检测函数
    :param video_url: 公网可访问的视频地址（OpenCV 支持的格式，如 mp4）
    :param timeout: 网络超时时间（秒）
    :return: (code, msg, data)
        0   检测成功
        400 参数错误 / 安全拦截
        404 视频无法访问或格式不支持
        500 检测失败
    """
    start_time = time.time()
    result = {
        "video_url": video_url,
        "clarity_score": 0,
        "clarity_level": "",
        "is_clear": False,
        "is_stretched": False,
        "stretch_desc": "",
        "stretch_ratio": 0,
        "video_width": 0,
        "video_height": 0,
        "sample_frame_position": 0,
        "cost_ms": 0,
    }

    # SSRF 校验前置，避免无谓的下载开销
    ok, reason = _assert_public_url(video_url)
    if not ok:
        return 400, reason, result

    temp_path = None
    cap = None
    try:
        temp_path, _ = _download_head(video_url, timeout)

        cap = cv2.VideoCapture(temp_path)
        if not cap.isOpened():
            return 404, "无法打开视频，请检查 URL 是否可访问、格式是否受支持", result

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        result["video_width"] = video_width
        result["video_height"] = video_height

        if total_frames <= 0 or video_width <= 0 or video_height <= 0:
            return 404, "无效的视频文件，无法读取视频信息", result

        # 取已下载部分的中间帧，避开片头黑帧
        target_frame = min(total_frames // 2, total_frames - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        if not ret or frame is None:
            return 500, "视频帧读取失败", result

        result["sample_frame_position"] = int(target_frame / fps) if fps > 0 else int(target_frame)

        clarity_score, clarity_level = calculate_clarity_score(frame)
        result["clarity_score"] = float(clarity_score)
        result["clarity_level"] = clarity_level
        result["is_clear"] = bool(clarity_score >= 60)

        is_stretched, stretch_desc, stretch_ratio = detect_stretch(frame)
        result["is_stretched"] = bool(is_stretched)
        result["stretch_desc"] = stretch_desc
        result["stretch_ratio"] = float(stretch_ratio)

        result["cost_ms"] = int(round((time.time() - start_time) * 1000))
        return 0, "检测完成", result

    except requests.exceptions.Timeout:
        return 404, f"视频下载超时（>{timeout}s），请检查网络或更换地址", result
    except requests.exceptions.RequestException as e:
        return 404, f"视频地址访问失败：{e}", result
    except Exception as e:
        return 500, f"检测异常：{e}", result
    finally:
        if cap is not None:
            cap.release()
        # Windows 上文件被 VideoCapture 占用时 unlink 会失败，需先 release 再删
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
