import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import { HoloInput, HoloSelect } from "../../components/ui/HoloInput";
import SectionTitle from "../../components/common/SectionTitle";
import { api } from "../../lib/api";
import type { RuleDef, RulesData, SettingsData, Severity } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";
import { onRulesChanged, emitRulesChanged } from "../../lib/events";
import CustomRulesTab from "./CustomRulesTab";

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const genKey = () => "r_" + Math.random().toString(16).slice(2, 10);

/* 规则总览：自定义规则（完整增删改）+ 内置标准规则（可编辑开关/标题/级别/说明/建议，可新增/删除） */
const SECTIONS: [string, string][] = [
  ["word", "Word 文档"],
  ["excel", "Excel 表格"],
  ["textnorm", "文本规范检测"],
  ["fluency", "语句通顺检测"],
];
const SEV_OPTIONS: [string, string][] = [
  ["low", "轻微"],
  ["medium", "一般"],
  ["high", "严重"],
];

function BuiltinRulesSection() {
  const toast = useToast();
  const [rules, setRules] = useState<RulesData | null>(null);
  const [saved, setSaved] = useState<RulesData | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  const load = () => {
    api
      .rules()
      .then((d) => {
        setRules(clone(d));
        setSaved(clone(d));
      })
      .catch((e) => toast((e as Error).message, "err"));
    api.settings().then(setSettings).catch(() => setSettings(null));
  };
  useEffect(() => {
    load();
    return onRulesChanged(load);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!rules) return null;

  const dirty = !!saved && JSON.stringify(rules) !== JSON.stringify(saved);

  const secOf = (sec: string) => (rules[sec] ?? {}) as Record<string, RuleDef>;

  const setAt = (sec: string, key: string, patch: Partial<RuleDef>) => {
    const secObj = secOf(sec);
    setRules((r) => (r ? { ...r, [sec]: { ...secObj, [key]: { ...secObj[key], ...patch } } } : r));
  };

  const setAllInSec = (sec: string, v: boolean) => {
    const secObj = secOf(sec);
    const nf: Record<string, RuleDef> = {};
    for (const [k, d] of Object.entries(secObj)) nf[k] = { ...d, enabled: v };
    setRules((r) => (r ? { ...r, [sec]: nf } : r));
  };

  const addRule = (sec: string) => {
    const key = genKey();
    const secObj = secOf(sec);
    setRules((r) =>
      r
        ? {
            ...r,
            [sec]: {
              ...secObj,
              [key]: { enabled: true, severity: "low", title: "新规则", desc: "", suggestion: "" },
            },
          }
        : r,
    );
    setFolded((s) => {
      const n = new Set(s);
      n.delete(sec);
      return n;
    });
    const label = SECTIONS.find(([k]) => k === sec)?.[1] ?? sec;
    toast(`已在「${label}」新增一条规则，编辑后点击「保存内置规则」生效`);
  };

  const delRule = (sec: string, key: string) => {
    const def = secOf(sec)[key];
    const name = def?.title ?? key;
    if (!window.confirm(`确认删除规则「${name}」？保存后生效，仅移除该配置项。`)) return;
    const secObj = { ...secOf(sec) };
    delete secObj[key];
    setRules((r) => (r ? { ...r, [sec]: secObj } : r));
  };

  const toggleFold = (sec: string) =>
    setFolded((s) => {
      const n = new Set(s);
      if (n.has(sec)) n.delete(sec);
      else n.add(sec);
      return n;
    });

  const save = async () => {
    setSaving(true);
    try {
      await api.saveRules(rules);
      if (settings) await api.saveSettings(settings);
      setSaved(clone(rules));
      emitRulesChanged();
      toast("内置标准规则已保存到 config/rules.json，下次检测生效");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };

  const discard = () => {
    if (!saved) return;
    setRules(clone(saved));
    toast("已撤销未保存的修改");
  };

  const sens = (settings?.detection?.fluency_sensitivity as string) || "normal";
  const setSens = (v: string) =>
    setSettings((d) => (d ? { ...d, detection: { ...(d.detection ?? {}), fluency_sensitivity: v } } : d));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" disabled={saving || !dirty} onClick={save}>
          {saving ? "保存中…" : "保存内置规则"}
        </HoloButton>
        {dirty && (
          <HoloButton size="sm" variant="ghost" onClick={discard}>
            撤销修改
          </HoloButton>
        )}
        {dirty && <span className="text-xs text-[var(--tone-danger)]">● 有未保存修改</span>}
        <span className="ml-auto text-right text-xs text-white/40">
          内置标准规则可直接编辑（标题 / 级别 / 说明 / 建议），可新增或删除；删除仅移除配置项，保存后下次检测生效
        </span>
      </div>

      {SECTIONS.map(([sec, label]) => {
        const secObj = secOf(sec);
        const entries = Object.entries(secObj);
        const isFolded = folded.has(sec);
        const enabledCount = entries.filter(([, d]) => d.enabled !== false).length;
        return (
          <HoloCard key={sec} className="overflow-hidden p-0" glow="sm">
            <div className="flex flex-wrap items-center gap-2.5 px-4 py-3">
              <button
                onClick={() => toggleFold(sec)}
                title="折叠 / 展开"
                className="rounded-xl border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs text-white/60 transition-all duration-500 hover:bg-white/10 hover:text-white active:scale-95"
              >
                {isFolded ? "▸" : "▾"}
              </button>
              <b className="text-sm text-white/90">{label}</b>
              <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[11px] text-white/50">
                {enabledCount}/{entries.length} 启用
              </span>
              <HoloButton size="sm" onClick={() => setAllInSec(sec, true)}>
                启用全部
              </HoloButton>
              <HoloButton size="sm" onClick={() => setAllInSec(sec, false)}>
                停用全部
              </HoloButton>
              {sec === "fluency" && (
                <HoloSelect
                  className="w-[220px] py-1.5"
                  value={sens}
                  onChange={(e) => setSens(e.target.value)}
                >
                  <option value="loose">灵敏度：宽松</option>
                  <option value="normal">灵敏度：常用（默认）</option>
                  <option value="strict">灵敏度：严格</option>
                </HoloSelect>
              )}
              <HoloButton size="sm" variant="primary" icon={<span>＋</span>} onClick={() => addRule(sec)}>
                添加规则
              </HoloButton>
            </div>

            {!isFolded &&
              (entries.length ? (
                <div className="overflow-x-auto border-t border-white/10">
                  <table className="holo-table w-full text-[13px]">
                    <thead>
                      <tr className="text-xs text-white/60">
                        <th className="w-[64px] px-4 py-2 text-left font-semibold">启用</th>
                        <th className="px-3 py-2 text-left font-semibold">规则</th>
                        <th className="w-[96px] px-3 py-2 text-left font-semibold">级别</th>
                        <th className="px-3 py-2 text-left font-semibold">说明</th>
                        <th className="px-3 py-2 text-left font-semibold">建议</th>
                        <th className="w-[64px] px-3 py-2 text-left font-semibold">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map(([key, def]) => (
                        <tr key={key} className="border-b border-white/5 text-white/85">
                          <td className="px-4 py-1.5">
                            <HoloSwitch
                              checked={def.enabled !== false}
                              onChange={(v) => setAt(sec, key, { enabled: v })}
                            />
                          </td>
                          <td className="px-3 py-1.5">
                            <div className="min-w-[150px]">
                              <HoloInput
                                className="py-1.5 font-semibold"
                                value={def.title ?? key}
                                onChange={(e) => setAt(sec, key, { title: e.target.value })}
                              />
                              <div className="mt-0.5 truncate font-mono text-[10px] text-white/30" title={key}>
                                {key}
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-1.5">
                            <HoloSelect
                              className="w-[92px] py-1.5"
                              value={def.severity ?? "low"}
                              onChange={(e) => setAt(sec, key, { severity: e.target.value as Severity })}
                            >
                              {SEV_OPTIONS.map(([v, l]) => (
                                <option key={v} value={v}>
                                  {l}
                                </option>
                              ))}
                            </HoloSelect>
                          </td>
                          <td className="px-3 py-1.5">
                            <HoloInput
                              className="min-w-[180px] py-1.5"
                              value={String(def.desc ?? "")}
                              placeholder="规则说明"
                              onChange={(e) => setAt(sec, key, { desc: e.target.value })}
                            />
                          </td>
                          <td className="px-3 py-1.5">
                            <HoloInput
                              className="min-w-[180px] py-1.5"
                              value={def.suggestion ?? ""}
                              placeholder="修复建议"
                              onChange={(e) => setAt(sec, key, { suggestion: e.target.value })}
                            />
                          </td>
                          <td className="whitespace-nowrap px-3 py-1.5">
                            <button
                              onClick={() => delRule(sec, key)}
                              aria-label="删除规则"
                              title="删除该规则（保存后生效）"
                              className="grid h-6 w-6 place-items-center rounded-lg border border-white/10 bg-white/5 text-xs text-white/60 transition-all duration-500 hover:border-[rgba(255,107,125,0.4)] hover:text-[var(--tone-danger)] active:scale-95"
                            >
                              ✕
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="border-t border-white/10">
                  <div className="px-4 py-4 text-center text-xs text-white/40">
                    该分区暂无规则，点击「添加规则」创建
                  </div>
                </div>
              ))}
          </HoloCard>
        );
      })}
    </div>
  );
}

export default function RulesOverviewTab() {
  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <SectionTitle className="!mb-0">自定义规则 · 可增删改查</SectionTitle>
        <CustomRulesTab />
      </section>
      <section className="space-y-3">
        <SectionTitle className="!mb-0">内置标准规则 · 可编辑增删改</SectionTitle>
        <BuiltinRulesSection />
      </section>
    </div>
  );
}