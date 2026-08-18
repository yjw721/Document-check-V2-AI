import type { ReactNode } from "react";
import Sidebar from "./Sidebar";

/* 应用布局：侧边栏 + 顶栏（标题/面包屑/状态 chips/主题切换）+ 内容区 */
interface AppLayoutProps {
  collapsed: boolean;
  onToggle: () => void;
  active: string;
  onNavigate: (key: string) => void;
  title: string;
  crumb: string;
  fileCount?: number;
  issueCount?: number;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  children: ReactNode;
}

export default function AppLayout({
  collapsed,
  onToggle,
  active,
  onNavigate,
  title,
  crumb,
  fileCount,
  issueCount,
  theme,
  onToggleTheme,
  children,
}: AppLayoutProps) {
  return (
    <div className="holo-space flex h-full">
      <Sidebar collapsed={collapsed} onToggle={onToggle} active={active} onNavigate={onNavigate} />
      <main className="relative z-[1] flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-4 border-b border-white/10 bg-[var(--header-bg)] px-6 backdrop-blur-xl">
          <div className="min-w-0">
            <h1 className="truncate text-[17px] font-bold text-white">{title}</h1>
            <div className="truncate text-[13px] text-white/40">{crumb}</div>
          </div>
          <div className="ml-auto flex items-center gap-2.5">
            <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3.5 py-1.5 text-xs text-white/60 backdrop-blur-xl">
              <span className="h-2 w-2 rounded-full bg-[#46d39a] shadow-[0_0_8px_#46d39a]" />
              离线运行
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3.5 py-1.5 text-xs text-white/60 backdrop-blur-xl">
              文件 <b className="text-white">{fileCount ?? 0}</b>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3.5 py-1.5 text-xs text-white/60 backdrop-blur-xl">
              问题 <b className="text-white">{issueCount ?? 0}</b>
            </span>
            {/* 白天 / 黑夜切换 */}
            <button
              onClick={onToggleTheme}
              title={theme === "dark" ? "切换到白天模式" : "切换到黑夜模式"}
              aria-label="切换白天 / 黑夜主题"
              className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3.5 py-1.5 text-xs text-white/60 backdrop-blur-xl transition-all duration-500 hover:bg-white/10 hover:text-white hover:shadow-[0_0_16px_var(--glow-tab)] active:scale-95"
            >
              <span className="text-[14px] leading-none">{theme === "dark" ? "☀️" : "🌙"}</span>
              <span className="hidden md:inline">{theme === "dark" ? "白天" : "黑夜"}</span>
            </button>
          </div>
        </header>
        <section className="flex-1 overflow-y-auto p-6 md:p-7">{children}</section>
      </main>
    </div>
  );
}
