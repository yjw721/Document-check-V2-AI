# -*- coding: utf-8 -*-
"""
本地 AI 模型自学习记忆（全程离线 · 人工校对成对样本）
=====================================================
原则：
    1. 样本必须由两份文档配对产生：【系统留存的原始检测文档】+【用户上传的人工修订文档】，
       两份齐全才允许加入记忆样本库（缺少修订文档按钮置灰，禁止静默自动学习）。
    2. 学习时仅调用本地 Ollama（复用 ai 组 base_url/model），零联网。
    3. 学习产出基于两份文档的【文本差异片段】：提炼【错误表述】→【修订后标准表述】，
       生成 词库条目 + 校验规则，自动合并到 wordbanks.json / custom_rules.json，
       标记 source=ai_learning（本地AI自学习生成-人工校对样本）。
    4. 仅存储差异片段文本，不完整存储整篇文档；配对入库后释放完整文件，降低磁盘占用。
    5. 记忆数据全部保存在本机 config/ai_memory/ 下；批量清空只删除学习生成的数据，
       不影响用户手动录入/外部导入的规则词库。
    6. 学习记忆数据（样本与学习记录）不出现在检测结果与导出报告中。
"""

from __future__ import annotations

import csv
import difflib
import io
import json
import os
import re
import shutil
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
SOURCE_LABEL = "本地AI自学习生成-人工校对样本"

MEM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "ai_memory")
SAMPLES_PATH = os.path.join(MEM_DIR, "samples.json")
LEARNED_PATH = os.path.join(MEM_DIR, "learned.json")
SRC_DIR = os.path.join(MEM_DIR, "source_docs")
SRC_META_PATH = os.path.join(MEM_DIR, "source_docs.json")

_LEARN_LOCK = threading.Lock()
_LEARN_TIMEOUT = 900.0

# 学习提示词：基于两份文档的差异片段（错误表述 → 修订后标准表述）提炼
_LEARN_PROMPT = (
    "你是一名文档规范工程师。以下是一组【原始文档错误片段】与【人工修订后正确片段】"
    "的差异对（用户人工校对确认）。请从差异对中识别错误类型"
    "（错别字、用词错误、语句语病、资产评估准则不规范表述等），提炼出具体的"
    "【错误表述】与【修订后标准表述】。\n"
    "要求：\n"
    "1. corrections[].error 必须是差异对中真实出现的错误表述（宁缺毋滥，最多 10 条）；\n"
    "2. rules[].pattern 是错误写法、错别字或不规范表述，rules[].suggestion 是标准正确术语；"
    "严禁生成 pattern 与 suggestion 相同的规则，禁止把标准正确术语作为匹配条件，"
    "禁止单个通用虚词作为 pattern；\n"
    "3. 只输出一个 JSON 对象，不要任何其它内容、解释或代码块标记。\n"
    'JSON 结构：{"corrections":[{"error":"错误表述","corrected":"修订后标准表述"}],'
    '"rules":[{"name":"规则名","match_mode":"keyword或regex","pattern":"错误写法",'
    '"severity":"low或medium或high","suggestion":"标准正确术语"}]}\n'
    "差异对（错误 → 正确）：\n"
)

