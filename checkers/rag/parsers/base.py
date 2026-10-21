# -*- coding: utf-8 -*-
"""解析器公共层。

统一产出 ``Block = (位置标识, 文本)`` 列表，后续切片层无需关心来源格式。
"""

from __future__ import annotations

import re
from typing import List, Tuple

#: 一个「块」：``(位置标识, 文本内容)``
Block = Tuple[str, str]

_ENCODINGS = ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "big5")

_WS_RUN = re.compile(r"[ \t\u3000]+")
_BLANK_RUN = re.compile(r"\n{3,}")


def decode_text(raw: bytes) -> str:
    """按常见中文编码依次尝试解码，全部失败时用 UTF-8 替换。"""
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def normalize(text: str) -> str:
    """统一换行、去掉行内连续空白、压缩超长空行。

    PDF 抽取常带大量软换行与排版空格，不处理会污染标题识别与切片。
    """
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ").replace("\ufeff", "")
    lines = [_WS_RUN.sub(" ", ln).strip() for ln in t.split("\n")]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def lines_to_blocks(text: str, prefix: str = "第 %d 行") -> List[Block]:
    """纯文本 → 逐行块（空行丢弃）。"""
    out: List[Block] = []
    for i, ln in enumerate(str(text or "").split("\n"), 1):
        s = ln.strip()
        if len(s) >= 2:
            out.append((prefix % i, s))
    return out
