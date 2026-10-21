import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import HoloBadge from "../components/ui/HoloBadge";
import StatCard from "../components/common/StatCard";
import SectionTitle from "../components/common/SectionTitle";
import HoloProgress from "../components/common/HoloProgress";
import EmptyState from "../components/common/EmptyState";
import { api } from "../lib/api";
import type { ValFinding, ValMeta, ValNodeRun, ValResult } from "../lib/types";
import { SEV_LABEL } from "../lib/constants";

/* ============================================================
   资产评估报告智能核查 · 四阶段闭环
   Ⅰ 上传预处理 → Ⅱ 智能切分 → Ⅲ 9 大核查节点 → Ⅳ 汇总复核
   对应《AI核查报告需求点提取》：5 份附件 / 9 节点 / 96 事项
   ============================================================ */

/** 需求文档四阶段原色 */
const STAGE_COLOR: Record<string, string> = {
  S1: "#0288D1",
  S2: "#006064",
  S3: "#2E7D32",
  S4: "#C62828",
};

const STAGE_ROMAN: Record<string, string> = {
  S1: "Ⅰ",
  S2: "Ⅱ",
  S3: "Ⅲ",
  S4: "Ⅳ",
};

const STATUS_TEXT: Record<string, string> = {
  done: "已完成",
  running: "运行中",
  skipped: "已跳过",
  canceled: "已取消",
  failed: "失败",
  blocked: "已阻断",
  pending: "待执行",
  aborted: "已中断",
};

