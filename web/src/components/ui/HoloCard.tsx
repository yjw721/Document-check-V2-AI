import type { CSSProperties, ReactNode } from "react";

/* 全息玻璃卡片：backdrop-blur + bg-white/5 + rounded-2xl + border-white/10 + 棱镜光晕 */
interface HoloCardProps {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  /** 大光晕（0 0 50px） */
  glow?: "sm" | "md" | "lg";
  onClick?: () => void;
}

const GLOW = {
  sm: "shadow-[0_0_15px_var(--glow-card-sm)]",
  md: "shadow-[0_0_25px_var(--glow-card-md)]",
  lg: "shadow-[0_0_50px_var(--glow-card-lg)]",
};

export default function HoloCard({ children, className = "", style, glow = "md", onClick }: HoloCardProps) {
  return (
    <div
      onClick={onClick}
      style={style}
      className={`rounded-2xl border border-white/10 bg-white/5 backdrop-blur-xl transition-all duration-500 ${GLOW[glow]} ${
        onClick ? "cursor-pointer hover:border-[var(--border-accent-soft)] hover:shadow-[0_0_30px_var(--glow-hover)]" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}
