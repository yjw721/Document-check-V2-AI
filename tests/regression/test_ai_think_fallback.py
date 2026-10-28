# -*- coding: utf-8 -*-
"""回归：思考型模型把答案输出在 thinking、content 为空时不得静默记 0 条命中。

背景（2026-10 线上症状）：qwen3.5 等思考模型偶发把 JSON 问题清单全部输出在
Ollama 响应的 message.thinking 字段，message.content 为空。旧实现只取 content，
空了既不报错也不重试 → AI 核验「完成」但命中 0 条 → 报告错误详情为零。

修复契约：
  1. 非流式：content 空且 thinking 非空 → 返回 thinking（下游自提 JSON + 严格取证）；
  2. 流式：同策略（thinking 缓冲兜底）；
  3. content 与 thinking 皆空 → 抛 AiError（触发 _request_issues 重试 / 失败降级）；
  4. 经 _request_issues 全链路：thinking 兜底文本能正常解析出问题条目。
"""
import json
import os
import sys
import urllib.request

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


ISSUE_JSON = json.dumps([{
    "quote": "项目总投资500万元，其中设备采购款300元",
    "category": "数据错误",
    "issue": "金额单位与数值规模不匹配",
    "detail": "500万元总投资中设备采购款仅300元，存在数量级矛盾",
    "suggestion": "核实设备采购款单位",
    "severity": "high",
    "confidence": 0.9,
}], ensure_ascii=False)

THINK_TEXT = "让我逐项检查这份材料……发现了金额问题，现在输出。\n" + ISSUE_JSON
SOURCE = "项目总投资500万元，其中设备采购款300元，线路总长度5KM。"
CFG = {"base_url": "http://127.0.0.1:11434", "model": "test-model", "timeout": 30}
MSGS = [{"role": "user", "content": "x"}]


def test_non_stream_fallback():
    print("\n== 1. 非流式 thinking 兜底 ==")
    orig = ai_checker._http_json
    try:
        # content 空 + thinking 有答案 → 返回 thinking 文本
        ai_checker._http_json = lambda *a, **k: {"message": {"content": "", "thinking": THINK_TEXT}}
        out = ai_checker._call_ollama(CFG, MSGS)
        chk("content 空时回退 thinking", ISSUE_JSON in out, f"len={len(out)}")
        items = ai_checker._parse_issues(out, SOURCE, 0.45)
        chk("thinking 文本可解析出条目", len(items) == 1
            and items[0]["category"] == "数据错误", items)

        # content 正常 → 行为不变
        ai_checker._http_json = lambda *a, **k: {"message": {"content": ISSUE_JSON}}
        out2 = ai_checker._call_ollama(CFG, MSGS)
        chk("content 正常时不受影响", out2 == ISSUE_JSON)

        # 双空 → 报错（可重试），而非静默成功
        ai_checker._http_json = lambda *a, **k: {"message": {"content": "", "thinking": ""}}
        raised = False
        try:
            ai_checker._call_ollama(CFG, MSGS)
        except ai_checker.AiError:
            raised = True
        chk("双空抛 AiError 触发重试", raised)
    finally:
        ai_checker._http_json = orig


