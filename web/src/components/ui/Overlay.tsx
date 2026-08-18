/* 全息加载遮罩：光谱 spinner + 渐变流动文字 */
export default function Overlay({ show, text = "处理中…" }: { show: boolean; text?: string }) {
  if (!show) return null;
  return (
    <div className="holo-overlay" role="status" aria-live="polite">
      <div className="relative text-center">
        <span className="holo-spinner relative block" />
        <div className="mt-5 text-sm text-white/80">{text}</div>
      </div>
    </div>
  );
}
