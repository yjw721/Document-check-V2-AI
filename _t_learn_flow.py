# -*- coding: utf-8 -*-
"""集成验证：本地 AI 自学习过程的可视化与进度显示。

用桩模型驱动真实学习链路（数据落在临时目录，不污染本机 samples/规则库），
校验：阶段日志格式、进度推进、实时指标、中止、任务内存回收。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


# ---------------------------------------------------------------- 隔离运行环境
_TMP = tempfile.mkdtemp(prefix="aimem_t_")
import checkers.ai_memory as am  # noqa: E402

am.SAMPLES_PATH = os.path.join(_TMP, "samples.json")
am.LEARNED_PATH = os.path.join(_TMP, "learned.json")
am.SRC_DIR = os.path.join(_TMP, "src")
am.SRC_META_PATH = os.path.join(_TMP, "src.json")

with open(am.SAMPLES_PATH, "w", encoding="utf-8") as fp:
    json.dump(
        {
            "settings": {"enabled": True},
            "samples": [
                {
                    "id": "s_t1",
                    "enabled": True,
                    "status": "pending",
                    "source_doc": "原始.docx",
                    "revised_doc": "修订.docx",
                    "created_at": "2026-10-21 12:00:00",
                    "diffs": [
                        {"old": "截止日期为界面提交当日", "new": "截至日期为界面提交当日"},
                        {"old": "帐面价值", "new": "账面价值"},
                        {"old": "该企业立马搞定了评估", "new": "该企业已完成评估"},
                    ],
                }
            ],
        },
        fp,
        ensure_ascii=False,
    )

# 隔离规则库 / 词库写入（避免污染本机数据）
_RULES = {"groups": []}
_WB = {"groups": []}
am.load_custom_rules = lambda: _RULES
am.save_custom_rules = lambda d: True
am.load_wordbanks = lambda: _WB
am.save_wordbanks = lambda d: True
am.load_learned = lambda: {"learned": []}


def _fake_save_learned(data):
    with open(am.LEARNED_PATH, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)
    return True


am.save_learned = _fake_save_learned

_TAG = str(int(time.time()))[-6:]
MODEL_OUT = {
    "corrections": [
        {"error": f"截止日期{_TAG}", "corrected": f"截至日期{_TAG}"},
        {"error": f"帐面价值{_TAG}", "corrected": f"账面价值{_TAG}"},
    ],
    "rules": [
        {"name": "口语化表达", "match_mode": "keyword", "pattern": f"立马搞定{_TAG}",
         "severity": "medium", "suggestion": f"已完成{_TAG}"},
    ],
    "standard_expression": "统一采用「截至日期」「账面价值」等规范表述",
}


def make_stub(chunks=30, delay=0.06, thinking=True):
    payload = json.dumps(MODEL_OUT, ensure_ascii=False)

    def _stub(cfg, messages, think=None, options=None, on_token=None):
        if on_token and thinking:
            for i in range(0, 200, 40):
                on_token(f"思考步骤 {i}：分析错误类型…\n", "thinking")
        step = max(1, len(payload) // chunks)
        for i in range(0, len(payload), step):
            time.sleep(delay)
            if on_token:
                on_token(payload[i:i + step], "content")
        return payload
    return _stub


import app as appmod  # noqa: E402
from checkers import ai_checker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(appmod.app)

print("\n== 1. 学习全链路：日志格式 + 实时进度 ==")
ai_checker._call_ollama = make_stub()
r = client.post("/api/ai_memory/samples/s_t1/learn")
j = r.json()
chk("启动学习成功", j.get("ok") is True, str(j))

seen_progress, seen_stages, seen_chars = [], [], []
st = {}
for _ in range(60):
    time.sleep(0.2)
    lg = client.get("/api/ai_memory/samples/s_t1/learn/logs").json()
    st = lg.get("state") or {}
    seen_progress.append(st.get("progress", 0))
    if st.get("stage"):
        seen_stages.append(st["stage"])
    seen_chars.append(st.get("chars", 0))
    if st.get("done"):
        break

logs = lg.get("logs") or []
stage_logs = [l for l in logs if l.get("kind") == "stage"]
chk("任务完成", bool(st.get("done")), str(st.get("message")))
chk("学习成功", bool(st.get("ok")), str(st.get("message")))
chk("存在 kind=stage 的阶段日志（前端据此渲染阶段流）", len(stage_logs) >= 4,
    f"stage 日志 {len(stage_logs)} 条")
chk("阶段日志文本带 [stage] 前缀",
    all(l["text"].startswith("[") and "]" in l["text"][:12] for l in stage_logs) if stage_logs else False,
    str([l["text"][:16] for l in stage_logs][:4]))
import re  # noqa: E402
keys = [re.match(r"^\[(\w+)\]", l["text"]).group(1) for l in stage_logs
        if re.match(r"^\[(\w+)\]", l["text"])]
chk("阶段覆盖 start→done", keys[0] == "start" and "done" in keys, str(keys))
chk("存在 thinking 流日志", any(l["kind"] == "thinking" for l in logs))
chk("存在 token 流日志", any(l["kind"] == "token" for l in logs))

uniq = sorted(set(seen_progress))
chk("进度随阶段推进（多于一个取值）", len(uniq) > 1, f"采样={uniq[:8]}")
chk("进度单调不减", all(seen_progress[i] <= seen_progress[i + 1] for i in range(len(seen_progress) - 1)),
    str(seen_progress[:10]))
chk("结束时进度为 100", st.get("progress") == 100, str(st.get("progress")))
chk("进度始终在 0-100 区间", all(0 <= p <= 100 for p in seen_progress), str(sorted(set(seen_progress))))
chk("输出字数随流式增长", max(seen_chars) > 0, f"chars={max(seen_chars)}")
chk("返回累计耗时 elapsed_sec", isinstance(st.get("elapsed_sec"), (int, float)) and st["elapsed_sec"] > 0,
    str(st.get("elapsed_sec")))
chk("阶段流经历了多个阶段", len(set(seen_stages)) >= 2, str(sorted(set(seen_stages))))

stx = st.get("stats") or {}
chk("产出词条 2 条", stx.get("entries") == 2, str(stx))
chk("产出规则 1 条", stx.get("rules") == 1, str(stx))
chk("返回 AI 提炼的标准表述", bool(stx.get("standard_expression")), str(stx))
sample = am.load_samples()["samples"][0]
chk("样本状态更新为 done", sample.get("status") == "done", str(sample.get("status")))

print("\n== 2. 中止学习 ==")
with open(am.SAMPLES_PATH, "w", encoding="utf-8") as fp:
    json.dump({"settings": {"enabled": True}, "samples": [dict(sample, id="s_t2", status="pending")]},
              fp, ensure_ascii=False)
ai_checker._call_ollama = make_stub(chunks=200, delay=0.08, thinking=False)
r = client.post("/api/ai_memory/samples/s_t2/learn")
chk("慢速任务启动", r.json().get("ok") is True, str(r.json()))
time.sleep(0.8)
c = client.post("/api/ai_memory/samples/s_t2/learn/cancel")
chk("中止接口受理", c.json().get("ok") is True, str(c.json()))
st2 = {}
for _ in range(40):
    time.sleep(0.2)
    st2 = client.get("/api/ai_memory/samples/s_t2/learn/logs").json().get("state") or {}
    if st2.get("done"):
        break
chk("任务已停止", st2.get("done") is True and st2.get("running") is False, str(st2.get("message")))
chk("标记为已中止", st2.get("cancelled") is True, str(st2))
s2 = am.load_samples()["samples"][0]
chk("样本回到待学习（不计为失败）", s2.get("status") == "pending", str(s2.get("status")))

print("\n== 3. 学习任务内存回收 ==")
with appmod._TASK_LOCK:
    appmod._MLEARN_LOGS.clear()
    appmod._MLEARN_STATE.clear()
    for i in range(20):
        k = f"old{i}"
        appmod._MLEARN_STATE[k] = {"running": False, "done": True, "ok": True}
        appmod._MLEARN_LOGS[k] = [{"ts": "00:00:00", "kind": "stage", "text": "x"}]
appmod._mtrim_tasks(keep="keep_me")
with appmod._TASK_LOCK:
    left = len(appmod._MLEARN_STATE)
chk("任务数收敛到上限", left <= appmod._MAX_MLEARN_TASKS, f"实际 {left}")
chk("日志随之回收", len(appmod._MLEARN_LOGS) == left,
    f"state={left} logs={len(appmod._MLEARN_LOGS)}")

print("\n== 4. 进度估算单调性（纯函数）==")
pcts = [appmod._mlearn_pct(s) for s in ("start", "prompt", "infer", "parse", "validate", "done")]
chk("阶段基准百分比单调递增", all(pcts[i] <= pcts[i + 1] for i in range(len(pcts) - 1)), str(pcts))
chk("推理期随输出增长", appmod._mlearn_pct("infer", 2000) > appmod._mlearn_pct("infer", 0),
    f"{appmod._mlearn_pct('infer', 0)} -> {appmod._mlearn_pct('infer', 2000)}")
chk("推理期进度封顶", appmod._mlearn_pct("infer", 10 ** 9) <= appmod._MLEARN_INFER_CAP,
    str(appmod._mlearn_pct("infer", 10 ** 9)))

print(f"\n总计：OK={OK}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
