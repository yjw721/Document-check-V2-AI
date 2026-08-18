/* 顶部标签页：激活高亮渐变 + 平滑过渡 */
export interface TabDef {
  key: string;
  name: string;
  icon?: string;
}

interface TabBarProps {
  tabs: TabDef[];
  active: string;
  onChange: (key: string) => void;
}

export default function TabBar({ tabs, active, onChange }: TabBarProps) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1.5 border-b border-white/10 px-3.5 py-3">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={active === t.key}
          onClick={() => onChange(t.key)}
          className={`flex items-center gap-2 rounded-2xl border px-4 py-2.5 text-sm font-semibold whitespace-nowrap transition-all duration-500 select-none ${
            active === t.key
              ? "border-transparent holo-tint text-white shadow-[0_0_20px_var(--glow-tab)]"
              : "border-transparent text-white/60 hover:bg-white/5 hover:text-white hover:border-white/10"
          }`}
        >
          {t.icon && <span>{t.icon}</span>}
          <span>{t.name}</span>
        </button>
      ))}
    </div>
  );
}
