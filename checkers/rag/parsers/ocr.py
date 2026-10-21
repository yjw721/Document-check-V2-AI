# -*- coding: utf-8 -*-
"""OCR 引擎抽象层（中文优化）。

引擎优先级：RapidOCR（默认，ONNXRuntime 推理，内置中英文识别模型、
无需外网下载、体积约 15MB）→ PaddleOCR → Tesseract（需系统安装 chi_sim）。

设计要点：
- 引擎实例**懒加载 + 全局单例**：RapidOCR 首次初始化要加载 ONNX 模型，
  约 1~3 秒，绝不能在模块导入时触发；
- 全部引擎不可用时 ``available=False``，调用方自动跳过 OCR 而非抛错；
- 识别结果按「从上到下、从左到右」排序，尽量还原阅读顺序。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

_LOCK = threading.RLock()
_ENGINE_CACHE: Dict[str, Any] = {"name": "", "engine": None, "error": ""}


class OcrEngine(ABC):
    """OCR 引擎契约。"""

    name = "base"

    @abstractmethod
    def available(self) -> bool:
        """引擎是否可用（依赖已安装且模型可加载）。"""

    @abstractmethod
    def image_to_text(self, image_bytes: bytes) -> str:
        """图片字节 → 文本；失败返回空串。"""


class RapidOcrEngine(OcrEngine):
    """RapidOCR（ONNXRuntime）—— 中文场景首选。"""

    name = "rapidocr"

    def __init__(self) -> None:
        self._ocr = None
        self._loaded = False
        self._err = ""

    def _ensure(self):
        if self._loaded:
            return self._ocr
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            self._ocr = RapidOCR()
        except Exception as exc:  # noqa: BLE001
            self._err = str(exc)
            self._ocr = None
        self._loaded = True
        return self._ocr

    def available(self) -> bool:
        return self._ensure() is not None

    def image_to_text(self, image_bytes: bytes) -> str:
        ocr = self._ensure()
        if ocr is None or not image_bytes:
            return ""
        try:
            result, _ = ocr(image_bytes)
        except Exception:  # noqa: BLE001 - 单张识别失败不影响整体
            return ""
        lines = _collect(result)
        return "\n".join(lines).strip()


class PaddleOcrEngine(OcrEngine):
    """PaddleOCR —— 未预装时自动跳过。"""

    name = "paddleocr"

    def __init__(self) -> None:
        self._ocr = None
        self._loaded = False

    def _ensure(self):
        if self._loaded:
            return self._ocr
        try:
            from paddleocr import PaddleOCR  # type: ignore
            self._ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except Exception:  # noqa: BLE001
            self._ocr = None
        self._loaded = True
        return self._ocr

    def available(self) -> bool:
        return self._ensure() is not None

    def image_to_text(self, image_bytes: bytes) -> str:
        ocr = self._ensure()
        if ocr is None or not image_bytes:
            return ""
        import io
        try:
            from PIL import Image  # type: ignore
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            import numpy as np  # type: ignore
            res = ocr.ocr(np.asarray(img), cls=True)
        except Exception:  # noqa: BLE001
            return ""
        lines: List[str] = []
        for page in res or []:
            for item in page or []:
                try:
                    lines.append(str(item[1][0]).strip())
                except (IndexError, TypeError):
                    continue
        return "\n".join(x for x in lines if x).strip()


class TesseractEngine(OcrEngine):
    """Tesseract —— 需本机安装并带 chi_sim 语言包。"""

    name = "tesseract"

    def available(self) -> bool:
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            pytesseract.get_tesseract_version()
            return True
        except Exception:  # noqa: BLE001
            return False

    def image_to_text(self, image_bytes: bytes) -> str:
        if not image_bytes:
            return ""
        try:
            import io
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            img = Image.open(io.BytesIO(image_bytes))
            return str(pytesseract.image_to_string(img, lang="chi_sim+eng") or "").strip()
        except Exception:  # noqa: BLE001
            return ""


def _collect(result: Any) -> List[str]:
    """从 RapidOCR 结果里取出文本行，并按纵向位置排序。

    RapidOCR 返回 ``[[box, text, score], ...]``，box 为 4 点坐标；
    仅按 box[0][1]（左上角 y）排序即可近似还原行序。
    """
    rows: List[Tuple[float, float, str]] = []
    for item in result or []:
        try:
            box, text, score = item[0], item[1], item[2]
        except (IndexError, TypeError):
            continue
        t = str(text or "").strip()
        if not t:
            continue
        try:
            y = float(box[0][1])
            x = float(box[0][0])
        except (IndexError, TypeError, ValueError):
            y = x = 0.0
        try:
            if float(score) < 0.45:      # 低置信度行直接丢弃，减少噪声
                continue
        except (TypeError, ValueError):
            pass
        rows.append((y, x, t))
    rows.sort(key=lambda r: (round(r[0] / 12.0), r[1]))   # 12px 内视为同一行
    return [r[2] for r in rows]


#: 自动探测顺序
_CANDIDATES = (RapidOcrEngine, PaddleOcrEngine, TesseractEngine)


def get_engine(preferred: str = "auto") -> Optional[OcrEngine]:
    """返回可用 OCR 引擎；无可用引擎时返回 None。

    ``preferred`` 为 ``auto`` 时按 ``_CANDIDATES`` 顺序探测；
    指定引擎名时优先尝试，失败再回退自动探测。
    """
    want = str(preferred or "auto").strip().lower()
    with _LOCK:
        if _ENGINE_CACHE.get("engine") is not None and _ENGINE_CACHE.get("name") == want:
            return _ENGINE_CACHE["engine"]
        engine: Optional[OcrEngine] = None
        err = ""
        if want not in ("auto", "", "none", "off", "false"):
            for cls in _CANDIDATES:
                if cls.name == want:
                    try:
                        inst = cls()
                        if inst.available():
                            engine = inst
                        else:
                            err = f"{want} 不可用"
                    except Exception as exc:  # noqa: BLE001
                        err = str(exc)
                    break
        if engine is None and not err:
            for cls in _CANDIDATES:
                try:
                    inst = cls()
                    if inst.available():
                        engine = inst
                        break
                except Exception:  # noqa: BLE001
                    continue
        if engine is None and not err:
            err = "未检测到可用 OCR 引擎（建议安装 rapidocr_onnxruntime）"
        _ENGINE_CACHE.update(name=want, engine=engine, error=err)
        return engine


def ocr_status(preferred: str = "auto") -> Dict[str, Any]:
    """供前端展示的 OCR 可用性诊断。"""
    eng = get_engine(preferred)
    return {
        "available": eng is not None,
        "engine": eng.name if eng else "",
        "error": "" if eng else str(_ENGINE_CACHE.get("error") or ""),
        "engines": [{"id": c.name, "installed": _installed(c)} for c in _CANDIDATES],
    }


def _installed(cls) -> bool:
    import importlib.util as u
    return u.find_spec("rapidocr_onnxruntime" if cls is RapidOcrEngine
                       else "paddleocr" if cls is PaddleOcrEngine
                       else "pytesseract") is not None


def ocr_image(image_bytes: bytes, preferred: str = "auto") -> str:
    """便捷入口：识别单张图片，返回文本（无引擎时返回空串）。"""
    eng = get_engine(preferred)
    if eng is None or not image_bytes:
        return ""
    return eng.image_to_text(image_bytes)
