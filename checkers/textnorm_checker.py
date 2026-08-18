# -*- coding: utf-8 -*-
"""
文字规范 / 表述问题检测引擎（本地词库 + 正则，全程离线）
=============================================================
检测内容（对应 config/rules.json 的 textnorm 段落）：
    1.  tn_colloquial  不规范用词 / 口语化表述
    2.  tn_redundant   重复冗余词句（语义重叠语病）
    3.  tn_confusable  易混淆近义词误用（制定/制订、权利/权力…）
    4.  tn_ambiguous   歧义句式 / 过长无标点长句 / 表意模糊词
    5.  tn_typo        中文错别字 / 常见易写错词语
    6.  tn_en_typo     英文拼写错误（recieve/receive、goverment/government…）
    7.  tn_abbrev      非正式简称 / 自创缩写
    8.  tn_units       数量 / 单位表述不统一
9.  tn_grammar     中英文语法错误（句式杂糅、关联词误配、主谓不一致、
                       时态 / 冠词 / 比较级误用等，本地正则）
    10. tn_vocab       中英文词汇搭配不当 / 用词错误（动宾搭配、语义赘余、
                       不可数名词量词误用等，本地正则）
    11. tn_asset_terms 资产评估术语表述不规范（依据《资产评估准则术语2020》：
                       术语误写 / 非规范变体，如 现金流折现法→现金流量折现法、
                       评估基准日期→评估基准日、委托方→委托人）

设计原则：
    - 仅依赖本地词库（dictionaries/*.txt）与本地正则，不联网、不调用任何
      外部大模型 / 语义接口、不上传文档内容。
    - 所有命中一律标记为【疑似表述不当/用词不规范，仅供人工复核】，
      不判定为确定性错误，最终是否修改由人工决定。
    - 词库文件用户可随时编辑增删，下一次检测自动生效。

保密说明：本模块仅在本地读取词库文本与文档文本，无任何网络行为。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from checkers.base import Issue, clip

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
_DICT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dictionaries")

# 疑似标记：附加在每条问题 detail 末尾，明确其“非确定性”性质
SUSPECT_TAG = "【疑似表述不当/用词不规范，仅供人工复核】"

# 过长无标点长句默认阈值（字），可被 rules.json textnorm.tn_ambiguous.max_sentence_len 覆盖
DEFAULT_MAX_SENTENCE_LEN = 80

# 句末标点（用于长句切分）
_SENT_END_RE = re.compile(r"[。！？；\n]")
# 自创 / 未定义缩写启发式：2~5 位连续大写字母
_ALLCAPS_RE = re.compile(r"\b[A-Z]{2,5}\b")


# ---------------------------------------------------------------------------
# 词库加载（纯本地文件读取）
# ---------------------------------------------------------------------------
def _read_lines(path: str) -> List[str]:
    """读取词库文本，去掉空行与 # 注释行，返回行列表。文件缺失返回空列表。"""
    if not os.path.exists(path):
        return []
    out: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fp:
            for ln in fp:
                s = ln.strip()
                if not s or s.startswith("#"):
                    continue
                out.append(s)
    except OSError:
        return []
    return out


def _load_kv(path: str) -> List[Tuple[str, str]]:
    """加载 短语[|说明] 形式的词库，返回 [(短语, 说明), ...]。"""
    out: List[Tuple[str, str]] = []
    for ln in _read_lines(path):
        if "|" in ln:
            a, b = ln.split("|", 1)
            out.append((a.strip(), b.strip()))
        else:
            out.append((ln.strip(), ""))
    return out


def _load_confusable(path: str) -> List[Tuple[Tuple[str, str], str]]:
    """加载 词A|词B|辨析 形式的近义词词库。"""
    out: List[Tuple[Tuple[str, str], str]] = []
    for ln in _read_lines(path):
        parts = ln.split("|", 2)
        if len(parts) >= 2:
            a = parts[0].strip()
            b = parts[1].strip()
            note = parts[2].strip() if len(parts) == 3 else ""
            if a and b:
                out.append(((a, b), note))
    return out


def _load_typo(path: str) -> List[Tuple[str, str]]:
    """加载 错误写法|正确写法 形式的错别字词库。"""
    out: List[Tuple[str, str]] = []
    for ln in _read_lines(path):
        if "|" in ln:
            a, b = ln.split("|", 1)
            a, b = a.strip(), b.strip()
            if a and b:
                out.append((a, b))
    return out


