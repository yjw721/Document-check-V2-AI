/* 全息任务进度条：实时百分比 + 当前处理阶段步骤指示 */
interface TaskProgressProps {
  percent: number;
  stageText: string;
}

const STAGES = ["文件解析", "页码定位", "格式错误检测", "语句通顺度检测", "行业词库规则匹配", "结果汇总", "AI 智能核验"];

export default function TaskProgress({ percent, stageText }: TaskProgressProps) {
  const pct = Math.max(0, Math.min(100, Math.round(percent)));
  const curIdx = STAGES.indexOf(stageText);
  return (
    <div>
      <div className="flex items-end justify-between">
        <span className="text-sm font-semibold text-white/85">{stageText || "准备中…"}</span>
        <span className="font-mono text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-[var(--holo-c1)] via-[var(--holo-c2)] to-[var(--holo-c3)]">
          {pct}%
        </span>
      </div>
      <div className="mt-2 h-3 overflow-hidden rounded-full bg-white/10 shadow-inner">
        <div
          className="h-full rounded-full bg-gradient-to-r from-[var(--holo-c1)] via-[var(--holo-c2)] to-[var(--holo-c3)] shadow-[0_0_18px_var(--glow-btn)] transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {STAGES.map((s, i) => {
          const active = i === curIdx;
          const past = curIdx > 0 && i < curIdx;
          return (
            <span
              key={s}
              className={`rounded-full px-2.5 py-1 text-[11px] transition-all duration-500 ${
                active
                  ? "bg-gradient-to-r from-[var(--holo-c1)]/30 to-[var(--holo-c3)]/30 text-white shadow-[0_0_12px_var(--glow-btn)] ring-1 ring-[var(--border-accent-soft)]"
                  : past
                    ? "bg-white/[0.07] text-white/45"
                    : "bg-white/[0.04] text-white/25"
              }`}
            >
              {past ? "✓ " : ""}
              {s}
            </span>
          );
        })}
      </div>
    </div>
  );
}
