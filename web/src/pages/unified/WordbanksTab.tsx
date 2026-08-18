import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import { HoloInput, HoloSelect, HoloTextarea } from "../../components/ui/HoloInput";
import HoloModal from "../../components/ui/HoloModal";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { WordEntry, WordGroup, WordbanksData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

const genId = (p: string) => p + Math.random().toString(16).slice(2, 10);

/* 标签2 · 自定义词库：分组管理、词条增删改查、分组启用禁用、批量导入 */
export default function WordbanksTab() {
  const toast = useToast();
  const [data, setData] = useState<WordbanksData | null>(null);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");

  useEffect(() => {
    api.wordbanks().then((d) => setData({ groups: d.groups || [] }));
  }, []);

  if (!data) return null;

  const updGroup = (gi: number, patch: Partial<WordGroup>) =>
    setData((d) => (d ? { ...d, groups: d.groups.map((g, i) => (i === gi ? { ...g, ...patch } : g)) } : d));

  const updEntry = (gi: number, ei: number, patch: Partial<WordEntry>) =>
    setData((d) =>
      d
        ? {
            ...d,
            groups: d.groups.map((g, i) =>
              i === gi ? { ...g, entries: g.entries.map((e, j) => (j === ei ? { ...e, ...patch } : e)) } : g,
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
              { id: genId("w"), name: "新词库", module: "text_word", scope: "all", enabled: true, entries: [] },
            ],
          }
        : d,
    );

  const delGroup = (gi: number) =>
    setData((d) => (d ? { ...d, groups: d.groups.filter((_, i) => i !== gi) } : d));

  const addEntry = (gi: number) =>
    setData((d) =>
      d
        ? {
            ...d,
            groups: d.groups.map((g, i) =>
              i === gi
                ? {
                    ...g,
                    entries: [
                      ...g.entries,
                      { id: genId("e"), keyword: "", tag: "", suggestion: "", enabled: true },
                    ],
                  }
                : g,
            ),
          }
        : d,
    );

  const delEntry = (gi: number, ei: number) =>
    setData((d) =>
      d ? { ...d, groups: d.groups.map((g, i) => (i === gi ? { ...g, entries: g.entries.filter((_, j) => j !== ei) } : g)) } : d,
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
              i === gi ? { ...g, enabled: v, entries: g.entries.map((e) => ({ ...e, enabled: v })) } : g,
            ),
          }
        : d,
    );
  };

  const save = async () => {
    await api.saveWordbanks(data);
    toast("词库已保存");
  };

  /* 批量导入：后端 /api/wordbanks/import 仅解析文本返回词条（不落盘），
     此处把返回词条并入一个「批量导入 · 日期」新分组，再整体保存。 */
  const doImport = async () => {
    const text = importText.trim();
    if (!text) {
      toast("请粘贴要导入的词条，每行一个", "warn");
      return;
    }
    try {
      const r = await api.wordbankImport({ text });
      if (!r.entries.length) {
        toast("未解析到有效词条，请检查格式（每行一条或 CSV）", "warn");
        return;
      }
      const group: WordGroup = {
        id: genId("w"),
        name: `批量导入 · ${new Date().toISOString().slice(0, 10)}`,
        module: "text_word",
        scope: "all",
        enabled: true,
        entries: r.entries,
      };
      const next: WordbanksData = { groups: [...data.groups, group] };
      await api.saveWordbanks(next);
      setData(next);
      setImportOpen(false);
      setImportText("");
      toast(`已导入 ${r.entries.length} 条词条`);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton size="sm" variant="primary" onClick={save}>
          保存词库
        </HoloButton>
        <HoloButton size="sm" icon={<span>＋</span>} onClick={addGroup}>
          新建词库
        </HoloButton>
        <HoloButton size="sm" icon={<span>📋</span>} onClick={() => setImportOpen(true)}>
          批量导入
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
                    className="w-[110px]"
                    value={g.module}
                    onChange={(e) => updGroup(gi, { module: e.target.value })}
                  >
                    <option value="text_word">文本词</option>
                    <option value="format_regex">格式正则</option>
                  </HoloSelect>
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
                  <HoloSwitch checked={g.enabled !== false} onChange={(v) => updGroup(gi, { enabled: v })} />
                  <button
                    onClick={() => delGroup(gi)}
                    aria-label="删除分组"
                    className="grid h-7 w-7 place-items-center rounded-xl border border-white/10 bg-white/5 text-xs text-white/60 transition-all duration-500 hover:border-[rgba(255,107,125,0.4)] hover:text-[#ff6b7d] active:scale-95"
                  >
                    ✕
                  </button>
                </div>

                {/* 词条表（折叠隐藏） */}
                {!isFolded && (
                  <div className="mt-3 overflow-x-auto rounded-2xl border border-white/10">
                    <table className="holo-table w-full text-[13px]">
                      <thead>
                        <tr className="text-xs text-white/60">
                          <th className="px-3 py-2 text-left font-semibold">关键词</th>
                          <th className="px-3 py-2 text-left font-semibold">标签</th>
                          <th className="px-3 py-2 text-left font-semibold">建议</th>
                          <th className="px-3 py-2 text-left font-semibold">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {g.entries.map((e, ei) => (
                          <tr key={e.id} className="border-b border-white/5 text-white/85">
                            <td className="px-3 py-1.5">
                              <HoloInput
                                className="min-w-[150px] py-1.5"
                                value={e.keyword}
                                onChange={(ev) => updEntry(gi, ei, { keyword: ev.target.value })}
                              />
                            </td>
                            <td className="px-3 py-1.5">
                              <HoloInput
                                className="min-w-[110px] py-1.5"
                                value={e.tag}
                                placeholder="如：禁用词 / 规范术语"
                                onChange={(ev) => updEntry(gi, ei, { tag: ev.target.value })}
                              />
                            </td>
                            <td className="px-3 py-1.5">
                              <HoloInput
                                className="min-w-[150px] py-1.5"
                                value={e.suggestion}
                                placeholder="建议替换"
                                onChange={(ev) => updEntry(gi, ei, { suggestion: ev.target.value })}
                              />
                            </td>
                            <td className="whitespace-nowrap px-3 py-1.5">
                              <div className="flex items-center gap-2">
                                <HoloSwitch
                                  checked={e.enabled !== false}
                                  onChange={(v) => updEntry(gi, ei, { enabled: v })}
                                />
                                <button
                                  onClick={() => delEntry(gi, ei)}
                                  aria-label="删除词条"
                                  className="grid h-6 w-6 place-items-center rounded-lg border border-white/10 bg-white/5 text-xs text-white/60 transition-all duration-500 hover:border-[rgba(255,107,125,0.4)] hover:text-[#ff6b7d] active:scale-95"
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
                  <HoloButton size="sm" icon={<span>＋</span>} onClick={() => addEntry(gi)}>
                    添加词条
                  </HoloButton>
                  <HoloButton size="sm" onClick={() => setGroupEnabled(gi, true)}>
                    启用本组
                  </HoloButton>
                  <HoloButton size="sm" onClick={() => setGroupEnabled(gi, false)}>
                    停用本组
                  </HoloButton>
                  <span className="ml-auto text-xs text-white/40">{g.entries.length} 条词条</span>
                </div>
              </HoloCard>
            );
          })}
        </div>
      ) : (
        <HoloCard className="p-6">
          <EmptyState text="暂无词库分组，点击上方「新建词库」" icon="📚" />
        </HoloCard>
      )}

      {/* 批量导入弹窗 */}
      <HoloModal open={importOpen} title="批量导入词条" onClose={() => setImportOpen(false)} width={520}>
        <HoloTextarea
          placeholder={"每行一个词条\n可附加 ｜标签｜建议，例如：\n截止日期 ｜规范用语｜截至日期\n目前 ｜口头语｜现"}
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
        />
        <div className="mt-4 flex justify-end gap-2.5">
          <HoloButton onClick={() => setImportOpen(false)}>取消</HoloButton>
          <HoloButton variant="primary" onClick={doImport}>
            导入词条
          </HoloButton>
        </div>
      </HoloModal>
    </div>
  );
}
