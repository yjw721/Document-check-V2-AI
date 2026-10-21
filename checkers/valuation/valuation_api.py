# -*- coding: utf-8 -*-
"""
资产评估报告智能核查 —— 后端 API（FastAPI Router，离线保密版）
=========================================================================
与《AI核查报告需求点提取》四阶段闭环对应的 HTTP 接口：

  GET  /api/valuation/meta          核查清单元信息（5 槽位 / 4 阶段 / 7 数据块 / 9 节点 96 事项）
  POST /api/valuation/upload         上传 5 类附件（按 slot 映射：report/statement/income/cost/market）
  POST /api/valuation/run            启动四阶段流水线（异步线程，返回 task_id）
  GET  /api/valuation/task/{tid}     轮询任务：进度 / 阶段 / 日志 / 结果 / 取消态
  POST /api/valuation/task/{tid}/cancel  取消任务
  GET  /api/valuation/result/{tid}  取最终核查结果 JSON
  GET  /api/valuation/report/{tid}   导出 Word 终稿核查报告（.docx）
  POST /api/valuation/reset          清空本机会话缓存（上传文件 + 任务态）

安全：仅监听 127.0.0.1；上传文件仅在本地缓存目录落盘、绝不外传；零网络连接。
前端页面通过此 Router 驱动「四阶段可视化核查」。
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .models import BLOCKS, STAGES
from .pipeline import Pipeline, load_checklist

router = APIRouter(prefix="/api/valuation", tags=["valuation"])

# ---------------------------------------------------------------------------
# 路径与内存态
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE = os.path.join(os.path.dirname(_ROOT), "tempfile") if False else os.path.join(
    os.environ.get("TEMP", "/tmp"), "doc_checker_valuation")
# 使用项目内缓存目录，便于管理与清理
_SESS_DIR = os.path.join(_ROOT, "cache", "valuation")
os.makedirs(_SESS_DIR, exist_ok=True)

ALLOWED: Dict[str, Dict[str, Any]] = {
    "report": {"ext": (".docx",), "kind": "doc", "name": "评估报告（全文）"},
    "statement": {"ext": (".docx",), "kind": "doc", "name": "评估说明"},
    "income": {"ext": (".xlsx", ".xlsm"), "kind": "excel", "name": "收益法测算表"},
    "cost": {"ext": (".xlsx", ".xlsm"), "kind": "excel", "name": "成本法测算表"},
    "market": {"ext": (".xlsx", ".xlsm"), "kind": "excel", "name": "市场法测算表"},
}

# 会话：session_id -> {slots: {slot: {name, path}}, dir}
# 说明：必须用可重入锁 RLock —— _progress 钩子在持有任务锁的上下文里还会调用 _log，
# _cancel_task / _run_pipeline 亦同；若用普通 Lock 会自我死锁（流水线线程卡死，轮询请求随之超时）。
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_SESS_LOCK = threading.RLock()

# 任务：task_id -> {status, progress, stage, stage_text, logs, cancel(Event),
#                   error, result, session_id}
_TASKS: Dict[str, Dict[str, Any]] = {}
_TASK_LOCK = threading.RLock()
_MAX_TASKS = 20


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def _task_snapshot(tid: str) -> Optional[Dict[str, Any]]:
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return None
        return {
            "task_id": tid,
            "status": t["status"],
            "progress": round(float(t["progress"]), 1),
            "stage": t["stage"],
            "stage_text": t["stage_text"],
            "logs": list(t["logs"]),
            "error": t["error"],
            "result": t.get("result"),
            "done": t["status"] in ("done", "canceled", "failed", "blocked"),
        }


def _make_task(session_id: str) -> str:
    tid = uuid.uuid4().hex[:12]
    with _TASK_LOCK:
        _TASKS[tid] = {
            "status": "running", "progress": 0.0, "stage": "S1",
            "stage_text": "文件上传与预处理", "logs": [],
            "cancel": threading.Event(), "error": "", "result": None,
            "session_id": session_id,
        }
        if len(_TASKS) > _MAX_TASKS:
            for old in list(_TASKS):
                if _TASKS[old]["status"] in ("done", "canceled", "failed", "blocked"):
                    del _TASKS[old]
                if len(_TASKS) <= _MAX_TASKS:
                    break
    return tid


def _log(tid: str, text: str) -> None:
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return
        line = f"{time.strftime('%H:%M:%S')} {text}"
        logs = t["logs"]
        if not logs or logs[-1] != line:
            logs.append(line)
            if len(logs) > 600:
                del logs[:-600]


def _progress(tid: str) -> Callable[[float, str, str], bool]:
    def hook(percent: float, stage: str, message: str) -> bool:
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if not t:
                return False
            if t["cancel"].is_set():
                return False
            t["progress"] = float(percent)
            if stage:
                t["stage"] = stage
                t["stage_text"] = {
                    "S1": "文件上传与预处理", "S2": "文件智能切分",
                    "S3": "9 大核查节点", "S4": "汇总与复核",
                }.get(stage, stage)
            if message:
                _log(tid, message)
        return True
    return hook


def _cancel_task(tid: str) -> bool:
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return False
        if t["status"] == "running":
            t["cancel"].set()
            t["status"] = "canceled"
            _log(tid, "任务已取消")
    return True


def _ai_cfg_from_settings() -> Dict[str, Any]:
    try:
        from config.settings_manager import load_settings
        return (load_settings().get("ai") or {})
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/meta")
def api_valuation_meta():
    """返回核查清单元信息，供前端渲染四阶段界面与槽位说明。"""
    checklist = load_checklist()
    slots = []
    for key, meta in ALLOWED.items():
        slots.append({
            "key": key, "name": meta["name"], "kind": meta["kind"],
            "ext": list(meta["ext"]), "need": key in ("report", "statement"),
        })
    return {
        "slots": slots,
        # checklist.json 仅含 nodes；阶段与数据块取自 models 常量，保证前端可直接渲染四阶段拓扑
        "stages": checklist.get("stages") or STAGES,
        "blocks": checklist.get("blocks") or [
            {"id": b["id"], "name": b["name"], "kind": b["kind"],
             "from": b["from"], "serve": b["serve"]}
            for b in BLOCKS.values()
        ],
        "nodes": [
            {
                "id": n.get("id"), "no": n.get("no"), "name": n.get("name"),
                "type": n.get("type", "single"),
                "inputs": n.get("inputs", []),
                "item_total": len(n.get("items", [])),
                "items": [
                    {"id": it.get("id"), "title": it.get("title"),
                     "engine": it.get("engine"), "severity": it.get("severity")}
                    for it in n.get("items", [])
                ],
            }
            for n in checklist.get("nodes", [])
        ],
        "node_count": len(checklist.get("nodes", [])),
        "item_count": sum(len(n.get("items", [])) for n in checklist.get("nodes", [])),
    }


@router.post("/upload")
async def api_valuation_upload(
        files: List[UploadFile] = File(...),
        slots: List[str] = Form(...)):
    """上传附件并按 slot 落盘（report/statement/income/cost/market）。"""
    if len(files) != len(slots):
        raise HTTPException(400, "files 与 slots 数量必须一致")
    session_id = uuid.uuid4().hex[:12]
    sdir = os.path.join(_SESS_DIR, session_id)
    os.makedirs(sdir, exist_ok=True)
    saved: List[Dict[str, Any]] = []
    used: Dict[str, bool] = {}
    try:
        for uf, slot in zip(files, slots):
            meta = ALLOWED.get(slot)
            if not meta:
                raise HTTPException(400, f"未知附件槽位：{slot}")
            if used.get(slot):
                raise HTTPException(400, f"槽位 {slot} 重复上传")
            used[slot] = True
            ext = os.path.splitext(uf.filename or "")[1].lower()
            if ext not in meta["ext"]:
                raise HTTPException(400,
                    f"槽位 {slot}（{meta['name']}）要求后缀 {meta['ext']}，"
                    f"收到：{ext or '无'}")
            safe = f"{slot}{ext}"
            dst = os.path.join(sdir, safe)
            content = await uf.read()
            with open(dst, "wb") as fp:
                fp.write(content)
            saved.append({"slot": slot, "name": os.path.basename(uf.filename or safe),
                          "size": len(content)})
        with _SESS_LOCK:
            _SESSIONS[session_id] = {
                "dir": sdir,
                "slots": {s["slot"]: {"name": s["name"], "path": os.path.join(sdir, f"{s['slot']}{os.path.splitext(s['name'])[1].lower()}")}
                          for s in saved},
            }
        return {"ok": True, "session_id": session_id, "files": saved}
    except HTTPException:
        shutil.rmtree(sdir, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(sdir, ignore_errors=True)
        raise HTTPException(500, f"上传失败：{exc}")


@router.post("/run")
def api_valuation_run(payload: Dict[str, Any]):
    """启动四阶段核查流水线（异步线程执行，返回 task_id 供轮询）。"""
    session_id = payload.get("session_id") or ""
    with _SESS_LOCK:
        sess = _SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(400, "会话不存在或已过期，请重新上传附件")
    slot_files = sess["slots"]
    if not slot_files:
        raise HTTPException(400, "会话中没有任何附件")

    use_ai = bool(payload.get("use_ai", False))
    nodes = payload.get("nodes") or None
    ai_cfg = _ai_cfg_from_settings()
    if use_ai and not ai_cfg.get("enabled"):
        use_ai = False  # 未配置本地 AI，则降级为纯规则引擎

    workdir = os.path.join(sess["dir"], "run_" + uuid.uuid4().hex[:6])
    os.makedirs(workdir, exist_ok=True)

    slots_payload = {
        slot: {"name": info["name"], "path": info["path"]}
        for slot, info in slot_files.items()
    }
    pipe = Pipeline(
        {"slots": slots_payload, "workdir": workdir, "use_ai": use_ai,
         "nodes": nodes if nodes else None},
        ai_cfg if use_ai else {"enabled": False},
        log=lambda s: None,
        progress=_progress_dummy(),
        cancel=lambda: False,
    )
    tid = _make_task(session_id)
    threading.Thread(target=_run_pipeline, args=(tid, pipe), daemon=True).start()
    return {"ok": True, "task_id": tid, "use_ai": use_ai}


def _progress_dummy():
    # 占位：真正的进度由运行线程内重新绑定（见 _run_pipeline）
    return lambda p, s, m: True


def _run_pipeline(tid: str, pipe: Pipeline) -> None:
    hook = _progress(tid)
    # 重新绑定 progress 钩子（构造时占位，运行期替换）
    pipe.progress_cb = hook
    cancel_event = _TASKS.get(tid, {}).get("cancel")

    def cancel_cb() -> bool:
        return bool(cancel_event and cancel_event.is_set())

    pipe.cancel_cb = cancel_cb
    try:
        result = pipe.run()
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if t:
                t["result"] = result
                t["status"] = result.get("status", "done")
                t["progress"] = 100.0
                t["stage"] = "S4"
                t["stage_text"] = "汇总与复核"
                if result.get("status") == "blocked":
                    _log(tid, "预处理硬阻断，核查终止：" + result.get("message", ""))
                else:
                    _log(tid, "核查完成，发现问题 "
                              + str(result.get("summary", {}).get("total", 0)) + " 条")
    except Exception as exc:  # noqa: BLE001
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if t and t["status"] == "running":
                t["status"] = "failed"
                t["error"] = f"{type(exc).__name__}：{str(exc)[:200]}"
                _log(tid, t["error"])


@router.get("/task/{tid}")
def api_valuation_task(tid: str):
    snap = _task_snapshot(tid)
    if snap is None:
        raise HTTPException(404, "任务不存在或已过期")
    return snap


@router.post("/task/{tid}/cancel")
def api_valuation_task_cancel(tid: str):
    if not _cancel_task(tid):
        raise HTTPException(404, "任务不存在或已过期")
    return {"ok": True}


@router.get("/result/{tid}")
def api_valuation_result(tid: str):
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            raise HTTPException(404, "任务不存在或已过期")
        if t["status"] not in ("done", "blocked"):
            return {"ready": False, "status": t["status"],
                    "progress": round(float(t["progress"]), 1)}
        result = t.get("result")
    if result is None:
        raise HTTPException(404, "结果不存在")
    return {"ready": True, "status": t["status"], "result": result}


@router.get("/report/{tid}")
def api_valuation_report(tid: str):
    """导出 Word 终稿核查报告。"""
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            raise HTTPException(404, "任务不存在或已过期")
        result = t.get("result")
        session_id = t.get("session_id")
    if result is None:
        raise HTTPException(400, "任务尚未完成，无法生成报告")
    checklist = load_checklist()
    sess = _SESSIONS.get(session_id or "")
    operator = (sess or {}).get("operator", "") if False else ""
    org = ""
    from .report_builder import build_valuation_report, default_report_name
    out_dir = os.path.join(_SESS_DIR, session_id or tid, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, default_report_name())
    build_valuation_report(result, out_path, checklist, operator=operator, org=org,
                           include_cover=True)
    return FileResponse(
        out_path, filename=os.path.basename(out_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/reset")
def api_valuation_reset(payload: Dict[str, Any] = {}):
    """清空本机会话缓存（上传文件与任务态）。"""
    session_id = (payload or {}).get("session_id") or ""
    with _SESS_LOCK:
        if session_id:
            sess = _SESSIONS.pop(session_id, None)
            if sess:
                shutil.rmtree(sess.get("dir", ""), ignore_errors=True)
        else:
            for sid, sess in list(_SESSIONS.items()):
                shutil.rmtree(sess.get("dir", ""), ignore_errors=True)
            _SESSIONS.clear()
    with _TASK_LOCK:
        _TASKS.clear()
    # 仅清理过期会话目录，保留正在执行的任务目录
    try:
        for name in os.listdir(_SESS_DIR):
            p = os.path.join(_SESS_DIR, name)
            if os.path.isdir(p) and not os.listdir(p):
                os.rmdir(p)
    except OSError:
        pass
    return {"ok": True}


@router.get("/sessions")
def api_valuation_sessions():
    """当前活跃会话（调试 / 状态展示用）。"""
    with _SESS_LOCK:
        return {"sessions": [
            {"session_id": sid, "slots": list(s["slots"].keys())}
            for sid, s in _SESSIONS.items()
        ]}
