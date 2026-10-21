# -*- coding: utf-8 -*-
"""
资产评估智能核查 —— 端到端自检
=========================================================================
用 tests/samples/valuation 下的 5 份「含已知错误」样本跑通四阶段流水线，
校验 9 大核查节点与本地规则引擎的命中情况，并与 EXPECTED 比对。

运行：python tests/run_valuation_check.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from checkers.valuation.pipeline import Pipeline  # noqa: E402
from tests.make_val_samples import EXPECTED  # noqa: E402

SAMPLE_DIR = os.path.join(ROOT, "tests", "samples", "valuation")
SLOT_FILES = {
    "report": "资产评估报告.docx",
    "statement": "资产评估说明.docx",
    "income": "收益法测算表.xlsx",
    "cost": "成本法测算表.xlsx",
    "market": "市场法测算表.xlsx",
}


def main() -> int:
    missing = [f for f in SLOT_FILES.values() if not os.path.exists(os.path.join(SAMPLE_DIR, f))]
    if missing:
        print("缺少样本文件，请先运行：python tests/make_val_samples.py")
        return 1

    workdir = tempfile.mkdtemp(prefix="val_check_")
    slots = {slot: {"name": name, "path": os.path.join(SAMPLE_DIR, name)}
             for slot, name in SLOT_FILES.items()}

    logs: list = []
    pipeline = Pipeline(
        payload={"slots": slots, "workdir": workdir, "use_ai": False},
        ai_cfg={"enabled": False},
        log=logs.append,
        progress=lambda p, s, m: True,
        cancel=lambda: False,
    )
    result = pipeline.run()

    print("=" * 72)
    print("阶段一 预处理：", result.get("preprocess", {}).get("message", ""))
    for item in result.get("preprocess", {}).get("items", []):
        print(f"  [{item.get('slot')}] {item.get('file')}：{item.get('message','')}")
        clean = item.get("clean") or {}
        if clean:
            print("    深度清理：隐藏表 {} / 名称 {} / 外链 {}".format(
                len(clean.get("hidden_sheets") or []), clean.get("defined_names"),
                clean.get("external_links")))

    print("-" * 72)
    print("阶段二 切分：")
    for b in result.get("split", {}).get("blocks", []):
        extra = ""
        if b.get("kind") == "excel":
            own = b.get("stats", {}).get("own_sheets") or []
            gen = b.get("general_sheets") or []
            extra = f"自有表 {len(own)} + 通用表 {len(gen)}"
        else:
            extra = f"{b.get('chars', 0)} 字"
        print(f"  {b.get('id')} {b.get('name')}：{extra}")

    print("-" * 72)
    print("阶段三 9 大核查节点：")
    for n in result.get("nodes", []):
        print(f"  {n.get('node_no')}{n.get('node_name')} [{n.get('status')}] "
              f"{n.get('item_done')}/{n.get('item_total')} 项，命中 {len(n.get('finding_brief', []))} 条"
              + (f"（{n.get('message','')}）" if n.get("status") != "done" else ""))

    print("-" * 72)
    summary = result.get("summary", {})
    print("阶段四 汇总：", result.get("message", ""))
    print(f"  去重：原始 {summary.get('findings_raw')} 条 → 合并重复 "
          f"{summary.get('dedup_merged')} 条 → 最终 {summary.get('total')} 条")
    print(f"  级别：严重 {summary.get('high')} / 一般 {summary.get('medium')} / 轻微 {summary.get('low')}")
    print(f"  来源：规则 {summary.get('by_origin', {}).get('rule')} / "
          f"AI {summary.get('by_origin', {}).get('ai')} / 复核 {summary.get('by_origin', {}).get('review')}")

    # 按节点统计
    print("-" * 72)
    print("问题分布：")
    for k, v in sorted((summary.get("by_node") or {}).items(), key=lambda x: -x[1]):
        print(f"  {k}：{v} 条")

    # 规则命中校验
    print("-" * 72)
    findings = result.get("findings", [])
    hit_rules = set()
    for f in findings:
        for key in EXPECTED:
            if key in (f.get("detail") or "") or key in (f.get("snippet") or ""):
                hit_rules.add(key)
    # 规则命中通过 item → rule_key 反查更可靠
    checklist = pipeline.checklist
    rule_of_item = {i["id"]: i.get("rule_key") for n in checklist["nodes"] for i in n["items"]}
    hit_rules = {rule_of_item.get(f.get("item_id")) for f in findings
                 if f.get("item_id") in rule_of_item and rule_of_item.get(f.get("item_id"))}

    print("规则命中校验（预期 vs 实际）：")
    ok = fail = 0
    for key in sorted(EXPECTED):
        if key in hit_rules:
            print(f"  ✔ {key}  —— {EXPECTED[key][0]}")
            ok += 1
        else:
            print(f"  ✘ {key}  —— {EXPECTED[key][0]}（未命中）")
            fail += 1

    print("-" * 72)
    print("问题明细（前 25 条）：")
    for f in findings[:25]:
        print(f"  [{f.get('severity')}] {f.get('node_no')}{f.get('node_name')} · "
              f"{f.get('item_title')}\n      {f.get('location')}：{f.get('detail')[:150]}")

    print("=" * 72)
    print(f"结果：{ok} 通过 / {fail} 未命中")
    shutil.rmtree(workdir, ignore_errors=True)
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