def _load_units(path: str) -> List[Tuple[re.Pattern, str]]:
    """加载 正则|建议 形式的单位词库（正则不区分大小写）。"""
    out: List[Tuple[re.Pattern, str]] = []
    for ln in _read_lines(path):
        if "|" not in ln:
            continue
        pat, sug = ln.split("|", 1)
        try:
            out.append((re.compile(pat.strip(), re.IGNORECASE), sug.strip()))
        except re.error:
            continue
    return out


def _load_patterns(path: str) -> List[Tuple[re.Pattern, str]]:
    """加载 正则模式|说明 形式的词库（语法/词汇规则，不区分大小写）。

    说明文字固定在行尾，故按最右侧的 | 切分，正则内部的交替符 | 不受影响；
    非法正则行自动跳过，不影响其余词条加载。
    """
    out: List[Tuple[re.Pattern, str]] = []
    for ln in _read_lines(path):
        if "|" not in ln:
            continue
        pat, note = ln.rsplit("|", 1)
        try:
            out.append((re.compile(pat.strip(), re.IGNORECASE), note.strip()))
        except re.error:
            continue
    return out


def _load_en_typo(path: str) -> List[Tuple[re.Pattern, str, str]]:
    """加载 错误拼写|正确拼写 形式的英文错词库。

    返回 [(整词匹配正则, 错误词根, 正确拼写), ...]。按「词根 + 可选常见词缀
    (ing/es/ed/ly/s/d)」整词匹配，大小写不敏感，使 recieved / recieving 等
    变形同样可命中，且建议中保留正确的词缀。
    """
    out: List[Tuple[re.Pattern, str, str]] = []
    _SUFFIX = r"(?:ing|es|ed|ly|s|d)?"
    for ln in _read_lines(path):
        if "|" not in ln:
            continue
        err, corr = ln.split("|", 1)
        err, corr = err.strip(), corr.strip()
        if not err or not corr:
            continue
        try:
            out.append((re.compile(r"\b" + re.escape(err) + _SUFFIX + r"\b",
                                   re.IGNORECASE), err, corr))
        except re.error:
            continue
    return out


def _load_abbrev(path: str) -> Tuple[List[Tuple[str, str]], set]:
    """加载缩写词库：返回 (不建议使用的简称列表, 白名单集合)。"""
    forbidden: List[Tuple[str, str]] = []
    allow: set = set()
    in_white = False
    for raw in _read_lines(path):
        if raw == "-----WHITELIST-----":
            in_white = True
            continue
        if in_white:
            allow.add(raw.strip().upper())
        elif "|" in raw:
            a, b = raw.split("|", 1)
            forbidden.append((a.strip(), b.strip()))
        else:
            forbidden.append((raw.strip(), ""))
    return forbidden, allow


