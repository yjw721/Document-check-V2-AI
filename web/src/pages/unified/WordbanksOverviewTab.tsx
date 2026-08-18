import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import SectionTitle from "../../components/common/SectionTitle";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { DictMeta, RulesData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";
import { onRulesChanged } from "../../lib/events";
import WordbanksTab from "./WordbanksTab";

const humanSize = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

/* 词库总览：自定义词库（完整增删改）+ 内置标准词库（只读，仅可启停对应检测项开关） */
function BuiltinDictSection() {
  const toast = useToast();
  const [files, setFiles] = useState<DictMeta[] | null>(null);
  const [rules, setRules] = useState<RulesData | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [contents, setContents] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const load = () => {
    api.dictionaries().then((d) => setFiles(d.files)).catch((e) => toast((e as Error).message, "err"));
    api.rules().then(setRules).catch(() => setRules(null));
  };
  useEffect(() => {
    load();
    return onRulesChanged(load);
  }, []);

  if (!files) return null;

  const toggle = (f: DictMeta) => {
    if (!rules) return;
    const tn = (rules.textnorm ?? {}) as Record<string, { enabled?: boolean }>;
    const def = tn[f.rule];
    if (!def) return;
    setRules((r) =>
      r
        ? { ...r, textnorm: { ...tn, [f.rule]: { ...def, enabled: !(def.enabled !== false) } } }
        : r,
    );
  };

  const save = async () => {
    if (!rules) return;
    setSaving(true);
    try {
      await api.saveRules(rules);
      toast("内置词库启停已保存到 config/rules.json，下次检测生效");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };

  const open = async (f: DictMeta) => {
    if (opened === f.file) {
      setOpened(null);
      return;
    }
    try {
      if (!(f.file in contents)) {
        const d = await api.dictionary(f.file);
        setContents((c) => ({ ...c, [f.file]: d.content }));
      }
      setOpened(f.file);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const tn = (rules?.textnorm ?? {}) as Record<string, { enabled?: boolean }>;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" disabled={saving || !rules} onClick={save}>
          {saving ? "保存中…" : "保存内置词库开关"}
        </HoloButton>
        <span className="ml-auto text-xs text-white/40">
          内置标准词库为只读内容，仅可启停对应检测项，不可修改或删除
        </span>
      </div>

      {files.length ? (
        files.map((f) => {
          const isOpen = opened === f.file;
          const def = tn[f.rule];
          const enabled = def ? def.enabled !== false : true;
          return (
            <HoloCard key={f.file} className="overflow-hidden p-0" glow="sm">
              <div className="flex flex-wrap items-center gap-2.5 px-4 py-3">
                <button
                  onClick={() => open(f)}
                  title="查看词库内容（只读）"
                  className="rounded-2xl border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs text-white/60 transition-all duration-500 hover:bg-white/10 hover:text-white active:scale-95"
                >
                  {isOpen ? "▾" : "▸"}
                </button>
                <b className="text-sm text-white/90">{f.title}</b>
                <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[11px] text-white/50">
                  {f.count} 条 · {humanSize(f.size)}
                </span>
                <HoloSwitch
                  checked={enabled}
                  onChange={() => toggle(f)}
                  disabled={!def}
                  label={def ? undefined : "未关联检测项"}
                />
                <span className="ml-auto text-xs text-white/40">{f.file}</span>
              </div>

              {isOpen && (
                <div className="border-t border-white/10 p-4">
                  <pre className="max-h-72 overflow-auto rounded-2xl border border-white/10 bg-white/5 p-3 font-mono text-[12px] leading-relaxed text-white/70">
                    {contents[f.file] ?? "加载中…"}
                  </pre>
                  <div className="mt-2 text-xs text-white/40">
                    每行一条；词库文件头部注释含格式说明。内置内容只读，如需增改请复制到自定义词库。
                  </div>
                </div>
              )}
            </HoloCard>
          );
        })
      ) : (
        <HoloCard className="p-6">
          <EmptyState text="暂无内置词库数据" icon="📖" />
        </HoloCard>
      )}
    </div>
  );
}

export default function WordbanksOverviewTab() {
  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <SectionTitle className="!mb-0">自定义词库 · 可增删改查</SectionTitle>
        <WordbanksTab />
      </section>
      <section className="space-y-3">
        <SectionTitle className="!mb-0">内置标准词库 · 只读仅可开关</SectionTitle>
        <BuiltinDictSection />
      </section>
    </div>
  );
}