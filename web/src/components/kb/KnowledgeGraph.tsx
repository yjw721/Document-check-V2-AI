import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KbGraphNode, KbGraphResp } from "../../lib/types";

/* 知识思维图谱 —— 自包含 canvas 力导向图（零第三方依赖）
 * - 节点：文档(doc) / 章节(sec)，按章节层级着色、按段数与关联数定大小
 * - 结构边(hierarchy)：文档→章→节 的树形骨架
 * - 关联边(related)：章节间语义相似 / 关键词共现（颜色由权重映射 青→紫）
 * - 布局：先同步预热迭代至基本收敛，再适配视图，之后低能量持续微调 —— 首屏即正确
 * - 交互：悬停高亮邻居 + tooltip、点击选中、拖拽节点、滚轮缩放、空白拖拽平移
 */

const DOC_COLOR = "#ff7eb6";
const SEC_COLORS = ["#56d6ff", "#8b9bff", "#b98bff", "#d68bff"]; // 按层级递进

const REP = 1500; // 斥力
const DAMP = 0.9;
const GRAVITY = 0.004; // 向心力
const SPRING_H = 0.05; // 结构边刚度
const SPRING_R = 0.018; // 关联边刚度
const WARMUP_TICKS = 260; // 首帧前同步预热迭代次数

function nodeColor(n: KbGraphNode): string {
  if (n.type === "doc") return DOC_COLOR;
  return SEC_COLORS[Math.min(SEC_COLORS.length - 1, Math.max(0, n.level - 1))];
}
function nodeR(n: KbGraphNode): number {
  const chunks = n.chunks || 0;
  const rel = n.rel_count || 0;
  if (n.type === "doc") return 15;
  return 5 + Math.min(9, Math.sqrt(chunks) * 1.4) + Math.min(4, rel * 1.1);
}

interface SimNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx: number | null;
  fy: number | null;
  r: number;
  color: string;
  label: string;
  level: number;
  type: "doc" | "sec";
  chunks: number;
  rel: number;
  data: KbGraphNode;
}
interface SimEdge {
  s: SimNode;
  t: SimNode;
  kind: "hierarchy" | "related";
  weight: number;
}

function buildSim(data: KbGraphResp) {
  const byId = new Map<string, SimNode>();
  const sim: SimNode[] = data.nodes.map((n) => {
    const node: SimNode = {
      id: n.id,
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      fx: null,
      fy: null,
      r: nodeR(n),
      color: nodeColor(n),
      label: n.label,
      level: n.level || 1,
      type: n.type,
      chunks: n.chunks || 0,
      rel: n.rel_count || 0,
      data: n,
    };
    byId.set(n.id, node);
    return node;
  });
  const edges: SimEdge[] = data.edges
    .map((e) => {
      const s = byId.get(e.source);
      const t = byId.get(e.target);
      if (!s || !t) return null;
      return { s, t, kind: e.kind, weight: e.weight };
    })
    .filter(Boolean) as SimEdge[];

  // 径向初始化：文档居中，章节按层级在同心圆上铺开
  const docs = sim.filter((n) => n.type === "doc");
  const parentOf = new Map<string, string>();
  edges.forEach((e) => {
    if (e.kind === "hierarchy") parentOf.set(e.s.id, e.t.id);
  });
  const children = new Map<string, SimNode[]>();
  sim.forEach((n) => children.set(n.id, []));
  parentOf.forEach((p, c) => {
    const pn = byId.get(p);
    const cn = byId.get(c);
    if (pn && cn) children.get(pn.id)!.push(cn);
  });

  const placed = new Set<string>();
  docs.forEach((d, di) => {
    d.x = docs.length > 1 ? (di - (docs.length - 1) / 2) * 90 : 0;
    d.y = 0;
    placed.add(d.id);
  });
  if (docs.length === 0 && sim.length) {
    sim[0].x = 0;
    sim[0].y = 0;
    placed.add(sim[0].id);
  }
  let frontier = [...docs];
  while (frontier.length) {
    const next: SimNode[] = [];
    frontier.forEach((parent) => {
      const kids = (children.get(parent.id) || []).filter((k) => !placed.has(k.id));
      kids.forEach((k, ki) => {
        placed.add(k.id);
        const ang = (ki / Math.max(1, kids.length)) * Math.PI * 2 + (k.level % 4) * 0.6;
        const r = 85 + k.level * 62;
        k.x = parent.x + Math.cos(ang) * r;
        k.y = parent.y + Math.sin(ang) * r;
        next.push(k);
      });
    });
    frontier = next;
  }
  // 孤立节点散布在外圈
  sim.forEach((n, i) => {
    if (!placed.has(n.id)) {
      placed.add(n.id);
      const a = (i / sim.length) * Math.PI * 2;
      n.x = Math.cos(a) * 240;
      n.y = Math.sin(a) * 240;
    }
  });
  return { sim, edges, byId };
}

