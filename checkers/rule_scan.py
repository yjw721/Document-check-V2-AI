# -*- coding: utf-8 -*-
"""
规则与词库一键扫描管理（纯本地离线）
====================================
扫描全部来源的规则与词库，自动分类：
    - invalid   无效条目：匹配=建议 / 空内容 / 正则语法错误 / 通用无意义单字 /
                 标准正确术语作匹配条件 / 词库行格式错误（缺少分隔符、两侧相同等）
    - duplicate 重复条目：匹配式 / 关键词 / 词库行内容重复（保留首次出现项）
    - normal    正常可用条目（不参与任何清理）

扫描来源：
    A. config/custom_rules.json   自定义校验规则
    B. config/wordbanks.json      自定义词库
    C. dictionaries/*.txt         内置词库（按各文件格式解析）

清理安全红线：
    1. 仅允许清理 invalid 与 duplicate 非保留项；normal 与重复保留项一律拒绝
    2. 清理前自动备份到 reports/scan_backups/，也可随时手动导出备份
    3. AI 学习记忆样本（config/ai_memory/samples.json）绝不改动；
       删除 ai_learning 产出的实体时同步移除 learned.json 对应记录（保持记忆一致）
    4. 内置词库按行号删除，仅移除命中行，注释与其它词条原样保留

保密说明：纯本地文件读写，无任何网络行为。
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from checkers.custom_rules import load_custom_rules, save_custom_rules
from checkers.wordbank import load_wordbanks, save_wordbanks
from checkers.ai_memory import load_learned, save_learned
from checkers.dictionary_manager import DICT_META, read_dictionary
from checkers.rule_filter import NOISE_SINGLE_CHARS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(ROOT, "reports", "scan_backups")

SCAN_SOURCES = 3
SOURCE_LABELS = {"custom_rules": "自定义规则", "wordbanks": "自定义词库", "dictionary": "内置词库"}

# 内置词库文件 -> 解析格式
#   kv:        短语|说明（说明可省略）
#   pair:      错误写法|正确写法（两侧必填）
#   confusable:词A|词B|辨析（前两列必填）
#   regex:     正则|说明（右侧说明/建议必填，非法正则跳过）
#   abbrev:    简称|说明 或 裸简称；-----WHITELIST----- 之后为白名单（裸词合法）
DICT_FORMATS: Dict[str, str] = {
    "colloquial.txt": "kv",
    "redundant.txt": "kv",
    "ambiguous.txt": "kv",
    "confusable.txt": "confusable",
    "typo.txt": "pair",
    "en_typo.txt": "pair",
    "abbrev.txt": "abbrev",
    "units.txt": "regex",
    "grammar.txt": "regex",
    "vocab.txt": "regex",
    "asset_terms.txt": "regex",
}

_CAT_LABEL = {"invalid": "无效", "duplicate": "重复", "normal": "正常"}


# ---------------------------------------------------------------------------
# 基础校验
# ---------------------------------------------------------------------------
def _validate_match(pattern: str, suggestion: str, mode: str,
                    suggestions: set) -> str:
    """校验校验规则的匹配式 / 建议。返回原因字符串，空串表示通过。"""
    if not pattern:
        return "匹配式为空或全为空白字符"
    if not suggestion:
        return "建议替换字段为空"
    if pattern == suggestion:
        return "匹配式与建议替换完全相同"
    if pattern in NOISE_SINGLE_CHARS:
        return "匹配式为纯通用无意义单字，易大量误命中"
    if mode == "regex":
        try:
            re.compile(pattern)
        except re.error:
            return "正则表达式无法编译"
    elif pattern in suggestions:
        return "标准正确术语不能作为匹配触发条件（标准术语只能放在建议栏）"
    return ""


def _validate_entry(keyword: str, suggestion: str, suggestions: set) -> str:
    """校验词库词条。返回原因字符串，空串表示通过。"""
    if not keyword:
        return "关键词为空或全为空白字符"
    if not suggestion:
        return "建议替换字段为空"
    if keyword == suggestion:
        return "关键词与建议替换完全相同"
    if keyword in NOISE_SINGLE_CHARS:
        return "关键词为纯通用无意义单字，易大量误命中"
    if keyword in suggestions:
        return "标准正确术语不能作为匹配触发条件（标准术语只能放在建议栏）"
    return ""


def _validate_dict_line(fmt: str, line: str) -> str:
    """校验内置词库单行。返回原因字符串，空串表示通过。"""
    if fmt == "kv":
        if "|" not in line:
            return "词库行格式错误：缺少 | 分隔符"
        a, b = line.split("|", 1)
        a, b = a.strip(), b.strip()
        if not a:
            return "匹配内容为空"
        if a == b:
            return "匹配内容与说明完全相同"
        return ""
    if fmt == "pair":
        if "|" not in line:
            return "词库行格式错误：缺少 | 分隔符"
        a, b = line.split("|", 1)
        a, b = a.strip(), b.strip()
        if not a:
            return "错误写法为空"
        if not b:
            return "正确写法为空"
        if a == b:
            return "错误写法与正确写法完全相同"
        return ""
    if fmt == "confusable":
        parts = line.split("|", 2)
        if len(parts) < 2:
            return "词库行格式错误：需要「词A|词B」两列"
        a, b = parts[0].strip(), parts[1].strip()
        if not a or not b:
            return "易混淆词对中存在空项"
        if a == b:
            return "词A 与 词B 完全相同"
        return ""
    if fmt == "regex":
        if "|" not in line:
            return "词库行格式错误：缺少 | 分隔符"
        pat, note = line.rsplit("|", 1)
        pat, note = pat.strip(), note.strip()
        if not pat:
            return "正则模式为空"
        if not note:
            return "说明 / 建议为空"
        if pat == note:
            return "正则模式与说明完全相同"
        try:
            re.compile(pat)
        except re.error:
            return "正则表达式无法编译"
        return ""
    if fmt == "abbrev":
        if "|" in line:
            token, note = line.split("|", 1)
            token, note = token.strip(), note.strip()
            if not token:
                return "简称内容为空"
            if token == note:
                return "简称与说明完全相同"
        elif not line:
            return "词库行内容为空"
        return ""
    return ""


# ---------------------------------------------------------------------------
# 全库扫描
# ---------------------------------------------------------------------------
def _collect_suggestions() -> set:
    """汇总库内全部建议（标准正确术语），用于「标准术语不能作匹配」判定。"""
    out: set = set()
    for g in load_custom_rules().get("groups", []):
        for r in g.get("rules", []):
            v = str(r.get("suggestion") or "").strip()
            if v:
                out.add(v)
    for g in load_wordbanks().get("groups", []):
        for e in g.get("entries", []):
            v = str(e.get("suggestion") or "").strip()
            if v:
                out.add(v)
    return out


def _mk_item(item_id: str, source: str, group_id: str, group_label: str,
             kind: str, **kw: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "item_id": item_id, "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "group_id": group_id, "group_label": group_label,
        "entity_id": "", "line_no": None, "kind": kind,
        "name": "", "pattern": "", "suggestion": "", "match_mode": "",
        "enabled": True, "tag": "", "category": "normal",
        "reason": "", "keep": False, "source_tag": "",
    }
    base.update(kw)
    return base


def _scan_custom_rules(suggestions: set, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    data = load_custom_rules()
    for g in data.get("groups", []):
        gid = str(g.get("id") or "")
        glabel = str(g.get("name") or gid)
        for r in g.get("rules", []):
            rid = str(r.get("id") or "")
            pattern = str(r.get("pattern") or "").strip()
            suggestion = str(r.get("suggestion") or "").strip()
            mode = str(r.get("match_mode") or "keyword")
            reason = _validate_match(pattern, suggestion, mode, suggestions)
            it = _mk_item(
                f"cr|{gid}|{rid}", "custom_rules", gid, glabel, "rule",
                entity_id=rid, name=str(r.get("name") or ""),
                pattern=pattern, suggestion=suggestion, match_mode=mode,
                enabled=bool(r.get("enabled", True)), tag=str(r.get("tag") or ""),
                source_tag=str(r.get("source") or ""),
            )
            if reason:
                it["category"] = "invalid"
                it["reason"] = reason
            elif (mode, pattern) in ctx["pat_map"]:
                it["category"] = "duplicate"
                it["reason"] = "匹配式与模式同库内已有条目完全一致（保留首次出现）"
                it["keep"] = False
            else:
                ctx["pat_map"][(mode, pattern)] = it["item_id"]
                if mode == "keyword" and pattern:
                    ctx["kw_map"].setdefault(pattern, it["item_id"])
            items.append(it)
    return items


def _scan_wordbanks(suggestions: set, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    data = load_wordbanks()
    for g in data.get("groups", []):
        gid = str(g.get("id") or "")
        glabel = str(g.get("name") or gid)
        for e in g.get("entries", []):
            eid = str(e.get("id") or "")
            keyword = str(e.get("keyword") or "").strip()
            suggestion = str(e.get("suggestion") or "").strip()
            reason = _validate_entry(keyword, suggestion, suggestions)
            it = _mk_item(
                f"wb|{gid}|{eid}", "wordbanks", gid, glabel, "entry",
                entity_id=eid, name=keyword, pattern=keyword,
                suggestion=suggestion, match_mode="keyword",
                enabled=bool(e.get("enabled", True)), tag=str(e.get("tag") or ""),
                source_tag=str(e.get("source") or ""),
            )
            if reason:
                it["category"] = "invalid"
                it["reason"] = reason
            elif keyword in ctx["kw_map"]:
                it["category"] = "duplicate"
                it["reason"] = "关键词同库内已有条目完全一致（保留首次出现）"
                it["keep"] = False
            else:
                ctx["kw_map"].setdefault(keyword, it["item_id"])
            items.append(it)
    return items


def _scan_dictionary_file(name: str, title: str,
                          ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    content = read_dictionary(name)
    if content is None:
        return items
    fmt = DICT_FORMATS.get(name, "kv")
    in_whitelist = False
    for ln, raw in enumerate(content.split("\n")):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if fmt == "abbrev" and line == "-----WHITELIST-----":
            in_whitelist = True
            continue
        it = _mk_item(
            f"d|{name}|{ln}", "dictionary", name, title, "line",
            line_no=ln, name=line, pattern=line,
        )
        reason = ""
        if fmt == "abbrev" and in_whitelist:
            if not line:
                reason = "白名单内容为空"
        else:
            reason = _validate_dict_line(fmt, line)
        if reason:
            it["category"] = "invalid"
            it["reason"] = reason
        elif (name, line) in ctx["line_map"]:
            it["category"] = "duplicate"
            it["reason"] = "词库内存在完全相同的行（保留首次出现）"
            it["keep"] = False
        else:
            ctx["line_map"][(name, line)] = it["item_id"]
        items.append(it)
    return items


def scan_all(progress: Optional[Callable[[float, str, str], bool]] = None) -> Dict[str, Any]:
    """扫描全部来源，返回分类结果（items 含定位信息与清理标记）。

    progress: 可选进度钩子 (percent 0-100, stage key, 日志文本) -> bool。
    """
    def _hook(pct: float, log: str = "") -> None:
        if progress:
            try:
                progress(pct, "scan", log)
            except Exception:  # noqa: BLE001 - 进度回调异常不阻断
                pass

    _hook(6, "开始扫描全部规则与词库…")
    suggestions = _collect_suggestions()
    ctx: Dict[str, Any] = {"pat_map": {}, "kw_map": {}, "line_map": {}}

    _hook(18, "扫描自定义校验规则（config/custom_rules.json）…")
    rule_items = _scan_custom_rules(suggestions, ctx)

    _hook(40, "扫描自定义词库（config/wordbanks.json）…")
    entry_items = _scan_wordbanks(suggestions, ctx)

    dict_items: List[Dict[str, Any]] = []
    total_files = len(DICT_META)
    for i, m in enumerate(DICT_META, 1):
        _hook(42 + int(58 * i / max(total_files, 1)),
              f"扫描内置词库 {i}/{total_files}：{m['title']}（{m['file']}）…")
        dict_items.extend(_scan_dictionary_file(m["file"], m["title"], ctx))
    _hook(100, "全部来源扫描完成，正在汇总…")

    items = rule_items + entry_items + dict_items
    invalid = sum(1 for it in items if it["category"] == "invalid")
    dups = [it for it in items if it["category"] == "duplicate"]
    dup_cleanable = sum(1 for it in dups if not it["keep"])
    normal = sum(1 for it in items if it["category"] == "normal")

    by_source: Dict[str, Any] = {}
    for src in ("custom_rules", "wordbanks", "dictionary"):
        its = [it for it in items if it["source"] == src]
        by_source[src] = {
            "label": SOURCE_LABELS[src],
            "scanned": len(its),
            "invalid": sum(1 for it in its if it["category"] == "invalid"),
            "duplicate": sum(1 for it in its if it["category"] == "duplicate"),
            "normal": sum(1 for it in its if it["category"] == "normal"),
        }
    # 待清理 = 仅自定义来源（内置标准规则/词库只读，禁止删除）
    cleanable = len(cleanable_ids(items=items))
    readonly = len([it for it in items if it["source"] == "dictionary"
                    and (it["category"] == "invalid" or (it["category"] == "duplicate" and not it["keep"]))])
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": {
            "scanned": len(items), "invalid": invalid,
            "duplicate": len(dups), "duplicate_cleanable": dup_cleanable,
            "normal": normal, "cleanable": cleanable, "readonly": readonly,
            "sources": SCAN_SOURCES, "by_source": by_source,
        },
        "items": items,
    }


def cleanable_ids(result: Optional[Dict[str, Any]] = None,
                  items: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """全部待清理条目 id（无效 + 重复非保留项）。

    内置标准规则 / 词库（dictionary 来源）只读，禁止删除，不列入待清理。
    """
    if items is None:
        result = result if result is not None else scan_all()
        items = result.get("items", [])
    return [it["item_id"] for it in items
            if it["source"] != "dictionary"
            and (it["category"] == "invalid"
                 or (it["category"] == "duplicate" and not it["keep"]))]


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------
def _sync_learned(removed_wb: set, removed_cr: set) -> None:
    """删除 ai_learning 产出后同步清理 learned.json 记录（记忆样本不动）。"""
    if not removed_wb and not removed_cr:
        return
    try:
        ldata = load_learned()
        before = len(ldata.get("learned", []))
        ldata["learned"] = [l for l in ldata.get("learned", [])
                            if not ((l.get("kind") == "wordbank"
                                     and l.get("entity_id") in removed_wb)
                                    or (l.get("kind") == "rule"
                                        and l.get("entity_id") in removed_cr))]
        if len(ldata["learned"]) != before:
            save_learned(ldata)
    except Exception:  # noqa: BLE001 - 记忆同步失败不影响清理主流程
        pass


def _delete_items(items: List[Dict[str, Any]]) -> int:
    """按 item 定位信息执行删除，返回删除条数。

    仅处理自定义来源（custom_rules / wordbanks）；内置词库条目由
    clean_items 提前拦截，此处不再处理。
    """
    cr: Dict[str, set] = {}
    wb: Dict[str, set] = {}
    for it in items:
        if it["source"] == "custom_rules":
            cr.setdefault(it["group_id"], set()).add(it["entity_id"])
        elif it["source"] == "wordbanks":
            wb.setdefault(it["group_id"], set()).add(it["entity_id"])

    removed = 0
    removed_cr_ids: set = set()
    removed_wb_ids: set = set()

    if cr:
        data = load_custom_rules()
        for g in data.get("groups", []):
            ids = cr.get(g.get("id"))
            if not ids:
                continue
            g["rules"] = [r for r in g.get("rules", []) if r.get("id") not in ids]
            removed += len(ids)
            removed_cr_ids.update(ids)
        # 学习产出分组清空后整体移除（手动分组保留）
        data["groups"] = [g for g in data.get("groups", [])
                          if g.get("id") != "ai_learning" or g.get("rules")]
        save_custom_rules(data)

    if wb:
        data = load_wordbanks()
        for g in data.get("groups", []):
            ids = wb.get(g.get("id"))
            if not ids:
                continue
            g["entries"] = [e for e in g.get("entries", []) if e.get("id") not in ids]
            removed += len(ids)
            removed_wb_ids.update(ids)
        data["groups"] = [g for g in data.get("groups", [])
                          if g.get("id") != "ai_learning_wb" or g.get("entries")]
        save_wordbanks(data)

    _sync_learned(removed_wb_ids, removed_cr_ids)
    return removed


def clean_items(item_ids: List[str], write_backup: bool = True) -> Tuple[bool, str, Dict[str, Any]]:
    """按 item_id 清理无效 / 重复（非保留）条目。

    安全红线：
        - normal 与重复保留项拒绝清理
        - 内置标准规则 / 词库（dictionary 来源）只读，禁止删除，一律拒绝
        - 样本记忆不触碰；词库文件仅删除命中行，注释与其它词条原样保留
    返回 (是否成功, 说明, 统计信息)。
    """
    result = scan_all()
    index = {it["item_id"]: it for it in result["items"]}
    to_delete: List[Dict[str, Any]] = []
    skipped_builtin = 0
    for iid in item_ids or []:
        it = index.get(iid)
        if not it:
            continue
        if it["source"] == "dictionary":
            skipped_builtin += 1  # 内置内容只读，禁止删除
            continue
        if it["category"] == "invalid" or (it["category"] == "duplicate" and not it["keep"]):
            to_delete.append(it)
    if skipped_builtin and not to_delete:
        return False, "内置标准规则 / 词库为只读内容，禁止删除（请勿勾选内置来源条目）", {"removed": 0}
    if not to_delete:
        return False, "没有可清理的条目（仅允许清理自定义来源的无效项与重复项）", {"removed": 0}

    removed = _delete_items(to_delete)
    backup_file = ""
    if write_backup:
        backup_file = _write_backup(to_delete, removed)
    note = "（另跳过内置只读条目 " + str(skipped_builtin) + " 条）" if skipped_builtin else ""
    return (True, f"已清理 {removed} 条条目{note}（自动备份：{backup_file or '已跳过'}）",
            {"removed": removed, "backup_file": backup_file, "stats": result["stats"]})


# ---------------------------------------------------------------------------
# 备份导出
# ---------------------------------------------------------------------------
def build_backup_text(items: List[Dict[str, Any]], title: str = "") -> str:
    lines = ["# 规则与词库清理备份", f"# 备份时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"]
    if title:
        lines.append(f"# {title}")
    lines.append(f"# 共 {len(items)} 条")
    for it in items:
        tag = it.get("source_tag") or ""
        tag_s = f"（{tag}）" if tag else ""
        loc = f"{it['source_label']}｜{it['group_label']}｜{it.get('name') or ''}"
        lines.append(f"[{_CAT_LABEL.get(it['category'], it['category'])}] {loc}{tag_s}")
        if it.get("pattern"):
            lines.append(f"    匹配式：{it['pattern']}")
        if it.get("suggestion"):
            lines.append(f"    建议：{it['suggestion']}")
        if it.get("reason"):
            lines.append(f"    原因：{it['reason']}")
    return "\n".join(lines) + "\n"


def build_backup_csv(items: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["分类", "来源", "分组/文件", "条目", "匹配式", "建议", "原因", "学习来源"])
    for it in items:
        w.writerow([
            _CAT_LABEL.get(it["category"], it["category"]),
            it["source_label"],
            it["group_label"],
            it.get("name") or "",
            it.get("pattern") or "",
            it.get("suggestion") or "",
            it.get("reason") or "",
            it.get("source_tag") or "",
        ])
    return buf.getvalue()


def _write_backup(items: List[Dict[str, Any]], removed: int) -> str:
    """清理前自动落盘备份，返回备份文件名（失败返回空串）。"""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        fname = f"scan_clean_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        with open(os.path.join(BACKUP_DIR, fname), "w", encoding="utf-8") as fp:
            fp.write(build_backup_text(items, title=f"清理前自动备份（本次清理 {removed} 条）"))
        return fname
    except OSError:
        return ""
