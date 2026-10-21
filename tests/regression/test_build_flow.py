# -*- coding: utf-8 -*-
"""集成验证：AI 规则词库智能生成（build）全链路修复。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


from checkers import ai_builder as ai_builder_module  # noqa: E402

_TAG = str(int(time.time()))[-5:]
PAYLOAD = {
    "wordbanks": [
        {"name": "易混淆词", "entries": [
            {"keyword": f"截止日期{_TAG}", "tag": "易混淆词", "suggestion": f"截至日期{_TAG}"},
            {"keyword": f"帐号{_TAG}", "tag": "易混淆词", "suggestion": f"账号{_TAG}"},
        ]}
    ],
    "rules": [
        {"name": "口语化表达", "match_mode": "keyword", "pattern": f"立马搞定{_TAG}",
         "severity": "medium", "suggestion": f"立即完成{_TAG}"},
        {"name": "金额单位缺失", "match_mode": "regex", "pattern": r"金额\d+\s*元(?!.*万元)",
         "severity": "high", "suggestion": "金额单位统一用「万元」"},
    ],
}


def slow_stub(chunks=40, delay=0.05):
    """模拟本地模型慢速流式输出（可用于验证中止）。"""
    def _stub(cfg, messages, think=None, options=None, on_token=None):
        import json as _json
        text = _json.dumps(PAYLOAD, ensure_ascii=False)
        step = max(1, len(text) // chunks)
        for i in range(0, len(text), step):
            time.sleep(delay)
            if on_token:
                on_token(text[i:i + step], "content")
        return text
    return _stub


def fast_stub(content=None):
    import json as _json
    payload = content or _json.dumps(PAYLOAD, ensure_ascii=False)

    def _stub(cfg, messages, think=None, options=None, on_token=None):
        if on_token:
            on_token(payload, "content")
        return payload
    return _stub


import app as appmod  # noqa: E402
import server.routes.ai_build as buildrt  # noqa: E402
from checkers import ai_checker  # noqa: E402
# 来源标记逻辑已上提到服务层（原位于 app.py 内），测试改为直接引用服务层实现
from server.services.ai import mark_source as _mark_source
from server.services.ai import BUILD_SOURCES as _BUILD_SOURCES
from server.state import TASK_LOCK as _TASK_LOCK

print("\n== 1. 来源标记写入（source / source_label 不再被 tag 吞掉）==")
res = {"wordbanks": [{"name": "g", "entries": [{"keyword": "a", "tag": "易混淆词", "suggestion": "b"}]}],
       "rules": [{"name": "r", "match_mode": "keyword", "pattern": "x", "severity": "low", "suggestion": "y"}]}
_mark_source(res, "ai_dialogue", "AI对话创建")
e = res["wordbanks"][0]["entries"][0]
chk("词条写入 source", e.get("source") == "ai_dialogue", str(e))
chk("词条写入 source_label", e.get("source_label") == "AI对话创建", str(e))
chk("词条 AI 分类 tag 未被覆盖", e.get("tag") == "易混淆词", str(e))
r0 = res["rules"][0]
chk("规则写入 source", r0.get("source") == "ai_dialogue", str(r0))
chk("规则写入 source_label", r0.get("source_label") == "AI对话创建", str(r0))
chk("渠道表覆盖 doc", _BUILD_SOURCES["doc"] == ("ai_doc", "AI文档创建"))

print("\n== 2. 空输入直接拒绝（不再空转一次本地推理）==")
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(appmod.app)
r = client.post("/api/ai/build/dialogue", json={"text": "   "})
chk("空需求被拦截", r.json().get("ok") is False and bool(r.json().get("message")), str(r.json()))
r = client.post("/api/ai/build/text", json={"text": ""})
chk("空文本被拦截", r.json().get("ok") is False, str(r.json()))

print("\n== 3. 完整生成流程（桩模型）==")
ai_builder_module._call_ollama = fast_stub()
r = client.post("/api/ai/build/dialogue", json={"text": "请生成截止日期相关规则"})
j = r.json()
chk("启动成功并返回 bid", r.status_code == 200 and j.get("ok") and bool(j.get("bid")), str(j))
bid = j.get("bid")
state = {}
for _ in range(40):
    time.sleep(0.15)
    lg = client.get(f"/api/ai/build/logs?bid={bid}").json()
    state = lg.get("state") or {}
    if state.get("done"):
        break
chk("任务完成", bool(state.get("done")), str(state.get("message")))
chk("生成成功", bool(state.get("ok")), str(state.get("message")))
result = state.get("result") or {}
rules = result.get("rules") or []
wbs = result.get("wordbanks") or []
chk("产出规则 2 条", len(rules) == 2,
    f"{[x.get('name') for x in rules]} | 过滤明细={state.get('filter')}")
chk("产出词条 2 条", sum(len(w.get("entries") or []) for w in wbs) == 2)
chk("规则带 source 标记", all(x.get("source") == "ai_dialogue" for x in rules), str(rules[:1]))
chk("规则带 source_label 标记", all(x.get("source_label") == "AI对话创建" for x in rules))
chk("词条带 source 标记",
    all(x.get("source") == "ai_dialogue" for w in wbs for x in (w.get("entries") or [])))
chk("返回入库过滤统计", isinstance(state.get("filter"), dict), str(state.get("filter")))

print("\n== 4. 中止生成任务 ==")
ai_builder_module._call_ollama = slow_stub(chunks=60, delay=0.08)
r = client.post("/api/ai/build/dialogue", json={"text": "慢速任务"})
bid2 = r.json().get("bid")
time.sleep(0.5)
c = client.post("/api/ai/build/cancel", json={"bid": bid2})
chk("中止接口受理", c.json().get("ok") is True, str(c.json()))
st = {}
for _ in range(30):
    time.sleep(0.2)
    st = client.get(f"/api/ai/build/logs?bid={bid2}").json().get("state") or {}
    if st.get("done"):
        break
chk("任务已停止", st.get("done") is True and st.get("running") is False, str(st.get("message")))
chk("标记为已中止", st.get("cancelled") is True, str(st))

print("\n== 5. 历史任务回收（不再无限占用内存）==")
buildrt._BUILD_LOGS.clear()
buildrt._BUILD_STATE.clear()
with _TASK_LOCK:
    for i in range(12):
        k = f"old{i}"
        buildrt._BUILD_STATE[k] = {"running": False, "done": True, "ok": True}
        buildrt._BUILD_LOGS[k] = [{"ts": "00:00:00", "kind": "stage", "text": "x"}]
buildrt._trim_build_tasks(keep="keep_me")
with _TASK_LOCK:
    left = len(buildrt._BUILD_STATE)
chk("任务数收敛到上限", left <= buildrt._MAX_BUILD_TASKS, f"实际 {left}")
chk("日志随之回收", len(buildrt._BUILD_LOGS) == left, f"state={left} logs={len(buildrt._BUILD_LOGS)}")

print("\n== 6. bid 唯一性 ==")
ids = {buildrt._new_build_bid() for _ in range(200)}
chk("200 次生成无重复 bid", len(ids) == 200, f"实际 {len(ids)}")

print(f"\n总计：OK={OK}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
