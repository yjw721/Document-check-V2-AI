/* 核验任务全局状态（跨 tab 共享：上传页提交、等待页轮询、报告页拦截）
 * 使用 React Context + useReducer 保证跨组件响应式更新
 */
import { createContext, useContext, useMemo, useReducer, type ReactNode } from "react";
import type { TaskStatus, TaskSnapshot, AiStream, AiTaskSummary } from "../lib/types";

export const TASK_KEY = "doc_checker_task_id";

interface TaskState {
  status: TaskStatus | null;
  progress: number;
  stage: string;
  stageText: string;
  logs: string[];
  error: string;
  taskId: string | null;
  ai_stream?: AiStream | null;
  ai_summary?: AiTaskSummary | null;
  /** 终态时间戳：任务结束后自增，供依赖「最近一次核验」的页面感知并刷新 */
  stamp: number;
}

type TaskAction =
  | { type: "SET_SNAPSHOT"; payload: TaskSnapshot }
  | { type: "SET_TASK_ID"; payload: string }
  | { type: "CLEAR" }
  | { type: "UPDATE_PROGRESS"; payload: { progress: number; stage: string; stageText: string; log?: string } };

/** 任务已进入终态（不再轮询） */
export function isFinishedStatus(s: TaskStatus | null): boolean {
  return s === "done" || s === "cancelled" || s === "error";
}

const initialState: TaskState = {
  status: null,
  progress: 0,
  stage: "",
  stageText: "",
  logs: [],
  error: "",
  taskId: null,
  stamp: 0,
};

/* 同步读取已保存的 task_id：避免 useEffect 异步恢复造成的「首帧无 taskId」空窗，
   否则上传后立刻切到等待页时轮询不会启动，必须手动刷新才恢复。 */
function readSavedTaskId(): string | null {
  try {
    return localStorage.getItem(TASK_KEY) || null;
  } catch {
    return null;
  }
}

function taskReducer(state: TaskState, action: TaskAction): TaskState {
  switch (action.type) {
    case "SET_SNAPSHOT": {
      const s = action.payload;
      const finished = s.status !== "running";
      return {
        ...state,
        status: s.status,
        progress: s.progress,
        stage: s.stage,
        stageText: s.stage_text,
        logs: s.logs,
        error: s.error,
        ai_stream: s.ai_stream ?? null,
        ai_summary: s.ai_summary ?? null,
        /* 首次进入终态时自增 stamp，触发依赖页面刷新 */
        stamp: finished && state.status === "running" ? Date.now() : state.stamp,
      };
    }
    case "SET_TASK_ID":
      return { ...state, taskId: action.payload };
    case "CLEAR":
      return { ...initialState, taskId: null };
    case "UPDATE_PROGRESS":
      return {
        ...state,
        progress: action.payload.progress,
        stage: action.payload.stage,
        stageText: action.payload.stageText,
        logs: action.payload.log ? [...state.logs, action.payload.log].slice(-400) : state.logs,
      };
    default:
      return state;
  }
}

interface TaskContextValue {
  state: TaskState;
  dispatch: React.Dispatch<TaskAction>;
  setTaskId: (id: string) => void;
  clear: () => void;
  /** 仅释放 taskId（保留快照），供任务结束后读取终态触发跳转 */
  releaseTask: () => void;
}

const TaskContext = createContext<TaskContextValue | null>(null);

export function TaskProvider({ children }: { children: ReactNode }) {
  /* taskId 同步初始化（惰性 initializer），保证首帧即可用 */
  const [state, dispatch] = useReducer(taskReducer, null, () => ({
    ...initialState,
    taskId: readSavedTaskId(),
  }));

  const setTaskId = (id: string) => {
    try {
      localStorage.setItem(TASK_KEY, id);
    } catch {
      /* ignore */
    }
    dispatch({ type: "SET_TASK_ID", payload: id });
  };

  const clear = () => {
    try {
      localStorage.removeItem(TASK_KEY);
    } catch {
      /* ignore */
    }
    dispatch({ type: "CLEAR" });
  };

  /* 仅清除 taskId，保留最近一次快照（状态/日志/进度）。
     用于「任务已结束」后仍能读取 status=done 以触发自动跳转。 */
  const releaseTask = () => {
    try {
      localStorage.removeItem(TASK_KEY);
    } catch {
      /* ignore */
    }
    dispatch({ type: "SET_TASK_ID", payload: "" });
  };

  const value = useMemo<TaskContextValue>(
    () => ({ state, dispatch, setTaskId, clear, releaseTask }),
    [state],
  );

  return <TaskContext.Provider value={value}>{children}</TaskContext.Provider>;
}

export function useTaskState() {
  const ctx = useContext(TaskContext);
  if (!ctx) throw new Error("useTaskState must be used within TaskProvider");
  return ctx;
}