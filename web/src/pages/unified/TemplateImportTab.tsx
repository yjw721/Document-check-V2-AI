import { useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloBadge from "../../components/ui/HoloBadge";
import { HoloSelect, FieldLabel } from "../../components/ui/HoloInput";
import SectionTitle from "../../components/common/SectionTitle";
import EmptyState from "../../components/common/EmptyState";
import DropZone, { type DropZoneHandle } from "../../components/common/DropZone";
import { api } from "../../lib/api";
import { FT_ICON, FT_LABEL, TP_CATS, TP_TAG_CLS } from "../../lib/constants";
import type { TemplateDraft } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

const ACCEPT = ".docx,.pdf,.txt,.csv,.scel";

/* 标签3 · 词库与标准规则批量导入：上传基准文件 → 解析草案 → 勾选 → 确认导入（仅追加不覆盖） */
export default function TemplateImportTab() {
  const toast = useToast();
  const dzRef = useRef<DropZoneHandle>(null);
  const [draft, setDraft] = useState<TemplateDraft | null>(null);
  const [category, setCategory] = useState("general");
  const [selRules, setSelRules] = useState<Set<string>>(new Set());
  const [selEntries, setSelEntries] = useState<Set<string>>(new Set());
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);

  /* ---- 选中状态与后端同步（templateSelect 契约：未列出的项视为取消选择） ---- */
  const sync = (rules: Set<string>, entries: Set<string>) => {
    void api
      .templateSelect({ rule_ids: [...rules], entry_ids: [...entries] })
      .catch((e) => toast((e as Error).message, "err"));
  };

  const toggleRule = (id: string) => {
    const next = new Set(selRules);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelRules(next);
    sync(next, selEntries);
  };

  const toggleEntry = (id: string) => {
    const next = new Set(selEntries);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelEntries(next);
    sync(selRules, next);
  };

  const setAllRules = (v: boolean) => {
    const next = new Set(v && draft ? draft.rules.map((r) => r.id) : []);
    setSelRules(next);
    sync(next, selEntries);
  };

  const setAllEntries = (v: boolean) => {
    const next = new Set(v && draft ? draft.entries.map((e) => e.id) : []);
    setSelEntries(next);
    sync(selRules, next);
  };

  /* ---- 上传解析 ---- */
  const onFiles = async (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("category", category);
    setUploading(true);
    try {
      const d = await api.templateUpload(fd);
      setDraft(d);
      setSelRules(new Set(d.rules.filter((r) => r.selected).map((r) => r.id)));
      setSelEntries(new Set(d.entries.filter((e) => e.selected).map((e) => e.id)));
      toast(`解析完成：${d.rules.length} 条规则、${d.entries.length} 条词条、${d.conflicts.length} 处冲突`);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setUploading(false);
    }
  };

  /* ---- 确认导入 / 清空 ---- */
  const doImport = async () => {
    if (!selRules.size && !selEntries.size) {
      toast("请先勾选要导入的规则或词条", "warn");
      return;
    }
    setBusy(true);
    try {
      await api.templateSelect({ rule_ids: [...selRules], entry_ids: [...selEntries] });
      const r = await api.templateImport({ rule_ids: [...selRules], entry_ids: [...selEntries] });
      const fr = r.filtered_rules ?? 0;
      const fe = r.filtered_entries ?? 0;
      toast(fr + fe > 0
        ? `已导入 ${r.imported_rules} 条规则、${r.imported_entries} 条词条（追加，不覆盖原有配置）；后端过滤无效规则 ${fr} 条、词条 ${fe} 条`
        : `已导入 ${r.imported_rules} 条规则、${r.imported_entries} 条词条（追加，不覆盖原有配置）`);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const clearDraft = async () => {
    try {
      await api.templateClear();
      setDraft(null);
      setSelRules(new Set());
      setSelEntries(new Set());
      toast("范本草案已清空");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const hasDraft = !!draft && draft.docs.length > 0;
  const totalRules = draft?.rules.length || 0;
  const totalEntries = draft?.entries.length || 0;

  return (
    <div className="space-y-4">
      {/* 上传区 */}
      <HoloCard className="p-5" glow="sm">
        <SectionTitle
          extra={
            <HoloBadge tone={hasDraft ? "ok" : "gray"}>{hasDraft ? `已载入 ${draft!.docs.length} 份基准文件` : "未上传"}</HoloBadge>
          }
        >
          基准文件上传
        </SectionTitle>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-[230px]">
            <FieldLabel>外部基准类别（决定标签与导入分组名）</FieldLabel>
            <HoloSelect value={category} onChange={(e) => setCategory(e.target.value)}>
              {TP_CATS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </HoloSelect>
          </div>
          <div className="min-w-[300px] flex-1">
            <DropZone ref={dzRef} accept={ACCEPT} disabled={uploading} onFiles={onFiles}>
              <div className="flex flex-col items-center gap-1.5 px-6 py-7 text-center">
                <span className="text-2xl">📥</span>
                <p className="text-sm text-white/80">拖拽基准文件到此处，或点击选择</p>
                <p className="text-xs text-white/40">
                  支持 .docx / .pdf / .txt / .csv / .scel，可多选，单个 ≤ 50MB · 仅本地内存解析，零联网
                </p>
              </div>
            </DropZone>
          </div>
        </div>
        {uploading && (
          <div className="mt-3 flex items-center gap-2.5 text-sm text-white/70">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--border-accent-soft)] border-t-[var(--border-accent)]" />
            正在解析基准文件，请稍候…
          </div>
        )}
      </HoloCard>

      {!hasDraft ? (
        <HoloCard className="p-6">
          <EmptyState text="暂无范本草案：上传基准文件后，这里会列出可勾选的规则与词条" icon="🗂️" />
        </HoloCard>
      ) : (
        <>
          {/* 已载入文件 */}
          <HoloCard className="p-5" glow="sm">
            <SectionTitle extra={<HoloBadge tone="gray">{draft!.docs.length} 份</HoloBadge>}>已解析文件</SectionTitle>
            <div className="flex flex-wrap gap-2">
              {draft!.docs.map((d, i) => (
                <div
                  key={i}
                  className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.04] px-3.5 py-2 text-sm"
                >
                  <span>{FT_ICON[d.file_type] || "📄"}</span>
                  <span className="max-w-[220px] truncate text-white/85" title={d.name}>
                    {d.name}
                  </span>
                  <HoloBadge tone={d.ok ? "ok" : "danger"}>{d.ok ? FT_LABEL[d.file_type] || d.file_type : "解析失败"}</HoloBadge>
                  {!d.ok && d.error && <span className="max-w-[180px] truncate text-xs text-[#ff6b7d]">{d.error}</span>}
                </div>
              ))}
            </div>
          </HoloCard>

          {/* 规则草案 */}
          {totalRules > 0 && (
            <HoloCard className="p-5" glow="sm">
              <SectionTitle
                extra={
                  <div className="flex items-center gap-2">
                    <HoloBadge tone="gray">
                      已选 {selRules.size} / {totalRules}
                    </HoloBadge>
                    <HoloButton size="sm" onClick={() => setAllRules(true)}>
                      ☑ 全选
                    </HoloButton>
                    <HoloButton size="sm" onClick={() => setAllRules(false)}>
                      清空
                    </HoloButton>
                  </div>
                }
              >
                规则草案
              </SectionTitle>
              <div className="overflow-x-auto rounded-2xl border border-white/10">
                <table className="holo-table w-full text-[13px]">
                  <thead>
                    <tr className="text-xs text-white/60">
                      <th className="px-3 py-2.5 text-left font-semibold">勾选</th>
                      <th className="px-3 py-2.5 text-left font-semibold">规则名</th>
                      <th className="px-3 py-2.5 text-left font-semibold">类型</th>
                      <th className="px-3 py-2.5 text-left font-semibold">匹配式</th>
                      <th className="px-3 py-2.5 text-left font-semibold">标签</th>
                      <th className="px-3 py-2.5 text-left font-semibold">建议</th>
                      <th className="px-3 py-2.5 text-left font-semibold">来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft!.rules.map((r) => (
                      <tr key={r.id} className="border-b border-white/5 text-white/85">
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={selRules.has(r.id)}
                            onChange={() => toggleRule(r.id)}
                            className="h-4 w-4 cursor-pointer rounded accent-purple-500"
                          />
                        </td>
                        <td className="px-3 py-2 font-medium">{r.name}</td>
                        <td className="px-3 py-2">
                          <HoloBadge tone="gray">{r.match_mode === "regex" ? "正则" : "子串"}</HoloBadge>
                        </td>
                        <td className="max-w-[260px] truncate px-3 py-2 font-mono text-xs text-white/70" title={r.pattern}>
                          {r.pattern}
                        </td>
                        <td className="px-3 py-2">
                          {r.tag ? (
                            <HoloBadge tone={TP_TAG_CLS[r.tag] || "gray"}>{r.tag}</HoloBadge>
                          ) : (
                            <span className="text-white/30">—</span>
                          )}
                        </td>
                        <td className="max-w-[200px] truncate px-3 py-2 text-white/70" title={r.suggestion}>
                          {r.suggestion || <span className="text-white/30">—</span>}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-white/40">
                          {r.source_doc}
                          {r.source_page ? ` · P${r.source_page}` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </HoloCard>
          )}

          {/* 词条草案 */}
          {totalEntries > 0 && (
            <HoloCard className="p-5" glow="sm">
              <SectionTitle
                extra={
                  <div className="flex items-center gap-2">
                    <HoloBadge tone="gray">
                      已选 {selEntries.size} / {totalEntries}
                    </HoloBadge>
                    <HoloButton size="sm" onClick={() => setAllEntries(true)}>
                      ☑ 全选
                    </HoloButton>
                    <HoloButton size="sm" onClick={() => setAllEntries(false)}>
                      清空
                    </HoloButton>
                  </div>
                }
              >
                词条草案
              </SectionTitle>
              <div className="overflow-x-auto rounded-2xl border border-white/10">
                <table className="holo-table w-full text-[13px]">
                  <thead>
                    <tr className="text-xs text-white/60">
                      <th className="px-3 py-2.5 text-left font-semibold">勾选</th>
                      <th className="px-3 py-2.5 text-left font-semibold">关键词</th>
                      <th className="px-3 py-2.5 text-left font-semibold">标签</th>
                      <th className="px-3 py-2.5 text-left font-semibold">建议</th>
                      <th className="px-3 py-2.5 text-left font-semibold">来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft!.entries.map((e) => (
                      <tr key={e.id} className="border-b border-white/5 text-white/85">
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={selEntries.has(e.id)}
                            onChange={() => toggleEntry(e.id)}
                            className="h-4 w-4 cursor-pointer rounded accent-purple-500"
                          />
                        </td>
                        <td className="px-3 py-2 font-medium">{e.keyword}</td>
                        <td className="px-3 py-2">
                          {e.tag ? (
                            <HoloBadge tone={TP_TAG_CLS[e.tag] || "gray"}>{e.tag}</HoloBadge>
                          ) : (
                            <span className="text-white/30">—</span>
                          )}
                        </td>
                        <td className="max-w-[240px] truncate px-3 py-2 text-white/70" title={e.suggestion}>
                          {e.suggestion || <span className="text-white/30">—</span>}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-white/40">
                          {e.source_doc}
                          {e.source_page ? ` · P${e.source_page}` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </HoloCard>
          )}

          {/* 矛盾冲突 */}
          {draft!.conflicts.length > 0 && (
            <HoloCard className="p-5" glow="sm">
              <SectionTitle extra={<HoloBadge tone="warn">{draft!.conflicts.length} 处</HoloBadge>}>范本矛盾提示</SectionTitle>
              <div className="space-y-2.5">
                {draft!.conflicts.map((c, i) => (
                  <div key={i} className="rounded-2xl border border-[rgba(255,180,84,0.25)] bg-[rgba(255,180,84,0.06)] px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-[#ffb454]">⚠️ {c.topic}</span>
                      <span className="text-xs text-white/40">{c.docs.join("、")}</span>
                    </div>
                    <ul className="mt-2 space-y-1">
                      {c.statements.map((s, j) => (
                        <li key={j} className="text-[13px] leading-relaxed text-white/80">
                          · {s.text}
                        </li>
                      ))}
                    </ul>
                    {c.suggestion && <p className="mt-2 text-[13px] text-[#ffb454]/90">建议：{c.suggestion}</p>}
                  </div>
                ))}
              </div>
            </HoloCard>
          )}

          {/* 参考资料 */}
          {draft!.references.length > 0 && (
            <HoloCard className="p-5" glow="sm">
              <SectionTitle extra={<HoloBadge tone="gray">{draft!.references.length} 条</HoloBadge>}>参考资料</SectionTitle>
              <div className="max-h-[240px] space-y-2 overflow-y-auto pr-1">
                {draft!.references.map((r, i) => (
                  <div key={i} className="flex items-start gap-2.5 rounded-2xl border border-white/5 bg-white/[0.03] px-3.5 py-2.5">
                    <span className="text-sm leading-none text-white/30">“</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] leading-relaxed text-white/80">{r.sentence}</p>
                      <p className="mt-1 text-[11px] text-white/40">
                        来源：{r.source_doc}
                        {r.source_page ? ` · 第 ${r.source_page} 页` : ""}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </HoloCard>
          )}

          {/* 操作区 */}
          <div className="flex flex-wrap items-center gap-2.5">
            <HoloButton
              variant="primary"
              disabled={!selRules.size && !selEntries.size}
              onClick={doImport}
              icon={<span>⬇</span>}
            >
              {busy ? "导入中…" : "导入选中项"}
            </HoloButton>
            <HoloButton variant="danger" onClick={clearDraft}>
              清空草案
            </HoloButton>
            <span className="ml-auto text-xs text-white/40">
              将已选内容追加为「外部导入 · 类别名」分组，绝不覆盖原有规则 / 词库
            </span>
          </div>
        </>
      )}
    </div>
  );
}