def test_stream_fallback():
    print("\n== 2. 流式 thinking 兜底 ==")

    class _FakeResp:
        """模拟 Ollama NDJSON 流式响应。"""
        def __init__(self, lines):
            self._lines = [json.dumps(l, ensure_ascii=False).encode("utf-8") for l in lines]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self._lines)

    def fake_urlopen(lines):
        def _open(req, timeout=None):
            return _FakeResp(lines)
        return _open

    orig_open = urllib.request.urlopen
    try:
        # content 空、thinking 分片输出答案 → 拼接 thinking 作为返回
        urllib.request.urlopen = fake_urlopen([
            {"message": {"thinking": "先思考……"}, "done": False},
            {"message": {"thinking": ISSUE_JSON}, "done": False},
            {"message": {"content": ""}, "done": True},
        ])
        tokens = []
        out = ai_checker._call_ollama_stream(
            "http://x/api/chat", {}, lambda t, k: tokens.append((t, k)), 30.0)
        chk("流式 content 空时回退 thinking", ISSUE_JSON in out, f"len={len(out)}")
        chk("on_token 仍收到 thinking 分片", any(k == "thinking" for _t, k in tokens))
        chk("thinking 不混入 content 流", all(
            not (k == "content" and t == ISSUE_JSON) for t, k in tokens))

        # 双空 → 报错
        urllib.request.urlopen = fake_urlopen([
            {"message": {"content": ""}, "done": False},
            {"message": {"content": ""}, "done": True},
        ])
        raised = False
        try:
            ai_checker._call_ollama_stream("http://x/api/chat", {}, lambda t, k: None, 30.0)
        except ai_checker.AiError:
            raised = True
        chk("流式双空抛 AiError", raised)

        # content 正常 → 行为不变
        urllib.request.urlopen = fake_urlopen([
            {"message": {"content": ISSUE_JSON[:10]}, "done": False},
            {"message": {"content": ISSUE_JSON[10:]}, "done": True},
        ])
        out3 = ai_checker._call_ollama_stream("http://x/api/chat", {}, lambda t, k: None, 30.0)
        chk("流式 content 正常时不受影响", out3 == ISSUE_JSON)
    finally:
        urllib.request.urlopen = orig_open


def test_request_issues_end_to_end():
    print("\n== 3. _request_issues 全链路 ==")
    orig = ai_checker._http_json
    try:
        ai_checker._http_json = lambda *a, **k: {"message": {"content": "", "thinking": THINK_TEXT}}
        ai_cfg = {"cache": {"enabled": False}}
        items, err = ai_checker._request_issues(
            ai_cfg, "local", MSGS, retry_n=0, min_confidence=0.45, source_text=SOURCE)
        chk("无报错", err is None, err)
        chk("问题条目正常产出", len(items) == 1 and items[0]["severity"] == "high", items)

        # 仅推理文本、无任何 JSON 结构 → 格式违规报错（重试/记失败），不得静默记 0
        ai_checker._http_json = lambda *a, **k: {
            "message": {"content": "Thinking Process:\n1. 分析请求……2. 检查金额……（无 JSON）",
                        "thinking": ""}}
        items2, err2 = ai_checker._request_issues(
            ai_cfg, "local", MSGS, retry_n=0, min_confidence=0.45, source_text=SOURCE)
        chk("纯推理文本按格式违规报错", items2 == [] and err2 and "JSON" in err2, (items2, err2))

        # 合法空数组 → 真实的"未发现问题"，不报错
        ai_checker._http_json = lambda *a, **k: {"message": {"content": "[]", "thinking": ""}}
        items3, err3 = ai_checker._request_issues(
            ai_cfg, "local", MSGS, retry_n=0, min_confidence=0.45, source_text=SOURCE)
        chk("合法空数组不报错", items3 == [] and err3 is None, (items3, err3))

        # 格式违规 → 重试携带纠偏消息；重试成功后正常产出
        seen_msgs = []

        def fake_call(cfg, m, think=None, options=None, on_token=None):
            seen_msgs.append(m)
            if len(seen_msgs) == 1:
                return "Thinking Process: 只有一段推理，没有 JSON"
            return ISSUE_JSON

        orig_call = ai_checker._call_ollama
        try:
            ai_checker._call_ollama = fake_call
            ai_cfg2 = {"cache": {"enabled": True, "max_entries": 5}}
            items4, err4 = ai_checker._request_issues(
                ai_cfg2, "local", MSGS, retry_n=1, min_confidence=0.45, source_text=SOURCE)
            chk("违规重试后成功产出", err4 is None and len(items4) == 1, (err4, items4))
            chk("重试请求附加纠偏消息",
                len(seen_msgs) == 2 and len(seen_msgs[1]) == len(MSGS) + 1
                and "JSON 数组" in seen_msgs[1][-1]["content"])
        finally:
            ai_checker._call_ollama = orig_call
    finally:
        ai_checker._http_json = orig


def main() -> int:
    test_non_stream_fallback()
    test_stream_fallback()
    test_request_issues_end_to_end()
    print(f"\n总计：OK={ok}  FAIL={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
