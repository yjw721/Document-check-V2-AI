/* 核验任务全局状态（跨 tab 共享：上传页提交、等待页轮询、报告页拦截） */
import type { TaskStatus } from "../lib/types";

export const TASK_KEY = "doc_checker_task_id";

let liveStatus: TaskStatus | null = null;

export const taskState = {
  get status(): TaskStatus | null {
    return liveStatus;
  },
  setStatus(s: TaskStatus | null) {
    liveStatus = s;
  },
  clear() {
    liveStatus = null;
    try {
      localStorage.removeItem(TASK_KEY);
    } catch {
      /* ignore */
    }
  },
};
