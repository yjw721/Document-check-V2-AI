import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import { HoloInput, HoloSelect } from "../../components/ui/HoloInput";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type {
  CustomRule,
  CustomRuleGroup,
  CustomRulesData,
  Severity,
} from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

const genId = (p: string) => p + Math.random().toString(16).slice(2, 10);

/* 标签1 · 自定义正则规则：增删改查、启用/停用、批量导出、分组折叠、批量启停 */
export default function CustomRulesTab() {
  const toast = useToast();
  const [data, setData] = useState<CustomRulesData | null>(null);
  const [folded, setFolded] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.customRules().then((d) => setData({ groups: d.groups || [] }));
  }, []);

  if (!data) return null;

  /* ---- 不可变更新辅助 ---- */
  const updGroup = (gi: number, patch: Partial<CustomRuleGroup>) =>
    setData((d) => (d ? { ...d, groups: d.groups.map((g, i) => (i === gi ? { ...g, ...patch } : g)) } : d));

  const updRule = (gi: number, ri: number, patch: Partial<CustomRule>) =>
    setData((d) =>
      d
        ? {
            ...d,
            groups: d.groups.map((g, i) =>
              i === gi ? { ...g, rules: g.rules.map((r, j) => (j === ri ? { ...r, ...patch } : r)) } : g,
            ),
          }
        : d,
    );

  const addGroup = () =>
    setData((d) =>
      d
        ? {
            ...d,
            groups: [
              ...d.groups,
              { id: genId("g"), name: "新规则组", category: "format_error", scope: "all", enabled: true, rules: [] },
            ],
          }
        : d,
    );

  const delGroup = (gi: number) =>
    setData((d) => (d ? { ...d, groups: d.groups.filter((_, i) => i !== gi) } : d));

  const addRule = (gi: number) =>
    setData((d) =>
      d
        ? {
            ...d,
            groups: d.groups.map((g, i) =>
              i === gi
                ? {
                    ...g,
                    rules: [
                      ...g.rules,
                      {
                        id: genId("r"),
                        name: "",
                        enabled: true,
                        match_mode: "keyword",
                        pattern: "",
                        severity: "low" as Severity,
                        tag: "",
                        suggestion: "",
                      },
                    ],
                  }
                : g,
            ),
          }
        : d,
    );

  const delRule = (gi: number, ri: number) =>
    setData((d) =>
      d ? { ...d, groups: d.groups.map((g, i) => (i === gi ? { ...g, rules: g.rules.filter((_, j) => j !== ri) } : g)) } : d,
    );

  const toggleFold = (id: string) =>
    setFolded((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const setGroupEnabled = (gi: number, v: boolean) => {
    setData((d) =>
      d
        ? {
            ...d,
            groups: d.groups.map((g, i) =>
              i === gi ? { ...g, enabled: v, rules: g.rules.map((r) => ({ ...r, enabled: v })) } : g,
            ),
          }
        : d,
    );
  };

  const save = async () => {
    await api.saveCustomRules(data);
    toast("自定义规则已保存");
  };

  const exportJson = () => {
    if (!data.groups.length) {
      toast("暂无规则可导出", "warn");
      return;
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `自定义正则规则_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`已导出 ${data.groups.length} 个规则组（JSON，可完整回导）`);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" onClick={save}>
          保存自定义规则
        </HoloButton>
        <HoloButton size="sm" icon={<span>＋</span>} onClick={addGroup}>
          新建规则组
        </HoloButton>
        <HoloButton size="sm" icon={<span>⬇</span>} onClick={exportJson}>
          批量导出
        </HoloButton>
      </div>

      {data.groups.length ? (
        <div className="space-y-3">
          {data.groups.map((g, gi) => {
            const isFolded = folded.has(g.id);
            return (
              <HoloCard key={g.id} className="p-4" glow="sm">
                {/* 分组头 */}
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => toggleFold(g.id)}
                    title="折叠 / 展开"
                    className="rounded-xl border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs text-white/60 transition-all duration-500 hover:bg-white/10 hover:text-white active:scale-95"
                  >
                    {isFolded ? "▸" : "▾"}
                  </button>
                  <HoloInput
                    className="min-w-[150px] flex-1 font-semibold"
                    value={g.name}
                    onChange={(e) => updGroup(gi, { name: e.target.value })}
                  />
                  <HoloSelect
                    className="w-[96px]"
                    value={g.scope}
                    onChange={(e) => updGroup(gi, { scope: e.target.value })}
                  >
                    <option value="all">全部</option>
                    <option value="word">Word</option>
                    <option value="excel">Excel</option>
                    <option value="pdf">PDF</option>
                  </HoloSelect>
                  <HoloSelect
                    className="w-[110px]"
                    value={g.category}
                    onChange={(e) => updGroup(gi, { category: e.target.value })}
                  >
                    <option value="format_error">格式错误</option>
                    <option value="expression">表述规范</option>
                  </HoloSelect>
                  <HoloSwitch checked={g.enabled !== false} onChange={(v) => updGroup(gi, { enabled: v })} />
                  <button
                    onClick={() => delGroup(gi)}
                    aria-label="删除分组"
                    className="grid h-7 w-7 place-items-center rounded-xl border border-white/10 bg-white/5 text-xs text-white/60 transition-all duration-500 hover:border-[rgba(255,107,125,0.4)] hover:text-[var(--tone-danger)] active:scale-95"
                  >
                    ✕
                  </button>
                </div>

                {/* 规则表（折叠隐藏） */}
                {!isFolded && (
                  <div className="mt-3 overflow-x-auto rounded-2xl border border-white/10">
                    <table className="holo-table w-full text-[13px]">
                      <thead>
                        <tr className="text-xs text-white/60">
                          <th className="px-3 py-2 text-left font-semibold">规则名</th>
                          <th className="px-3 py-2 text-left font-semibold">模式</th>
                          <th className="px-3 py-2 text-left font-semibold">匹配式</th>
                          <th className="px-3 py-2 text-left font-semibold">级别</th>
                          <th className="px-3 py-2 text-left font-semibold">建议</th>
                          <th className="px-3 py-2 text-left font-semibold">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {g.rules.map((r, ri) => (
                          <tr key={r.id} className="border-b border-white/5 text-white/85">
                            <td className="px-3 py-1.5">
                              <HoloInput
                                className="min-w-[120px] py-1.5"
                                value={r.name}
                                onChange={(e) => updRule(gi, ri, { name: e.target.value })}
                              />
                            </td>
                            <td className="px-3 py-1.5">
                              <HoloSelect
                                className="w-[80px] py-1.5"
                                value={r.match_mode}
                                onChange={(e) => updRule(gi, ri, { match_mode: e.target.value as "keyword" | "regex" })}
                              >
                                <option value="keyword">子串</option>
                                <option value="regex">正则</option>
                              </HoloSelect>
                            </td>
                            <td className="px-3 py-1.5">
                              <HoloInput
                                className="min-w-[140px] py-1.5 font-mono"
                                value={r.pattern}
                                onChange={(e) => updRule(gi, ri, { pattern: e.target.value })}
                              />
                            </td>
                            <td className="px-3 py-1.5">
                              <HoloSelect
                                className="w-[80px] py-1.5"
                                value={r.severity}
                                onChange={(e) => updRule(gi, ri, { severity: e.target.value as Severity })}
                              >
                                <option value="low">轻微</option>
                                <option value="medium">一般</option>
                                <option value="high">严重</option>
                              </HoloSelect>
                            </td>
                            <td className="px-3 py-1.5">
                              <HoloInput
                                className="min-w-[140px] py-1.5"
                                value={r.suggestion}
                                onChange={(e) => updRule(gi, ri, { suggestion: e.target.value })}
                              />
                            </td>
                            <td className="whitespace-nowrap px-3 py-1.5">
                              <div className="flex items-center gap-2">
                                <HoloSwitch
                                  checked={r.enabled !== false}
                                  onChange={(v) => updRule(gi, ri, { enabled: v })}
                                />
                                <button
                                  onClick={() => delRule(gi, ri)}
                                  aria-label="删除规则"
                                  className="grid h-6 w-6 place-items-center rounded-lg border border-white/10 bg-white/5 text-xs text-white/60 transition-all duration-500 hover:border-[rgba(255,107,125,0.4)] hover:text-[var(--tone-danger)] active:scale-95"
                                >
                                  ✕
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* 分组操作行 */}
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <HoloButton size="sm" icon={<span>＋</span>} onClick={() => addRule(gi)}>
                    添加规则
                  </HoloButton>
                  <HoloButton size="sm" onClick={() => setGroupEnabled(gi, true)}>
                    启用本组
                  </HoloButton>
                  <HoloButton size="sm" onClick={() => setGroupEnabled(gi, false)}>
                    停用本组
                  </HoloButton>
                  <span className="ml-auto text-xs text-white/40">{g.rules.length} 条规则</span>
                </div>
              </HoloCard>
            );
          })}
        </div>
      ) : (
        <HoloCard className="p-6">
          <EmptyState text="暂无自定义规则，点击上方「新建规则组」" icon="🧩" />
        </HoloCard>
      )}
    </div>
  );
}
