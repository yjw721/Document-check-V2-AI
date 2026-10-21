import { useEffect, useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloBadge from "../../components/ui/HoloBadge";
import TaskProgress from "../../components/common/TaskProgress";
import ThinkingLog from "../../components/common/ThinkingLog";
import { api } from "../../lib/api";
import { useTaskState } from "../../lib/taskState";
import type { AiStream } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

/* 标签3 · 核验等待界面：实时进度条 + AI 思考过程动态面板 + 取消/错误/自动跳转 */
export default function WaitingTab() {
  const toast = useToast();
  const { state, dispatch, clear, releaseTask } = useTaskState();
  const [gone, setGone] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  /* 已触发过终态处理（跳转 / 清理），防止重复执行 */
  const settled = useRef(false);
  /* 连续网络失败计数：容忍瞬时抖动，避免误判为「任务不存在」 */
  const failCount = useRef(0);

  const taskId = state.taskId;

  useEffect(() => {
    if (!taskId) return;
    let stop = false;
    settled.current = false;
    failCount.current = 0;

    const loop = async () => {
      let cont = false;
      try {
        const s = await api.taskPoll(taskId);
        if (stop) return;
        failCount.current = 0;
        /* 先落快照（终态也要落，供下面的自动跳转读取状态），再决定是否继续 */
        dispatch({ type: "SET_SNAPSHOT", payload: s });
        cont = s.status === "running";
        if (!cont) {
          /* 终态：仅释放 taskId（保留快照），避免清空 status 导致跳转条件失效 */
          releaseTask();
        }
      } catch {
        if (stop) return;
        failCount.current += 1;
        /* 容忍 2 次瞬时失败（后端忙碌 / 网络抖动），第 3 次才判定任务失效 */
        if (failCount.current < 3) {
          cont = true;
        } else {
          setGone(true);
          clear();
        }
      }
      if (cont && !stop) {
        window.setTimeout(loop, 800);
      }
    };
    loop();

    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  /* 核验完成 → 自动跳转错误结果明细页 */
  useEffect(() => {
    if (state.status === "done" && !settled.current) {
      settled.current = true;
      toast("核验完成，已跳转错误详情");
      const t = window.setTimeout(() => {
        window.location.hash = "#detection/issues";
      }, 400);
      return () => window.clearTimeout(t);
    }
    if (state.status === "error" && !settled.current) {
      settled.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status]);

  const cancel = async () => {
    if (!taskId) return;
    if (!window.confirm("确定取消当前核验任务？已完成部分将被丢弃，无法生成报告。")) return;
    setCancelling(true);
    try {
      await api.taskCancel(taskId);
      dispatch({
        type: "SET_SNAPSHOT",
        payload: {
          status: "cancelled",
          progress: state.progress,
          stage: state.stage,
          stage_text: state.stageText,
          logs: state.logs,
          error: state.error,
        },
      });
      clear();
      toast("已取消核验");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setCancelling(false);
    }
  };

  const back = () => {
    clear();
    window.location.hash = "#detection/upload";
  };

  const status = state.status ?? "running";
  const finished = status === "done" || status === "cancelled" || status === "error";
  const done = status === "done";

  /* taskId 已被释放（任务结束）但仍有终态快照 → 继续展示结果卡片而非空态 */
  if (!taskId && !finished) {
    return (
      <HoloCard className="p-8 text-center" glow="sm">
        <div className="text-4xl">🧭</div>
        <p className="mt-3 text-sm text-white/70">当前没有进行中的核验任务</p>
        <div className="mt-4 flex justify-center gap-2">
          <HoloButton onClick={back}>返回导入与检测</HoloButton>
        </div>
      </HoloCard>
    );
  }

  return (
    <div className="space-y-4">
      <HoloCard className="p-6" glow="sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-lg">🤖</span>
            <h3 className="text-sm font-semibold tracking-wide text-white/85">文档核验进行中</h3>
            <HoloBadge tone={status === "running" ? "warn" : status === "done" ? "ok" : "danger"}>
              {status === "running" ? "运行中" : status === "done" ? "已完成" : status === "cancelled" ? "已取消" : "异常中断"}
            </HoloBadge>
          </div>
          {!finished && (
            <HoloButton variant="danger" size="sm" onClick={cancel} disabled={cancelling}>
              {cancelling ? "正在取消…" : "取消核验"}
            </HoloButton>
          )}
        </div>

        <div className="mt-5">
          <TaskProgress percent={state.progress} stageText={state.stageText ?? "文件解析"} />
        </div>

        {(status === "error" || gone) && (
          <div className="mt-4 rounded-xl border border-[rgba(255,107,125,0.35)] bg-[rgba(255,107,125,0.08)] px-3.5 py-3 text-xs text-[var(--tone-danger-soft)]">
            {state.error || "任务不存在或已过期，请重新发起核验。"}（已完成部分进度保留在进度条中，结果未写入报告）
          </div>
        )}
        {status === "cancelled" && (
          <p className="mt-4 text-xs text-white/45">
            任务已取消：中间缓存已清空，已完成部分不会写入结果与报告。
          </p>
        )}

        {!done && (
          <div className="mt-4">
            <ThinkingLog logs={state.logs} />
          </div>
        )}

        {/* AI 智能核验 · 本地模型推理流（逐 token 实时展示） */}
        {state.ai_stream && (
          <div className="mt-4">
            <AiStreamPanel
              stream={state.ai_stream}
              running={state.status === "running" && state.stage === "ai"}
            />
          </div>
        )}

        {/* AI 核验阶段汇总（任务完成时展示，失败仅提示、不污染问题统计） */}
        {finished && state.ai_summary && state.ai_summary.files > 0 && (
          <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 py-3 text-xs leading-relaxed">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">🧠</span>
              <b className="text-xs font-semibold tracking-wide text-white/85">AI 智能核验汇总</b>
              <HoloBadge tone={state.ai_summary.fail > 0 || state.ai_summary.partial > 0 ? "warn" : "ok"}>
                {state.ai_summary.fail > 0 || state.ai_summary.partial > 0 ? "部分未完成" : "全部完成"}
              </HoloBadge>
            </div>
            <p className="mt-1.5 text-white/75">
              已核验 {state.ai_summary.files} 个文件：命中 {state.ai_summary.hit} · 完成 {state.ai_summary.ok} ·
              已跳过 {state.ai_summary.skip} · 失败 {state.ai_summary.fail}（部分失败 {state.ai_summary.partial}）
            </p>
            {(state.ai_summary.fail > 0 || state.ai_summary.partial > 0) && (
              <p className="mt-1 text-[var(--tone-warn-soft)]">
                部分文件 AI 核验未完成：具体原因见「文件列表」中各文件的 AI 状态徽标；阶段失败不再写入问题列表，不会污染问题统计与报告。
              </p>
            )}
          </div>
        )}

        {finished && (
          <div className="mt-5 flex flex-wrap items-center gap-2">
            {done ? (
              <>
                <HoloButton variant="primary" onClick={() => (window.location.hash = "#detection/issues")}>
                  查看错误详情
                </HoloButton>
                <HoloButton variant="ghost" onClick={() => (window.location.hash = "#detection/files")}>
                  文件列表
                </HoloButton>
                <HoloButton variant="ghost" onClick={() => (window.location.hash = "#detection/report")}>
                  报告导出
                </HoloButton>
              </>
            ) : (
              <HoloButton onClick={back}>返回导入与检测</HoloButton>
            )}
          </div>
        )}
      </HoloCard>
    </div>
  );
}

/* ---------- AI 智能核验 · 本地模型推理流面板 ---------- */
function AiStreamPanel({ stream, running }: { stream: AiStream; running: boolean }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [viewChunk, setViewChunk] = useState<number | null>(null);
  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [stream.content, stream.thinking]);

  const history = stream.history ?? [];
  const viewed = history.find((h) => h.chunk === viewChunk) ?? null;
  const displayContent = viewed ? viewed.content : stream.content;
  const displayThinking = viewed ? viewed.thinking : stream.thinking;

  return (
    <HoloCard className="overflow-hidden p-0" glow="sm">
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-4 py-2.5">
        <span className="text-sm">🧠</span>
        <b className="text-xs font-semibold tracking-wide text-white/85">AI 智能核验 · 本地模型推理</b>
        <HoloBadge tone={running ? "warn" : "gray"}>
          {running ? "推理中…" : stream.total > 0 ? "推理完成" : "等待推理"}
        </HoloBadge>
        <span className="ml-auto truncate text-[11px] text-white/40" title={stream.file}>
          {stream.file}
        </span>
        <span className="shrink-0 text-[11px] text-white/50">
          第 {stream.chunk}/{stream.total || "?"} 段
        </span>
      </div>

      {/* 当前段输入预览：模型正在分析的内容 */}
      {(stream.preview || (viewed && viewed.preview)) && (
        <div className="border-b border-white/[0.07] bg-white/[0.02] px-4 py-2">
          <div className="text-[10px] tracking-wide text-white/35">
            {viewed ? `已归档 · 第 ${viewed.chunk} 段输入` : running ? "模型正在分析以下文本" : "本段输入文本"}
          </div>
          <p className="mt-0.5 line-clamp-2 break-all text-[11px] text-white/55">
            {viewed ? viewed.preview : stream.preview}
          </p>
        </div>
      )}

      {history.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-white/[0.07] bg-white/[0.02] px-4 py-2">
          <span className="text-[10px] text-white/35">分段回看</span>
          {history.map((h) => (
            <button
              key={h.chunk}
              onClick={() => setViewChunk(viewChunk === h.chunk ? null : h.chunk)}
              className={`rounded-lg border px-2 py-0.5 text-[11px] transition-all duration-300 ${
                viewChunk === h.chunk
                  ? "border-[rgba(79,214,201,0.45)] bg-[rgba(79,214,201,0.12)] text-[var(--tone-cyan)]"
                  : "border-white/10 bg-white/[0.04] text-white/55 hover:text-white"
              }`}
            >
              第 {h.chunk} 段
            </button>
          ))}
          {viewChunk !== null && (
            <button
              onClick={() => setViewChunk(null)}
              className="rounded-lg px-2 py-0.5 text-[11px] text-[var(--tone-cyan)] hover:bg-white/5"
            >
              返回实时 ✕
            </button>
          )}
        </div>
      )}

      {displayThinking && (
        <details className="border-b border-white/[0.07] bg-white/[0.02]">
          <summary className="cursor-pointer px-4 py-2 text-[11px] text-white/55 select-none hover:text-white/85">
            🧠 模型思考链（{displayThinking.length} 字）{running && !viewed ? "· 生成中…" : ""}
          </summary>
          <div className="max-h-44 overflow-y-auto border-t border-white/[0.07] px-4 py-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-white/60">
            {displayThinking}
          </div>
        </details>
      )}

      <div
        ref={boxRef}
        className="max-h-72 overflow-y-auto bg-[rgba(10,10,31,0.6)] px-4 py-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-cyan-100/85"
      >
        {displayContent || (running ? "等待模型输出…" : "（该段无模型输出）")}
        {running && !viewed && (
          <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-[var(--tone-cyan)] align-middle" />
        )}
      </div>

      <div className="border-t border-white/[0.07] px-4 py-1.5 text-[10px] text-white/35">
        全程离线 · 逐 token 实时输出 · 每段推理输入与输出均可回看（联网模式不展示逐字推理）
      </div>
    </HoloCard>
  );
}
