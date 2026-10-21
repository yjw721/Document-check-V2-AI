# -*- coding: utf-8 -*-
"""
四阶段核查流水线编排
=========================================================================
严格对应需求文档的闭环：

    阶段一 文件上传与预处理  →  #REF! 硬拦截
    阶段二 文件智能切分      →  7 个数据块（通用表强制附加）
    阶段三 9 大核查节点      →  96 项核查事项（规则 + AI）
    阶段四 汇总与复核        →  自动去重 → AI 综合复核 → 最终核查报告

编排特性：
    * 进度真实：按「阶段 + 节点 + 核查事项」三级推进，无假进度；
    * 可取消：每个检查点回调 cancel()，返回 True 立即中止；
    * 容错：单规则 / 单节点 / 单段 AI 失败均降级为提示，不中断整体；
    * 状态自持：返回的结构体可直接 JSON 序列化供前端渲染。
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

from . import preprocess, splitter
from .ai_nodes import run_ai_item, build_block_text
from .dedup import dedup_findings
from .models import Block, NodeRun, Finding, now_text
from .review import ai_review
from .rules import RuleContext, run_rule

# 阶段进度区间
STAGE_RANGE = {
    "S1": (0.0, 15.0),
    "S2": (15.0, 30.0),
    "S3": (30.0, 85.0),
    "S4": (85.0, 100.0),
}

STAGE_NAME = {
    "S1": "文件上传与预处理",
    "S2": "文件智能切分",
    "S3": "9 大核查节点",
    "S4": "汇总与复核",
}


def load_checklist(path: str = "") -> Dict[str, Any]:
    """加载 9 节点 / 96 项核查事项清单。"""
    if not path:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config", "valuation", "checklist.json")
    import json
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


class Pipeline:
    """四阶段流水线执行器。"""

    def __init__(self, payload: Dict[str, Any], ai_cfg: Dict[str, Any],
                 log: Optional[Callable[[str], None]] = None,
                 progress: Optional[Callable[[float, str, str], bool]] = None,
                 cancel: Optional[Callable[[], bool]] = None,
                 on_token: Optional[Callable[[str, str], None]] = None):
        self.payload = payload or {}
        self.ai_cfg = ai_cfg or {}
        self.log = log or (lambda s: None)
        self.progress_cb = progress or (lambda p, s, m: True)
        self.cancel_cb = cancel or (lambda: False)
        self.on_token = on_token
        self.workdir = payload.get("workdir") or ""
        self.use_ai = bool(payload.get("use_ai")) and bool(self.ai_cfg.get("enabled"))
        self.checklist = load_checklist()
        self.blocks: Dict[str, Block] = {}
        self.block_paths: Dict[str, str] = {}
        self.nodes: List[NodeRun] = []
        self.findings: List[Finding] = []
        self.state: Dict[str, Any] = {
            "status": "running", "stage": "S1", "stage_name": STAGE_NAME["S1"],
            "percent": 0.0, "message": "准备开始", "logs": [], "started": now_text(),
        }

    # ---------------- 基础工具 ----------------
    def _log(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self.state["logs"].append(line)
        if len(self.state["logs"]) > 800:
            self.state["logs"] = self.state["logs"][-800:]
        self.log(line)

    def _progress(self, stage: str, percent: float, message: str = "") -> bool:
        lo, hi = STAGE_RANGE.get(stage, (0.0, 100.0))
        pct = max(lo, min(hi, percent))
        self.state.update({
            "stage": stage, "stage_name": STAGE_NAME.get(stage, ""),
            "percent": round(pct, 1), "message": message or self.state.get("message", ""),
        })
        if message:
            self.state["message"] = message
        return self.progress_cb(pct, STAGE_NAME.get(stage, ""), message)

    def _canceled(self) -> bool:
        return bool(self.cancel_cb())

    # ---------------- 阶段一 ----------------
    def stage1_preprocess(self) -> Dict[str, Any]:
        self._progress("S1", 1, "阶段一：上传附件与预处理")
        slots = self.payload.get("slots") or {}
        clean_dir = os.path.join(self.workdir, "cleaned")
        os.makedirs(clean_dir, exist_ok=True)

        # 落盘上传文件（slots 提供 {slot: {"name","path"}}，path 为上传临时文件）
        slot_files: Dict[str, Dict[str, str]] = {}
        for slot, info in slots.items():
            src = (info or {}).get("path") or ""
            if src and os.path.exists(src):
                slot_files[slot] = {"name": info.get("name") or os.path.basename(src),
                                    "path": src}
        self._log(f"阶段一：接收到 {len(slot_files)} 份附件")
        res = preprocess.run_preprocess(slot_files, clean_dir)
        self._progress("S1", 14, res.get("message", ""))
        for item in res.get("items", []):
            self._log(f"预处理[{item.get('slot')}] {item.get('file')}：{item.get('message', '')}")

        # 记录清理后文件路径（供后续阶段使用）
        for item in res.get("items", []):
            if item.get("out_path"):
                self.payload.setdefault("cleaned", {})[item["slot"]] = {
                    "name": item.get("file", ""), "path": item["out_path"]}
        res["blocked_message"] = res.get("message", "")
        return res

    # ---------------- 阶段二 ----------------
    def stage2_split(self) -> Dict[str, Any]:
        self._progress("S2", 16, "阶段二：文件智能切分")
        cleaned = self.payload.get("cleaned") or {}
        slots = self.payload.get("slots") or {}
        manual_all = self.payload.get("manual_sheets") or {}

        split_res: Dict[str, Any] = {}
        for slot in ("income", "cost", "market"):
            info = cleaned.get(slot)
            if not info:
                split_res[slot] = {"slot": slot, "sheets": [], "general": [],
                                   "by_method": {slot: []}, "error": "未上传该测算表"}
                continue
            if slot not in slots:
                split_res[slot] = {"slot": slot, "sheets": [], "general": [],
                                   "by_method": {slot: []}, "error": "未上传该测算表"}
                continue
            self._log(f"阶段二：切分 {info['name']}（{slot}）")
            split_res[slot] = splitter.split_workbook(
                slot, info["path"], info["name"], self.ai_cfg,
                manual_all.get(slot), self._log)
            if split_res[slot].get("engine") == "ai":
                self._log(f"  AI 判定完成：{len(split_res[slot].get('sheets', []))} 张表")

        # 评估说明与报告
        st_info = cleaned.get("statement")
        statement_split = {"texts": {"income": "", "cost": "", "market": "", "common": ""},
                           "sections": []}
        if st_info:
            self._log(f"阶段二：切分评估说明 {st_info['name']}")
            statement_split = splitter.split_statement(st_info["path"], st_info["name"])
            self._log("  说明分段字数：" + "、".join(
                f"{k} {v}" for k, v in (statement_split.get("chars") or {}).items()))
        rp_info = cleaned.get("report")
        report_split = {"text": "", "chars": 0, "tables": 0}
        if rp_info:
            self._log(f"阶段二：读取评估报告 {rp_info['name']}（不切分）")
            report_split = splitter.split_report(rp_info["path"], rp_info["name"])

        blocks = splitter.build_blocks(cleaned, split_res, statement_split, report_split)
        self.blocks = {b.block_id: b for b in blocks}
        for b in blocks:
            src = cleaned.get(b.source_slot) or {}
            self.block_paths[b.block_id] = src.get("path", "")

        payload_blocks = []
        for b in blocks:
            d = b.to_dict()
            d["id"] = b.block_id
            d["ready"] = bool(b.kind == "doc" and (b.text or "").strip()) or bool(
                b.kind == "excel" and b.sheets)
            d["chars"] = len(b.text or "") if b.kind == "doc" else 0
            payload_blocks.append(d)
        self._progress("S2", 29, "阶段二完成：已生成 7 个数据块（三法底稿块均含通用表）")
        return {
            "blocks": payload_blocks,
            "workbook_split": {k: v for k, v in split_res.items()},
            "statement_split": {
                "sections": statement_split.get("sections", []),
                "chars": statement_split.get("chars", {}),
                "error": statement_split.get("error", ""),
            },
            "report_split": {"chars": report_split.get("chars", 0),
                             "tables": report_split.get("tables", 0),
                             "error": report_split.get("error", "")},
        }

    # ---------------- 阶段三 ----------------
    def stage3_nodes(self) -> None:
        nodes_cfg = self.checklist.get("nodes", [])
        selected = set(self.payload.get("nodes") or [n["id"] for n in nodes_cfg])
        todo = [n for n in nodes_cfg if n["id"] in selected]
        total_items = sum(len(n.get("items", [])) for n in todo) or 1
        done_items = 0
        lo, hi = STAGE_RANGE["S3"]

        for idx, node in enumerate(todo, 1):
            if self._canceled():
                break
            run = NodeRun(node_id=node["id"], node_no=node.get("no", ""),
                          node_name=node.get("name", ""),
                          node_type=node.get("type", "single"),
                          inputs=list(node.get("inputs", [])),
                          item_total=len(node.get("items", [])))
            t0 = time.time()

            # 输入块可用性检查
            missing = [b for b in node.get("inputs", []) if not self._block_ready(b)]
            if missing:
                run.status = "skipped"
                run.message = "缺少输入数据块：" + "、".join(missing)
                self._log(f"节点 {node.get('no')}{node.get('name')} 跳过：{run.message}")
                self.nodes.append(run)
                done_items += run.item_total
                continue

            run.status = "running"
            self._log(f"节点 {node.get('no')}{node.get('name')} 开始（{run.item_total} 项）")
            shared: set = set()          # 节点内共享：避免同一段文本被多项核查重复扫描

            for item in node.get("items", []):
                if self._canceled():
                    break
                ctx = RuleContext(node, item, self.blocks, self.block_paths,
                                  self._log, shared=shared)
                got: List[Finding] = []
                engine = item.get("engine") or "ai"
                rule_key = item.get("rule_key") or ""

                # 1) 本地规则引擎
                if engine in ("rule", "hybrid") and rule_key:
                    try:
                        got += run_rule(rule_key, ctx)
                    except Exception as exc:  # noqa: BLE001
                        self._log(f"  [{item.get('id')}] 规则执行异常：{exc}")

                # 2) AI 语义核查
                if engine in ("ai", "hybrid") and self.use_ai:
                    try:
                        got += run_ai_item(ctx, self.ai_cfg, self._log, self.on_token)
                    except Exception as exc:  # noqa: BLE001
                        self._log(f"  [{item.get('id')}] AI 核查异常：{exc}")

                for f in got:
                    f.engine = item.get("engine", "")
                run.findings += got
                run.rule_count += sum(1 for f in got if f.origin == "rule")
                run.ai_count += sum(1 for f in got if f.origin == "ai")
                run.item_done += 1
                done_items += 1
                self.findings += got

                pct = lo + (hi - lo) * (done_items / total_items)
                self._progress("S3", pct,
                               f"阶段三：{node.get('no')}{node.get('name')} "
                               f"({idx}/{len(todo)}) · {item.get('title', '')}")

            run.status = "done" if not self._canceled() else "aborted"
            run.seconds = round(time.time() - t0, 2)
            if not run.message:
                run.message = (f"完成 {run.item_total} 项核查，命中 {len(run.findings)} 条"
                               f"（规则 {run.rule_count} / AI {run.ai_count}）")
            self._log(f"节点 {node.get('no')}{node.get('name')} {run.message}")
            self.nodes.append(run)

    def _block_ready(self, block_id: str) -> bool:
        b = self.blocks.get(block_id)
        if b is None:
            return False
        if b.kind == "doc":
            return bool((b.text or "").strip())
        return bool(b.sheets) and bool(self.block_paths.get(block_id))

    # ---------------- 阶段四 ----------------
    def stage4_summary(self) -> Dict[str, Any]:
        self._progress("S4", 86, "阶段四：结果汇总（自动去重）")
        kept, dedup_stats = dedup_findings(self.findings)
        self._log(f"阶段四：去重前 {dedup_stats['total']} 条 → 去重后 {dedup_stats['kept']} 条"
                  f"（合并重复 {dedup_stats['merged']} 条）")

        review_res: Dict[str, Any] = {"ok": False, "message": "", "conclusion": "",
                                      "removed": {}, "added": []}
        if self.use_ai:
            self._progress("S4", 92, "阶段四：AI 综合复核（排查误报 / 补充漏报）")
            material = self._material_text()
            review_res = ai_review(kept, material, self.ai_cfg, self._log, self.on_token)

        # 应用复核结果：剔除误报 + 追加漏报
        final: List[Finding] = []
        removed_ids = review_res.get("removed") or {}
        for f in kept:
            if f.id in removed_ids:
                f.state = "ignored"
                f.suggestion = (f.suggestion + f" 【AI 复核判定为误报：{removed_ids[f.id]}】").strip()
            final.append(f)
        for f in review_res.get("added") or []:
            final.append(f)

        # 最终统计（误报不计入）
        active = [f for f in final if f.state != "ignored"]
        summary = {
            "node_total": len(self.checklist.get("nodes", [])),
            "node_done": sum(1 for n in self.nodes if n.status == "done"),
            "node_skipped": sum(1 for n in self.nodes if n.status == "skipped"),
            "item_total": sum(len(n.get("items", [])) for n in self.checklist.get("nodes", [])),
            "item_done": sum(n.item_done for n in self.nodes),
            "findings_raw": len(self.findings),
            "dedup_merged": dedup_stats.get("merged", 0),
            "review_removed": len(removed_ids),
            "review_added": len(review_res.get("added") or []),
            "total": len(active),
            "high": sum(1 for f in active if f.severity == "high"),
            "medium": sum(1 for f in active if f.severity == "medium"),
            "low": sum(1 for f in active if f.severity == "low"),
            "by_node": {},
            "by_origin": {
                "rule": sum(1 for f in active if f.origin == "rule"),
                "ai": sum(1 for f in active if f.origin == "ai"),
                "review": sum(1 for f in active if f.origin == "review"),
            },
        }
        for f in active:
            key = f"{f.node_no}{f.node_name}"
            summary["by_node"][key] = summary["by_node"].get(key, 0) + 1

        self._progress("S4", 100, "核查完成，已生成最终核查结果")
        return {
            "dedup": dedup_stats,
            "review": {
                "ok": review_res.get("ok", False),
                "message": review_res.get("message", ""),
                "conclusion": review_res.get("conclusion", ""),
                "removed": removed_ids,
                "added_count": len(review_res.get("added") or []),
            },
            "summary": summary,
            "findings": [f.to_dict() for f in final],
        }

    def _material_text(self, max_chars: int = 24000) -> str:
        """拼接待复核材料原文（供 AI 全局复核使用）。"""
        ctx = RuleContext({}, {}, self.blocks, self.block_paths, self._log)
        parts: List[str] = []
        for bid, blk in self.blocks.items():
            if bid in ("B1", "B2", "B3"):
                text = build_block_text(ctx, bid, max_chars=8000)
            else:
                text = (blk.text or "")[:8000]
            if text.strip():
                parts.append(f"『{bid} {blk.name}』\n{text}")
        return ("\n\n".join(parts))[:max_chars]

    # ---------------- 主流程 ----------------
    def run(self) -> Dict[str, Any]:
        started = time.time()
        result: Dict[str, Any] = {
            "status": "done", "stage": "S4", "percent": 100.0, "message": "",
            "logs": [], "preprocess": {}, "split": {}, "nodes": [],
            "dedup": {}, "review": {}, "summary": {}, "findings": [],
        }
        try:
            # 阶段一
            pre = self.stage1_preprocess()
            result["preprocess"] = pre
            if pre.get("blocked"):
                result.update({
                    "status": "blocked", "stage": "S1", "percent": 15.0,
                    "message": pre.get("blocked_message") or "预处理未通过",
                })
                result["logs"] = self.state["logs"]
                self.state.update(result)
                return result
            if self._canceled():
                return self._abort(result)

            # 阶段二
            result["split"] = self.stage2_split()
            if self._canceled():
                return self._abort(result)

            # 阶段三
            self.stage3_nodes()
            if self._canceled():
                return self._abort(result)

            # 阶段四
            result.update(self.stage4_summary())
            result["status"] = "done"
            result["message"] = (
                f"核查完成：{result['summary'].get('node_done', 0)} 个节点 / "
                f"{result['summary'].get('item_done', 0)} 项核查事项，"
                f"发现问题 {result['summary'].get('total', 0)} 条"
                f"（严重 {result['summary'].get('high', 0)}）"
            )
        except Exception as exc:  # noqa: BLE001 - 兜底，保证任务状态可回收
            result.update({"status": "failed", "message": f"流水线执行失败：{exc}"})
            self._log(f"流水线异常：{exc}")

        result["nodes"] = [n.to_dict() for n in self.nodes]
        result["logs"] = self.state["logs"]
        result["seconds"] = round(time.time() - started, 2)
        result["stage"] = self.state.get("stage", "S4")
        result["percent"] = self.state.get("percent", 100.0)
        self.state.update({k: v for k, v in result.items() if k != "logs"})
        return result

    def _abort(self, result: Dict[str, Any]) -> Dict[str, Any]:
        result.update({
            "status": "canceled", "message": "核查已取消",
            "nodes": [n.to_dict() for n in self.nodes],
            "logs": self.state["logs"],
            "percent": self.state.get("percent", 0.0),
            "stage": self.state.get("stage", "S3"),
        })
        self.state.update({k: v for k, v in result.items() if k != "logs"})
        return result
