# -*- coding: utf-8 -*-
"""
AI 规则/词库生成器（对话式创建 + 文档自建）
=================================================
两种来源（均复用 AI 智能核验的连接配置 mode/base_url/model 等）：
    build_dialogue(text)   用户自然语言描述需求 → 生成 词库分组 + 规则 的结构化 JSON
    build_from_doc(raw)    上传规范/标准/示例文档 → AI 阅读后提取术语与规范 → 同上

输出结构（生成物，前端预览确认后写入自定义词库 / 自定义规则）：
    {"wordbanks": [{"name": "分组名", "entries": [{"keyword","tag","suggestion"}]}],
     "rules":     [{"name", "match_mode", "pattern", "severity", "suggestion"}]}
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from checkers.ai_checker import (
    AiError, DEFAULTS as _AI_DEFAULTS, _call_ollama, _call_openai,
    _extract_ref_text, clip, resolve_local_model,
)

# 生成结果里单次最多条目数（防止模型产出过多/过少）
MAX_ENTRIES = 40
MAX_RULES = 30

_SYS_PROMPT = (
    "你是一名文档质量规则工程师，负责把用户的诉求转化为可落地的检测规则与词库。\n"
    "请严格输出一个 JSON 对象（不要任何其它内容、解释或代码块标记），结构如下：\n"
    '{"wordbanks":[{"name":"词库分组名","entries":[{"keyword":"目标词/短语",'
    '"tag":"分类标签(如 禁用词/规范术语/易混淆词)","suggestion":"建议替换为"}]}],\n'
    ' "rules":[{"name":"规则名","match_mode":"keyword或regex","pattern":"关键词或正则表达式",'
    '"severity":"low或medium或high","suggestion":"整改建议"}]}\n'
    "要求：\n"
    "1. 词库 keyword 必须是文档中会真实出现的原文表述；suggestion 给出规范替代。\n"
    "2. 规则优先用 keyword 子串匹配（match_mode=keyword），需要更复杂匹配时才用 regex；"
    "regex 必须能在 Python re 中运行。\n"
    "3. 数量宁精勿滥：词库最多 30 条、规则最多 15 条；没有合适的就返回空数组。\n"
    "4. severity 按严重程度取 low/medium/high。\n"
    "5. 规则用途为【错误检测】：pattern 必须是错误写法、错别字、不规范表述、"
    "禁用语句；suggestion 给出【标准正确术语】。严禁生成「匹配内容和替换内容一模一样」"
    "的规则；禁止拿标准正确术语作为匹配条件（标准术语只能放在 suggestion 栏）；"
    "禁止使用单个通用虚词作为 pattern。输出完成后自行自检，存在 pattern 等于 "
    "suggestion 的条目直接不要输出。\n"
)

_DOC_PROMPT = (
    "你是一名文档质量规则工程师。以下是用户上传的文档（规范/标准/示例/词表）。\n"
    "请通读文档，提取其中隐含的【检测规则与词库】，用于核验其它同类文档：\n"
    "  - 词库：规范术语与替代用法（如文档明确给出某词的定义或禁用说明）、易混淆词对、"
    "文档中反复强调必须统一使用的表述；\n"
    "  - 规则：文档明示或暗示的书写要求（如金额单位必须为万元、编号必须连续、"
    "必须写全称不得缩写等），转成可检测的 keyword/regex 规则。\n"
    "请严格输出一个 JSON 对象（不要任何其它内容、解释或代码块标记），结构如下：\n"
    '{"wordbanks":[{"name":"词库分组名","entries":[{"keyword":"目标词/短语",'
    '"tag":"分类标签","suggestion":"建议替换为"}]}],\n'
    ' "rules":[{"name":"规则名","match_mode":"keyword或regex","pattern":"关键词或正则表达式",'
    '"severity":"low或medium或high","suggestion":"整改建议"}]}\n'
    "数量宁精勿滥：词库最多 30 条、规则最多 15 条；没有合适的就返回空数组。\n"
    "规则用途为【错误检测】：pattern 必须是错误写法、错别字、不规范表述、"
    "禁用语句；suggestion 给出【标准正确术语】。严禁生成「匹配内容和替换内容一模一样」"
    "的规则；禁止拿标准正确术语作为匹配条件（标准术语只能放在 suggestion 栏）；"
    "禁止使用单个通用虚词作为 pattern。输出完成后自行自检，存在 pattern 等于 "
    "suggestion 的条目直接不要输出。\n"
)

_DOC_BULLET_PROMPT = (
    "你是文档质量规则工程师。请通读以下文档，用简洁要点列出其中隐含的、"
    "可自动检测的书写要求（含需统一的术语/表述）。\n"
    "要求：每点一句话、不超过 15 个字；总点数不超过 10 条；"
    "直接列出要点即可，不要 JSON、不要解释、不要序号以外的任何修饰。\n"
    "文档：\n"
)


def _repair_json(s: str) -> str:
    """轻量修复模型输出 JSON：尾随逗号 + 字符串内未转义的裸引号。"""
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    out: List[str] = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == '"':
            if not in_str:
                in_str = True
                out.append(ch)
            else:
                prev = s[i - 1] if i > 0 else ""
                if prev == "\\":
                    out.append(ch)
                else:
                    nxt = s[i + 1] if i + 1 < n else ""
                    if nxt in (",", "}", "]", ":", " ", "\n", "\r", "\t") or nxt == "":
                        in_str = False
                        out.append(ch)
                    else:
                        out.append('\\"')
            i += 1
            continue
        if ch == "\\" and in_str and i + 1 < n:
            out.append(ch)
            out.append(s[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _balance_json(s: str) -> str:
    """补齐被截断 JSON 缺失的闭合括号/引号（非字符串感知的简单栈平衡）。

    仅用于模型输出在尾部被截断（num_predict 不足）时的兜底恢复，
    尽量把不完整的对象补全为可解析结构。
    """
    stack: List[str] = []
    in_str = False
    esc = False
    opens = {"{": "}", "[": "]"}
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in opens:
            stack.append(opens[ch])
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()
    # 追加缺失的闭合符（反向）
    return s + "".join(reversed(stack))


def _try_load(segment: str) -> Any:
    s = segment.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_json(s))
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_balance_json(_repair_json(s)))
    except json.JSONDecodeError:
        return None


def _extract_json_obj(content: str) -> Dict[str, Any]:
    """从模型输出中提取 JSON 对象（容忍 ```json 包裹、前后缀文本、尾随逗号与裸引号）。

    候选策略：尝试所有可解析的完整对象；优先选取【含 wordbanks/rules 键】的对象
    （即真正的生成结果，避免误取思考链中的伪 JSON 碎片），其次取覆盖文本范围最大者。
    """
    text = content.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    starts = [x.start() for x in re.finditer(r"\{", text)]
    ends = [x.start() for x in re.finditer(r"\}", text)]
    if not starts or not ends:
        raise AiError("模型未返回 JSON 对象")
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for si in range(len(starts)):
        s = starts[si]
        for ei in range(len(ends) - 1, -1, -1):
            e = ends[ei]
            if e <= s:
                break
            obj = _try_load(text[s:e + 1])
            if isinstance(obj, dict):
                candidates.append((e - s, obj))
                break  # 该起点已解析出最大端点对象，无需继续缩小范围
    if not candidates:
        raise AiError(f"模型返回的 JSON 无法解析：{clip(text, 80)}")
    # 优先：含生成结果键的对象
    schema = [obj for _, obj in candidates if ("wordbanks" in obj or "rules" in obj)]
    if schema:
        # 同组内取范围最大者（更完整的对象）
        return max(schema, key=lambda o: len(json.dumps(o, ensure_ascii=False)))
    # 兜底：范围最大者
    return max(candidates, key=lambda c: c[0])[1]


def _norm_severity(sev: str) -> str:
    return sev if sev in ("low", "medium", "high") else "medium"


def _clean_pattern(pattern: str) -> str:
    """清洗模型输出的正则（剥离 Python repr 风格包装：r'...' / "..." 等）。"""
    p = pattern.strip()
    if len(p) >= 2 and p[0] == "r" and p[1] in ("'", '"') and p[-1] == p[1]:
        p = p[2:-1]
    if len(p) >= 2 and p[0] in ("'", '"') and p[-1] == p[0]:
        p = p[1:-1]
    return p


def _parse_result(obj: Dict[str, Any]) -> Dict[str, Any]:
    """清洗模型输出为受控结构。"""
    wbs, rls = [], []
    for wb in obj.get("wordbanks") or []:
        if not isinstance(wb, dict):
            continue
        entries = []
        for e in (wb.get("entries") or [])[:MAX_ENTRIES]:
            if not isinstance(e, dict):
                continue
            kw = str(e.get("keyword") or "").strip()
            if not kw:
                continue
            entries.append({
                "keyword": clip(kw, 80),
                "tag": clip(str(e.get("tag") or "").strip(), 40),
                "suggestion": clip(str(e.get("suggestion") or "").strip(), 120),
            })
        name = str(wb.get("name") or "").strip() or "AI 生成词库"
        if entries:
            wbs.append({"name": clip(name, 30), "entries": entries})
    for r in (obj.get("rules") or [])[:MAX_RULES]:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        pattern = _clean_pattern(str(r.get("pattern") or ""))
        mode = str(r.get("match_mode") or "keyword")
        if not name or not pattern:
            continue
        if mode not in ("keyword", "regex"):
            mode = "keyword"
        if mode == "regex":
            try:
                re.compile(pattern)
            except re.error as exc:
                raise AiError(f"规则「{name}」的正则无法编译：{exc}")
        rls.append({
            "name": clip(name, 40),
            "match_mode": mode,
            "pattern": clip(pattern, 200),
            "severity": _norm_severity(str(r.get("severity") or "medium")),
            "suggestion": clip(str(r.get("suggestion") or "").strip(), 120),
        })
    return {"wordbanks": wbs, "rules": rls}


def _gen(ai: Dict[str, Any], sys_prompt: str, user_text: str,
         is_bullet: bool = False, _retry: bool = False) -> Tuple[bool, str, Dict[str, Any]]:
    """调用模型并解析生成结果，返回 (是否成功, 说明, 结果)。

    is_bullet=True：仅提取简短要点文本（返回 {"text": ...}），不要求 JSON。
    _retry=True：空结果的第二次尝试（首轮通常因思考耗尽输出额度被截断）。
    """
    cfg = {**_AI_DEFAULTS, **(ai or {})}
    # 生成为低频重任务：qwen3 系本地模型的思考链无法硬禁（模板层强制），
    # 思考会占用大量 token 预算，必须把 num_predict 抬高到足以容纳
    # 思考 + JSON 输出，否则 JSON 被截断导致解析失败（表现为“生成无效”）。
    # CPU 上思考+输出最长可达 10-20 分钟，超时下限放宽到 30 分钟。
    cfg["timeout"] = max(float(cfg.get("timeout") or 60), 1800)
    # 首次预算偏低以控速，空结果重试时显著加大，避免截断
    np_default = 20000 if not _retry else 32000
    mode = cfg.get("mode") or "local"
    if mode == "local":
        _model, _sync = resolve_local_model(cfg)
        if _sync:
            cfg["model"] = _model
    empty = {"text": ""} if is_bullet else {"wordbanks": [], "rules": []}
    try:
        if mode == "local":
            if is_bullet:
                # 纯文本要点任务：自然生成（禁思考在该 tag 上易触发
                # llama-server 解析错误），正文短、截断风险低
                content = _call_ollama(cfg, [{"role": "system", "content": sys_prompt},
                                             {"role": "user", "content": user_text}])
            else:
                content = _call_ollama(cfg, [{"role": "system", "content": sys_prompt},
                                             {"role": "user", "content": user_text}],
                                        think=False, options={"num_predict": np_default})
        else:
            content = _call_openai(cfg, [{"role": "system", "content": sys_prompt},
                                         {"role": "user", "content": user_text}])
    except AiError as exc:
        return False, str(exc), empty
    if is_bullet:
        text = (content or "").strip()
        if len(text) < 5:
            return False, "要点提取为空（本地模型思考过长被截断）", empty
        return True, "要点提取完成", {"text": clip(text, 2000)}
    try:
        obj = _extract_json_obj(content)
        result = _parse_result(obj)
    except AiError as exc:
        return False, str(exc), empty
    if not result["wordbanks"] and not result["rules"] and not _retry:
        return _gen(ai, sys_prompt, user_text, is_bullet=False, _retry=True)
    return True, "生成完成", result


def build_dialogue(text: str, ai: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """对话式创建：按自然语言需求生成词库与规则。"""
    text = (text or "").strip()
    if not text:
        return False, "需求不能为空", {"wordbanks": [], "rules": []}
    if len(text) > 2000:
        text = text[:2000] + "…"
    return _gen(ai, _SYS_PROMPT, "用户需求：\n" + text)


def build_from_doc(raw: bytes, filename: str, ai: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """文档自建：AI 阅读上传文档后提取词库与规则。

    两段式生成：先让模型输出简短要点（思考长但正文短，不易被截断），
    再基于要点生成 JSON（输入短，思考短），提高本地 CPU 推理的成效率。
    """
    try:
        doc_text = _extract_ref_text(filename, raw)
    except ValueError as exc:
        return False, str(exc), {"wordbanks": [], "rules": []}
    if len(doc_text.strip()) < 20:
        return False, "文档有效文本过少，无法提取", {"wordbanks": [], "rules": []}
    doc_text = doc_text[:2500] + ("…" if len(doc_text) > 2500 else "")

    ok, msg, bullets = _gen(ai, _DOC_BULLET_PROMPT, doc_text, is_bullet=True)
    if not ok:
        return ok, msg, {"wordbanks": [], "rules": []}
    bullets = bullets.get("text", "")
    if len(bullets.strip()) < 10:
        ok, msg, result = _gen(ai, _DOC_PROMPT, "上传文档内容：\n" + doc_text)
        return ok, (msg + "（要点提取为空，已直接生成）" if ok else msg), result
    return _gen(ai, _DOC_PROMPT, "以下是根据上传文档提取的检测要点，请据此生成词库与规则：\n" + bullets)


def build_from_text(text: str, ai: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """文本式创建：粘贴准则/规范/范本文本 → AI 读取并批量生成检测词库与校验规则。"""
    text = (text or "").strip()
    if not text:
        return False, "粘贴内容不能为空", {"wordbanks": [], "rules": []}
    if len(text) > 6000:
        text = text[:6000] + "…"
    return _gen(ai, _DOC_PROMPT, "用户粘贴的准则/规范/范本文本：\n" + text)