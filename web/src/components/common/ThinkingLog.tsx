import { useEffect, useRef, useState } from "react";

/* AI 思考过程动态面板：实时滚动日志，可折叠（默认展开），贴底自动跟随滚动 */
interface ThinkingLogProps {
  logs: string[];
}

function fmtTime() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export default function ThinkingLog({ logs }: ThinkingLogProps) {
  const [open, setOpen] = useState(true);
  const [stick, setStick] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  stickRef.current = stick;

  useEffect(() => {
    const box = boxRef.current;
    if (box && stickRef.current) {
      box.scrollTop = box.scrollHeight;
    }
  }, [logs, open]);

  const onScroll = () => {
    const box = boxRef.current;
    if (!box) return;
    setStick(box.scrollHeight - box.scrollTop - box.clientHeight < 40);
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-[rgba(10,10,30,0.55)] backdrop-blur-xl">
      <button
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-white/[0.05]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--holo-c2)] opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--holo-c2)]" />
        </span>
        <span className="text-xs font-semibold tracking-wide text-white/80">AI 思考过程</span>
        <span className="text-[10px] text-white/35">实时动作日志</span>
        <span className={`ml-auto text-[10px] text-white/45 transition-transform duration-300 ${open ? "rotate-180" : ""}`}>
          ▼
        </span>
      </button>
      {open && (
        <div
          ref={boxRef}
          onScroll={onScroll}
          className="h-52 space-y-1 overflow-y-auto border-t border-white/[0.07] px-3.5 py-2.5 font-mono text-[11px] leading-relaxed"
        >
          {logs.length === 0 && <div className="text-white/30">等待任务启动…</div>}
          {logs.map((line, i) => (
            <div key={i} className="flex gap-2">
              <span className="shrink-0 text-white/30">{fmtTime()}</span>
              <span className="break-all text-cyan-100/80">{line}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