const SEV_TONE: Record<string, "sev-high" | "sev-medium" | "sev-low"> = {
  high: "sev-high",
  medium: "sev-medium",
  low: "sev-low",
};

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function ValuationPage() {
  const [meta, setMeta] = useState<ValMeta | null>(null);
  const [err, setErr] = useState("");

  /* Ⅲ：5 个附件槽位的文件 */
  const [picked, setPicked] = useState<Record<string, File>>({});
  const [sessionId, setSessionId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadedName, setUploadedName] = useState<Record<string, string>>({});

  /* 执行态 */
  const [taskId, setTaskId] = useState("");
  const [progress, setProgress] = useState(0);
  const [stageText, setStageText] = useState("");
  const [stage, setStage] = useState("S1");
  const [status, setStatus] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [result, setResult] = useState<ValResult | null>(null);
  const [useAi, setUseAi] = useState(false);

  /* 结果筛选 */
  const [sevFilter, setSevFilter] = useState<"all" | "high" | "medium" | "low">("all");
  const [originFilter, setOriginFilter] = useState<"all" | "rule" | "ai" | "review">("all");
  const [busy, setBusy] = useState(false);
  const logBoxRef = useRef<HTMLDivElement>(null);

  /* ---------- 载入元信息 ---------- */
  useEffect(() => {
    api.valMeta().then(setMeta).catch((e: Error) => setErr(e.message));
  }, []);

  /* ---------- 任务轮询 ---------- */
  useEffect(() => {
    if (!taskId) return;
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.valTask(taskId);
        if (!alive) return;
        setProgress(s.progress);
        setStageText(s.stage_text);
        setStage(s.stage || "S1");
        setStatus(s.status);
        setLogs(s.logs.slice(-80));
        if (s.done) {
          setResult(s.result);
          setBusy(false);
          if (s.result?.summary) return;
        } else {
          setTimeout(tick, 600);
        }
      } catch {
        // 瞬时网络抖动不应让进度条卡死：静默重试 3 次后让出
        if (alive) setTimeout(tick, 1000);
      }
    };
    void tick();
    return () => {
      alive = false;
    };
  }, [taskId]);

  /* 日志自动滚到底 */
  useEffect(() => {
    const el = logBoxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  /* ---------- 阶段 Ⅰ：上传 5 个附件 ---------- */
  const requiredReady = useMemo(() => {
    if (!meta) return false;
    return meta.slots.filter((s) => s.need).every((s) => picked[s.key]);
  }, [meta, picked]);

  const pickCount = Object.keys(picked).length;

  const onPick = (key: string, f: File | null) => {
    setPicked((prev) => {
      const next = { ...prev };
      if (f) next[key] = f;
      else delete next[key];
      return next;
    });
  };

  const doUpload = async () => {
    setUploading(true);
    setErr("");
    try {
      const files = Object.entries(picked).map(([, f]) => f);
      const slots = Object.keys(picked);
      const r = await api.valUpload(files, slots);
      setSessionId(r.session_id);
      setUploadedName(
        Object.fromEntries(r.files.map((f) => [f.slot, `${f.name}（${fmtSize(f.size)}）`])),
      );
      setResult(null);
      setTaskId("");
      setProgress(0);
      setLogs([]);
      setStatus("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const doRun = async () => {
    if (!sessionId) return;
    setBusy(true);
    setErr("");
    setResult(null);
    setLogs([]);
    setProgress(0);
    try {
      const r = await api.valRun({ session_id: sessionId, use_ai: useAi });
      setTaskId(r.task_id);
      setUseAi(r.use_ai);
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  const doCancel = () => {
    if (taskId) void api.valCancel(taskId).catch(() => {});
  };

  const doReset = async () => {
    await api.valReset(sessionId).catch(() => {});
    setPicked({});
    setUploadedName({});
    setSessionId("");
    setTaskId("");
    setResult(null);
    setLogs([]);
    setProgress(0);
    setStatus("");
    setStage("S1");
  };

  /* ---------- 阶段 Ⅳ：导出 Word 报告 ---------- */
  const doExport = useCallback(async () => {
    if (!taskId) return;
    try {
      const blob = await api.valReport(taskId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `资产评估核查报告_${new Date().toISOString().slice(0, 10)}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [taskId]);

  /* ---------- 结果派生数据 ---------- */
  const findings: ValFinding[] = useMemo(() => {
    const all = result?.findings ?? [];
    return all.filter((f) => {
      if (f.state === "ignored") return false;
      if (sevFilter !== "all" && f.severity !== sevFilter) return false;
      if (originFilter !== "all" && f.origin !== originFilter) return false;
      return true;
    });
  }, [result, sevFilter, originFilter]);

  const nodes: ValNodeRun[] = result?.nodes ?? [];
  const summary = result?.summary;

  /* 阶段推进着色：未开始→S1，上传后→S2，运行中→S3，完成→S4 */
  const curStage = result ? "S4" : busy ? stage : sessionId ? "S2" : "S1";

  if (err && !meta) {
    return (
      <HoloCard className="p-6">
        <div className="py-10 text-center text-white/40">加载失败：{err}</div>
        <div className="pb-6 text-center text-xs text-white/30">
          请确认后端服务已在 http://127.0.0.1:8501 启动
        </div>
      </HoloCard>
    );
  }

  return (
    <div className="space-y-4">
      {/* ================= 头部 ================= */}
      <HoloCard className="overflow-hidden p-6" glow="lg">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-xl font-extrabold tracking-wide text-white md:text-2xl">
              资产评估报告智能核查
            </h2>
            <p className="mt-1.5 text-[13px] text-white/55">
              5 份输入附件 → 四阶段闭环 → {meta?.node_count ?? 9} 大核查节点 /{" "}
              {meta?.item_count ?? 96} 项核查事项 → 去重复核后输出终稿报告
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <HoloBadge tone="accent">本地离线 · 零联网</HoloBadge>
            <HoloBadge tone="gray">规则引擎 + AI 语义核查</HoloBadge>
          </div>
        </div>

        {/* 四阶段进度条 */}
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          {(meta?.stages?.length
            ? meta.stages
            : [
                { id: "S1", no: "Ⅰ", name: "文件上传与预处理" },
                { id: "S2", no: "Ⅱ", name: "文件智能切分" },
                { id: "S3", no: "Ⅲ", name: "9 大核查节点" },
                { id: "S4", no: "Ⅳ", name: "汇总与复核" },
              ]
          ).map((s) => {
            const color = STAGE_COLOR[s.id] ?? "#7928ca";
            const active = s.id === curStage;
            const done =
              (curStage === "S4" && s.id !== "S4") ||
              (curStage === "S3" && (s.id === "S1" || s.id === "S2"));
            return (
              <div
                key={s.id}
                className="rounded-2xl border px-3.5 py-3 transition-all duration-500"
                style={{
                  borderColor: active ? `${color}80` : "rgba(255,255,255,0.10)",
                  background: active ? `${color}22` : "rgba(255,255,255,0.04)",
                  boxShadow: active ? `0 0 25px ${color}55` : "none",
                }}
              >
                <div className="flex items-center gap-2">
                  <span
                    className="flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-extrabold text-white"
                    style={{ background: `linear-gradient(135deg, ${color}, ${color}99)` }}
                  >
                    {done ? "✓" : STAGE_ROMAN[s.id] ?? s.no}
                  </span>
                  <span className="text-[13px] font-bold text-white/90">阶段 {STAGE_ROMAN[s.id] ?? s.no}</span>
                </div>
                <div className="mt-1.5 text-[12px] leading-snug text-white/55">{s.name}</div>
              </div>
            );
          })}
        </div>
      </HoloCard>

      {/* ================= 阶段 Ⅰ · 上传 5 个附件 ================= */}
      <HoloCard className="p-6">
        <SectionTitle
          extra={
            <span className="text-xs text-white/40">
              已选 {pickCount} / {meta?.slots.length ?? 5} 份
            </span>
          }
        >
          阶段 Ⅰ · 文件上传与预处理
        </SectionTitle>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {(meta?.slots ?? []).map((slot) => {
            const f = picked[slot.key];
            const done = uploadedName[slot.key];
            return (
              <div
                key={slot.key}
                className="rounded-2xl border border-white/10 bg-white/5 p-4 backdrop-blur-xl transition-all duration-500 hover:border-[var(--border-accent-soft)] hover:shadow-[0_0_20px_var(--glow-btn)]"
              >
                <div className="flex items-start gap-2">
                  <span className="text-lg">{slot.kind === "doc" ? "📘" : "📗"}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[13px] font-bold text-white/90">{slot.name}</span>
                      {slot.need ? (
                        <HoloBadge tone="danger">必传</HoloBadge>
                      ) : (
                        <HoloBadge tone="gray">选传</HoloBadge>
                      )}
                    </div>
                    <div className="mt-0.5 text-[11px] text-white/35">{slot.ext.join(" / ")}</div>
                  </div>
                </div>

                <div className="mt-3 text-[12px]">
                  {f ? (
                    <div className="truncate text-white/75" title={f.name}>
                      📎 {f.name}
                    </div>
                  ) : done ? (
                    <div className="truncate text-[var(--tone-ok)]" title={done}>
                      ✅ {done}
                    </div>
                  ) : (
                    <div className="text-white/30">未选择文件</div>
                  )}
                </div>

                <div className="mt-3 flex gap-2">
                  <label
                    className="inline-flex flex-1 cursor-pointer items-center justify-center gap-2 rounded-2xl border border-white/20 bg-white/10 px-3 py-[7px] text-xs font-semibold text-white backdrop-blur-xl transition-all duration-500 hover:scale-[1.02] hover:border-[var(--border-accent-soft)] hover:shadow-[0_0_20px_var(--glow-btn)] active:scale-95"
                    role="button"
                    tabIndex={0}
                    aria-label={`选择${slot.name}`}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        (e.currentTarget.querySelector("input") as HTMLInputElement | null)?.click();
                      }
                    }}
                  >
                    选择文件
                    <input
                      type="file"
                      className="hidden"
                      accept={slot.ext.join(",")}
                      onChange={(e) => onPick(slot.key, e.target.files?.[0] ?? null)}
                    />
                  </label>
                  {f && (
                    <HoloButton size="sm" variant="danger" onClick={() => onPick(slot.key, null)}>
                      清除
                    </HoloButton>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <HoloButton variant="primary" icon="📥" disabled={!requiredReady || uploading} onClick={doUpload}>
            {uploading ? "上传中…" : "上传并预处理"}
          </HoloButton>
          <HoloButton disabled={!sessionId || busy} onClick={doReset} icon="🧹">
            重置
          </HoloButton>
          {!requiredReady && (
            <span className="text-xs text-white/40">需先选择「评估报告」与「评估说明」两份必传件</span>
          )}
          {err && <span className="text-xs text-[var(--tone-danger)]">⚠ {err}</span>}
        </div>

        {/* 预处理明细（#REF! 硬拦截结果） */}
        {result?.preprocess?.items?.length ? (
          <div className="mt-4 overflow-hidden rounded-2xl border border-white/10">
            <table className="w-full text-left text-[12.5px]">
              <thead className="bg-white/5 text-white/60">
                <tr>
                  <th className="px-3 py-2 font-semibold">槽位</th>
                  <th className="px-3 py-2 font-semibold">文件</th>
                  <th className="px-3 py-2 font-semibold">预处理结果</th>
                </tr>
              </thead>
              <tbody>
                {result.preprocess.items.map((it) => (
                  <tr key={it.slot} className="border-t border-white/[0.07]">
                    <td className="px-3 py-2 text-white/45">{it.slot}</td>
                    <td className="px-3 py-2 text-white/80">{it.file}</td>
                    <td className="px-3 py-2 text-white/70">{it.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {result?.preprocess?.blocked && (
          <div className="mt-4 rounded-2xl border border-[rgba(255,107,125,0.4)] bg-[rgba(255,107,125,0.12)] px-4 py-3 text-[13px] text-[var(--tone-danger)]">
            ⛔ 预处理硬阻断：{result.preprocess.blocked_message || "存在 #REF! 错误，请修复后重新上传"}
          </div>
        )}
      </HoloCard>

      {/* ================= 阶段 Ⅱ / Ⅲ · 切分与 9 大节点 ================= */}
      <HoloCard className="p-6">
        <SectionTitle
          extra={
            <span className="text-xs text-white/40">
              {STATUS_TEXT[status] || (sessionId ? "待执行" : "等待上传")}
            </span>
          }
        >
          阶段 Ⅱ / Ⅲ · 智能切分与 9 大核查节点
        </SectionTitle>

        <div className="flex flex-wrap items-center gap-2">
          <HoloButton
            variant="primary"
            icon="🚀"
            disabled={!sessionId || busy}
            onClick={doRun}
          >
            {busy ? "核查进行中…" : "开始四阶段核查"}
          </HoloButton>
          <HoloButton variant="danger" disabled={!busy || !taskId} onClick={doCancel} icon="⏹">
            终止
          </HoloButton>
          <label className="ml-1 inline-flex cursor-pointer select-none items-center gap-2 rounded-2xl border border-white/15 bg-white/5 px-3 py-2 text-xs text-white/70 backdrop-blur-xl transition-all duration-500 hover:border-[var(--border-accent-soft)]">
            <input
              type="checkbox"
              className="accent-[var(--accent-ink)]"
              checked={useAi}
              onChange={(e) => setUseAi(e.target.checked)}
            />
            启用 AI 语义核查与综合复核
          </label>
        </div>

        {/* 进度 */}
        {(busy || progress > 0) && (
          <div className="mt-4">
            <div className="flex items-end justify-between">
              <span className="text-[13px] font-semibold text-white/85">{stageText || "准备中…"}</span>
              <span className="font-mono text-xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-[var(--holo-c1)] via-[var(--holo-c2)] to-[var(--holo-c3)]">
                {Math.round(progress)}%
              </span>
            </div>
            <div className="mt-2 h-3 overflow-hidden rounded-full bg-white/10 shadow-inner">
              <div
                className="h-full rounded-full bg-gradient-to-r from-[var(--holo-c1)] via-[var(--holo-c2)] to-[var(--holo-c3)] shadow-[0_0_18px_var(--glow-btn)] transition-[width] duration-500 ease-out"
                style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
              />
            </div>
          </div>
        )}

        {/* 切分结果：7 个数据块 */}
        {result?.split?.blocks?.length ? (
          <div className="mt-5">
            <div className="mb-2 text-[13px] font-bold text-white/80">阶段 Ⅱ · 切分后的 7 个数据块</div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
              {result.split.blocks.map((b) => (
                <div
                  key={b.id}
                  className="rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5 text-center backdrop-blur-xl transition-all duration-500 hover:border-[var(--border-accent-soft)] hover:shadow-[0_0_20px_var(--glow-btn)]"
                >
                  <div className="text-[11px] font-bold text-white/40">{b.id}</div>
                  <div className="mt-0.5 text-[12px] font-semibold text-white/85">{b.name}</div>
                  <div className="mt-1 text-[11px] text-white/35">
                    {b.kind === "excel"
                      ? `自有 ${b.stats?.own_sheets?.length ?? 0} + 通用 ${b.general_sheets?.length ?? 0}`
                      : `${b.chars ?? 0} 字`}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* 9 大节点执行状态 */}
        {nodes.length ? (
          <div className="mt-5 overflow-x-auto rounded-2xl border border-white/10">
            <table className="w-full min-w-[620px] text-left text-[12.5px]">
              <thead className="bg-white/5 text-white/60">
                <tr>
                  <th className="px-3 py-2 font-semibold">节点</th>
                  <th className="px-3 py-2 font-semibold">名称</th>
                  <th className="px-3 py-2 font-semibold">状态</th>
                  <th className="px-3 py-2 font-semibold">事项</th>
                  <th className="px-3 py-2 font-semibold">命中</th>
                  <th className="px-3 py-2 font-semibold">耗时</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <tr key={n.node_id} className="border-t border-white/[0.07]">
                    <td className="whitespace-nowrap px-3 py-2 text-white/45">{n.node_no}</td>
                    <td className="px-3 py-2 text-white/85">{n.node_name}</td>
                    <td className="px-3 py-2">
                      <HoloBadge tone={n.status === "done" ? "ok" : n.status === "skipped" ? "warn" : "gray"}>
                        {STATUS_TEXT[n.status] ?? n.status}
                      </HoloBadge>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-white/70">
                      {n.item_done}/{n.item_total}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-white/70">
                      {n.finding_brief?.length ?? 0}
                      <span className="ml-1 text-white/30">
                        （规则 {n.rule_count} / AI {n.ai_count}）
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-white/45">{n.seconds}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {/* 运行日志 */}
        {logs.length ? (
          <details className="mt-4" open={busy}>
            <summary className="cursor-pointer select-none text-[13px] font-bold text-white/70">
              执行日志（{logs.length} 条）
            </summary>
            <div
              ref={logBoxRef}
              className="mt-2 max-h-52 overflow-auto rounded-2xl border border-white/10 bg-black/30 p-3 font-mono text-[11.5px] leading-relaxed text-white/60"
            >
              {logs.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
            </div>
          </details>
        ) : null}
      </HoloCard>

      {/* ================= 阶段 Ⅳ · 汇总与复核 ================= */}
      {result && summary ? (
        <>
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            <StatCard label="核查节点" value={`${summary.node_done}/${summary.node_total}`} icon="🧩" />
            <StatCard label="核查事项" value={`${summary.item_done}/${summary.item_total}`} icon="📋" />
            <StatCard
              label="发现问题"
              value={summary.total}
              icon="🐞"
              tone="danger"
              sub={`去重合并 ${summary.dedup_merged} 条`}
            />
            <StatCard label="严重问题" value={summary.high} icon="⚠️" tone={summary.high ? "danger" : "ok"} />
          </div>

          <div className="flex flex-col gap-4 xl:flex-row">
            <div className="flex min-w-0 flex-1 flex-col gap-4">
              <HoloCard className="p-6">
                <SectionTitle>问题级别分布</SectionTitle>
                <HoloProgress high={summary.high} medium={summary.medium} low={summary.low} />
                <div className="mt-3 flex flex-wrap gap-4">
                  {(["high", "medium", "low"] as const).map((k) => (
                    <span key={k} className="inline-flex items-center gap-2 text-[13px] text-white/80">
                      <HoloBadge tone={SEV_TONE[k]}>{SEV_LABEL[k]}</HoloBadge> {summary[k]}
                    </span>
                  ))}
                  <span className="text-[13px] text-white/45">
                    规则 {summary.by_origin?.rule ?? 0} / AI {summary.by_origin?.ai ?? 0} / 复核{" "}
                    {summary.by_origin?.review ?? 0}
                  </span>
                </div>
              </HoloCard>

              <HoloCard className="p-6">
                <SectionTitle
                  extra={
                    <HoloButton variant="primary" size="sm" icon="📝" onClick={doExport}>
                      导出 Word 报告
                    </HoloButton>
                  }
                >
                  阶段 Ⅳ · 去重与 AI 复核
                </SectionTitle>
                <div className="space-y-2 text-[13px] text-white/70">
                  <div>
                    原始命中 <b className="text-white">{summary.findings_raw}</b> 条 → 去重保留{" "}
                    <b className="text-white">{summary.total}</b> 条
                    {summary.dedup_merged > 0 && (
                      <span className="text-white/45">（合并跨节点重复 {summary.dedup_merged} 条）</span>
                    )}
                  </div>
                  {result.review?.ok ? (
                    <>
                      <div>
                        AI 复核：剔除误报 <b className="text-white">{summary.review_removed}</b> 条、补充漏报{" "}
                        <b className="text-white">{summary.review_added}</b> 条
                      </div>
                      {result.review.conclusion && (
                        <div className="rounded-2xl border border-white/10 bg-white/5 px-3.5 py-2.5 text-white/65">
                          🧠 {result.review.conclusion}
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="text-white/45">
                      未启用 AI 复核（纯本地规则引擎结果），建议对严重与一般问题人工复核确认。
                    </div>
                  )}
                </div>
              </HoloCard>
            </div>

            {/* 各节点问题分布 */}
            <div className="min-w-0 flex-1">
              <HoloCard className="p-6">
                <SectionTitle>各节点问题分布</SectionTitle>
                {Object.keys(summary.by_node ?? {}).length ? (
                  <div className="space-y-2.5 text-[13px]">
                    {Object.entries(summary.by_node ?? {})
                      .sort((a, b) => b[1] - a[1])
                      .map(([name, cnt]) => {
                        const max = Math.max(...Object.values(summary.by_node ?? {}), 1);
                        return (
                          <div key={name}>
                            <div className="mb-1 flex justify-between text-white/70">
                              <span className="truncate pr-2">{name}</span>
                              <span className="tabular-nums text-white/50">{cnt}</span>
                            </div>
                            <div className="h-2 overflow-hidden rounded-full bg-white/10">
                              <div
                                className="holo-accent-bar holo-accent-bar-r h-full rounded-full shadow-[0_0_12px_var(--glow-btn)]"
                                style={{ width: `${(cnt / max) * 100}%` }}
                              />
                            </div>
                          </div>
                        );
                      })}
                  </div>
                ) : (
                  <EmptyState text="本次核查未发现问题" icon="✨" />
                )}
              </HoloCard>
            </div>
          </div>

          {/* 问题明细清单 */}
          <HoloCard className="p-6">
            <SectionTitle
              extra={
                <div className="flex flex-wrap gap-1.5">
                  {(["all", "high", "medium", "low"] as const).map((k) => (
                    <button
                      key={k}
                      onClick={() => setSevFilter(k)}
                      className={`rounded-2xl border px-3 py-1.5 text-xs font-semibold transition-all duration-500 active:scale-95 ${
                        sevFilter === k
                          ? "border-transparent text-white shadow-[0_0_20px_var(--glow-tab)] holo-tint"
                          : "border-white/10 text-white/55 hover:bg-white/5 hover:text-white"
                      }`}
                    >
                      {k === "all" ? `全部 ${result.findings.length}` : `${SEV_LABEL[k]} ${summary[k]}`}
                    </button>
                  ))}
                  <span className="mx-1 w-px self-stretch bg-white/10" />
                  {(["all", "rule", "ai", "review"] as const).map((k) => (
                    <button
                      key={k}
                      onClick={() => setOriginFilter(k)}
                      className={`rounded-2xl border px-3 py-1.5 text-xs font-semibold transition-all duration-500 active:scale-95 ${
                        originFilter === k
                          ? "border-transparent text-white shadow-[0_0_20px_var(--glow-tab)] holo-tint"
                          : "border-white/10 text-white/55 hover:bg-white/5 hover:text-white"
                      }`}
                    >
                      {k === "all" ? "全部来源" : k === "rule" ? "规则引擎" : k === "ai" ? "AI 核查" : "AI 复核"}
                    </button>
                  ))}
                </div>
              }
            >
              问题明细清单（{findings.length} 条）
            </SectionTitle>

            {findings.length ? (
              <div className="max-h-[520px] overflow-auto rounded-2xl border border-white/10">
                <table className="w-full min-w-[860px] text-left text-[12.5px]">
                  <thead className="sticky top-0 bg-[#150c2b]/95 text-white/60 backdrop-blur-xl">
                    <tr>
                      <th className="px-3 py-2 font-semibold">级别</th>
                      <th className="px-3 py-2 font-semibold">位置</th>
                      <th className="px-3 py-2 font-semibold">核查事项</th>
                      <th className="px-3 py-2 font-semibold">问题说明</th>
                      <th className="px-3 py-2 font-semibold">整改建议</th>
                    </tr>
                  </thead>
                  <tbody>
                    {findings.map((f) => (
                      <tr key={f.id} className="border-t border-white/[0.07] align-top">
                        <td className="whitespace-nowrap px-3 py-2">
                          <HoloBadge tone={SEV_TONE[f.severity] ?? "gray"}>{SEV_LABEL[f.severity] ?? f.severity}</HoloBadge>
                        </td>
                        <td className="px-3 py-2 text-white/70">
                          <div className="max-w-[190px] break-words">{f.location || "—"}</div>
                          {f.file && <div className="mt-0.5 text-[11px] text-white/35">{f.file}</div>}
                        </td>
                        <td className="px-3 py-2 text-white/80">
                          {f.item_title}
                          {f.merged_count > 1 && (
                            <span className="ml-1 text-[11px] text-white/40">×{f.merged_count}</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-white/70">
                          <div className="max-w-[340px] whitespace-pre-wrap break-words">{f.detail}</div>
                          {f.snippet && (
                            <div className="mt-1 max-w-[340px] break-words text-[11px] text-white/35">
                              原文：{f.snippet}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2 text-white/60">
                          <div className="max-w-[240px] whitespace-pre-wrap break-words">{f.suggestion || "—"}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState text="未发现符合条件的问题" icon="✨" />
            )}
          </HoloCard>
        </>
      ) : null}
    </div>
  );
}
