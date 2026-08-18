import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import { HoloTextarea } from "../../components/ui/HoloInput";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { DictMeta } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

/* 标签5 · 内置词库：dictionaries/*.txt 全量展示 / 编辑 / 保存
   （对应 textnorm 各检测项，纯本地文件，保存后下次检测自动生效） */
const humanSize = (n: number) =>
  n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;

export default function BuiltinDictTab() {
  const toast = useToast();
  const [files, setFiles] = useState<DictMeta[] | null>(null);
  const [opened, setOpened] = useState<string | null>(null); // 正在编辑的词库文件名
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const load = () =>
    api
      .dictionaries()
      .then((d) => setFiles(d.files))
      .catch((e) => toast((e as Error).message, "err"));
  useEffect(() => {
    load();
  }, []);

  if (!files) return null;

  const open = async (f: DictMeta) => {
    if (opened === f.file) {
      setOpened(null);
      return;
    }
    try {
      if (!(f.file in drafts)) {
        const d = await api.dictionary(f.file);
        setDrafts((ds) => ({ ...ds, [f.file]: d.content }));
      }
      setOpened(f.file);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const save = async (f: DictMeta) => {
    setSaving(f.file);
    try {
      await api.saveDictionary(f.file, drafts[f.file] ?? "");
      toast(`「${f.title}」词库已保存，下次检测自动生效`);
      setOpened(null);
      await load();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" onClick={load}>
          刷新列表
        </HoloButton>
      </div>

      {files.length ? (
        files.map((f) => {
          const isOpen = opened === f.file;
          return (
            <HoloCard key={f.file} className="overflow-hidden p-0" glow="sm">
              {/* 词库头 */}
              <div className="flex flex-wrap items-center gap-2.5 px-4 py-3">
                <button
                  onClick={() => open(f)}
                  title="展开 / 收起"
                  className="rounded-2xl border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs text-white/60 transition-all duration-500 hover:bg-white/10 hover:text-white active:scale-95"
                >
                  {isOpen ? "▾" : "▸"}
                </button>
                <b className="text-sm text-white/90">{f.title}</b>
                <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[11px] text-white/50">
                  {f.count} 条 · {humanSize(f.size)}
                </span>
                <span className="ml-auto text-xs text-white/40">{f.file}</span>
              </div>

              {/* 编辑区 */}
              {isOpen && (
                <div className="border-t border-white/10 p-4">
                  <HoloTextarea
                    className="h-64 font-mono text-[12px]"
                    placeholder={`编辑 ${f.file} 的内容…`}
                    value={drafts[f.file] ?? ""}
                    onChange={(e) => setDrafts((ds) => ({ ...ds, [f.file]: e.target.value }))}
                  />
                  <div className="mt-3 flex flex-wrap items-center gap-2.5">
                    <HoloButton variant="primary" disabled={saving === f.file} onClick={() => save(f)}>
                      {saving === f.file ? "保存中…" : "保存词库"}
                    </HoloButton>
                    <HoloButton
                      onClick={() => setOpened(null)}
                    >
                      取消
                    </HoloButton>
                    <span className="ml-auto text-xs text-white/40">
                      每行一条；词库文件头部注释含格式说明，编辑前请先阅读
                    </span>
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
