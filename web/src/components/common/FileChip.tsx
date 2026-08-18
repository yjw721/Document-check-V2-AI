/* 文件列表项：玻璃 chip + 删除按钮 */
interface FileChipProps {
  name: string;
  sizeText?: string;
  icon?: string;
  onRemove?: () => void;
}

export default function FileChip({ name, sizeText, icon = "📎", onRemove }: FileChipProps) {
  return (
    <div className="flex items-center gap-2.5 rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5 text-[13px] text-white/80 backdrop-blur-xl transition-all duration-500 hover:border-[var(--border-accent-soft)] hover:shadow-[0_0_20px_var(--glow-card-sm)]">
      <span>{icon}</span>
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {sizeText && <span className="text-xs text-white/40">{sizeText}</span>}
      {onRemove && (
        <button
          onClick={onRemove}
          aria-label={`移除 ${name}`}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-xl border border-white/10 bg-white/5 text-xs text-white/60 transition-all duration-500 hover:border-[rgba(255,107,125,0.4)] hover:bg-[rgba(255,107,125,0.12)] hover:text-[#ff6b7d] active:scale-95"
        >
          ✕
        </button>
      )}
    </div>
  );
}
