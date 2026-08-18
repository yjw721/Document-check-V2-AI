# -*- coding: utf-8 -*-
"""
本地 AI 模型自学习记忆（全程离线）
=================================================
原则：
    1. 仅学习【用户人工确认正确】的内容——样本只能由用户在界面主动添加，
       AI 绝不自动采集文档内容（核验流程不产生任何样本）。
    2. 学习时仅调用本地 Ollama（复用 ai 组 base_url/model），零联网。
    3. 基于人工确认的正确样本，提炼「标准表述」，生成 词库条目 + 校验规则，
       自动合并到 wordbanks.json / custom_rules.json 并标记 source=ai_learning。
    4. 记忆数据全部保存在本机 config/ai_memory/ 下；批量清空只删除学习生成
       的数据，不影响用户手动导入/编写的规则词库。
    5. 学习记忆数据（样本与学习记录）不出现在检测结果与导出报告中。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from checkers.custom_rules import (
    VALID_MODE, VALID_SCOPE as _CR_SCOPE, load_custom_rules, save_custom_rules,
    SEVERITY_LABELS as _CR_SEV,
)
from checkers.wordbank import load_wordbanks, save_wordbanks

# 学习产出合并用的分组标识（检测时由 CustomRuleEngine / WordBankEngine 自动加载）
RULE_GROUP_ID = "ai_learning"
RULE_GROUP_NAME = "本地AI学习"
WB_GROUP_ID = "ai_learning_wb"
WB_GROUP_NAME = "本地AI学习"
SOURCE_TAG = "ai_learning"

MEM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "ai_memory")
SAMPLES_PATH = os.path.join(MEM_DIR, "samples.json")
LEARNED_PATH = os.path.join(MEM_DIR, "learned.json")

_LEARN_LOCK = threading.Lock()
_LEARN_TIMEOUT = 900.0

# 学习提示词：仅从人工确认样本中提炼，禁止臆造
_LEARN_PROMPT = (
    "你是一名文档规范工程师。以下文本是一段【经用户人工确认为正确】的文档内容，"
    "可作为规范样本。请从中提炼可复用的规范知识，用于自动核验其它同类文档。\n"
    "要求：\n"
    "1. wordbank_entries.keyword 必须是样本原文中真实出现的词/短语（规范用法示范），"
    "suggestion 说明其应保持的规范写法；\n"
    "2. rules 把样本体现的书写要求转成可检测规则：match_mode=keyword 优先，"
    "确有需要才用 regex，regex 必须能在 Python re 中编译；\n"
    "3. 宁缺毋滥：每类最多 5 条，没有合适的就返回空数组；\n"
    "4. 只输出一个 JSON 对象，不要任何其它内容、解释或代码块标记。\n"
    'JSON 结构：{"standard_expression":"标准表述（一句话概括样本的规范用法）",'
    '"wordbank_entries":[{"keyword":"词/短语","suggestion":"规范说明"}],'
    '"rules":[{"name":"规则名","match_mode":"keyword或regex","pattern":"关键词或正则表达式",'
    '"severity":"low或medium或high","suggestion":"整改建议"}]}\n'
    "规范样本：\n"
)

# 模型能力不足 / 服务异常 → 友好提示映射
_FRIENDLY_HINTS = (
    (("HTTP 500", "peg-native"), "本地模型算力不足或响应异常（思考链解析失败），"
     "请稍后重试，或在系统设置中换用官方 qwen3 系列模型"),
    (("Connection refused", "连接失败"), "本地 AI 服务未启动：请先启动 Ollama 服务，再重试学习"),
    (("timed out", "timeout", "Timeout"), "本地模型响应超时：当前设备算力不足时生成较慢，"
     "请稍后重试，或换用更小的本地模型"),
    (("HTTP",), "本地模型服务响应异常，请检查 Ollama 状态后重试"),
)


def _friendly_ai_error(exc: Exception) -> str:
    msg = str(exc)
    for kws, hint in _FRIENDLY_HINTS:
        if any(k in msg for k in kws):
            return hint
    return msg[:150] or "本地 AI 学习失败，请稍后重试"


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def _empty_samples() -> Dict[str, Any]:
    return {"settings": {"enabled": True}, "samples": []}


def _empty_learned() -> Dict[str, Any]:
    return {"learned": []}


def _load_json(path: str, empty: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return empty()


def _save_json(path: str, data: Any) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def load_samples() -> Dict[str, Any]:
    return _load_json(SAMPLES_PATH, _empty_samples)


def save_samples(data: Dict[str, Any]) -> bool:
    return _save_json(SAMPLES_PATH, data)


def load_learned() -> Dict[str, Any]:
    return _load_json(LEARNED_PATH, _empty_learned)


def save_learned(data: Dict[str, Any]) -> bool:
    return _save_json(LEARNED_PATH, data)


def is_enabled() -> bool:
    return bool(load_samples().get("settings", {}).get("enabled", True))


def set_enabled(on: bool) -> bool:
    data = load_samples()
    data.setdefault("settings", {})["enabled"] = bool(on)
    return save_samples(data)


def _gen_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# 学习引擎（本地 Ollama，全程离线）
# ---------------------------------------------------------------------------
def _call_learn(ai_cfg: Dict[str, Any], sample_text: str) -> Dict[str, Any]:
    """调用本地模型提炼标准表述，返回结构化结果（解析失败抛 AiError）。"""
    from checkers.ai_builder import _extract_json_obj
    from checkers.ai_checker import AiError, DEFAULTS, _call_ollama, resolve_local_model

    cfg = {**DEFAULTS, **{k: v for k, v in (ai_cfg or {}).items()}}
    cfg["mode"] = "local"  # 自学习强制本地离线
    cfg["timeout"] = _LEARN_TIMEOUT
    _model, _sync = resolve_local_model(cfg)
    if _sync:
        cfg["model"] = _model
    content = _call_ollama(cfg, [
        {"role": "system", "content": _LEARN_PROMPT},
        {"role": "user", "content": f"{sample_text}\n" if sample_text.strip() else "(空)"},
    ], think=False, options={"num_predict": 3000})
    obj = _extract_json_obj(content)
    if not isinstance(obj, dict):
        raise AiError("模型返回结果不是合法 JSON 对象")
    return obj


def _norm_severity(sev: Any) -> str:
    s = str(sev or "low").strip().lower()
    return s if s in _CR_SEV else "low"


def _norm_entries(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in (raw or [])[:5]:
        if not isinstance(e, dict):
            continue
        kw = str(e.get("keyword") or "").strip()
        if kw:
            out.append({"keyword": kw[:80],
                        "suggestion": str(e.get("suggestion") or "")[:200]})
    return out


def _norm_rules(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in (raw or [])[:5]:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()[:60]
        mode = str(r.get("match_mode") or "keyword").strip().lower()
        if mode not in VALID_MODE:
            mode = "keyword"
        pattern = str(r.get("pattern") or "").strip()[:300]
        if not name or not pattern:
            continue
        if mode == "regex":
            try:
                re.compile(pattern)
            except re.error:
                continue  # 非法正则直接丢弃，避免污染规则库
        out.append({"name": name, "match_mode": mode, "pattern": pattern,
                    "severity": _norm_severity(r.get("severity")),
                    "suggestion": str(r.get("suggestion") or "")[:200]})
    return out


def _merge_to_groups(entries: List[Dict[str, Any]], rules: List[Dict[str, Any]],
                     sample_id: str) -> Tuple[List[Dict[str, Any]], int]:
    """把学习产出合并进 wordbanks.json / custom_rules.json，返回 (learned 记录, 跳过数)。"""
    learned: List[Dict[str, Any]] = []
    skipped = 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # ---- 词库条目 ----
    wb = load_wordbanks()
    wbg = next((g for g in wb.get("groups", []) if g.get("id") == WB_GROUP_ID), None)
    if wbg is None:
        wbg = {"id": WB_GROUP_ID, "name": WB_GROUP_NAME, "module": "text_word",
               "scope": "all", "enabled": True, "entries": []}
        wb.setdefault("groups", []).append(wbg)
    for e in entries:
        kw = e["keyword"]
        if any(x.get("keyword") == kw and x.get("source") == SOURCE_TAG
               for x in wbg.get("entries", [])):
            skipped += 1
            continue
        eid = _gen_id("e")
        wbg.setdefault("entries", []).append({
            "id": eid, "keyword": kw, "tag": "AI学习",
            "suggestion": e.get("suggestion", ""), "enabled": True,
            "source": SOURCE_TAG, "sample_id": sample_id,
        })
        learned.append({"id": _gen_id("l"), "sample_id": sample_id, "kind": "wordbank",
                        "group_id": WB_GROUP_ID, "entity_id": eid, "keyword": kw,
                        "suggestion": e.get("suggestion", ""), "enabled": True,
                        "created_at": now})
    save_wordbanks(wb)

    # ---- 校验规则 ----
    cr = load_custom_rules()
    crg = next((g for g in cr.get("groups", []) if g.get("id") == RULE_GROUP_ID), None)
    if crg is None:
        crg = {"id": RULE_GROUP_ID, "name": RULE_GROUP_NAME, "category": "format_error",
               "scope": "all", "enabled": True, "rules": []}
        cr.setdefault("groups", []).append(crg)
    for r in rules:
        if any(x.get("pattern") == r["pattern"] and x.get("source") == SOURCE_TAG
               for x in crg.get("rules", [])):
            skipped += 1
            continue
        rid = _gen_id("r")
        crg.setdefault("rules", []).append({
            "id": rid, "name": r["name"], "enabled": True,
            "match_mode": r["match_mode"], "pattern": r["pattern"],
            "severity": r["severity"], "tag": "AI学习",
            "suggestion": r["suggestion"], "source": SOURCE_TAG,
            "sample_id": sample_id,
        })
        learned.append({"id": _gen_id("l"), "sample_id": sample_id, "kind": "rule",
                        "group_id": RULE_GROUP_ID, "entity_id": rid, "name": r["name"],
                        "match_mode": r["match_mode"], "pattern": r["pattern"],
                        "severity": r["severity"], "suggestion": r["suggestion"],
                        "enabled": True, "created_at": now})
    save_custom_rules(cr)
    return learned, skipped


def learn_sample(sid: str, ai_cfg: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """对指定样本执行一次本地学习。返回 (是否成功, 说明, 产出统计)。"""
    with _LEARN_LOCK:
        data = load_samples()
        sample = next((s for s in data.get("samples", []) if s.get("id") == sid), None)
        if not sample:
            return False, "样本不存在或已被删除", {}
        if not data.get("settings", {}).get("enabled", True):
            return False, "本地AI自学习已关闭，请先在总开关中启用", {}
        if not sample.get("enabled", True):
            return False, "该样本已禁用，请先启用后再学习", {}
        content = str(sample.get("content") or "").strip()
        if not content:
            return False, "样本内容为空，无法学习", {}

        sample["status"] = "learning"
        sample["error"] = ""
        save_samples(data)

        try:
            obj = _call_learn(ai_cfg, content)
            entries = _norm_entries(obj.get("wordbank_entries"))
            rules = _norm_rules(obj.get("rules"))
            if not entries and not rules:
                # 本地 CPU 推理偶发输出截断/重复：自动重试一次
                obj = _call_learn(ai_cfg, content)
                entries = _norm_entries(obj.get("wordbank_entries"))
                rules = _norm_rules(obj.get("rules"))
            if not entries and not rules:
                raise ValueError("模型未提炼出可用内容（样本过于简单或表述不规范）")
            learned, skipped = _merge_to_groups(entries, rules, sid)
            if not learned:
                return False, "提炼内容与已有学习成果重复，未新增", {}
            standard = str(obj.get("standard_expression") or "").strip()[:300]
            ldata = load_learned()
            ldata.setdefault("learned", []).extend(learned)
            save_learned(ldata)

            sample["status"] = "done"
            sample["learned_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            sample["result_count"] = len(learned)
            save_samples(data)
            stats = {"entries": sum(1 for l in learned if l["kind"] == "wordbank"),
                     "rules": sum(1 for l in learned if l["kind"] == "rule"),
                     "standard_expression": standard, "skipped": skipped}
            return True, "学习完成", stats
        except Exception as exc:  # noqa: BLE001 - 学习失败不中断
            sample["status"] = "failed"
            sample["error"] = _friendly_ai_error(exc)
            save_samples(data)
            return False, sample["error"], {}


# ---------------------------------------------------------------------------
# 产出管理（词库条目 / 校验规则的启用、禁用、删除）
# ---------------------------------------------------------------------------
def _find_learned(ldata: Dict[str, Any], lid: str) -> Optional[Dict[str, Any]]:
    return next((l for l in ldata.get("learned", []) if l.get("id") == lid), None)


def toggle_learned(lid: str, on: bool) -> bool:
    ldata = load_learned()
    rec = _find_learned(ldata, lid)
    if not rec:
        return False
    kind = rec.get("kind")
    gid = rec.get("group_id")
    eid = rec.get("entity_id")
    ok = False
    if kind == "wordbank":
        wb = load_wordbanks()
        for g in wb.get("groups", []):
            if g.get("id") == gid:
                for e in g.get("entries", []):
                    if e.get("id") == eid:
                        e["enabled"] = bool(on)
                        ok = save_wordbanks(wb)
        if ok:
            rec["enabled"] = bool(on)
    else:
        cr = load_custom_rules()
        for g in cr.get("groups", []):
            if g.get("id") == gid:
                for r in g.get("rules", []):
                    if r.get("id") == eid:
                        r["enabled"] = bool(on)
                        ok = save_custom_rules(cr)
        if ok:
            rec["enabled"] = bool(on)
    if ok:
        save_learned(ldata)
    return ok


def delete_learned(lid: str) -> bool:
    ldata = load_learned()
    rec = _find_learned(ldata, lid)
    if not rec:
        return False
    kind = rec.get("kind")
    gid = rec.get("group_id")
    eid = rec.get("entity_id")
    ok = False
    if kind == "wordbank":
        wb = load_wordbanks()
        for g in wb.get("groups", []):
            if g.get("id") == gid:
                g["entries"] = [e for e in g.get("entries", [])
                                if e.get("id") != eid]
                ok = save_wordbanks(wb)
                if ok and not g["entries"]:
                    wb["groups"] = [x for x in wb.get("groups", []) if x.get("id") != gid]
                    save_wordbanks(wb)
    else:
        cr = load_custom_rules()
        for g in cr.get("groups", []):
            if g.get("id") == gid:
                g["rules"] = [r for r in g.get("rules", []) if r.get("id") != eid]
                ok = save_custom_rules(cr)
                if ok and not g["rules"]:
                    cr["groups"] = [x for x in cr.get("groups", []) if x.get("id") != gid]
                    save_custom_rules(cr)
    if ok:
        ldata["learned"] = [l for l in ldata["learned"] if l.get("id") != lid]
        save_learned(ldata)
    return ok


def clear_all() -> int:
    """批量清空本地AI学习记忆：删除全部样本与学习产出（不动用户手动数据）。"""
    removed = 0
    # 词库中的学习条目
    wb = load_wordbanks()
    for g in list(wb.get("groups", [])):
        before = len(g.get("entries", []))
        g["entries"] = [e for e in g.get("entries", []) if e.get("source") != SOURCE_TAG]
        removed += before - len(g["entries"])
    wb["groups"] = [g for g in wb.get("groups", [])
                    if g.get("source") != SOURCE_TAG and g.get("entries")]
    save_wordbanks(wb)
    # 自定义规则中的学习规则
    cr = load_custom_rules()
    for g in list(cr.get("groups", [])):
        before = len(g.get("rules", []))
        g["rules"] = [r for r in g.get("rules", []) if r.get("source") != SOURCE_TAG]
        removed += before - len(g["rules"])
    cr["groups"] = [g for g in cr.get("groups", [])
                    if g.get("source") != SOURCE_TAG and g.get("rules")]
    save_custom_rules(cr)
    # 重置样本与学习记录（保留总开关）
    enabled = is_enabled()
    save_samples({"settings": {"enabled": enabled}, "samples": []})
    save_learned(_empty_learned())
    return removed


# ---------------------------------------------------------------------------
# 对外载荷
# ---------------------------------------------------------------------------
def payload() -> Dict[str, Any]:
    samples = load_samples()
    learned = load_learned().get("learned", [])
    lst = [{
        "id": s.get("id"), "content": s.get("content", ""),
        "source": s.get("source", ""), "note": s.get("note", ""),
        "status": s.get("status", "pending"), "enabled": s.get("enabled", True),
        "learned_at": s.get("learned_at"), "result_count": s.get("result_count", 0),
        "error": s.get("error", ""), "created_at": s.get("created_at", ""),
    } for s in samples.get("samples", [])]
    return {
        "enabled": bool(samples.get("settings", {}).get("enabled", True)),
        "samples": lst,
        "learned": learned,
        "stats": {
            "samples": len(lst),
            "pending": sum(1 for s in lst if s["status"] == "pending"),
            "done": sum(1 for s in lst if s["status"] == "done"),
            "failed": sum(1 for s in lst if s["status"] == "failed"),
            "entries": sum(1 for l in learned if l.get("kind") == "wordbank"),
            "rules": sum(1 for l in learned if l.get("kind") == "rule"),
        },
    }
