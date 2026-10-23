import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ButtonHTMLAttributes,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { KbGraphNode, KbGraphResp } from "../../lib/types";

/* 知识思维图谱 —— 自包含 canvas 力导向图（零第三方依赖）
 * - 节点：文档(doc) / 章节(sec)，按章节层级着色、按段数与关联数定大小
 * - 结构边(hierarchy)：文档→章→节 的树形骨架
 * - 关联边(related)：章节间语义相似 / 关键词共现（颜色由权重映射 青→紫）
 * - 布局：预热后持续动画，支持暂停/继续、速度、重排、适应屏幕
 * - 交互：悬停高亮、点击选中、拖拽节点、空白平移、滚轮/双指缩放、双击居中
 */

const DOC_COLOR = "#ff7eb6";
const SEC_COLORS = ["#56d6ff", "#8b9bff", "#b98bff", "#d68bff"];

const REP = 1500;
const DAMP = 0.9;
const GRAVITY = 0.004;
const SPRING_H = 0.05;
const SPRING_R = 0.018;
const WARMUP_TICKS = 220;
const MIN_SCALE = 0.2;
const MAX_SCALE = 4;
const CLICK_SLOP = 4;
const SPEEDS = [0.5, 1, 1.5, 2] as const;

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function easeOutCubic(v: number): number {
  return 1 - Math.pow(1 - v, 3);
}

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

function nodeLabel(n: SimNode): string {
  const raw = n.type === "doc"
    ? n.label.replace(/\.(docx?|pdf|xlsx?|pptx?|txt|md|png|jpe?g)$/i, "")
    : n.label;
  const max = n.type === "doc" ? 16 : 14;
  return raw.length > max ? `${raw.slice(0, max)}…` : raw;
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

interface View {
  scale: number;
  ox: number;
  oy: number;
}

interface DragState {
  mode: "none" | "node" | "pan";
  node: SimNode | null;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  moved: boolean;
}

interface PointerPoint {
  x: number;
  y: number;
}

interface PinchState {
  dist: number;
  scale: number;
  wx: number;
  wy: number;
}

interface Tip {
  x: number;
  y: number;
  node: SimNode;
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
      label: n.label || n.full_path || n.id,
      level: n.level || 0,
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

  const docs = sim.filter((n) => n.type === "doc");
  const parentOf = new Map<string, string>();
  edges.forEach((e) => {
    if (e.kind !== "hierarchy") return;
    const parent = e.s.level <= e.t.level ? e.s : e.t;
    const child = parent === e.s ? e.t : e.s;
    if (parent.level < child.level) parentOf.set(child.id, parent.id);
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
    if (docs.length === 1) {
      d.x = 0;
      d.y = 0;
    } else if (docs.length <= 3) {
      d.x = (di - (docs.length - 1) / 2) * 190;
      d.y = 0;
    } else {
      const a = -Math.PI / 2 + (di / docs.length) * Math.PI * 2;
      const radius = 145 + Math.sqrt(docs.length) * 48;
      d.x = Math.cos(a) * radius;
      d.y = Math.sin(a) * radius;
    }
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
        const r = 88 + k.level * 62;
        k.x = parent.x + Math.cos(ang) * r;
        k.y = parent.y + Math.sin(ang) * r;
        next.push(k);
      });
    });
    frontier = next;
  }

  sim.forEach((n, i) => {
    if (!placed.has(n.id)) {
      placed.add(n.id);
      const a = (i / Math.max(1, sim.length)) * Math.PI * 2;
      n.x = Math.cos(a) * 250;
      n.y = Math.sin(a) * 250;
    }
  });

  return { sim, edges, byId };
}

