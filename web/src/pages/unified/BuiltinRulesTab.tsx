import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import { HoloInput, HoloSelect } from "../../components/ui/HoloInput";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { RuleDef, RulesData, SettingsData, Severity } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

/* 标签4 · 内置标准规则：word / excel / textnorm / fluency 全部分区，
   启停开关 + 严重级别 + 整改建议 编辑，保存写入 config/rules.json（纯本地） */
const SECTIONS: [string, string][] = [
  ["word", "Word 文档"],
  ["excel", "Excel 表格"],
  ["textnorm", "文本规范检测"],
  ["fluency", "语句通顺检测"],
];

const SEV_OPTS: [Severity, string][] = [
  ["high", "严重"],
  ["medium", "一般"],
  ["low", "轻微"],
];

export default function BuiltinRulesTab() {
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
  }, []);

  if (!rules) return null;

  const setAt = (sec: string, key: string, field: string, v: unknown) => {
    const secObj = (rules[sec] ?? {}) as Record<string, RuleDef>;
    setRules((r) =>
      r
        ? {
            ...r,
            [sec]: { ...secObj, [key]: { ...secObj[key], [field]: v } },
          }
        : r,
    );
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
      toast("内置标准规则已保存到 config/rules.json，下次检测生效");
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

  const sens = (settings?.detection?.fluency_sensitivity as string) || "normal";
  const setSens = (v: string) =>
    setSettings((d) => (d ? { ...d, detection: { ...(d.detection ?? {}), fluency_sensitivity: v } } : d));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" disabled={saving} onClick={save}>
          {saving ? "保存中…" : "保存规则"}
        </HoloButton>
        <HoloButton size="sm" onClick={restore}>恢复默认</HoloButton>
      </div>

      {SECTIONS.map(([sec, label]) => {
        const secObj = (rules[sec] ?? {}) as Record<string, RuleDef>;
        const entries = Object.entries(secObj);
        const isFolded = folded.has(sec);
        const enabledCount = entries.filter(([, d]) => d.enabled !== false).length;
        return (
          <HoloCard key={sec} className="overflow-hidden p-0" glow="sm">
            {/* 分区头 */}
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
                  <HoloButton size="sm" onClick={() => {
                    const nf: Record<string, RuleDef> = {};
                    for (const [k, d] of Object.entries(secObj)) nf[k] = { ...d, enabled: true };
                    setRules((r) => (r ? { ...r, fluency: nf } : r));
                  }}>
                    启用全部
                  </HoloButton>
                  <HoloButton size="sm" onClick={() => {
                    const nf: Record<string, RuleDef> = {};
                    for (const [k, d] of Object.entries(secObj)) nf[k] = { ...d, enabled: false };
                    setRules((r) => (r ? { ...r, fluency: nf } : r));
                  }}>
                    停用全部
                  </HoloButton>
                </div>
              )}
            </div>

            {/* 规则列表 */}
            {!isFolded &&
              (entries.length ? (
                <div className="border-t border-white/10">
                  {entries.map(([key, def]) => (
                    <div key={key} className="flex flex-wrap items-center gap-3 border-b border-white/5 px-4 py-2.5 last:border-0">
                      <HoloSwitch
                        checked={def.enabled !== false}
                        onChange={(v) => setAt(sec, key, "enabled", v)}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] text-white/85">{def.title ?? key}</div>
                        <div className="text-[11px] text-white/40" title={String(def.desc ?? "")}>
                          {String(def.desc ?? "") || "—"}
                        </div>
                      </div>
                      <HoloSelect
                        className="w-[104px] py-1"
                        value={def.severity ?? "medium"}
                        onChange={(e) => setAt(sec, key, "severity", e.target.value)}
                      >
                        {SEV_OPTS.map(([v, n]) => (
                          <option key={v} value={v}>
                            {n}
                          </option>
                        ))}
                      </HoloSelect>
                      <HoloInput
                        className="w-[260px] py-1"
                        placeholder="整改建议"
                        value={def.suggestion ?? ""}
                        onChange={(e) => setAt(sec, key, "suggestion", e.target.value)}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="border-t border-white/10">
                  <EmptyState text="该分区暂无规则" icon="✨" />
                </div>
              ))}
          </HoloCard>
        );
      })}
    </div>
  );
}