# 模型能力不足 / 服务异常 → 友好提示映射
_FRIENDLY_HINTS = (
    (("HTTP 500", "peg-native"), "本地模型算力不足，本次无法执行AI生成/自学习，不影响文档正常检测"),
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
# 原始检测文档留存 / 修订文档配对（成对样本）
# ---------------------------------------------------------------------------
def save_source_docs(files: List[Tuple[str, str]]) -> None:
    """检测任务完成后留存原始待检测文档（供自学习配对）。

    files: [(缓存路径, 文件名), ...]；仅在自学习总开关开启时留存。
    """
    try:
        os.makedirs(SRC_DIR, exist_ok=True)
        meta = {"task_id": _gen_id("t"), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "files": [], "revised": None}
        for idx, (path, name) in enumerate(files):
            try:
                dst = os.path.join(SRC_DIR, f"src_{idx:03d}_{os.path.basename(name)}")
                shutil.copy2(path, dst)
                meta["files"].append({"name": name, "path": dst})
            except OSError:
                continue
        if meta["files"]:
            _save_json(SRC_META_PATH, meta)
    except OSError:
        pass


def _load_src_meta() -> Dict[str, Any]:
    meta = _load_json(SRC_META_PATH, dict)
    if not isinstance(meta, dict) or not meta.get("files"):
        return {}
    return meta


def _src_valid(meta: Dict[str, Any]) -> bool:
    return all(os.path.isfile(f["path"]) for f in meta.get("files", []))


def source_docs_status() -> Dict[str, Any]:
    """当前配对状态：原始文档列表 / 是否已上传修订文档 / 缓存是否有效。"""
    meta = _load_src_meta()
    if not meta:
        return {"available": False, "valid": False, "files": [], "revised": None,
                "message": "原始待检测文档缓存已失效，无法配对学习，请重新执行文档检测流程"}
    files = [{"name": f["name"], "path": f["path"]} for f in meta.get("files", [])]
    valid = _src_valid(meta)
    return {
        "available": True, "valid": valid, "files": files,
        "revised": meta.get("revised"),
        "message": "" if valid else "原始待检测文档缓存已失效，无法配对学习，请重新执行文档检测流程",
    }


def upload_revised(name: str, raw: bytes) -> Tuple[bool, str, Dict[str, Any]]:
    """上传人工修改后的修订文档，与原始文档配对。"""
    meta = _load_src_meta()
    if not meta:
        return False, "原始待检测文档缓存已失效，无法配对学习，请重新执行文档检测流程", {}
    if not _src_valid(meta):
        return False, "原始待检测文档缓存已失效，无法配对学习，请重新执行文档检测流程", {}
    try:
        os.makedirs(SRC_DIR, exist_ok=True)
        dst = os.path.join(SRC_DIR, f"rev_{int(time.time())}_{os.path.basename(name)}")
        with open(dst, "wb") as fp:
            fp.write(raw)
        meta["revised"] = {"name": name, "path": dst,
                           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_json(SRC_META_PATH, meta)
        return True, "修订文档已上传，配对成功", source_docs_status()
    except OSError:
        return False, "修订文档保存失败，请重试", {}


def _clip(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def compute_diffs() -> Tuple[bool, str, List[Dict[str, Any]]]:
    """比对原始文档与修订文档的文本差异，提取差异片段对（错误片段 → 正确片段）。

    返回 (是否成功, 说明, 差异对列表)。解析失败给出友好提示，不阻断其它功能。
    """
    from checkers.ai_checker import _extract_ref_text
    meta = _load_src_meta()
    if not meta or not meta.get("revised"):
        return False, "缺少人工修订文档，无法比对", []
    if not _src_valid(meta):
        return False, "原始待检测文档缓存已失效，无法配对学习，请重新执行文档检测流程", []
    pairs: List[Dict[str, Any]] = []
    for f in meta["files"]:
        try:
            old_text = _extract_ref_text(f["name"], open(f["path"], "rb").read())
        except Exception:  # noqa: BLE001 - 单文档解析失败不阻断
            old_text = ""
        try:
            new_text = _extract_ref_text(meta["revised"]["name"],
                                         open(meta["revised"]["path"], "rb").read())
        except Exception:  # noqa: BLE001
            new_text = ""
        if not old_text.strip() or not new_text.strip():
            return False, "文档解析失败，无法进行比对学习", []
        for old_seg, new_seg in _text_diffs(old_text, new_text):
            if pairs and pairs[-1]["doc"] == f["name"] and len(pairs) >= 30:
                break
            pairs.append({"doc": f["name"], "old": _clip(old_seg), "new": _clip(new_seg)})
    if not pairs:
        return False, "两份文档文本完全一致，没有可学习的差异", []
    return True, f"解析完成，共发现 {len(pairs)} 处文本差异", pairs


def _text_diffs(old_text: str, new_text: str, max_pairs: int = 30) -> List[Tuple[str, str]]:
    """按段落做文本差异：提取【变更块】的旧文本与新文本，返回 (旧片段, 新片段)。"""
    old_lines = [l.rstrip("\n") for l in old_text.splitlines()]
    new_lines = [l.rstrip("\n") for l in new_text.splitlines()]
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    out: List[Tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old_seg = "\n".join(old_lines[i1:i2])
        new_seg = "\n".join(new_lines[j1:j2])
        if not old_seg.strip() or not new_seg.strip():
            continue
        if old_seg.strip() == new_seg.strip():
            continue
        out.append((old_seg, new_seg))
        if len(out) >= max_pairs:
            break
    return out


def add_sample(diffs: List[Dict[str, Any]], source_doc: str, revised_doc: str,
               note: str = "", content: str = "") -> Tuple[bool, str]:
    """把已配对的差异片段确认加入本地记忆样本库（仅存差异文本，释放完整文档）。

    兼容旧版 content 入参（检测报告「标记正确」入口）：转成单条差异，修订值留空，
    用户可在样本列表中补充修订后正确表述后再学习。
    """
    if not is_enabled():
        return False, "本地AI自学习已关闭，请先启用"
    if content and not diffs:
        diffs = [{"old": _clip(content, 300), "new": ""}]
    if not diffs:
        return False, "没有可加入的差异片段"
    data = load_samples()
    sample = {
        "id": _gen_id("s"),
        "diffs": [{"old": _clip(d.get("old", ""), 300), "new": _clip(d.get("new", ""), 300)}
                  for d in diffs],
        "source_doc": source_doc or "—",
        "revised_doc": revised_doc or "—",
        "note": note[:200],
        "status": "pending", "enabled": True,
        "learned_at": None, "result_count": 0, "error": "",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data.setdefault("samples", []).append(sample)
    save_samples(data)
    # 配对入库完成：释放完整文档（仅保留差异片段），降低磁盘占用
    _release_source_docs()
    return True, "样本已存入本地记忆库"


def _release_source_docs() -> None:
    try:
        if os.path.isdir(SRC_DIR):
            shutil.rmtree(SRC_DIR, ignore_errors=True)
        if os.path.isfile(SRC_META_PATH):
            os.remove(SRC_META_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 学习引擎（本地 Ollama，全程离线）
# ---------------------------------------------------------------------------
def _call_learn(ai_cfg: Dict[str, Any], diffs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """调用本地模型基于差异片段提炼错误表述与修订后标准表述。"""
    from checkers.ai_builder import _extract_json_obj
    from checkers.ai_checker import AiError, DEFAULTS, _call_ollama, resolve_local_model

    cfg = {**DEFAULTS, **{k: v for k, v in (ai_cfg or {}).items()}}
    cfg["mode"] = "local"  # 自学习强制本地离线
    cfg["timeout"] = _LEARN_TIMEOUT
    _model, _sync = resolve_local_model(cfg)
    if _sync:
        cfg["model"] = _model
    lines = []
    for i, d in enumerate(diffs[:10], 1):
        lines.append(f"{i}. 错误：{d.get('old', '')}\n   正确：{d.get('new', '')}")
    user_text = "\n".join(lines) if lines else "(无差异内容)"
    content = _call_ollama(cfg, [
        {"role": "system", "content": _LEARN_PROMPT},
        {"role": "user", "content": user_text},
    ], think=False, options={"num_predict": 3000})
    obj = _extract_json_obj(content)
    if not isinstance(obj, dict):
        raise AiError("模型返回结果不是合法 JSON 对象")
    return obj


def _norm_severity(sev: Any) -> str:
    s = str(sev or "low").strip().lower()
    return s if s in _CR_SEV else "low"


def _norm_corrections(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in (raw or [])[:10]:
        if not isinstance(c, dict):
            continue
        err = str(c.get("error") or "").strip()
        corr = str(c.get("corrected") or "").strip()
        if err and corr:
            out.append({"keyword": err[:80], "suggestion": corr[:200]})
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
                     sample_id: str) -> Tuple[List[Dict[str, Any]], int, int]:
    """把学习产出合并进 wordbanks.json / custom_rules.json。

    返回 (learned 记录, 重复跳过数, 无效规则过滤数)。
    入库前执行统一前置校验：无效规则（匹配=建议 / 空建议 / 通用单字 /
    标准术语作匹配 / 库内重复）一律拦截并记录日志。
    """
    from checkers.rule_filter import build_existing, filter_entries, filter_rules
    learned: List[Dict[str, Any]] = []
    skipped = 0
    filtered = 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    existing = build_existing()

    # ---- 词库条目（基础校验：非空 / 匹配=建议 / 通用单字 / 库内重复） ----
    wb = load_wordbanks()
    wbg = next((g for g in wb.get("groups", []) if g.get("id") == WB_GROUP_ID), None)
    if wbg is None:
        wbg = {"id": WB_GROUP_ID, "name": WB_GROUP_NAME, "module": "text_word",
               "scope": "all", "enabled": True, "entries": []}
        wb.setdefault("groups", []).append(wbg)
    for e in entries:
        kw = e["keyword"]
        kept, dropped = filter_entries([e], existing, "ai_learning", check_standard=False)
        if dropped:
            filtered += len(dropped)
            continue
        if any(x.get("keyword") == kw and x.get("source") == SOURCE_TAG
               for x in wbg.get("entries", [])):
            skipped += 1
            continue
        eid = _gen_id("e")
        wbg.setdefault("entries", []).append({
            "id": eid, "keyword": kw, "tag": SOURCE_LABEL,
            "suggestion": e.get("suggestion", ""), "enabled": True,
            "source": SOURCE_TAG, "sample_id": sample_id,
        })
        learned.append({"id": _gen_id("l"), "sample_id": sample_id, "kind": "wordbank",
                        "group_id": WB_GROUP_ID, "entity_id": eid, "keyword": kw,
                        "suggestion": e.get("suggestion", ""), "enabled": True,
                        "created_at": now})
        existing["keywords"].add(kw)
    save_wordbanks(wb)

    # ---- 校验规则（入库前置校验全量执行） ----
    cr = load_custom_rules()
    crg = next((g for g in cr.get("groups", []) if g.get("id") == RULE_GROUP_ID), None)
    if crg is None:
        crg = {"id": RULE_GROUP_ID, "name": RULE_GROUP_NAME, "category": "format_error",
               "scope": "all", "enabled": True, "rules": []}
        cr.setdefault("groups", []).append(crg)
    for r in rules:
        kept, dropped = filter_rules([r], existing, "ai_learning")
        if dropped:
            filtered += len(dropped)
            continue
        if any(x.get("pattern") == r["pattern"] and x.get("source") == SOURCE_TAG
               for x in crg.get("rules", [])):
            skipped += 1
            continue
        rid = _gen_id("r")
        crg.setdefault("rules", []).append({
            "id": rid, "name": r["name"], "enabled": True,
            "match_mode": r["match_mode"], "pattern": r["pattern"],
            "severity": r["severity"], "tag": SOURCE_LABEL,
            "suggestion": r["suggestion"], "source": SOURCE_TAG,
            "sample_id": sample_id,
        })
        learned.append({"id": _gen_id("l"), "sample_id": sample_id, "kind": "rule",
                        "group_id": RULE_GROUP_ID, "entity_id": rid, "name": r["name"],
                        "match_mode": r["match_mode"], "pattern": r["pattern"],
                        "severity": r["severity"], "suggestion": r["suggestion"],
                        "enabled": True, "created_at": now})
        existing["patterns"].add((r["match_mode"], r["pattern"]))
    save_custom_rules(cr)
    return learned, skipped, filtered


def learn_sample(sid: str, ai_cfg: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """对指定成对样本执行一次本地学习（差异片段 → 词条 + 校验规则）。"""
    with _LEARN_LOCK:
        data = load_samples()
        sample = next((s for s in data.get("samples", []) if s.get("id") == sid), None)
        if not sample:
            return False, "样本不存在或已被删除", {}
        if not data.get("settings", {}).get("enabled", True):
            return False, "本地AI自学习已关闭，请先在总开关中启用", {}
        if not sample.get("enabled", True):
            return False, "该样本已禁用，请先启用后再学习", {}
        diffs = sample.get("diffs") or []
        if not diffs:
            return False, "样本差异片段为空，无法学习", {}

        sample["status"] = "learning"
        sample["error"] = ""
        save_samples(data)

        try:
            obj = _call_learn(ai_cfg, diffs)
            entries = _norm_corrections(obj.get("corrections"))
            rules = _norm_rules(obj.get("rules"))
            if not entries and not rules:
                # 本地 CPU 推理偶发输出截断/重复：自动重试一次
                obj = _call_learn(ai_cfg, diffs)
                entries = _norm_corrections(obj.get("corrections"))
                rules = _norm_rules(obj.get("rules"))
            if not entries and not rules:
                raise ValueError("模型未提炼出可用内容（差异片段过于简单或表述不规范）")
            learned, skipped, filtered = _merge_to_groups(entries, rules, sid)
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
                     "standard_expression": standard, "skipped": skipped,
                     "filtered": filtered}
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


def delete_sample(sid: str) -> bool:
    """单条删除记忆样本（不影响其已学习产出的规则词条）。"""
    data = load_samples()
    before = len(data.get("samples", []))
    data["samples"] = [s for s in data.get("samples", []) if s.get("id") != sid]
    if len(data["samples"]) != before:
        save_samples(data)
        return True
    return False


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
    # 重置样本与学习记录（保留总开关），并释放未入库的配对文档
    enabled = is_enabled()
    save_samples({"settings": {"enabled": enabled}, "samples": []})
    save_learned(_empty_learned())
    _release_source_docs()
    return removed


# ---------------------------------------------------------------------------
# 导出学习产出（csv / txt，用于备份迁移）
# ---------------------------------------------------------------------------
def export_learned(format_name: str, kind: str) -> Tuple[bool, str, str, str]:
    """导出学习产出的词库/规则。

    返回 (是否成功, 说明, 文件名, 文本内容)。
    kind: wordbanks=词库条目（keyword,suggestion） / rules=校验规则（pattern,suggestion）
    """
    fmt = (format_name or "txt").lower()
    if fmt not in ("csv", "txt"):
        return False, "不支持的导出格式", "", ""
    learned = load_learned().get("learned", [])
    if kind == "rules":
        items = [l for l in learned if l.get("kind") == "rule"]
        rows = [(l.get("name", ""), l.get("match_mode", "keyword"), l.get("pattern", ""),
                 l.get("severity", "medium"), l.get("suggestion", ""), l.get("enabled", True))
                for l in items]
        header = ["规则名", "匹配模式", "匹配式", "级别", "建议替换", "启用"]
    else:
        items = [l for l in learned if l.get("kind") == "wordbank"]
        rows = [(l.get("keyword", ""), l.get("suggestion", ""), l.get("enabled", True))
                for l in items]
        header = ["错误表述", "修订后标准表述", "启用"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        content = buf.getvalue()
        fname = f"ai_learning_{kind}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    else:
        if kind == "rules":
            lines = [f"规则名：{r[0]}\n匹配模式：{r[1]}\n匹配式：{r[2]}\n级别：{r[3]}\n"
                     f"建议替换：{r[4]}\n启用：{'是' if r[5] else '否'}\n---"
                     for r in rows]
        else:
            lines = [f"{r[0]} → {r[1]}（{'启用' if r[2] else '禁用'}）" for r in rows]
        content = "\n".join(lines) + "\n"
        fname = f"ai_learning_{kind}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    return True, f"已导出 {len(rows)} 条记录", fname, content


# ---------------------------------------------------------------------------
# 对外载荷
# ---------------------------------------------------------------------------
def payload() -> Dict[str, Any]:
    samples = load_samples()
    learned = load_learned().get("learned", [])
    lst = [{
        "id": s.get("id"), "diffs": s.get("diffs", []),
        "source_doc": s.get("source_doc", ""), "revised_doc": s.get("revised_doc", ""),
        "note": s.get("note", ""),
        "status": s.get("status", "pending"), "enabled": s.get("enabled", True),
        "learned_at": s.get("learned_at"), "result_count": s.get("result_count", 0),
        "error": s.get("error", ""), "created_at": s.get("created_at", ""),
    } for s in samples.get("samples", [])]
    return {
        "enabled": bool(samples.get("settings", {}).get("enabled", True)),
        "samples": lst,
        "learned": learned,
        "source_status": source_docs_status(),
        "stats": {
            "samples": len(lst),
            "pending": sum(1 for s in lst if s["status"] == "pending"),
            "done": sum(1 for s in lst if s["status"] == "done"),
            "failed": sum(1 for s in lst if s["status"] == "failed"),
            "entries": sum(1 for l in learned if l.get("kind") == "wordbank"),
            "rules": sum(1 for l in learned if l.get("kind") == "rule"),
        },
    }