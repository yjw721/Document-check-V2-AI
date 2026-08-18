import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import { HoloSelect } from "../../components/ui/HoloInput";
import SectionTitle from "../../components/common/SectionTitle";
import { api } from "../../lib/api";
import type { RuleDef, RulesData, SettingsData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";
import { onRulesChanged } from "../../lib/events";
import CustomRulesTab from "./CustomRulesTab";

/* 规则总览：自定义规则（完整增删改）+ 内置标准规则（只读，仅可启停开关 / 恢复默认） */
const SECTIONS: [string, string][] = [
  ["word", "Word 文档"],
  ["excel", "Excel 表格"],
  ["textnorm", "文本规范检测"],
  ["fluency", "语句通顺检测"],
];

function BuiltinRulesSection() {
  const toast = useToast();
  const [rules, setRules] = useState<RulesData | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  const load = () => {
    api.rules().then(setRules).catch((e) => toast((e as Error).message, "err"));
    api.settings().then(setSettings).catch(() => setSettings(null));
  };
  useEffect(() => {
    load();
    return onRulesChanged(load);
  }, []);

  if (!rules) return null;

  const setAt = (sec: string, key: string, v: boolean) => {
    const secObj = (rules[sec] ?? {}) as Record<string, RuleDef>;
    setRules((r) => (r ? { ...r, [sec]: { ...secObj, [key]: { ...secObj[key], enabled: v } } } : r));
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
      toast("内置标准规则开关已保存到 config/rules.json，下次检测生效");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };

  const restore = async () => {
    try {
      const r = await api.restoreRules();
      setRules(r.data);
      toast("已恢复默认规则配置");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const setAllInSec = (sec: string, v: boolean) => {
    const secObj = (rules[sec] ?? {}) as Record<string, RuleDef>;
    const nf: Record<string, RuleDef> = {};
    for (const [k, d] of Object.entries(secObj)) nf[k] = { ...d, enabled: v };
    setRules((r) => (r ? { ...r, [sec]: nf } : r));
  };

  const sens = (settings?.detection?.fluency_sensitivity as string) || "normal";
  const setSens = (v: string) =>
    setSettings((d) => (d ? { ...d, detection: { ...(d.detection ?? {}), fluency_sensitivity: v } } : d));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" disabled={saving} onClick={save}>
          {saving ? "保存中…" : "保存内置规则开关"}
        </HoloButton>
        <HoloButton size="sm" onClick={restore}>恢复默认</HoloButton>
        <span className="ml-auto text-xs text-white/40">
          内置标准规则为只读内容，仅可启停开关，不可修改或删除
        </span>
      </div>

      {SECTIONS.map(([sec, label]) => {
        const secObj = (rules[sec] ?? {}) as Record<string, RuleDef>;
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
              {sec === "fluency" && (
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  <HoloSelect
                    className="w-[220px] py-1.5"
                    value={sens}
                    onChange={(e) => setSens(e.target.value)}
                  >
                    <option value="loose">灵敏度：宽松</option>
                    <option value="normal">灵敏度：常用（默认）</option>
                    <option value="strict">灵敏度：严格</option>
                  </HoloSelect>
                  <HoloButton size="sm" onClick={() => setAllInSec(sec, true)}>启用全部</HoloButton>
                  <HoloButton size="sm" onClick={() => setAllInSec(sec, false)}>停用全部</HoloButton>
                </div>
              )}
            </div>

            {!isFolded &&
              (entries.length ? (
                <div className="border-t border-white/10">
                  {entries.map(([key, def]) => (
                    <div key={key} className="flex flex-wrap items-center gap-3 border-b border-white/5 px-4 py-2.5 last:border-0">
                      <HoloSwitch
                        checked={def.enabled !== false}
                        onChange={(v) => setAt(sec, key, v)}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] text-white/85">{def.title ?? key}</div>
                        <div className="text-[11px] text-white/40" title={String(def.desc ?? "")}>
                          {String(def.desc ?? "") || "—"}
                        </div>
                      </div>
                      <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-white/50">
                        {def.severity === "high" ? "严重" : def.severity === "low" ? "轻微" : "一般"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="border-t border-white/10">
                  <div className="px-4 py-4 text-center text-xs text-white/40">该分区暂无规则</div>
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
        <SectionTitle className="!mb-0">内置标准规则 · 只读仅可开关</SectionTitle>
        <BuiltinRulesSection />
      </section>
    </div>
  );
}