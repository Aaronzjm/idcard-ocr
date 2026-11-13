# -*- coding: utf-8 -*-
"""
身份证 OCR 核心识别模块
基于 PaddleOCR，支持正反面自动识别、结构化字段提取、身份证号合法性校验

设计要点：
- OCR 模型懒加载（首次调用时才初始化），服务启动不阻塞，/health 可立即响应
- 模型初始化加锁，避免并发请求下重复加载导致内存暴涨
"""
import re
import threading
from typing import Dict, List, Tuple

import cv2
import numpy as np

# -------------------------- 全局配置 --------------------------
# 身份证号加权因子和校验码映射（国标 GB 11643-1999）
ID_CARD_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
ID_CARD_CHECK_CODES = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]

# 正反面判定关键词
FRONT_KEYWORDS = {"姓名", "性别", "民族", "出生", "住址", "公民身份号码"}
BACK_KEYWORDS = {"签发机关", "有效期限"}

# OCR 引擎参数
_OCR_CONFIG = {
    "use_angle_cls": True,   # 支持旋转/倒拍证件，不需要可置 False 提速
    "lang": "ch",
    "det_db_thresh": 0.2,    # 降低检测阈值，避免漏检浅色/模糊文字
    "det_db_box_thresh": 0.3,
    "det_db_unclip_ratio": 2.0,  # 扩大检测框范围，避免文字被截断
    "rec_batch_num": 6,
    "drop_score": 0.3,       # 降低置信度阈值，弱文字也保留
}

# 签发机关名称补全表：key=OCR 识别结果前缀，value=补全后的完整名称。
# OCR 漏字时（如「市公安局」漏掉首字）可在此登记本地区常见机关名做纠正。
# 默认为空，避免把特定地区的名称硬编码进开源代码；按需在部署环境自行配置。
AUTHORITY_FIXES: Dict[str, str] = {}

# -------------------------- OCR 引擎懒加载 --------------------------
_ocr_engine = None
_ocr_lock = threading.Lock()


def get_ocr():
    """
    获取全局 OCR 引擎实例（懒加载 + 双检锁）。
    首次调用会下载模型（约 20MB）并初始化，耗时较长；后续调用直接复用。
    """
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            # 延迟到函数内部导入，避免模块导入期就触发模型加载
            from paddleocr import PaddleOCR

            _ocr_engine = PaddleOCR(show_log=False, **_OCR_CONFIG)
            print("[OK] OCR 模型加载完成")
    return _ocr_engine


def warmup() -> None:
    """预热：提前加载模型，可在服务启动后由运维脚本调用，避免首个请求超时。"""
    get_ocr()


# -------------------------- 工具函数 --------------------------
def validate_id_card(id_num: str) -> bool:
    """
    校验 18 位身份证号合法性（GB 11643-1999 校验码算法）
    :param id_num: 身份证号字符串
    :return: True=合法
    """
    if len(id_num) != 18:
        return False
    if not id_num[:17].isdigit():
        return False
    if not (id_num[17].isdigit() or id_num[17].upper() == "X"):
        return False
    total = sum(int(id_num[i]) * ID_CARD_WEIGHTS[i] for i in range(17))
    return id_num[17].upper() == ID_CARD_CHECK_CODES[total % 11]


def preprocess_image(image_bytes: bytes) -> np.ndarray:
    """图片预处理：解码 + 缩放 + 对比度增强，提升模糊图片识别率"""
    img_array = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("图片损坏或格式不支持，请上传 jpg/png/webp 图片")

    # 归一化到合适宽度：过大浪费算力，过小容易漏检小字
    h, w = img.shape[:2]
    if w > 1400:
        scale = 1200 / w
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    elif w < 800:
        scale = 1000 / w
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    # 灰度 + CLAHE 自适应直方图均衡化，增强对比度
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def ocr_recognize(img: np.ndarray) -> List[Dict]:
    """
    调用 PaddleOCR 识别全部文字，返回带坐标、文本、置信度的结构化结果
    :return: 文字块列表，每项含 bbox / text / confidence / x_center / y_center
    """
    result = get_ocr().ocr(img, cls=True)
    text_blocks: List[Dict] = []
    if not result or not result[0]:
        return text_blocks

    for line in result[0]:
        bbox, (text, conf) = line
        text_blocks.append({
            "bbox": bbox,
            "text": text.strip(),
            "confidence": round(float(conf), 3),
            # bbox 是四点坐标，取几何中心用于行列归组
            "y_center": sum(p[1] for p in bbox) / 4,
            "x_center": sum(p[0] for p in bbox) / 4,
        })
    # 先按 y 分行，同行再按 x 排序，还原阅读顺序
    text_blocks.sort(key=lambda x: (x["y_center"], x["x_center"]))
    return text_blocks


