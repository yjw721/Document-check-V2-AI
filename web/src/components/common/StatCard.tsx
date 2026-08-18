import type { ReactNode } from "react";
import HoloCard from "../ui/HoloCard";

/* 全息统计卡 */
interface StatCardProps {
  label: string;
  value: ReactNode;
  icon: string;
  tone?: "default" | "danger" | "ok" | "warn";
  sub?: string;
}

const TONES = {
  default: "text-white",
  danger: "text-[var(--tone-danger)]",
  ok: "text-[var(--tone-ok)]",
  warn: "text-[var(--tone-warn)]",
};

export default function StatCard({ label, value, icon, tone = "default", sub }: StatCardProps) {
  return (
    <HoloCard className="p-6">
      <div className="flex items-center gap-2.5 text-sm text-white/70">
        <span className="text-lg">{icon}</span>
        {label}
      </div>
      <div className={`mt-2 text-3xl font-extrabold leading-none tracking-wide ${TONES[tone]}`}>{value}</div>
      {sub && <div className="mt-1.5 text-xs text-white/40">{sub}</div>}
    </HoloCard>
  );
}
