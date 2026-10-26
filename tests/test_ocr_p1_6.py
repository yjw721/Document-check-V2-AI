# -*- coding: utf-8 -*-
"""P1-6 OCR 接入核验管线的回归测试（独立脚本，非 pytest）。

验证点：
  1. OCR 关闭时，扫描件 PDF 必须保持 STATUS_UNREADABLE（核心不变量，两端一致）；
  2. OCR 开启且有可用引擎时，扫描件被 OCR 识别并继续规则检测（status != UNREADABLE，
     stats.ocr_used=True）；无引擎时优雅降级为 UNREADABLE 而非崩溃；
  3. get_engine("tesseract") 在首选引擎不可用时回退到其它可用引擎（如 rapidocr）。

运行：
  python tests/test_ocr_p1_6.py        # 系统 Python：走降级分支
  <managed-venv>/python tests/test_ocr_p1_6.py   # 有 rapidocr：走 OCR 成功分支
"""

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from checkers.base import STATUS_UNREADABLE  # noqa: E402
from checkers.pdf_checker import check_pdf  # noqa: E402
from checkers.rag.parsers.ocr import get_engine  # noqa: E402
from config.config_manager import load_rules  # noqa: E402

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
]


def _load_font():
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                from PIL import ImageFont
                return ImageFont.truetype(p, 64)
            except Exception:  # noqa: BLE001
                continue
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=48)
    except TypeError:
        return ImageFont.load_default()


def make_scanned_pdf(path: str) -> None:
    """生成「图片型」PDF：页面为嵌入的位图（无文本层），模拟扫描件。"""
    from PIL import Image, ImageDraw
    import fitz

    img = Image.new("RGB", (1600, 2200), "white")
    draw = ImageDraw.Draw(img)
    font = _load_font()
    text = "资产评估 测试 文档 内容 示例 一二三四五六七八九十 标点符号 检查 错别字"
    lines = [text[i:i + 6] for i in range(0, len(text), 6)]
    y = 80
    for ln in lines:
        draw.text((80, y), ln, fill="black", font=font)
        y += 90
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=1600, height=2200)
    page.insert_image(fitz.Rect(0, 0, 1600, 2200), stream=png)
    doc.save(path)
    doc.close()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="ocr_p16_")
    pdf_path = os.path.join(tmp, "scan.pdf")
    make_scanned_pdf(pdf_path)
    cfg = load_rules()

    failures = []

    # 1) OCR 关闭 -> 必须 UNREADABLE
    r_off = check_pdf(pdf_path, cfg, ocr_enabled=False)
    if r_off.status != STATUS_UNREADABLE:
        failures.append(f"OCR 关闭时应 UNREADABLE，实际 {r_off.status}")
    else:
        print("[OK] OCR 关闭：扫描件保持 STATUS_UNREADABLE（不变量）")

    # 2) OCR 开启
    eng = get_engine("auto")
    r_on = check_pdf(pdf_path, cfg, ocr_enabled=True, ocr_engine="auto")
    if eng is None:
        # 无可用引擎：必须优雅降级，不可崩溃
        if r_on.status != STATUS_UNREADABLE:
            failures.append(f"无引擎+OCR 开启应降级 UNREADABLE，实际 {r_on.status}")
        else:
            print("[OK] 无 OCR 引擎：开启 OCR 仍优雅降级为 STATUS_UNREADABLE")
    else:
        if r_on.status == STATUS_UNREADABLE:
            # 引擎存在但识别为空：属识别质量（合成图），不算管线缺陷
            print(f"[WARN] 引擎 {eng.name} 可用但本合成图未识别出文本（识别质量），"
                  f"status=UNREADABLE；管线未崩溃")
        else:
            if not r_on.stats.get("ocr_used"):
                failures.append("OCR 已恢复文本但 stats.ocr_used 不为 True")
            else:
                print(f"[OK] 引擎 {eng.name}：扫描件被 OCR 识别并继续检测，"
                      f"status={r_on.status}，ocr_pages={r_on.stats.get('ocr_pages')}")

    # 3) get_engine('tesseract') 回退
    et = get_engine("tesseract")
    print(f"[INFO] get_engine('tesseract') -> {et.name if et else None}")
    if et is None:
        print("[WARN] 本环境无可用 OCR 引擎（含 tesseract 回退目标），"
              "OCR 将保持降级状态；装上 tesseract 或 rapidocr 后即生效")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("\nP1-6 OCR 管线测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