function GraphButton({
  active = false,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={
        "inline-flex h-7 items-center justify-center gap-1 rounded-lg border px-2 text-[11px] font-medium transition-colors " +
        (active
          ? "border-[var(--border-accent-soft)] bg-white/15 text-white"
          : "border-white/10 bg-white/[0.04] text-white/60 hover:bg-white/10 hover:text-white/90") +
        " disabled:cursor-not-allowed disabled:opacity-40 " +
        className
      }
      {...rest}
    />
  );
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
  const rootRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const searchWrapRef = useRef<HTMLDivElement | null>(null);
  const simRef = useRef<SimNode[]>([]);
  const edgesRef = useRef<SimEdge[]>([]);
  const byIdRef = useRef<Map<string, SimNode>>(new Map());
  const viewRef = useRef<View>({ scale: 1, ox: 0, oy: 0 });
  const alphaRef = useRef(0);
  const rafRef = useRef(0);
  const camRafRef = useRef(0);
  const runningRef = useRef(false);
  const speedRef = useRef(1);
  const lastFrameRef = useRef(0);
  const sizeRef = useRef({ w: 800, h: 520, dpr: 1 });
  const sizeReadyRef = useRef(false);
  const dragRef = useRef<DragState>({
    mode: "none",
    node: null,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
    moved: false,
  });
  const pointersRef = useRef<Map<number, PointerPoint>>(new Map());
  const pinchRef = useRef<PinchState | null>(null);
  const hoverRef = useRef<SimNode | null>(null);

  const [relatedOn, setRelatedOn] = useState(showRelated);
  const [hierarchyOn, setHierarchyOn] = useState(true);
  const [labelsOn, setLabelsOn] = useState(true);
  const [animating, setAnimating] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [cursor, setCursor] = useState<"grab" | "pointer" | "grabbing">("grab");
  const [tip, setTip] = useState<Tip | null>(null);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeResult, setActiveResult] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    setRelatedOn(showRelated);
  }, [showRelated]);

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [] as KbGraphNode[];
    const words = q.split(/\s+/).filter(Boolean);
    return (data?.nodes ?? [])
      .filter((n) => {
        const hay = `${n.label} ${n.full_path} ${n.sample || ""} ${n.type === "doc" ? "文档" : "章节"}`.toLowerCase();
        return words.every((w) => hay.includes(w));
      })
      .slice(0, 8);
  }, [data, query]);

  useEffect(() => {
    setActiveResult(0);
  }, [query]);

  const neighbors = useCallback((id: string | undefined, showH: boolean, showR: boolean) => {
    if (!id) return null;
    const set = new Set<string>([id]);
    edgesRef.current.forEach((e) => {
      if (e.kind === "hierarchy" && !showH) return;
      if (e.kind === "related" && !showR) return;
      if (e.s.id === id) set.add(e.t.id);
      if (e.t.id === id) set.add(e.s.id);
    });
    return set;
  }, []);

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
      const speed = Math.hypot(nd.vx, nd.vy);
      if (speed > 18) {
        nd.vx = (nd.vx / speed) * 18;
        nd.vy = (nd.vy / speed) * 18;
      }
      if (nd.fx != null && nd.fy != null) {
        nd.x = nd.fx;
        nd.y = nd.fy;
      } else {
        const dx = nd.vx * a;
        const dy = nd.vy * a;
        const step = Math.hypot(dx, dy);
        const scale = step > 26 ? 26 / step : 1;
        nd.x += dx * scale;
        nd.y += dy * scale;
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
    const sprite = 1 / Math.max(0.0001, scale);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(scale, scale);

    const hov = hoverRef.current;
    const hovSet = neighbors(hov?.id, hierarchyOn, relatedOn);
    const selSet = neighbors(selectedId, hierarchyOn, relatedOn);
    const focusSet = hov ? hovSet : selSet;

    edgesRef.current.forEach((e) => {
      if (e.kind === "hierarchy" && !hierarchyOn) return;
      if (e.kind === "related" && !relatedOn) return;
      const connected = focusSet ? focusSet.has(e.s.id) && focusSet.has(e.t.id) : true;
      const touches = focusSet ? focusSet.has(e.s.id) || focusSet.has(e.t.id) : false;
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.lineTo(e.t.x, e.t.y);
      if (e.kind === "hierarchy") {
        ctx.strokeStyle = focusSet
          ? connected
            ? "rgba(190,200,240,0.62)"
            : "rgba(150,160,200,0.06)"
          : "rgba(160,170,210,0.18)";
        ctx.lineWidth = (connected ? 1.35 : 1) * sprite;
      } else {
        const wgt = clamp(e.weight, 0, 1);
        const alpha = focusSet ? (connected ? 0.22 + wgt * 0.62 : 0.025) : 0.12 + wgt * 0.45;
        const r = Math.round(120 + wgt * 120);
        const g = Math.round(190 - wgt * 70);
        ctx.strokeStyle = `rgba(${r},${g},255,${alpha})`;
        ctx.lineWidth = (touches ? 1.1 : 0.8) * sprite + wgt * 1.5 * sprite;
      }
      ctx.stroke();
    });

    simRef.current.forEach((n) => {
      const dim = focusSet && !focusSet.has(n.id);
      if (n.id === selectedId) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 7 * sprite, 0, Math.PI * 2);
        ctx.globalAlpha = 0.16;
        ctx.fillStyle = n.color;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      if (n.id === hov?.id) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 4 * sprite, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(220,245,255,0.42)";
        ctx.lineWidth = 1.4 * sprite;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.globalAlpha = dim ? 0.22 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.lineWidth = (n.id === selectedId ? 2.2 : 1.1) * sprite;
      ctx.strokeStyle = n.id === selectedId ? "#ffffff" : "rgba(255,255,255,0.35)";
      ctx.stroke();
    });

    if (labelsOn) {
      const force = new Set<string>();
      if (selectedId) force.add(selectedId);
      if (hov?.id) force.add(hov.id);
      const candidates = simRef.current
        .filter((n) => {
          if (n.type === "doc") return true;
          if (force.has(n.id)) return true;
          if (focusSet && focusSet.has(n.id)) return true;
          return n.level <= 1 && scale >= 0.55;
        })
        .sort((a, b) => {
          const rank = (n: SimNode) =>
            n.id === selectedId ? 0 : n.id === hov?.id ? 1 : n.type === "doc" ? 2 : n.level;
          return rank(a) - rank(b);
        })
        .slice(0, 140);
      const boxes: { x1: number; y1: number; x2: number; y2: number }[] = [];
      candidates.forEach((n) => {
        const text = nodeLabel(n);
        const fontPx = n.type === "doc" ? 12 : 10;
        const fontWorld = fontPx * sprite;
        ctx.font = `${fontWorld}px -apple-system, "PingFang SC", sans-serif`;
        const tw = ctx.measureText(text).width;
        const x = n.x;
        const y = n.y + n.r + 9 * sprite;
        const pad = 2 * sprite;
        const box = { x1: x - tw / 2 - pad, y1: y - fontWorld, x2: x + tw / 2 + pad, y2: y + fontWorld * 0.35 };
        if (boxes.some((b) => !(box.x1 > b.x2 || box.x2 < b.x1 || box.y1 > b.y2 || box.y2 < b.y1))) {
          return;
        }
        boxes.push(box);
        const dim = focusSet && !focusSet.has(n.id);
        ctx.globalAlpha = dim ? 0.3 : 0.92;
        ctx.fillStyle = n.type === "doc" ? "#ffd0e6" : "#dff3ff";
        ctx.textAlign = "center";
        ctx.fillText(text, x, y);
        ctx.globalAlpha = 1;
      });
    }

    ctx.restore();
  }, [hierarchyOn, labelsOn, neighbors, relatedOn, selectedId]);

  const drawRef = useRef(draw);
  drawRef.current = draw;

  useEffect(() => {
    drawRef.current();
  }, [draw]);

  const animateViewTo = useCallback((target: View, duration = 300) => {
    if (camRafRef.current) cancelAnimationFrame(camRafRef.current);
    const from = { ...viewRef.current };
    const started = performance.now();
    const step = (now: number) => {
      const k = clamp((now - started) / duration, 0, 1);
      const e = easeOutCubic(k);
      viewRef.current = {
        scale: from.scale + (target.scale - from.scale) * e,
        ox: from.ox + (target.ox - from.ox) * e,
        oy: from.oy + (target.oy - from.oy) * e,
      };
      drawRef.current();
      if (k < 1) {
        camRafRef.current = requestAnimationFrame(step);
      } else {
        camRafRef.current = 0;
        setZoom(target.scale);
      }
    };
    camRafRef.current = requestAnimationFrame(step);
  }, []);

  const fitView = useCallback(
    (animated = true) => {
      const sim = simRef.current;
      if (!sim.length) return;
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      sim.forEach((n) => {
        minX = Math.min(minX, n.x - n.r);
        minY = Math.min(minY, n.y - n.r);
        maxX = Math.max(maxX, n.x + n.r);
        maxY = Math.max(maxY, n.y + n.r);
      });
      const { w, h } = sizeRef.current;
      const pad = 54;
      const sw = maxX - minX || 1;
      const sh = maxY - minY || 1;
      const scale = clamp(
        Math.min((w - pad * 2) / sw, (h - pad * 2) / sh, 1.15),
        MIN_SCALE,
        1.15
      );
      const cx = (minX + maxX) / 2;
      const cy = (minY + maxY) / 2;
      const target = {
        scale,
        ox: w / 2 - cx * scale,
        oy: h / 2 - cy * scale,
      };
      if (animated) {
        animateViewTo(target, 320);
      } else {
        viewRef.current = target;
        setZoom(scale);
        drawRef.current();
      }
    },
    [animateViewTo]
  );

  const focusNode = useCallback(
    (node: SimNode) => {
      const { w, h } = sizeRef.current;
      const scale = clamp(Math.max(1.12, Math.min(2.2, viewRef.current.scale)), MIN_SCALE, 2.2);
      animateViewTo(
        {
          scale,
          ox: w / 2 - node.x * scale,
          oy: h / 2 - node.y * scale,
        },
        340
      );
    },
    [animateViewTo]
  );

  const ensureNodeVisible = useCallback(
    (node: SimNode) => {
      const { w, h } = sizeRef.current;
      const v = viewRef.current;
      const sx = node.x * v.scale + v.ox;
      const sy = node.y * v.scale + v.oy;
      const margin = Math.min(72, w * 0.12, h * 0.12);
      if (sx < margin || sx > w - margin || sy < margin || sy > h - margin) {
        const scale = clamp(Math.max(v.scale, 0.9), MIN_SCALE, 1.4);
        animateViewTo(
          {
            scale,
            ox: w / 2 - node.x * scale,
            oy: h / 2 - node.y * scale,
          },
          320
        );
      }
    },
    [animateViewTo]
  );

  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number, animated = false) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      if (camRafRef.current) {
        cancelAnimationFrame(camRafRef.current);
        camRafRef.current = 0;
      }
      const rect = canvas.getBoundingClientRect();
      const mx = clientX - rect.left;
      const my = clientY - rect.top;
      const v = viewRef.current;
      const ns = clamp(v.scale * factor, MIN_SCALE, MAX_SCALE);
      if (Math.abs(ns - v.scale) < 0.0001) return;
      const target = {
        scale: ns,
        ox: mx - ((mx - v.ox) * ns) / v.scale,
        oy: my - ((my - v.oy) * ns) / v.scale,
      };
      if (animated) {
        animateViewTo(target, 180);
      } else {
        viewRef.current = target;
        setZoom(ns);
        drawRef.current();
      }
    },
    [animateViewTo]
  );

  const zoomBy = useCallback(
    (factor: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor, true);
    },
    [zoomAt]
  );

  const startLoop = useCallback(() => {
    if (!simRef.current.length) return;
    runningRef.current = true;
    setAnimating(true);
    if (rafRef.current) return;
    lastFrameRef.current = 0;
    const frame = () => {
      rafRef.current = 0;
      if (!runningRef.current) return;
      const a = alphaRef.current;
      if (a <= 0.015) {
        runningRef.current = false;
        setAnimating(false);
        drawRef.current();
        return;
      }
      const now = performance.now();
      const prev = lastFrameRef.current || now;
      lastFrameRef.current = now;
      const dt = clamp((now - prev) / 16.667, 0.35, 2.5);
      const s = speedRef.current;
      tickOnce(Math.min(1.8, a * s * dt));
      alphaRef.current = a * Math.pow(0.985, s * dt);
      drawRef.current();
      rafRef.current = requestAnimationFrame(frame);
    };
    rafRef.current = requestAnimationFrame(frame);
  }, [tickOnce]);

  const pauseAnimation = useCallback(() => {
    runningRef.current = false;
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = 0;
    setAnimating(false);
    drawRef.current();
  }, []);

  const resumeAnimation = useCallback(() => {
    if (!simRef.current.length) return;
    if (alphaRef.current < 0.08) alphaRef.current = 0.35;
    startLoop();
  }, [startLoop]);

  const reheat = useCallback(
    (alpha = 0.35) => {
      alphaRef.current = Math.max(alphaRef.current, alpha);
      startLoop();
    },
    [startLoop]
  );

  const relayout = useCallback(() => {
    simRef.current.forEach((n, i) => {
      const a = (i / Math.max(1, simRef.current.length)) * Math.PI * 2 + (i % 3) * 0.29;
      const radius = 100 + (i % 7) * 34;
      n.x = Math.cos(a) * radius;
      n.y = Math.sin(a) * radius;
      n.vx = 0;
      n.vy = 0;
      n.fx = null;
      n.fy = null;
    });
    const warmup = Math.max(40, Math.min(WARMUP_TICKS, Math.round(48000 / Math.max(1, simRef.current.length))));
    for (let i = 0; i < warmup; i++) tickOnce(1);
    alphaRef.current = 0.48;
    fitView(true);
    startLoop();
  }, [fitView, startLoop, tickOnce]);

  const toggleFullscreen = useCallback(async () => {
    const el = rootRef.current;
    if (!el) return;
    if (document.fullscreenElement === el) {
      await document.exitFullscreen?.().catch(() => {});
      setFullscreen(false);
      return;
    }
    setFullscreen(true);
    if (el.requestFullscreen) {
      await el.requestFullscreen().catch(() => {});
    }
  }, []);

  const pickResult = useCallback(
    (n: KbGraphNode) => {
      const node = byIdRef.current.get(n.id);
      onSelect?.(n);
      if (node) focusNode(node);
      setQuery(n.label);
      setSearchOpen(false);
      setActiveResult(0);
    },
    [focusNode, onSelect]
  );

  useEffect(() => {
    if (!data || !data.nodes.length) {
      runningRef.current = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
      alphaRef.current = 0;
      simRef.current = [];
      edgesRef.current = [];
      byIdRef.current = new Map();
      hoverRef.current = null;
      setTip(null);
      setAnimating(false);
      drawRef.current();
      return;
    }
    const { sim, edges, byId } = buildSim(data);
    simRef.current = sim;
    edgesRef.current = edges;
    byIdRef.current = byId;
    pointersRef.current.clear();
    pinchRef.current = null;
    dragRef.current = { mode: "none", node: null, startX: 0, startY: 0, lastX: 0, lastY: 0, moved: false };
    hoverRef.current = null;
    setTip(null);
    setCursor("grab");
    const warmup = Math.max(40, Math.min(WARMUP_TICKS, Math.round(48000 / Math.max(1, sim.length))));
    for (let i = 0; i < warmup; i++) tickOnce(1);
    alphaRef.current = 0.55;
    fitView(false);
    startLoop();
  }, [data, fitView, startLoop, tickOnce]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const ready = wrap.clientWidth > 0 && wrap.clientHeight > 0;
      const w = wrap.clientWidth || 800;
      const h = wrap.clientHeight || 520;
      const prev = sizeRef.current;
      if (w === prev.w && h === prev.h && dpr === prev.dpr) return;
      if (sizeReadyRef.current) {
        viewRef.current.ox += (w - prev.w) / 2;
        viewRef.current.oy += (h - prev.h) / 2;
      }
      sizeRef.current = { w, h, dpr };
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      if (ready && !sizeReadyRef.current) {
        sizeReadyRef.current = true;
        fitView(false);
      } else {
        drawRef.current();
      }
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [fitView]);

  useEffect(() => {
    if (!selectedId) return;
    const node = byIdRef.current.get(selectedId);
    if (node) ensureNodeVisible(node);
  }, [ensureNodeVisible, selectedId]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? sizeRef.current.h : 1;
      const factor = Math.exp(-e.deltaY * unit * 0.0015);
      zoomAt(e.clientX, e.clientY, factor);
    };
    canvas.addEventListener("wheel", handler, { passive: false });
    return () => canvas.removeEventListener("wheel", handler);
  }, [zoomAt]);

  useEffect(() => {
    if (!searchOpen) return;
    const onPointer = (e: PointerEvent) => {
      const el = searchWrapRef.current;
      if (el && !el.contains(e.target as Node)) setSearchOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [searchOpen]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === rootRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => {
    if (!fullscreen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.fullscreenElement) setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [fullscreen]);

  useEffect(
    () => () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (camRafRef.current) cancelAnimationFrame(camRafRef.current);
    },
    []
  );

  const toWorld = (sx: number, sy: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const { ox, oy, scale } = viewRef.current;
    const rect = canvas.getBoundingClientRect();
    return {
      x: (sx - rect.left - ox) / scale,
      y: (sy - rect.top - oy) / scale,
    };
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

  const clearHover = () => {
    hoverRef.current = null;
    setTip(null);
    setCursor("grab");
    drawRef.current();
  };

  const onMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const p = pointersRef.current.get(e.pointerId);
    if (p) {
      p.x = e.clientX;
      p.y = e.clientY;
    }

    if (pinchRef.current && pointersRef.current.size >= 2) {
      const [a, b] = [...pointersRef.current.values()];
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const mx = (a.x + b.x) / 2 - rect.left;
      const my = (a.y + b.y) / 2 - rect.top;
      const ns = clamp(pinchRef.current.scale * (dist / Math.max(1, pinchRef.current.dist)), MIN_SCALE, MAX_SCALE);
      viewRef.current = {
        scale: ns,
        ox: mx - pinchRef.current.wx * ns,
        oy: my - pinchRef.current.wy * ns,
      };
      setZoom(ns);
      setTip(null);
      drawRef.current();
      return;
    }

    const drag = dragRef.current;
    if (drag.mode === "node" && drag.node) {
      const { x, y } = toWorld(e.clientX, e.clientY);
      if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) > CLICK_SLOP) {
        drag.moved = true;
      }
      drag.node.fx = x;
      drag.node.fy = y;
      setTip(null);
      reheat(0.28);
      drawRef.current();
      return;
    }

    if (drag.mode === "pan") {
      if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) <= CLICK_SLOP) {
        return;
      }
      drag.moved = true;
      viewRef.current.ox += e.clientX - drag.lastX;
      viewRef.current.oy += e.clientY - drag.lastY;
      drag.lastX = e.clientX;
      drag.lastY = e.clientY;
      setCursor("grabbing");
      drawRef.current();
      return;
    }

    const node = nodeAt(e.clientX, e.clientY);
    if (node?.id !== hoverRef.current?.id) {
      hoverRef.current = node;
      drawRef.current();
    }
    setCursor(node ? "pointer" : "grab");
    if (node && !drag.node) {
      const rect = wrapRef.current?.getBoundingClientRect();
      if (rect) {
        setTip({ x: e.clientX - rect.left, y: e.clientY - rect.top, node });
      }
    } else {
      setTip(null);
    }
  };

  const onDown = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    rootRef.current?.focus({ preventScroll: true });
    canvas.setPointerCapture?.(e.pointerId);
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    setTip(null);

    if (pointersRef.current.size === 2) {
      const [a, b] = [...pointersRef.current.values()];
      const rect = canvas.getBoundingClientRect();
      const mx = (a.x + b.x) / 2 - rect.left;
      const my = (a.y + b.y) / 2 - rect.top;
      const v = viewRef.current;
      pinchRef.current = {
        dist: Math.hypot(a.x - b.x, a.y - b.y),
        scale: v.scale,
        wx: (mx - v.ox) / v.scale,
        wy: (my - v.oy) / v.scale,
      };
      const drag = dragRef.current;
      if (drag.node) {
        drag.node.fx = null;
        drag.node.fy = null;
      }
      dragRef.current = { mode: "none", node: null, startX: 0, startY: 0, lastX: 0, lastY: 0, moved: true };
      hoverRef.current = null;
      setCursor("grabbing");
      drawRef.current();
      return;
    }

    if (pointersRef.current.size > 2) return;

    const node = nodeAt(e.clientX, e.clientY);
    dragRef.current = {
      mode: node ? "node" : "pan",
      node,
      startX: e.clientX,
      startY: e.clientY,
      lastX: e.clientX,
      lastY: e.clientY,
      moved: false,
    };
    if (node) {
      node.fx = node.x;
      node.fy = node.y;
      hoverRef.current = node;
    } else {
      hoverRef.current = null;
    }
    setCursor("grabbing");
    drawRef.current();
  };

  const finishPointer = (e: ReactPointerEvent<HTMLCanvasElement>, cancelled: boolean) => {
    const canvas = canvasRef.current;
    const drag = dragRef.current;
    pointersRef.current.delete(e.pointerId);

    if (pinchRef.current) {
      pinchRef.current = null;
      if (pointersRef.current.size === 1) {
        const remaining = [...pointersRef.current.values()][0];
        dragRef.current = {
          mode: "pan",
          node: null,
          startX: remaining.x,
          startY: remaining.y,
          lastX: remaining.x,
          lastY: remaining.y,
          moved: true,
        };
        setCursor("grabbing");
      } else {
        dragRef.current = { mode: "none", node: null, startX: 0, startY: 0, lastX: 0, lastY: 0, moved: false };
        setCursor("grab");
      }
      drawRef.current();
      return;
    }

    if (drag.mode === "node" && drag.node) {
      drag.node.fx = null;
      drag.node.fy = null;
      if (!cancelled && !drag.moved) onSelect?.(drag.node.data);
      reheat(0.28);
    } else if (drag.mode === "pan" && !cancelled && !drag.moved) {
      onSelect?.(null);
    }

    dragRef.current = { mode: "none", node: null, startX: 0, startY: 0, lastX: 0, lastY: 0, moved: false };
    if (canvas?.hasPointerCapture?.(e.pointerId)) {
      canvas.releasePointerCapture(e.pointerId);
    }
    const node = nodeAt(e.clientX, e.clientY);
    hoverRef.current = cancelled ? null : node;
    setTip(null);
    setCursor(node ? "pointer" : "grab");
    drawRef.current();
  };

  const onUp = (e: ReactPointerEvent<HTMLCanvasElement>) => finishPointer(e, false);
  const onCancel = (e: ReactPointerEvent<HTMLCanvasElement>) => finishPointer(e, true);

  const onDoubleClick = (e: ReactMouseEvent<HTMLCanvasElement>) => {
    const node = nodeAt(e.clientX, e.clientY);
    if (node) {
      onSelect?.(node.data);
      focusNode(node);
    }
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return;
    if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      zoomBy(1.2);
    } else if (e.key === "-") {
      e.preventDefault();
      zoomBy(1 / 1.2);
    } else if (e.key === "0" || e.key.toLowerCase() === "f") {
      e.preventDefault();
      fitView(true);
    } else if (e.key.toLowerCase() === "r") {
      e.preventDefault();
      relayout();
    } else if (e.key === " ") {
      e.preventDefault();
      if (animating) pauseAnimation();
      else resumeAnimation();
    } else if (e.key === "Escape") {
      onSelect?.(null);
      setQuery("");
      setSearchOpen(false);
    }
  };

  const legend = useMemo(
    () => [
      { c: DOC_COLOR, t: "文档", on: true },
      { c: SEC_COLORS[0], t: "一级章节", on: true },
      { c: SEC_COLORS[1], t: "二级章节", on: true },
      { c: SEC_COLORS[2], t: "三级+章节", on: true },
      { c: "rgba(120,200,255,0.8)", t: "关联边", on: relatedOn },
      { c: "rgba(160,170,210,0.6)", t: "结构边", on: hierarchyOn },
    ],
    [hierarchyOn, relatedOn]
  );

  const activeNode = results[Math.min(activeResult, Math.max(0, results.length - 1))];
  const zoomPct = Math.round(zoom * 100);

  return (
    <div
      ref={rootRef}
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label="知识思维图谱，支持缩放、平移、搜索和动画控制"
      className={
        "flex flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0b0b1c] outline-none " +
        (fullscreen ? "fixed inset-0 z-[100] h-screen w-screen rounded-none border-0" : "")
      }
    >
      <div className="relative z-20 flex flex-wrap items-center gap-1.5 border-b border-white/10 bg-white/[0.025] px-2.5 py-2">
        <div ref={searchWrapRef} className="relative">
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSearchOpen(true);
            }}
            onFocus={() => {
              if (query.trim()) setSearchOpen(true);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                if (results.length) setActiveResult((v) => Math.min(v + 1, results.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                if (results.length) setActiveResult((v) => Math.max(v - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                if (activeNode) pickResult(activeNode);
              } else if (e.key === "Escape") {
                e.preventDefault();
                setQuery("");
                setSearchOpen(false);
                e.currentTarget.blur();
              }
            }}
            placeholder="搜索节点…"
            className="h-7 w-[180px] rounded-lg border border-white/10 bg-black/20 px-2.5 text-[11px] text-white/80 outline-none transition-colors placeholder:text-white/30 focus:border-[var(--border-accent-soft)] sm:w-[215px]"
            aria-label="搜索图谱节点"
          />
          {searchOpen && query.trim() && (
            <div className="absolute left-0 top-[calc(100%+4px)] z-30 max-h-[250px] w-[290px] overflow-y-auto rounded-xl border border-white/15 bg-[#12122a]/98 p-1 shadow-2xl backdrop-blur">
              {results.length ? (
                results.map((n, i) => (
                  <button
                    key={n.id}
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pickResult(n)}
                    className={
                      "block w-full rounded-lg px-2 py-1.5 text-left transition-colors " +
                      (i === activeResult ? "bg-white/15" : "hover:bg-white/10")
                    }
                  >
                    <span className="block truncate text-[11px] text-white/85">{n.label}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-white/35">
                      {n.type === "doc" ? "文档" : `章节 · L${n.level}`} · {n.full_path}
                    </span>
                  </button>
                ))
              ) : (
                <div className="px-2 py-2 text-[11px] text-white/35">未找到匹配节点</div>
              )}
            </div>
          )}
        </div>

        <span className="mx-0.5 h-4 w-px bg-white/10" />
        <GraphButton
          active={hierarchyOn}
          onClick={() => setHierarchyOn((v) => !v)}
          aria-pressed={hierarchyOn}
          title="显示/隐藏文档层级结构边"
        >
          结构
        </GraphButton>
        <GraphButton
          active={relatedOn}
          onClick={() => setRelatedOn((v) => !v)}
          aria-pressed={relatedOn}
          title="显示/隐藏语义与关键词关联边"
        >
          关联
        </GraphButton>
        <GraphButton
          active={labelsOn}
          onClick={() => setLabelsOn((v) => !v)}
          aria-pressed={labelsOn}
          title="显示/隐藏节点标签"
        >
          标签
        </GraphButton>

        <span className="mx-0.5 h-4 w-px bg-white/10" />
        <GraphButton
          onClick={animating ? pauseAnimation : resumeAnimation}
          title={animating ? "暂停力导向动画（空格）" : "继续力导向动画（空格）"}
        >
          {animating ? "暂停" : "继续"}
        </GraphButton>
        <select
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="h-7 rounded-lg border border-white/10 bg-black/20 px-1 text-[11px] text-white/65 outline-none focus:border-[var(--border-accent-soft)]"
          title="布局动画速度"
          aria-label="布局动画速度"
        >
          {SPEEDS.map((s) => (
            <option key={s} value={s} className="bg-[#12122a]">
              {s}×
            </option>
          ))}
        </select>

        <span className="mx-0.5 h-4 w-px bg-white/10" />
        <div className="flex h-7 items-center rounded-lg border border-white/10 bg-white/[0.04]">
          <button
            type="button"
            onClick={() => zoomBy(1 / 1.2)}
            className="h-full px-2 text-[13px] text-white/60 transition-colors hover:text-white"
            title="缩小（-）"
            aria-label="缩小图谱"
          >
            −
          </button>
          <button
            type="button"
            onClick={() => fitView(true)}
            className="min-w-[42px] border-x border-white/10 px-1.5 text-[10.5px] text-white/55 transition-colors hover:text-white"
            title="适应屏幕（F）"
          >
            {zoomPct}%
          </button>
          <button
            type="button"
            onClick={() => zoomBy(1.2)}
            className="h-full px-2 text-[13px] text-white/60 transition-colors hover:text-white"
            title="放大（+）"
            aria-label="放大图谱"
          >
            +
          </button>
        </div>
        <GraphButton onClick={() => fitView(true)} title="缩放至适应屏幕（F）">
          适应
        </GraphButton>
        <GraphButton onClick={relayout} title="重新运行动画布局（R）">
          重排
        </GraphButton>
        <GraphButton onClick={toggleFullscreen} title={fullscreen ? "退出全屏" : "全屏查看"}>
          {fullscreen ? "退出" : "全屏"}
        </GraphButton>
      </div>

      <div
        ref={wrapRef}
        className={"relative w-full " + (fullscreen ? "min-h-0 flex-1" : "h-[520px]")}
        style={{ touchAction: "none" }}
      >
        <canvas
          ref={canvasRef}
          className="block h-full w-full"
          style={{ cursor }}
          onPointerMove={onMove}
          onPointerDown={onDown}
          onPointerUp={onUp}
          onPointerCancel={onCancel}
          onPointerLeave={() => {
            if (dragRef.current.mode !== "none" || pointersRef.current.size) return;
            clearHover();
          }}
          onDoubleClick={onDoubleClick}
        />

        <div className="pointer-events-none absolute bottom-3 left-3 flex max-w-[calc(100%-24px)] flex-wrap gap-x-3 gap-y-1 rounded-xl border border-white/10 bg-black/35 px-3 py-2 text-[10.5px] text-white/65 backdrop-blur">
          {legend.map((l) => (
            <span key={l.t} className={"flex items-center gap-1.5 " + (l.on ? "" : "opacity-35 line-through")}>
              <i className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: l.c }} />
              {l.t}
            </span>
          ))}
        </div>

        <div className="pointer-events-none absolute bottom-3 right-3 hidden rounded-xl border border-white/10 bg-black/35 px-3 py-1.5 text-[10.5px] text-white/40 backdrop-blur sm:block">
          拖动平移 · 滚轮/双指缩放 · 双击居中 · 空格暂停 · Esc 取消
        </div>

        {tip && (
          <div
            className="pointer-events-none absolute z-10 max-w-[260px] rounded-xl border border-white/15 bg-[#12122a]/95 px-3 py-2 text-[11px] text-white/85 shadow-xl backdrop-blur"
            style={{
              left: Math.max(8, Math.min(tip.x + 14, (sizeRef.current.w || 800) - 272)),
              top: Math.max(8, Math.min(tip.y + 14, (sizeRef.current.h || 520) - 92)),
            }}
          >
            <div className="mb-0.5 font-medium text-white">{tip.node.label}</div>
            <div className="text-white/55">
              {tip.node.type === "doc" ? "文档" : `章节 · 层级 ${tip.node.level}`} · {tip.node.chunks} 段
              {tip.node.rel > 0 && ` · ${tip.node.rel} 条关联`}
            </div>
            {tip.node.data.full_path !== tip.node.label && (
              <div className="mt-0.5 line-clamp-2 text-white/40">{tip.node.data.full_path}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
