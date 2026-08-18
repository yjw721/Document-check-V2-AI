/* 空状态占位 */
export default function EmptyState({ text = "暂无数据", icon = "💫" }: { text?: string; icon?: string }) {
  return (
    <div className="px-10 py-10 text-center text-white/40">
      <div className="mb-2 text-2xl opacity-70">{icon}</div>
      {text}
    </div>
  );
}
