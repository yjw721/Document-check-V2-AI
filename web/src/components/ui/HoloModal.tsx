import { useEffect, type ReactNode } from "react";
import HoloCard from "./HoloCard";

/* 全息模态弹窗：玻璃态 + 光晕 + Esc 关闭 + 遮罩点击关闭 */
interface HoloModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  width?: number;
  footer?: ReactNode;
}

export default function HoloModal({ open, title, onClose, children, width = 560, footer }: HoloModalProps) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-[rgba(5,8,16,0.66)] p-4 backdrop-blur-md"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="max-h-[86vh] w-full overflow-auto"
        style={{ maxWidth: width }}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <HoloCard glow="lg" className="p-6">
          <div className="mb-4 flex items-center justify-between gap-4">
            <h3 className="holo-text text-xl font-bold">{title}</h3>
            <button
              onClick={onClose}
              aria-label="关闭"
              className="grid h-8 w-8 place-items-center rounded-2xl border border-white/10 bg-white/5 text-white/70 transition-all duration-500 hover:border-[var(--border-accent-soft)] hover:text-white hover:shadow-[0_0_20px_var(--glow-btn)] active:scale-95"
            >
              ✕
            </button>
          </div>
          <div className="text-sm text-white/80">{children}</div>
          {footer && <div className="mt-5 flex justify-end gap-3">{footer}</div>}
        </HoloCard>
      </div>
    </div>
  );
}
