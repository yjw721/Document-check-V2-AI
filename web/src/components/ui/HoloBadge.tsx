import type { ReactNode } from "react";

/* 全息徽章：状态/级别配色 + 棱镜微光晕 */
type Tone = "ok" | "warn" | "danger" | "sev-high" | "sev-medium" | "sev-low" | "gray" | "accent";

interface HoloBadgeProps {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}

const TONES: Record<Tone, string> = {
  ok: "bg-[rgba(70,211,154,0.14)] text-[#46d39a] border-[rgba(70,211,154,0.3)]",
  warn: "bg-[rgba(255,180,84,0.14)] text-[#ffb454] border-[rgba(255,180,84,0.3)]",
  danger: "bg-[rgba(255,107,125,0.14)] text-[#ff6b7d] border-[rgba(255,107,125,0.3)]",
  "sev-high": "bg-[rgba(255,107,125,0.16)] text-[#ff6b7d] border-[rgba(255,107,125,0.35)]",
  "sev-medium": "bg-[rgba(255,180,84,0.16)] text-[#ffb454] border-[rgba(255,180,84,0.35)]",
  "sev-low": "bg-[rgba(79,214,201,0.16)] text-[#4fd6c9] border-[rgba(79,214,201,0.35)]",
  gray: "bg-white/5 text-white/70 border-white/10",
  accent: "bg-[var(--glow-accent-soft)] text-[var(--accent)] border-[var(--border-accent-soft)]",
};

export default function HoloBadge({ tone = "gray", children, className = "" }: HoloBadgeProps) {
  return (
    <span
      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