class TextNormChecker:
    """文字规范检测器：对传入的文本片段做 7 类本地匹配检测。"""

    KIND = "textnorm"

    def __init__(self, config: Any, issues_out: List[Issue], limit: int) -> None:
        self.cfg = config
        self.issues = issues_out          # 与调用方共享的同一问题列表（共用上限保护）
        self._limit = limit
        self._banks = self._load_banks()

    # ------------------------------------------------------------------
    # 词库加载
    # ------------------------------------------------------------------
    def _load_banks(self) -> Dict[str, Any]:
        return {
            "colloquial": _load_kv(os.path.join(_DICT_DIR, "colloquial.txt")),
            "redundant": _load_kv(os.path.join(_DICT_DIR, "redundant.txt")),
            "confusable": _load_confusable(os.path.join(_DICT_DIR, "confusable.txt")),
            "ambiguous": _load_kv(os.path.join(_DICT_DIR, "ambiguous.txt")),
            "typo": _load_typo(os.path.join(_DICT_DIR, "typo.txt")),
            "en_typo": _load_en_typo(os.path.join(_DICT_DIR, "en_typo.txt")),
            "units": _load_units(os.path.join(_DICT_DIR, "units.txt")),
            "abbrev": _load_abbrev(os.path.join(_DICT_DIR, "abbrev.txt")),
            "grammar": _load_patterns(os.path.join(_DICT_DIR, "grammar.txt")),
            "vocab": _load_patterns(os.path.join(_DICT_DIR, "vocab.txt")),
            "asset_terms": _load_patterns(os.path.join(_DICT_DIR, "asset_terms.txt")),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _on(self, rule_key: str) -> bool:
        if len(self.issues) >= self._limit:
            return False
        return self.cfg.is_enabled(self.KIND, rule_key)

    def _add(self, rule_key: str, location: str, detail: str, snippet: str = "") -> None:
        if len(self.issues) >= self._limit:
            return
        self.issues.append(
            Issue(
                rule_key=rule_key,
                rule_title=self.cfg.title(self.KIND, rule_key),
                severity=self.cfg.severity(self.KIND, rule_key),
                location=location,
                detail=detail + SUSPECT_TAG,
                snippet=clip(snippet, 120),
                suggestion=self.cfg.suggestion(self.KIND, rule_key),
            )
        )

    # ------------------------------------------------------------------
    # 主入口：对一段文本做全部文字规范检测
    # ------------------------------------------------------------------
    def check_text(self, location: str, text: str) -> None:
        """对单段文本（段落 / 表格单元格 / Excel 单元格）执行全部文字规范检测。"""
        if not text:
            return
        # 若规则配置中根本没有 textnorm 段落，则不运行（避免标题错乱）
        if not self.cfg.section(self.KIND):
            return

        self._check_colloquial(location, text)
        self._check_redundant(location, text)
        self._check_confusable(location, text)
        self._check_ambiguous(location, text)
        self._check_typo(location, text)
        self._check_en_typo(location, text)
        self._check_abbrev(location, text)
        self._check_units(location, text)
        self._check_grammar(location, text)
        self._check_vocab(location, text)
        self._check_asset_terms(location, text)

    # ------------------------------------------------------------------
    # 1) 口语化 / 不规范用词
    # ------------------------------------------------------------------
    def _check_colloquial(self, location: str, text: str) -> None:
        if not self._on("tn_colloquial"):
            return
        for phrase, note in self._banks["colloquial"]:
            if phrase and phrase in text:
                extra = f"，建议书面表达：{note}" if note else ""
                self._add("tn_colloquial", location,
                          f"出现疑似口语化 / 不规范表述「{phrase}」{extra}", text)

    # ------------------------------------------------------------------
    # 2) 重复冗余词句
    # ------------------------------------------------------------------
    def _check_redundant(self, location: str, text: str) -> None:
        if not self._on("tn_redundant"):
            return
        for phrase, note in self._banks["redundant"]:
            if phrase and phrase in text:
                extra = f"（{note}）" if note else ""
                self._add("tn_redundant", location,
                          f"出现疑似语义重复冗余表述「{phrase}」{extra}", text)

    # ------------------------------------------------------------------
    # 3) 易混淆近义词误用
    # ------------------------------------------------------------------
    def _check_confusable(self, location: str, text: str) -> None:
        if not self._on("tn_confusable"):
            return
        seen: set = set()
        for (a, b), note in self._banks["confusable"]:
            hit = [t for t in (a, b) if t and t in text]
            if not hit:
                continue
            key = (a, b)
            if key in seen:
                continue
            seen.add(key)
            found = "、".join(hit)
            self._add("tn_confusable", location,
                      f"出现易混淆近义词「{found}」{note}", text)

    # ------------------------------------------------------------------
    # 4) 歧义句式 / 过长无标点长句 / 表意模糊词
    # ------------------------------------------------------------------
    def _check_ambiguous(self, location: str, text: str) -> None:
        if not self._on("tn_ambiguous"):
            return
        max_len = int(self.cfg.param(self.KIND, "tn_ambiguous", "max_sentence_len",
                                     DEFAULT_MAX_SENTENCE_LEN) or DEFAULT_MAX_SENTENCE_LEN)

        # 4.1 表意模糊词
        for word, note in self._banks["ambiguous"]:
            if word and word in text:
                extra = f"（{note}）" if note else ""
                self._add("tn_ambiguous", location,
                          f"出现易产生歧义 / 表意模糊词「{word}」{extra}", text)

        # 4.2 过长无标点长句（按句末标点切分，取最长片段判定）
        segments = _SENT_END_RE.split(text)
        reported = False
        for seg in segments:
            seg = seg.strip()
            if len(seg) > max_len:
                self._add("tn_ambiguous", location,
                          f"疑似过长无标点长句（{len(seg)} 字，超过阈值 {max_len} 字），"
                          f"建议适当断句以避免歧义", seg)
                reported = True
                break  # 同一文本只提示一次长句问题
        # 整段无句末标点且偏长
        if not reported and not _SENT_END_RE.search(text) and len(text.strip()) > max_len:
            self._add("tn_ambiguous", location,
                      f"疑似过长无标点长句（{len(text.strip())} 字，超过阈值 {max_len} 字），"
                      f"建议适当断句以避免歧义", text)

    # ------------------------------------------------------------------
    # 5) 错别字 / 易写错词语
    # ------------------------------------------------------------------
    def _check_typo(self, location: str, text: str) -> None:
        if not self._on("tn_typo"):
            return
        for err, corr in self._banks["typo"]:
            if err and err in text:
                self._add("tn_typo", location,
                          f"疑似错别字：「{err}」可能为「{corr}」的误写", text)

    # ------------------------------------------------------------------
    # 6) 英文拼写错误（整词匹配）
    # ------------------------------------------------------------------
    def _check_en_typo(self, location: str, text: str) -> None:
        if not self._on("tn_en_typo"):
            return
        for pat, err, corr in self._banks["en_typo"]:
            m = pat.search(text)
            if m:
                word = m.group(0)
                suffix = word[len(err):] if word.lower().startswith(err.lower()) else ""
                self._add("tn_en_typo", location,
                          f"疑似英文拼写错误：「{word}」应为「{corr}{suffix}」", text)

    # ------------------------------------------------------------------
    # 7) 非正式简称 / 自创缩写
    # ------------------------------------------------------------------
    def _check_abbrev(self, location: str, text: str) -> None:
        if not self._on("tn_abbrev"):
            return
        forbidden, allow = self._banks["abbrev"]

        # 6.1 明确列入“不建议使用”的简称
        for token, note in forbidden:
            if token and token in text:
                extra = f"（{note}）" if note else ""
                self._add("tn_abbrev", location,
                          f"出现非正式简称「{token}」{extra}", text)

        # 6.2 自创 / 未定义缩写启发式（2~5 位大写字母，且不在白名单）
        for tok in set(_ALLCAPS_RE.findall(text)):
            if tok in allow:
                continue
            self._add("tn_abbrev", location,
                      f"疑似自创 / 未定义缩写「{tok}」，建议首次出现时给出全称", text)

    # ------------------------------------------------------------------
    # 8) 数量 / 单位表述不统一
    # ------------------------------------------------------------------
    def _check_units(self, location: str, text: str) -> None:
        if not self._on("tn_units"):
            return
        # 7.1 非标准单位符号（正则匹配）
        for pat, sug in self._banks["units"]:
            m = pat.search(text)
            if m:
                self._add("tn_units", location,
                          f"数量 / 单位表述不规范：命中「{m.group(0).strip()}」，{sug}", text)

        # 7.2 万元 / 元 混用（同一文本内同时出现）
        has_wan_yuan = "万元" in text
        has_plain_yuan = bool(re.search(r"(?<!万)元", text))
        if has_wan_yuan and has_plain_yuan:
            self._add("tn_units", location,
                      "同一处同时出现「万元」与「元」，数量单位表述不统一，"
                      "建议统一为同一计量单位（如一律用“元”并注明金额）", text)

        # 7.3 亿元 / 万元 混用
        if "亿元" in text and "万元" in text:
            self._add("tn_units", location,
                      "同一处同时出现「亿元」与「万元」，数量单位表述不统一，建议统一量级", text)

    # ------------------------------------------------------------------
    # 9) 中英文语法错误（本地正则）
    # ------------------------------------------------------------------
    def _check_grammar(self, location: str, text: str) -> None:
        if not self._on("tn_grammar"):
            return
        for pat, note in self._banks["grammar"]:
            m = pat.search(text)
            if m:
                self._add("tn_grammar", location,
                          f"疑似语法错误：命中「{m.group(0)}」，{note}", text)

    # ------------------------------------------------------------------
    # 10) 中英文词汇搭配不当 / 用词错误（本地正则）
    # ------------------------------------------------------------------
    def _check_vocab(self, location: str, text: str) -> None:
        if not self._on("tn_vocab"):
            return
        for pat, note in self._banks["vocab"]:
            m = pat.search(text)
            if m:
                self._add("tn_vocab", location,
                          f"疑似词汇搭配不当：命中「{m.group(0)}」，{note}", text)

    # ------------------------------------------------------------------
    # 11) 资产评估术语表述不规范（依据《资产评估准则术语2020》）
    # ------------------------------------------------------------------
    def _check_asset_terms(self, location: str, text: str) -> None:
        if not self._on("tn_asset_terms"):
            return
        for pat, note in self._banks["asset_terms"]:
            m = pat.search(text)
            if m:
                self._add("tn_asset_terms", location,
                          f"疑似资产评估术语表述不规范：命中「{m.group(0)}」，{note}", text)
