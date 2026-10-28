# -*- coding: utf-8 -*-
"""AI 输出 JSON 结构判定回归：收缩 _has_json_structure，杜绝思考型模型
推理文本中零星 JSON 碎片引发的「静默记 0」假阴性。

回归背景：模型返回约 6630 字纯英文推理文本，其中散落零星 JSON 碎片
（对象 / 列表，如 {"name": "x"}）。旧版 _has_json_structure 只要见到「任何
JSON 结构」就放行 → 解析出 0 条且无报错，造成「看似成功、实际 0 命中」。

收紧后：必须是「对象数组」或「对象包裹的对象数组」形状，且数组中的对象必须
命中 issue/quote/severity/suggestion 等输出契约字段；空数组 [] 仍视为合法的
「未发现问题」结果。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from checkers.ai_checker import _has_json_structure, _parse_issues

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def _long_reasoning(n: int = 6630) -> str:
    """构造一段约 n 字符的英文推理文本，模拟思考型模型的 thinking 输出。"""
    para = (
        "We begin by analyzing the document segment against the provided reference "
        "norms. First, we consider whether the monetary amounts are internally "
        "consistent and whether the stated figures reconcile with the supporting "
        "schedules. Next, we evaluate the logical flow of the narrative and check "
        "for any contradictions between the summarized conclusions and the detailed "
        "evidence presented in the appendix. "
    )
    out = []
    while len("".join(out)) < n:
        out.append(para)
    return "".join(out)[:n]


def main():
    print("== AI 输出 JSON 结构判定（假阴性修复）==")

    # 1) 空数组 [] 是合法的「未发现问题」结果，必须放行
    check("空数组 [] 视为合法结果（True）", _has_json_structure("[]") is True)

    # 2) 真实问题数组（英文键）
    real_en = '[{"issue": "金额勾稽不平", "quote": "总资产 100", "severity": "high"}]'
    check("真实问题数组（英文键）True", _has_json_structure(real_en) is True)
    check("真实问题数组可解析出 1 条", len(_parse_issues(real_en, "总资产 100")) == 1)

    # 3) 真实问题数组（中文键）
    real_cn = '[{"问题": "引用缺失", "引用": "详见第三节", "建议": "补充出处"}]'
    check("真实问题数组（中文键）True", _has_json_structure(real_cn) is True)

    # 4) 对象包裹真实问题数组
    wrapped = '{"issues": [{"issue": "x", "quote": "y", "severity": "low"}]}'
    check("对象包裹真实问题数组 True", _has_json_structure(wrapped) is True)

    # 5) 伪数组对象（碎片，无契约字段）-> 必须 False（修复点）
    frag_arr = '[{"name": "x", "val": 1}, {"name": "y", "val": 2}]'
    check("伪数组 [{'name':'x'}] False（修复点）", _has_json_structure(frag_arr) is False)

    # 6) 散落数字列表碎片
    check("数字列表 [1,2,3] False", _has_json_structure("[1, 2, 3]") is False)

    # 7) 散落对象碎片（如 chat message 形态）
    check("对象碎片 {'Role':'analyst'} False",
          _has_json_structure('{"Role": "analyst", "content": "thinking"}') is False)

    # 8) 对象包裹碎片数组 -> False
    check("对象包裹碎片数组 False",
          _has_json_structure('{"x": [{"name": "x"}]}') is False)

    # 9) 6630 字推理 + 伪数组碎片 -> 必须 False（用户报告的根因场景）
    reason = _long_reasoning(6630)
    assert len(reason) >= 6600, "reasoning text too short"
    bug_case = reason + frag_arr
    check("6630 字推理 + 伪数组碎片 False（根因场景）",
          _has_json_structure(bug_case) is False)
    # 该场景若被放行，解析会得到 0 条且无报错——即假阴性；这里确认结构判定已拦截
    check("根因场景解析确实为 0 条（需经重试而非静默记 0）",
          len(_parse_issues(bug_case, reason)) == 0)

    # 10) 6630 字推理 + 真实问题数组 -> 必须 True（正确响应仍识别）
    ok_case = reason + real_en
    check("6630 字推理 + 真实问题数组 True", _has_json_structure(ok_case) is True)

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
