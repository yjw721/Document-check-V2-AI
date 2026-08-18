/* 全息光谱进度条：渐变分段 */
interface HoloProgressProps {
  /** {高,中,低} 数量 */
  high: number;
  medium: number;
  low: number;
}

export default function HoloProgress({ high, medium, low }: HoloProgressProps) {
  const tot = high + medium + low || 1;
  const seg = (n: number, cls: string) =>
    n > 0 ? <div className={`h-full ${cls}`} style={{ width: `${(n / tot) * 100}%` }} /> : null;
  return (
    <div className="flex h-2.5 overflow-hidden rounded-full bg-white/10" role="img" aria-label="问题严重度分布">
      {seg(high, "holo-progress bg-[#ff6b7d] shadow-[0_0_12px_rgba(255,107,125,0.5)]")}
      {seg(medium, "bg-[#ffb454] shadow-[0_0_12px_rgba(255,180,84,0.5)]")}
      {seg(low, "bg-[#4fd6c9] shadow-[0_0_12px_rgba(79,214,201,0.5)]")}
    </div>
  );
}
