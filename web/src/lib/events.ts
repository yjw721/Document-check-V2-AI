/* ============================================================
   规则词库数据变更事件总线
   扫描清理 / 批量导入等操作完成后广播，各列表页（自定义规则 /
   自定义词库）监听后自动重新拉取，实现「清理后实时刷新规则列表」。
   ============================================================ */

export const RULES_CHANGED_EVENT = "rules-data-changed";

export function emitRulesChanged(): void {
  window.dispatchEvent(new CustomEvent(RULES_CHANGED_EVENT));
}

export function onRulesChanged(cb: () => void): () => void {
  window.addEventListener(RULES_CHANGED_EVENT, cb);
  return () => window.removeEventListener(RULES_CHANGED_EVENT, cb);
}