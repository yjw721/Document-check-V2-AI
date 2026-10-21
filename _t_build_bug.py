# -*- coding: utf-8 -*-
"""复现 / 验证 AI 规则词库智能生成（ai_builder）的解析与入库链路问题。"""
import json
import sys
import time

sys.path.insert(0, r"E:\AIsoftware\文档核验AI")

from checkers import ai_builder as ab  # noqa: E402

CALLS = {"n": 0}


def make_stub(reply):
    def _stub(cfg, messages, think=None, options=None, on_token=None):
        CALLS["n"] += 1
        if on_token:
            on_token(reply, "content")
        return reply
    return _stub


GOOD = {
    "wordbanks": [
        {"name": "易混淆词", "entries": [
            {"keyword": "截止日期", "tag": "易混淆词", "suggestion": "截至日期"},
            {"keyword": "帐号", "tag": "易混淆词", "suggestion": "账号"},
        ]}
    ],
    "rules": [
        {"name": "口语化表达", "match_mode": "keyword", "pattern": "立马搞定",
         "severity": "medium", "suggestion": "立即完成"},
        {"name": "金额单位缺失", "match_mode": "regex", "pattern": r"金额\d+\s*元(?!.*万元)",
         "severity": "high", "suggestion": "金额单位统一用「万元」"},
    ],
}


def case(name, reply, kind="dialogue"):
    CALLS["n"] = 0
    ab._call_ollama = make_stub(reply)
    t0 = time.time()
    if kind == "dialogue":
        ok, msg, res = ab.build_dialogue("请把截止日期改为截至日期；禁止口语化表达", {})
    else:
        ok, msg, res = ab.build_from_text("准则文本示例……" * 50, {})
    dt = time.time() - t0
    print(f"--- {name} ---")
    print(f"  ok={ok} msg={msg} 耗时={dt:.2f}s 调用次数={CALLS['n']}")
    print(f"  wordbanks={len(res.get('wordbanks') or [])} rules={len(res.get('rules') or [])}")
    if res.get("wordbanks"):
        print("  wb0:", json.dumps(res["wordbanks"][0], ensure_ascii=False)[:200])
    if res.get("rules"):
        print("  rule0:", json.dumps(res["rules"][0], ensure_ascii=False)[:200])
    return ok, msg, res


# 1. 干净 JSON
case("1 干净 JSON", json.dumps(GOOD, ensure_ascii=False))

# 2. 思考链 + JSON
case("2 思考链前缀+JSON", "我先分析一下……\n用户需要3类规则。\n" + json.dumps(GOOD, ensure_ascii=False))

# 3. 思考链里含伪 JSON（无 wordbanks/rules 键）
case("3 含伪JSON片段", '先思考 {"analysis": {"a": 1, "b": [1,2]}} 然后输出\n'
     + json.dumps(GOOD, ensure_ascii=False))

# 4. 截断 JSON（尾部砍掉）
trunc = json.dumps(GOOD, ensure_ascii=False)
case("4 截断JSON（尾部砍掉）", trunc[: len(trunc) - 40])

# 4b. 深度截断（只剩一半）
case("4b 深度截断", trunc[: int(len(trunc) * 0.6)])

# 5. 中文引号嵌入
bad = json.dumps(GOOD, ensure_ascii=False).replace("截至日期", '统一写“截至”日期')
case("5 中文全角引号", bad)

# 6. 代码块包裹 + 单个尾随逗号（真实常见形态）
_single = json.dumps(GOOD, ensure_ascii=False)
_single = _single[: _single.rfind("}")] + ",}"
case("6 ```json包裹+尾随逗号", "好的：\n```json\n" + _single + "\n```")

# 7. 长思考链性能（大量花括号）—— 单独开关，避免拖慢主流程
if "--perf" in sys.argv:
    import random  # noqa: E402
    noise = "".join(random.choice(["思考中{a:1}", "再看{b:[2]}", "继续{c:{d:3}}", "。\n"])
                    for _ in range(int(sys.argv[sys.argv.index("--perf") + 1])))
    t0 = time.time()
    case("7 长思考链", noise + json.dumps(GOOD, ensure_ascii=False))
    print(f"  >>> 总耗时 {time.time() - t0:.2f}s")
