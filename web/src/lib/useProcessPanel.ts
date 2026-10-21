import { useEffect, useMemo, useRef, useState } from "react";

/* 
 * 统一的进程面板 Hook：复用 BuildProcessPanel / LearnProcessPanel 的核心逻辑
 * - 阶段流状态推导
 * - 思考链/输出内容聚合
 * - 自动滚动
 * - 近期日志摘要
 */
export interface ProcessStage {
  key: string;
  label: string;
  icon: string;
  done: boolean;
  active: boolean;
}

export interface ProcessLogEntry {
  ts: string;
  kind: "stage" | "token" | "thinking" | "error";
  text: string;
}

export interface ProcessStats {
  entries?: number;
  rules?: number;
  filtered?: number;
  skipped?: number;
  standard_expression?: string;
}

export interface ProcessState {
  running: boolean;
  done: boolean;
  ok?: boolean;
  message?: string;
  stats?: ProcessStats;
}

export function useProcessPanel(
  logs: ProcessLogEntry[],
  state: ProcessState,
  stageKeys: string[],
  stageMeta?: Record<string, { label: string; icon: string }>
) {
  const viewRef = useRef<HTMLDivElement>(null);
  const [thinkingExpanded, setThinkingExpanded] = useState(false);

  // 阶段状态推导
  const stages = useMemo<ProcessStage[]>(() => {
    const seen = new Set(
      logs
        .filter((l) => l.kind === "stage")
        .map((l) => l.text.match(/^\[(\w+)\]/)?.[1] ?? "")
    );
    return stageKeys.map((key) => {
      let done = false;
      let active = false;
      if (key === "done") {
        done = state.done;
        active = false;
      } else {
        const hit = seen.has(key);
        done = hit;
        active = !done && (state.running || !state.done);
      }
      const meta = stageMeta?.[key];
      return {
        key,
        label: meta?.label ?? key,
        icon: meta?.icon ?? (key === "done" ? "✅" : "•"),
        done,
        active,
      };
    });
  }, [logs, state, stageKeys, stageMeta]);

  // 聚合内容
  const thinking = useMemo(
    () => logs.filter((l) => l.kind === "thinking").map((l) => l.text).join(""),
    [logs]
  );
  const output = useMemo(
    () => logs.filter((l) => l.kind === "token").map((l) => l.text).join(""),
    [logs]
  );
  const errLog = useMemo(
    () => logs.find((l) => l.kind === "error")?.text,
    [logs]
  );

  // 自动滚动到底部
  useEffect(() => {
    const el = viewRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thinking, output, errLog]);

  const runTone = state.done ? (state.ok ? "ok" : "danger") : "warn";
  const runLabel = state.done ? (state.ok ? "推理完成" : "推理失败") : "推理中…（流式输出）";

  return {
    stages,
    thinking,
    output,
    errLog,
    thinkingExpanded,
    setThinkingExpanded,
    viewRef,
    runTone,
    runLabel,
  };
}