# -*- coding: utf-8 -*-
"""验证：AI 核验「广撒网 + 规则复核」链路的确定性验算能力。

覆盖：中文大写数字/金额解析、显式算式自洽、大小写金额冲突、
以及 _rule_recheck 对候选的保留 / 剔除 / 提升行为。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from checkers import ai_checker  # noqa: E402

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


# ---------------- 中文大写数字 ----------------
chk("壹拾贰万 → 120000", ai_checker._cn_to_int("壹拾贰万") == 120000,
    ai_checker._cn_to_int("壹拾贰万"))
chk("三百二十 → 320", ai_checker._cn_to_int("三百二十") == 320,
    ai_checker._cn_to_int("三百二十"))
chk("拾 → 10", ai_checker._cn_to_int("拾") == 10, ai_checker._cn_to_int("拾"))
chk("含无关字符 → None", ai_checker._cn_to_int("abc") is None)
chk("空串 → None", ai_checker._cn_to_int("") is None)

# ---------------- 金额解析 ----------------
chk("大写「壹拾贰万元整」→ 120000",
    ai_checker._extract_cn_amount("壹拾贰万元整") == 120000.0,
    ai_checker._extract_cn_amount("壹拾贰万元整"))
chk("小写「120万元」→ 1200000",
    ai_checker._extract_num_amount("120万元") == 1200000.0,
    ai_checker._extract_num_amount("120万元"))
chk("无金额 → None", ai_checker._extract_num_amount("无任何金额") is None)

# ---------------- 显式算式自洽 ----------------
chk("80+120=200 自洽", ai_checker._verify_arithmetic("80+120=200") is True)
chk("80+120=300 矛盾", ai_checker._verify_arithmetic("80+120=300") is False)
chk("10+20+30=60 多项求和自洽", ai_checker._verify_arithmetic("10+20+30=60") is True)
chk("10+20+30=70 多项求和矛盾", ai_checker._verify_arithmetic("10+20+30=70") is False)
chk("无算式 → None", ai_checker._verify_arithmetic("合计与分项不符") is None)

# ---------------- 大小写金额冲突 ----------------
it_conflict = {"issue": "大写金额与小写金额不一致",
               "detail": "大写壹拾贰万元整，小写为13万元"}
it_same = {"issue": "大写金额与小写金额不一致",
           "detail": "大写壹拾贰万元整，小写为12万元"}
it_na = {"issue": "错别字", "detail": "某处用词不当"}
chk("大小写确实不一致 → True", ai_checker._amount_case_conflict(it_conflict) is True)
chk("大小写实际一致 → False（判为误报）",
    ai_checker._amount_case_conflict(it_same) is False)
chk("不涉及大写金额 → None", ai_checker._amount_case_conflict(it_na) is None)

# ---------------- 规则复核整体行为 ----------------
source = "本报告合计金额为300万元，其中A项80万元，B项120万元。"
cands = [
    {   # 1. 模型算对（80+120=200）且文中写 300 → 确证，应保留并提升为 high
        "quote": "合计金额为300万元", "issue": "合计与分项不符",
        "detail": "80+120=200，文中写为300", "category": "表格计算",
        "severity": "medium", "confidence": 0.7, "grounded": True,
    },
    {   # 2. 模型自己算错（80+120=300 却仍称不符）→ 必为误报，剔除
        "quote": "合计金额为300万元", "issue": "合计与分项不符",
        "detail": "80+120=300，文中写为300", "category": "表格计算",
        "severity": "medium", "confidence": 0.8, "grounded": True,
    },
    {   # 3. 引用在原文中不存在 → 防幻觉剔除
        "quote": "这个金额完全是编造的原文", "issue": "数据错误",
        "detail": "金额错误", "category": "数据错误",
        "severity": "high", "confidence": 0.9, "grounded": False,
    },
    {   # 4. 把握度低于门槛 → 剔除
        "quote": "A项80万元", "issue": "数据存疑", "detail": "可能有问题",
        "category": "数据错误", "severity": "low", "confidence": 0.2,
        "grounded": True,
    },
    {   # 5. 普通候选（无算式、把握度达标）→ 保留但不提升
        "quote": "B项120万元", "issue": "单位缺失", "detail": "缺少单位说明",
        "category": "数据错误", "severity": "medium", "confidence": 0.6,
        "grounded": True,
    },
]
kept, stat = ai_checker._rule_recheck(cands, source, 0.45)

chk("候选 5 条 → 保留 2 条", len(kept) == 2, f"实际 {len(kept)}")
chk("剔除模型算错的误报", stat.get("dropped_arith") == 1, stat)
chk("剔除编造引用", stat.get("dropped_ungrounded") == 1, stat)
chk("剔除低把握度项", stat.get("dropped_lowconf") == 1, stat)
chk("确证 1 条", stat.get("verified") == 1, stat)

by_issue = {it["issue"]: it for it in kept}
good = by_issue.get("合计与分项不符")
chk("确证项保留", good is not None)
if good:
    chk("确证项提升为 high", good["severity"] == "high", good["severity"])
    chk("确证项标记 verified", good.get("verified") is True)
    chk("确证项把握度提升", float(good["confidence"]) >= 0.9, good["confidence"])
bad = by_issue.get("合计与分项不符（算错）")
plain = by_issue.get("单位缺失")
chk("普通项保留", plain is not None)
if plain:
    chk("普通项未标记为确证", plain.get("verified") is False)

# 全部候选都不合法时应被清空
kept2, stat2 = ai_checker._rule_recheck([cands[1], cands[2]], source, 0.45)
chk("全为误报时输出为空", kept2 == [], kept2)

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