export default function KnowledgeGraph({
  data,
  selectedId,
  onSelect,
  showRelated = true,
}: {
  data: KbGraphResp | null;
  selectedId?: string;
  onSelect?: (n: KbGraphNode | null) => void;
  showRelated?: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const simRef = useRef<SimNode[]>([]);
  const edgesRef = useRef<SimEdge[]>([]);
  const byIdRef = useRef<Map<string, SimNode>>(new Map());
  const viewRef = useRef({ scale: 1, ox: 0, oy: 0 });
  const alphaRef = useRef(0);
  const rafRef = useRef<number>(0);
  const dragRef = useRef<{ node: SimNode | null; pan: boolean; px: number; py: number }>(
    { node: null, pan: false, px: 0, py: 0 }
  );
  const hoverRef = useRef<SimNode | null>(null);
  const sizeRef = useRef({ w: 800, h: 520, dpr: 1 });
  const [, setHoverTick] = useState(0); // 仅用于触发 tooltip 重渲染
  const [tip, setTip] = useState<{ x: number; y: number; n: SimNode } | null>(null);

  const neighbors = useCallback((id: string | undefined) => {
    if (!id) return null;
    const set = new Set<string>([id]);
    edgesRef.current.forEach((e) => {
      if (e.s.id === id) set.add(e.t.id);
      if (e.t.id === id) set.add(e.s.id);
    });
    return set;
  }, []);

  /* 单步力导向迭代 */
  const tickOnce = useCallback((a: number) => {
    const sim = simRef.current;
    const edges = edgesRef.current;
    const n = sim.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const ni = sim[i];
        const nj = sim[j];
        let dx = ni.x - nj.x;
        let dy = ni.y - nj.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) {
          dx = (Math.random() - 0.5) * 0.1;
          dy = (Math.random() - 0.5) * 0.1;
          d2 = dx * dx + dy * dy + 0.01;
        }
        const d = Math.sqrt(d2);
        const f = Math.min(REP / d2, 40);
        const fx = (f * dx) / d;
        const fy = (f * dy) / d;
        ni.vx += fx;
        ni.vy += fy;
        nj.vx -= fx;
        nj.vy -= fy;
      }
    }
    edges.forEach((e) => {
      const dx = e.t.x - e.s.x;
      const dy = e.t.y - e.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const L = e.kind === "hierarchy" ? 72 : 135;
      const k = e.kind === "hierarchy" ? SPRING_H : SPRING_R;
      const f = (d - L) * k;
      const ux = (dx / d) * f;
      const uy = (dy / d) * f;
      e.s.vx += ux;
      e.s.vy += uy;
      e.t.vx -= ux;
      e.t.vy -= uy;
    });
    sim.forEach((nd) => {
      nd.vx += -nd.x * GRAVITY;
      nd.vy += -nd.y * GRAVITY;
      nd.vx *= DAMP;
      nd.vy *= DAMP;
      if (nd.fx != null && nd.fy != null) {
        nd.x = nd.fx;
        nd.y = nd.fy;
      } else {
        nd.x += nd.vx * a;
        nd.y += nd.vy * a;
      }
    });
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { w, h, dpr } = sizeRef.current;
    const { scale, ox, oy } = viewRef.current;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(scale, scale);

    const hov = hoverRef.current;
    const hovSet = neighbors(hov?.id);
    const selSet = neighbors(selectedId);
    const focusSet = hov ? hovSet : selSet;

    // 边
    edgesRef.current.forEach((e) => {
      if (!showRelated && e.kind === "related") return;
      const connected = focusSet ? focusSet.has(e.s.id) && focusSet.has(e.t.id) : true;
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.lineTo(e.t.x, e.t.y);
      if (e.kind === "hierarchy") {
        ctx.strokeStyle = focusSet
          ? connected
            ? "rgba(180,190,230,0.55)"
            : "rgba(150,160,200,0.06)"
          : "rgba(160,170,210,0.16)";
        ctx.lineWidth = 1;
      } else {
        const wgt = Math.max(0, Math.min(1, e.weight));
        const al = focusSet ? (connected ? 0.15 + wgt * 0.6 : 0.03) : 0.12 + wgt * 0.45;
        const r = Math.round(120 + wgt * 120);
        const g = Math.round(190 - wgt * 70);
        ctx.strokeStyle = `rgba(${r},${g},255,${al})`;
        ctx.lineWidth = 0.8 + wgt * 1.6;
      }
      ctx.stroke();
    });

    // 节点
    simRef.current.forEach((n) => {
      const dim = focusSet && !focusSet.has(n.id);
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.globalAlpha = dim ? 0.22 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = n.id === selectedId ? "#ffffff" : "rgba(255,255,255,0.35)";
      if (n.id === selectedId) ctx.lineWidth = 2.4;
      ctx.stroke();
      const showLabel = n.type === "doc" || (focusSet ? focusSet.has(n.id) : n.level <= 1);
      if (showLabel && (scale > 0.85 || n.type === "doc")) {
        ctx.globalAlpha = dim ? 0.3 : 0.92;
        ctx.fillStyle = n.type === "doc" ? "#ffd0e6" : "#dff3ff";
        ctx.font = `${n.type === "doc" ? 12 : 10}px -apple-system, "PingFang SC", sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(n.label, n.x, n.y + n.r + 11);
        ctx.globalAlpha = 1;
      }
    });
    ctx.restore();
  }, [showRelated, selectedId, neighbors]);

  const fitView = useCallback(() => {
    const sim = simRef.current;
    if (!sim.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    sim.forEach((n) => {
      minX = Math.min(minX, n.x - n.r);
      minY = Math.min(minY, n.y - n.r);
      maxX = Math.max(maxX, n.x + n.r);
      maxY = Math.max(maxY, n.y + n.r);
    });
    const { w, h } = sizeRef.current;
    const pad = 56;
    const sw = maxX - minX || 1;
    const sh = maxY - minY || 1;
    const scale = Math.max(0.3, Math.min((w - pad * 2) / sw, (h - pad * 2) / sh, 1.05));
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    viewRef.current = {
      scale,
      ox: w / 2 - cx * scale,
      oy: h / 2 - cy * scale,
    };
    draw();
  }, [draw]);

  const loopRef = useRef<() => void>(() => {});
  loopRef.current = () => {
    const a = alphaRef.current;
    if (a <= 0.02) {
      rafRef.current = 0;
      return;
    }
    tickOnce(a);
    alphaRef.current = a * 0.985;
    draw();
    rafRef.current = requestAnimationFrame(() => loopRef.current());
  };

  const reheat = useCallback((alpha = 0.6) => {
    alphaRef.current = Math.max(alphaRef.current, alpha);
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(() => loopRef.current());
    }
  }, []);

  /* 数据变化：预热 → 适配视图 → 低能量微调 */
  useEffect(() => {
    if (!data || !data.nodes.length) {
      simRef.current = [];
      edgesRef.current = [];
      byIdRef.current = new Map();
      draw();
      return;
    }
    const { sim, edges, byId } = buildSim(data);
    simRef.current = sim;
    edgesRef.current = edges;
    byIdRef.current = byId;
    for (let i = 0; i < WARMUP_TICKS; i++) tickOnce(1);
    alphaRef.current = 0.3;
    fitView();
    draw();
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(() => loopRef.current());
    }
  }, [data, tickOnce, fitView, draw]);

  /* 尺寸自适应 */
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const ro = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      const w = wrap.clientWidth || 800;
      const h = wrap.clientHeight || 520;
      sizeRef.current = { w, h, dpr };
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      draw();
    });
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [draw]);

  // 坐标换算：屏幕 → 世界
  const toWorld = (sx: number, sy: number) => {
    const { ox, oy, scale } = viewRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = (sx - rect.left - ox) / scale;
    const y = (sy - rect.top - oy) / scale;
    return { x, y };
  };
  const nodeAt = (sx: number, sy: number): SimNode | null => {
    const { x, y } = toWorld(sx, sy);
    let best: SimNode | null = null;
    let bestD = Infinity;
    simRef.current.forEach((n) => {
      const dx = n.x - x;
      const dy = n.y - y;
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d < n.r + 4 && d < bestD) {
        best = n;
        bestD = d;
      }
    });
    return best;
  };

  const onMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (drag.node) {
      const { x, y } = toWorld(e.clientX, e.clientY);
      drag.node.fx = x;
      drag.node.fy = y;
      reheat(0.5);
      return;
    }
    if (drag.pan) {
      viewRef.current.ox += e.clientX - drag.px;
      viewRef.current.oy += e.clientY - drag.py;
      drag.px = e.clientX;
      drag.py = e.clientY;
      draw();
      return;
    }
    const node = nodeAt(e.clientX, e.clientY);
    hoverRef.current = node;
    setHoverTick((t) => t + 1);
    if (node) {
      setTip({ x: e.clientX, y: e.clientY, n: node });
    } else {
      setTip(null);
    }
  };
  const onDown = (e: React.PointerEvent) => {
    const node = nodeAt(e.clientX, e.clientY);
    if (node) {
      dragRef.current = { node, pan: false, px: e.clientX, py: e.clientY };
      node.fx = node.x;
      node.fy = node.y;
    } else {
      dragRef.current = { node: null, pan: true, px: e.clientX, py: e.clientY };
    }
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onUp = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (drag.node) {
      drag.node.fx = null;
      drag.node.fy = null;
      reheat(0.4);
      onSelect?.(drag.node.data);
    } else if (drag.pan && Math.abs(e.clientX - drag.px) < 3 && Math.abs(e.clientY - drag.py) < 3) {
      onSelect?.(null);
    }
    dragRef.current = { node: null, pan: false, px: 0, py: 0 };
  };
  const onWheel = (e: React.WheelEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const v = viewRef.current;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const ns = Math.max(0.25, Math.min(3.2, v.scale * factor));
    v.ox = mx - ((mx - v.ox) * ns) / v.scale;
    v.oy = my - ((my - v.oy) * ns) / v.scale;
    v.scale = ns;
    draw();
  };

  const relayout = useCallback(() => {
    // 打散后重新预热并适配
    simRef.current.forEach((n, i) => {
      const a = (i / Math.max(1, simRef.current.length)) * Math.PI * 2;
      n.x = Math.cos(a) * (120 + (i % 5) * 40);
      n.y = Math.sin(a) * (120 + (i % 5) * 40);
      n.vx = n.vy = 0;
      n.fx = n.fy = null;
    });
    for (let i = 0; i < WARMUP_TICKS; i++) tickOnce(1);
    alphaRef.current = 0.3;
    fitView();
    draw();
    reheat(0.3);
  }, [tickOnce, fitView, draw, reheat]);

  const legend = useMemo(
    () => [
      { c: DOC_COLOR, t: "文档" },
      { c: SEC_COLORS[0], t: "一级章节" },
      { c: SEC_COLORS[1], t: "二级章节" },
      { c: SEC_COLORS[2], t: "三级+章节" },
      { c: "rgba(120,200,255,0.8)", t: "关联边(语义/共现)" },
      { c: "rgba(160,170,210,0.6)", t: "结构边(层级)" },
    ],
    []
  );

  return (
    <div
      ref={wrapRef}
      className="relative h-[520px] w-full overflow-hidden rounded-2xl border border-white/10 bg-[#0b0b1c]"
      style={{ touchAction: "none" }}
    >
      <canvas
        ref={canvasRef}
        className="block h-full w-full cursor-grab active:cursor-grabbing"
        onPointerMove={onMove}
        onPointerDown={onDown}
        onPointerUp={onUp}
        onPointerLeave={() => {
          hoverRef.current = null;
          setTip(null);
        }}
        onWheel={onWheel}
      />
      {/* 工具栏 */}
      <div className="absolute right-3 top-3 flex gap-2">
        <button
          className="rounded-lg border border-white/10 bg-white/[0.06] px-2.5 py-1 text-[11px] text-white/70 transition-all hover:bg-white/15 hover:text-white"
          onClick={relayout}
          title="重新运行力导向布局"
        >
          ↻ 重新布局
        </button>
        <button
          className="rounded-lg border border-white/10 bg-white/[0.06] px-2.5 py-1 text-[11px] text-white/70 transition-all hover:bg-white/15 hover:text-white"
          onClick={fitView}
          title="缩放至适应屏幕"
        >
          ⤢ 适应屏幕
        </button>
      </div>
      {/* 图例 */}
      <div className="absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-[10.5px] text-white/70 backdrop-blur">
        {legend.map((l) => (
          <span key={l.t} className="flex items-center gap-1.5">
            <i className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: l.c }} />
            {l.t}
          </span>
        ))}
      </div>
      <div className="absolute bottom-3 right-3 rounded-xl border border-white/10 bg-black/30 px-3 py-1.5 text-[10.5px] text-white/50 backdrop-blur">
        悬停高亮关联 · 点击查看详情 · 滚轮缩放
      </div>
      {/* 悬停 tooltip */}
      {tip && (
        <div
          className="pointer-events-none absolute z-10 max-w-[260px] rounded-xl border border-white/15 bg-[#12122a]/95 px-3 py-2 text-[11px] text-white/85 shadow-xl backdrop-blur"
          style={{
            left: Math.min(tip.x + 14, (sizeRef.current.w || 800) - 270),
            top: Math.min(tip.y + 14, (sizeRef.current.h || 520) - 90),
          }}
        >
          <div className="mb-0.5 font-medium text-white">{tip.n.label}</div>
          <div className="text-white/55">
            {tip.n.type === "doc" ? "文档" : `章节 · 层级 ${tip.n.level}`} · {tip.n.chunks} 段
            {tip.n.rel > 0 && ` · ${tip.n.rel} 条关联`}
          </div>
          {tip.n.data.full_path !== tip.n.label && (
            <div className="mt-0.5 line-clamp-2 text-white/40">{tip.n.data.full_path}</div>
          )}
        </div>
      )}
    </div>
  );
}