def detect_side(text_blocks: List[Dict]) -> str:
    """
    根据关键词判断正反面
    :return: front=人像面, back=国徽面, unknown=非身份证
    """
    all_text = "".join(b["text"] for b in text_blocks)
    front_hits = sum(1 for kw in FRONT_KEYWORDS if kw in all_text)
    back_hits = sum(1 for kw in BACK_KEYWORDS if kw in all_text)

    if front_hits >= 2:
        return "front"
    if back_hits >= 1:
        return "back"
    return "unknown"


def merge_blocks_by_line(text_blocks: List[Dict], y_threshold: int = 45) -> List[str]:
    """
    按行合并 OCR 文本块：y 坐标差小于阈值视为同一行，按 x 排序后拼接。
    解决 OCR 把一行文字拆成多块导致的字段提取失败问题。
    """
    if not text_blocks:
        return []

    sorted_blocks = sorted(text_blocks, key=lambda x: x["y_center"])
    lines: List[str] = []
    current_line = [sorted_blocks[0]]
    current_y = sorted_blocks[0]["y_center"]

    def _flush(blocks: List[Dict]) -> str:
        # 过滤置信度过低的识别残片，减少噪声干扰
        kept = [b for b in blocks if b["confidence"] >= 0.5]
        kept.sort(key=lambda x: x["x_center"])
        return "".join(b["text"] for b in kept).replace(" ", "")

    for b in sorted_blocks[1:]:
        if abs(b["y_center"] - current_y) < y_threshold:
            current_line.append(b)
        else:
            lines.append(_flush(current_line))
            current_line = [b]
            current_y = b["y_center"]
    lines.append(_flush(current_line))
    return lines


# -------------------------- 正面字段提取 --------------------------
def extract_front_fields(text_blocks: List[Dict]) -> Dict:
    """提取人像面字段：姓名、性别、民族、出生、住址、公民身份号码"""
    fields = {
        "name": None,
        "gender": None,
        "nation": None,
        "birth": None,
        "address": None,
        "id_number": None,
        "id_valid": False,
    }
    all_text = "".join(b["text"] for b in text_blocks)

    # 1. 身份证号：18 位定长且可校验，最容易准确定位，优先提取
    id_num = None
    for b in text_blocks:
        clean = b["text"].replace(" ", "").replace("公民身份号码", "").replace("公民身份号", "")
        if len(clean) < 18:
            continue
        for i in range(len(clean) - 17):
            candidate = clean[i:i + 18]
            if validate_id_card(candidate):
                id_num = candidate
                fields["id_number"] = candidate
                fields["id_valid"] = True
                break
        if id_num:
            break

    # 2. 姓名：同块提取 → 相邻块提取 → 位置兜底，三级策略
    name_found = False
    for i, b in enumerate(text_blocks):
        text = b["text"].replace(" ", "")
        if "姓名" not in text:
            continue
        # 情况 1：「姓名张三」在同一块内
        match = re.search(r"[\u4e00-\u9fa5]{2,4}", text.split("姓名")[-1].strip())
        if match:
            fields["name"] = match.group()
            name_found = True
            break
        # 情况 2：「姓名」与人名被拆成相邻两块
        if i + 1 < len(text_blocks):
            match = re.search(r"[\u4e00-\u9fa5]{2,4}", text_blocks[i + 1]["text"].replace(" ", "").strip())
            if match:
                fields["name"] = match.group()
                name_found = True
                break

    # 情况 3：兜底——姓名位于身份证号左上方，取 x 轴对齐最好的 2-4 字中文块
    if not name_found and id_num:
        id_block = next((b for b in text_blocks if id_num in b["text"].replace(" ", "")), None)
        if id_block:
            candidates = []
            for b in text_blocks:
                dy = id_block["y_center"] - b["y_center"]
                if 0 < dy < 250 and b["x_center"] < id_block["x_center"] + 100:
                    t = b["text"].replace(" ", "")
                    if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t):
                        candidates.append((abs(b["x_center"] - id_block["x_center"]), t))
            if candidates:
                candidates.sort()
                fields["name"] = candidates[0][1]

    # 3. 性别
    if "男" in all_text:
        fields["gender"] = "男"
    elif "女" in all_text:
        fields["gender"] = "女"

    # 4. 民族：兼容「民族汉」「民族 汉」等识别变体
    nation_match = re.search(r"民族[\u4e00-\u9fa5]", all_text)
    if nation_match:
        fields["nation"] = nation_match.group()[-1] + "族"
    elif "汉" in all_text and fields["gender"] == "男":
        fields["nation"] = "汉族"

    # 5. 出生日期
    for b in text_blocks:
        if "出生" in b["text"]:
            birth_text = b["text"].replace("出生", "").strip()
            if all(k in birth_text for k in ("年", "月", "日")):
                fields["birth"] = birth_text
                break

    # 6. 住址：截取到「公民身份号码」之前，避免把号码并入地址
    address_start, id_start = -1, len(text_blocks)
    for i, b in enumerate(text_blocks):
        if "住址" in b["text"]:
            address_start = i
        if "公民身份" in b["text"] or (id_num and id_num in b["text"].replace(" ", "")):
            id_start = i
            break
    if address_start != -1:
        parts = []
        for j in range(address_start, min(address_start + 4, id_start)):
            t = text_blocks[j]["text"].replace("住址", "").replace("住 ", "").strip()
            if t and "公民身份" not in t:
                parts.append(t)
        fields["address"] = "".join(parts)

    return fields


