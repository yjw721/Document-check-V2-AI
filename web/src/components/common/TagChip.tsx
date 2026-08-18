/* 筛选 chip：点击切换，激活态渐变高亮 */
interface TagChipProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

export default function TagChip({ label, active, onClick }: TagChipProps) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center rounded-full border px-3.5 py-1.5 text-xs font-medium transition-all duration-500 select-none active:scale-95 ${
        active
          ? "border-[var(--border-accent-soft)] bg-gradient-to-r from-[var(--holo-c1)]/20 via-[var(--holo-c2)]/25 to-[var(--holo-c3)]/20 text-white shadow-[0_0_15px_var(--glow-card-sm)]"
          : "border-white/10 bg-white/5 text-white/60 hover:bg-white/10 hover:text-white"
      }`}
    >
      {label}
    </button>
  );
}
