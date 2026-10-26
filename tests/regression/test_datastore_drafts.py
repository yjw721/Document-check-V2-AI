# -*- coding: utf-8 -*-
"""验证：统一本地数据层（草稿箱）与「采纳并入规则/词库」联动链路。

覆盖：产出持久化（不再只存内存）、列表/筛选、详情、状态流转、统一统计，
以及采纳时合并进正式规则库 / 词库的正确性（含非法 pattern 过滤）。

测试全程使用临时路径与替换的读写函数，不触碰真实 config/*.json。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from checkers import datastore  # noqa: E402
import server.routes.ai_drafts as ad  # noqa: E402

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


TMP = tempfile.mkdtemp(prefix="_t_datastore_")
STORE = os.path.join(TMP, "data_store.json")

RESULT = {
    "rules": [
        {"name": "金额单位缺失", "match_mode": "keyword", "pattern": "万元整",
         "severity": "high", "suggestion": "补充单位"},
        {"name": "危险正则", "match_mode": "regex", "pattern": "(a+)+",
         "severity": "medium", "suggestion": "应被过滤"},
        {"name": "空模式", "match_mode": "keyword", "pattern": "   ",
         "severity": "low", "suggestion": "应被过滤"},
    ],
    "wordbanks": [
        {"name": "财务术语", "entries": [
            {"keyword": "评估基准日", "tag": "术语", "suggestion": "统一用法"},
            {"keyword": "", "tag": "空词条", "suggestion": "应被过滤"},
        ]},
    ],
}

# ---------------- 持久化（核心：产出不再只存内存） ----------------
did = datastore.add_draft("b1_1", "dialogue", RESULT, "ai_dialogue", "AI对话创建",
                          {"removed": 1}, path=STORE)
chk("产出持久化返回草稿 id", bool(did), did)
chk("草稿文件已落盘", os.path.exists(STORE))

d = datastore.get_draft(did, path=STORE)
chk("可取回完整草稿", d is not None)
if d:
    chk("草稿状态为待确认", d.get("status") == datastore.DRAFT_PENDING, d.get("status"))
    chk("保留来源标记", d.get("source") == "ai_dialogue" and d.get("source_label") == "AI对话创建")
    chk("统计：规则 3 条", (d.get("stats") or {}).get("rules") == 3)
    chk("统计：词条 2 条", (d.get("stats") or {}).get("entries") == 2)
    chk("完整载荷已保存", len(d.get("rules") or []) == 3 and len(d.get("wordbanks") or []) == 1)

# ---------------- 列表与筛选 ----------------
items = datastore.list_drafts(path=STORE)
chk("列表返回 1 条", len(items) == 1, len(items))
chk("列表不含大载荷（轻量）", "rules" not in (items[0] if items else {}))
chk("按状态筛选 pending", len(datastore.list_drafts(status="pending", path=STORE)) == 1)
chk("按状态筛选 accepted 为空", len(datastore.list_drafts(status="accepted", path=STORE)) == 0)
chk("按来源筛选", len(datastore.list_drafts(source="ai_dialogue", path=STORE)) == 1)
chk("按来源筛选（不匹配）", len(datastore.list_drafts(source="ai_doc", path=STORE)) == 0)

# ---------------- 状态流转 ----------------
chk("标记为已采纳", datastore.set_draft_status(did, datastore.DRAFT_ACCEPTED,
                                              note="入库规则 1 条", path=STORE))
d2 = datastore.get_draft(did, path=STORE)
chk("状态已变更", d2.get("status") == datastore.DRAFT_ACCEPTED)
chk("备注已写入", d2.get("note") == "入库规则 1 条")
chk("处理时间已记录", bool(d2.get("handled_at")))

# ---------------- 统一统计 ----------------
st = datastore.store_stats(path=STORE)
chk("统计总数 1", st.get("total") == 1, st)
chk("状态分布含 accepted", (st.get("by_status") or {}).get("accepted") == 1)
chk("来源分布含 ai_dialogue", (st.get("by_source") or {}).get("ai_dialogue") == 1)
chk("已采纳规则计数", st.get("accepted_rules") == 3, st.get("accepted_rules"))
chk("已采纳词条计数", st.get("accepted_entries") == 2, st.get("accepted_entries"))

# ---------------- 采纳：合并进正式规则库 / 词库（联动检测链路） ----------------
# 用替换的读写函数承接合并结果，避免写入真实配置
_calls = {"rules_saved": None, "wb_saved": None}
_orig = {
    "load_cr": ad.load_custom_rules, "save_cr": ad.save_custom_rules,
    "load_wb": ad.load_wordbanks, "save_wb": ad.save_wordbanks,
}
ad.load_custom_rules = lambda *a, **k: {"groups": []}
ad.save_custom_rules = lambda data, *a, **k: (_calls.__setitem__("rules_saved", data), True)[1]
ad.load_wordbanks = lambda *a, **k: {"groups": []}
ad.save_wordbanks = lambda data, *a, **k: (_calls.__setitem__("wb_saved", data), True)[1]

try:
    added = ad._merge_into_libraries(d)
    chk("入库规则 1 条（非法已被过滤）", added["rules"] == 1, added)
    chk("入库词条 1 条（空词条被过滤）", added["entries"] == 1, added)

    saved_rules = _calls["rules_saved"]
    chk("规则库已写回", saved_rules is not None)
    if saved_rules:
        g = (saved_rules.get("groups") or [])[0]
        chk("生成规则分组", g.get("enabled") is True and len(g.get("rules") or []) == 1)
        r = (g.get("rules") or [])[0]
        chk("规则字段完整", all(k in r for k in
                                ("id", "name", "enabled", "match_mode", "pattern",
                                 "severity", "suggestion")))
        chk("保留来源标记（可溯源）",
            r.get("source") == "ai_dialogue" and r.get("source_label") == "AI对话创建")

    saved_wb = _calls["wb_saved"]
    chk("词库已写回", saved_wb is not None)
    if saved_wb:
        g = (saved_wb.get("groups") or [])[0]
        chk("生成词库分组", g.get("enabled") is True and len(g.get("entries") or []) == 1)
        e = (g.get("entries") or [])[0]
        chk("词条字段完整", all(k in e for k in ("id", "keyword", "tag", "suggestion", "enabled")))
        chk("词条保留来源标记", e.get("source") == "ai_dialogue")
finally:
    ad.load_custom_rules, ad.save_custom_rules = _orig["load_cr"], _orig["save_cr"]
    ad.load_wordbanks, ad.save_wordbanks = _orig["load_wb"], _orig["save_wb"]

# ---------------- 安全校验 ----------------
chk("灾难性正则被拒", ad._pattern_ok("regex", "(a+)+") is False)
chk("非法正则被拒", ad._pattern_ok("regex", "[unclosed") is False)
chk("超长 pattern 被拒", ad._pattern_ok("keyword", "x" * 501) is False)
chk("空 pattern 被拒", ad._pattern_ok("keyword", "  ") is False)
chk("合法关键词通过", ad._pattern_ok("keyword", "万元整") is True)
chk("合法正则通过", ad._pattern_ok("regex", r"\d{4}年") is True)

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
