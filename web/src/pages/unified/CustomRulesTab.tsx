import { useEffect, useMemo, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import { HoloInput, HoloSelect } from "../../components/ui/HoloInput";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { CustomRule, CustomRuleGroup, CustomRulesData, Severity, ActivityLog } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";
import { onRulesChanged, emitRulesChanged } from "../../lib/events";
import { navGuard } from "../../lib/unsaved";
import SectionTitle from "../../components/common/SectionTitle";

const genId = (p: string) => p + Math.random().toString(16).slice(2, 10);
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

/* 新规则（库内无 id）入库前客户端校验，规则与后端 validate_rule 对齐 */
function ruleError(r: CustomRule): string | null {
  const pat = (r.pattern || "").trim();
  const sug = (r.suggestion || "").trim();
  const label = `规则「${r.name || "（未命名）"}」`;
  if (!pat) return `${label}匹配式为空`;
  if (!sug) return `${label}建议替换为空`;
  if (pat === sug) return `${label}匹配式与建议替换相同`;
  if (r.match_mode === "regex") {
    try {
      new RegExp(pat);
    } catch {
      return `${label}正则表达式无效`;
    }
  }
  return null;
}

/* 生成「本组修改项」可读摘要，写入操作日志 */
function diffGroup(saved: CustomRuleGroup | undefined, cur: CustomRuleGroup): string {
  if (!saved) return `新增规则组「${cur.name}」`;
  const parts: string[] = [];
  if (saved.name !== cur.name) parts.push(`组名「${saved.name}」→「${cur.name}」`);
  if (saved.enabled !== cur.enabled) parts.push(`总开关=${cur.enabled ? "启用" : "停用"}`);
  if (saved.category !== cur.category) parts.push(`分类=${cur.category}`);
  if (saved.scope !== cur.scope) parts.push(`范围=${cur.scope}`);
  const sById = new Map((saved.rules || []).map((r) => [r.id, r]));
  const cById = new Map((cur.rules || []).map((r) => [r.id, r]));
  const added = cur.rules.filter((r) => !sById.has(r.id));
  const removed = saved.rules.filter((r) => !cById.has(r.id));
  const changed = cur.rules.filter((r) => sById.has(r.id) && JSON.stringify(r) !== JSON.stringify(sById.get(r.id)));
  if (added.length) parts.push(`新增${added.length}条规则（${added.map((r) => r.name || "未命名").join("、")}）`);
  if (removed.length) parts.push(`删除${removed.length}条规则（${removed.map((r) => r.name || "未命名").join("、")}）`);
  if (changed.length) parts.push(`修改${changed.length}条规则（${changed.map((r) => r.name || "未命名").join("、")}）`);
  return parts.length ? parts.join("；") : "无变更";
}

/* 标签1 · 自定义正则规则：分组底部保存/重置、脏检测高亮、新规则校验、离开守卫、操作日志 */
export default function CustomRulesTab() {
  const toast = useToast();
  const [data, setData] = useState<CustomRulesData | null>(null);
  const [saved, setSaved] = useState<CustomRulesData>({ groups: [] });
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [logs, setLogs] = useState<ActivityLog[]>([]);

  const load = () => api.customRules().then((d) => { const init = { groups: d.groups || [] }; setData(clone(init)); setSaved(clone(init)); });
  useEffect(() => {
    load();
    return onRulesChanged(load);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshLogs = () => api.activityLogs("rules", 50).then((r) => setLogs(r.logs)).catch(() => {});
  useEffect(() => { refreshLogs(); }, []);

  /* 脏检测：按组 id 比对当前编辑态与已保存快照 */
  const { dirtySet, anyDirty } = useMemo(() => {
    if (!data) return { dirtySet: new Set<number>(), anyDirty: false };
    const savedById = new Map(saved.groups.map((g) => [g.id, g]));
    const dirty = new Set<number>();
    data.groups.forEach((g, i) => {
      const s = savedById.get(g.id);
      if (!s || JSON.stringify(g) !== JSON.stringify(s)) dirty.add(i);
    });
    const idsChanged = saved.groups.length !== data.groups.length ||
      saved.groups.some((g) => !data.groups.find((x) => x.id === g.id));
    return { dirtySet: dirty, anyDirty: dirty.size > 0 || idsChanged };
  }, [data, saved]);

  /* 跨组件未保存守卫 + 浏览器关闭/刷新提示 */
  useEffect(() => { navGuard.dirty = anyDirty; }, [anyDirty]);
  useEffect(() => () => { navGuard.dirty = false; }, []);
  useEffect(() => {
    if (!anyDirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [anyDirty]);

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

  /* 校验某组内所有「新规则」（库内无 id） */
  const validateGroup = (gi: number): string | null => {
    const knownIds = new Set(saved.groups.flatMap((g) => g.rules.map((r) => r.id)));
    const g = data!.groups[gi];
    for (const r of g.rules) {
      if (!knownIds.has(r.id)) {
        const err = ruleError(r);
        if (err) return err;
      }
    }
    return null;
  };

  /* 保存本组修改：校验 -> 全量入库（后端按组写回 JSON）-> 记录日志 -> 刷新 */
  const saveGroup = async (gi: number) => {
    const err = validateGroup(gi);
    if (err) {
      toast(`保存失败：${err}，请检查配置后重试`, "err");
      return;
    }
    setSaving(true);
    try {
      const cur = data!;
      const res = await api.saveCustomRules(cur);
      setSaved(clone(cur));
      const detail = diffGroup(saved.groups[gi], cur.groups[gi]);
      await api.logActivity({ module: "rules", action: "save_group", detail }).catch(() => {});
      refreshLogs();
      emitRulesChanged();
      const dropped = (res as { rejected?: unknown[] }).rejected?.length || 0;
      toast(dropped ? `本组规则已保存，但 ${dropped} 条无效规则未入库` : "本组规则保存成功，已生效");
    } catch {
      toast("保存失败，请检查配置后重试", "err");
    } finally {
      setSaving(false);
    }
  };

  /* 保存全部规则 */
  const saveAll = async () => {
    const knownIds = new Set(saved.groups.flatMap((g) => g.rules.map((r) => r.id)));
    for (const g of data!.groups) {
      for (const r of g.rules) {
        if (!knownIds.has(r.id)) {
          const err = ruleError(r);
          if (err) { toast(`保存失败：${err}，请检查配置后重试`, "err"); return; }
        }
      }
    }
    setSaving(true);
    try {
      const cur = data!;
      await api.saveCustomRules(cur);
      setSaved(clone(cur));
      await api.logActivity({ module: "rules", action: "save_all", detail: `保存全部规则（${cur.groups.length} 个组）` }).catch(() => {});
      refreshLogs();
      emitRulesChanged();
      toast("全部规则保存成功，已生效");
    } catch {
      toast("保存失败，请检查配置后重试", "err");
    } finally {
      setSaving(false);
    }
  };

  const resetGroup = (gi: number) => {
    setData((d) =>
      d
        ? {
            ...d,
            groups: d.groups.map((g, i) =>
              i === gi ? clone(saved.groups.find((s) => s.id === g.id) || g) : g,
            ),
          }
        : d,
    );
    toast("已重置本组未保存修改");
  };

  const exportJson = () => {
    if (!data!.groups.length) {
      toast("暂无规则可导出", "warn");
      return;
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `自定义正则规则_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`已导出 ${data!.groups.length} 个规则组（JSON，可完整回导）`);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" onClick={saveAll} disabled={saving || !anyDirty}>
          保存全部规则
        </HoloButton>
        <HoloButton size="sm" icon={<span>＋</span>} onClick={addGroup}>
          新建规则组
        </HoloButton>
        <HoloButton size="sm" icon={<span>⬇</span>} onClick={exportJson}>
          批量导出
        </HoloButton>
        {anyDirty && <span className="text-xs text-[var(--tone-danger)]">● 有未保存修改</span>}
      </div>

      {data.groups.length ? (
        <div className="space-y-3">
          {data.groups.map((g, gi) => {
            const isFolded = folded.has(g.id);
            const dirty = dirtySet.has(gi);
            return (
              <HoloCard
                key={g.id}
                className={`p-4 ${dirty ? "border-[var(--border-accent)]" : ""}`}
                glow="sm"
              >
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
                          <th className="px-3 py-2 text-left font-semibold">来源</th>
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
                            <td className="max-w-[150px] px-3 py-1.5 text-[11px]">
                              {r.source_label || r.tag ? (
                                <span className="text-[var(--tone-cyan)]" title={r.source || ""}>
                                  🤖 {r.source_label || r.tag}
                                </span>
                              ) : (
                                <span className="text-white/30">手动</span>
                              )}
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
                  <span className="text-xs text-white/40">{g.rules.length} 条规则</span>
                  <span className="ml-auto flex flex-wrap items-center gap-2">
                    {dirty && (
                      <HoloButton size="sm" variant="ghost" onClick={() => resetGroup(gi)} disabled={saving}>
                        重置
                      </HoloButton>
                    )}
                    <HoloButton
                      size="sm"
                      variant="primary"
                      className={dirty ? "shadow-[0_0_16px_var(--glow-tab)] ring-2 ring-[var(--border-accent)]" : ""}
                      onClick={() => saveGroup(gi)}
                      disabled={saving || !dirty}
                    >
                      保存本组修改
                    </HoloButton>
                  </span>
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

      {/* 操作记录（修改项 / 操作人 / 时间） */}
      <HoloCard className="p-4" glow="sm">
        <div className="flex flex-wrap items-center gap-2">
          <SectionTitle className="!mb-0">本页操作记录</SectionTitle>
          <span className="ml-auto text-[11px] text-white/40">最近 {logs.length} 条 · 操作人/时间/修改项</span>
          <HoloButton size="sm" variant="ghost" onClick={() => api.activityLogsClear("rules").then(refreshLogs).catch(() => {})}>
            清空
          </HoloButton>
        </div>
        <div className="mt-2 max-h-52 space-y-1 overflow-y-auto pr-1">
          {logs.length ? (
            logs.map((l, i) => (
              <div key={i} className="flex flex-wrap items-baseline gap-x-2 rounded-lg bg-white/[0.03] px-2.5 py-1.5 text-xs">
                <span className="text-white/45">{l.ts}</span>
                <span className="rounded bg-white/10 px-1.5 py-0.5 text-white/70">{l.operator}</span>
                <span className="text-white/85">{l.detail || l.action}</span>
              </div>
            ))
          ) : (
            <div className="py-4 text-center text-xs text-white/35">暂无保存记录</div>
          )}
        </div>
      </HoloCard>
    </div>
  );
}