# -------------------------- 反面字段提取 --------------------------
def _fix_authority(text: str) -> str:
    """
    签发机关名称纠偏：补齐 OCR 常漏的「局」字，并按 AUTHORITY_FIXES 表纠正已知错字。
    """
    if not text:
        return text
    if not text.endswith("局"):
        text += "局"
    return AUTHORITY_FIXES.get(text, text)


def extract_back_fields(text_blocks: List[Dict]) -> Dict:
    """提取国徽面字段：签发机关、有效期限"""
    fields = {"authority": None, "valid_date": None}

    # 先按行合并，避免分块漏字
    lines = merge_blocks_by_line(text_blocks)
    all_text = "".join(lines)

    date_pattern = r"\d{4}\.\d{2}\.\d{2}-(\d{4}\.\d{2}\.\d{2}|长期)"

    # 1. 有效期限格式固定，优先匹配；签发机关在其上一行
    for idx, line in enumerate(lines):
        if not re.search(date_pattern, line):
            continue
        fields["valid_date"] = re.search(date_pattern, line).group()
        if idx > 0:
            # 去掉「签发机关」前缀（容忍「发机关」「签机关」等识别误差）
            auth_line = re.sub(r"^.*机关", "", lines[idx - 1])
            auth_match = re.search(r"[\u4e00-\u9fa5]*[市县区]公安局", auth_line)
            if auth_match:
                fields["authority"] = auth_match.group()
        break

    # 2. 兜底：从所有行里模糊匹配公安机关名称
    if not fields["authority"]:
        for line in lines:
            # 剔除「签发机关」「有效期限」等标签残片，避免拼进机关名
            line_clean = re.sub(r"[签发机关有效期]", "", line)
            auth_match = re.search(r"([\u4e00-\u9fa5]*[市县区]?)公安(局)?", line_clean)
            if auth_match:
                fields["authority"] = _fix_authority(auth_match.group())
                break

    # 3. 最后兜底：整段文本匹配有效期
    if not fields["valid_date"]:
        m = re.search(date_pattern, all_text)
        if m:
            fields["valid_date"] = m.group()

    return fields


# -------------------------- 对外主入口 --------------------------
def recognize_id_card(image_bytes: bytes) -> Tuple[int, str, Dict]:
    """
    主识别函数：输入图片二进制，返回结构化结果
    :return: (code, msg, data)
        0   识别成功
        400 参数错误（图片损坏/格式不支持）
        404 未识别到身份证
        500 识别失败/关键字段缺失
    """
    try:
        img = preprocess_image(image_bytes)
        text_blocks = ocr_recognize(img)
        if not text_blocks:
            return 404, "未识别到任何文字，请上传清晰的身份证照片", {}

        side = detect_side(text_blocks)
        if side == "unknown":
            return 404, "未识别到有效身份证，请上传清晰的身份证正反面照片", {}

        result = {"side": side}
        if side == "front":
            result.update(extract_front_fields(text_blocks))
            if not result["id_number"] or not result["name"]:
                return 500, "识别失败，关键字段缺失，请重新拍摄上传", result
        else:
            result.update(extract_back_fields(text_blocks))
            # 反面只要拿到一个字段就返回成功，避免小瑕疵拦截正确结果
            if not result["authority"] and not result["valid_date"]:
                return 500, "识别失败，关键字段缺失，请重新拍摄上传", result

        return 0, "识别成功", result

    except ValueError as e:
        return 400, str(e), {}
    except Exception as e:
        return 500, f"识别服务内部错误: {e}", {}


# -------------------------- 本地测试入口 --------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：python ocr_core.py <身份证图片路径>")
        sys.exit(1)
    with open(sys.argv[1], "rb") as f:
        img_bytes = f.read()
    code, msg, data = recognize_id_card(img_bytes)
    print(f"状态码：{code}")
    print(f"提示：{msg}")
    print(f"结果：{data}")
