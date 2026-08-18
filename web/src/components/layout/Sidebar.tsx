import { NAV } from "../../lib/constants";

/* 全息玻璃侧边栏：backdrop-blur + 激活菜单渐变高亮 + 可折叠 */
interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  active: string;
  onNavigate: (key: string) => void;
}

export default function Sidebar({ collapsed, onToggle, active, onNavigate }: SidebarProps) {
  return (
    <aside
      className={`flex h-full shrink-0 flex-col border-r border-white/10 bg-white/5 backdrop-blur-2xl transition-all duration-500 ${
        collapsed ? "w-[74px]" : "w-[262px]"
      }`}
    >
      {/* 品牌区 */}
      <div className="flex h-16 items-center gap-3 border-b border-white/10 px-4">
        <div className="holo-bg grid h-9 w-9 shrink-0 place-items-center rounded-2xl text-lg shadow-[0_0_20px_var(--glow-card-md)]">
          🛡
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="truncate text-[15px] font-bold text-white">文档核验中心</div>
            <div className="truncate text-[11px] text-white/50">离线保密版 · 全息渐变</div>
          </div>
        )}
      </div>

      {/* 导航 */}
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-3">
        {NAV.map((n) => {
          const isActive = active === n.key;
          return (
            <button
              key={n.key}
              onClick={() => onNavigate(n.key)}
              aria-current={isActive ? "page" : undefined}
              title={collapsed ? n.name : undefined}
              className={`flex items-center gap-3 rounded-2xl border px-3 py-2.5 text-sm transition-all duration-500 select-none ${
                collapsed ? "justify-center" : ""
              } ${
                isActive
                  ? "border-transparent bg-gradient-to-r from-[var(--holo-c1)]/25 via-[var(--holo-c2)]/30 to-[var(--holo-c3)]/25 text-white shadow-[0_0_20px_var(--glow-tab)]"
                  : "border-transparent text-white/60 hover:bg-white/5 hover:text-white hover:border-white/10"
              }`}
            >
              <span
                className={`w-[22px] shrink-0 text-center text-[17px] ${
                  isActive ? "drop-shadow-[0_0_8px_var(--glow-accent-soft)]" : ""
                }`}
              >
                {n.ic}
              </span>
              {!collapsed && <span className="truncate">{n.name}</span>}
            </button>
          );
        })}
      </nav>

      {/* 底部折叠开关 */}
      <div className="border-t border-white/10 p-3">
        <button
          onClick={onToggle}
          className="flex w-full items-center justify-center gap-2.5 rounded-2xl border border-white/10 bg-white/5 px-2 py-2 text-[13px] text-white/60 transition-all duration-500 hover:bg-white/10 hover:text-white active:scale-95"
        >
          <span>{collapsed ? "»" : "«"}</span>
          {!collapsed && <span>收起侧栏</span>}
        </button>
      </div>
    </aside>
  );
}
