/* ============================================================
   主题引擎：配色方案（scheme）+ 自定义强调色（accent）
   - 配色方案：holographic（全息渐变，默认）/ dark（深色收敛）
   - 强调色：仅驱动光效 / 高亮 accent 派生变量，不破坏宇宙基底
   - 应用方式：<html data-scheme> + --accent-rgb 等 CSS 变量注入
   ============================================================ */

export type ThemeScheme = "holographic" | "dark";

export const THEME_SCHEMES: Record<ThemeScheme, { name: string; desc: string; defaultAccent: string }> = {
  holographic: {
    name: "全息渐变 Holographic",
    desc: "默认基准：粉紫青三色渐变光晕 + 流光文字，全息玻璃质感",
    defaultAccent: "#00d4ff",
  },
  dark: {
    name: "深色收敛 Dark",
    desc: "备选深色：收敛彩色光斑与渐变，突出强调色，更沉稳内敛",
    defaultAccent: "#7928ca",
  },
};

export const ACCENT_PRESETS = ["#00d4ff", "#7928ca", "#ff0080", "#22c55e", "#f59e0b", "#ef4444"];

/* hex → "r, g, b" 字符串（供 rgba(var(--accent-rgb), x) 使用） */
export function hexToRgbStr(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) return "0, 212, 255";
  const v = parseInt(m[1], 16);
  return `${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}`;
}

export function isThemeScheme(v: unknown): v is ThemeScheme {
  return v === "holographic" || v === "dark";
}

/* 应用主题到 <html>：data-scheme + accent 派生变量（实时预览 / 全局统一） */
export function applyTheme(scheme: unknown, accent: unknown): void {
  const s = isThemeScheme(scheme) ? scheme : "holographic";
  const def = THEME_SCHEMES[s].defaultAccent;
  const acc = /^#?[0-9a-f]{6}$/i.test(String(accent ?? "").trim()) ? String(accent).trim() : def;
  const root = document.documentElement;
  root.dataset.scheme = s;
  root.style.setProperty("--accent", acc);
  root.style.setProperty("--accent-rgb", hexToRgbStr(acc));
}

/* 恢复全息渐变默认主题配色 */
export function resetTheme(): { scheme: ThemeScheme; accent: string } {
  return { scheme: "holographic", accent: "" };
}
