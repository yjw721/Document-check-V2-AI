import type { ButtonHTMLAttributes, ReactNode } from "react";

/* 全息按钮：rounded-2xl + 渐变/玻璃 + transition-all + 光晕 + active:scale-95 */
type Variant = "primary" | "ghost" | "danger";

interface HoloButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "md" | "sm";
  icon?: ReactNode;
  children?: ReactNode;
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-2xl font-semibold transition-all duration-500 select-none " +
  "hover:scale-[1.02] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 " +
  "disabled:active:scale-100";

const VARIANTS: Record<Variant, string> = {
  primary:
    "holo-btn-primary text-[#fff] " +
    "shadow-[0_0_25px_var(--glow-btn)] hover:shadow-[0_0_30px_var(--glow-btn)] hover:border-[var(--border-accent-soft)]",
  ghost:
    "bg-white/10 text-white border border-white/20 backdrop-blur-xl " +
    "hover:bg-white/[0.16] hover:border-[var(--border-accent-soft)] hover:shadow-[0_0_30px_var(--glow-btn)]",
  danger:
    "bg-[rgba(255,107,125,0.14)] text-[var(--tone-danger-soft)] border border-[rgba(255,107,125,0.4)] " +
    "hover:bg-[rgba(255,107,125,0.24)] hover:shadow-[0_0_30px_rgba(255,107,125,0.35)]",
};

const SIZES = {
  md: "px-[18px] py-[11px] text-sm",
  sm: "px-3 py-[7px] text-xs",
};

export default function HoloButton({
  variant = "ghost",
  size = "md",
  icon,
  children,
  className = "",
  ...rest
}: HoloButtonProps) {
  return (
    <button className={`${BASE} ${VARIANTS[variant]} ${SIZES[size]} ${className}`} {...rest}>
      {icon}
      {children}
    </button>
  );
}
