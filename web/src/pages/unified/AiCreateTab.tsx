import { useEffect, useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import HoloModal from "../../components/ui/HoloModal";
import { HoloTextarea } from "../../components/ui/HoloInput";
import HoloBadge from "../../components/ui/HoloBadge";
import TabBar from "../../components/common/TabBar";
import SectionTitle from "../../components/common/SectionTitle";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type {
  AiBuildResult,
  AiBuildRule,
  AiBuildWordbank,
  AiMemoryData,
  AiMemorySample,
  AiDiff,
  CustomRulesData,
  WordbanksData,
  RuleFilterStat,
} from "../../lib/types";
import { useToast } from "../../components/ui/Toast";
import { SEV_LABEL } from "../../lib/constants";

const genId = (p: string) => p + Math.random().toString(16).slice(2, 10);

const SUB_TABS = [
  { key: "dialogue", name: "对话式创建", icon: "💬" },
  { key: "text", name: "文本式创建", icon: "📝" },
  { key: "learning", name: "自学习记忆", icon: "🧠" },
] as const;

function download(blob: Blob, fname: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fname;
  a.click();
  URL.revokeObjectURL(url);
}

/* AI 规则词库智能生成统一模块：
   Tab1 对话式创建 / Tab2 文本式创建 / Tab3 自学习记忆（成对样本差异学习）
   所有渠道生成的规则词条统一经后端入库前置校验，来源标记：
   AI对话创建 / AI文本创建 / 本地AI自学习生成-人工校对样本；产物不入检测报告 */
export default function AiCreateTab() {
  const toast = useToast();
  const [sub, setSub] = useState<string>("dialogue");

  const [dialogue, setDialogue] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"dialogue" | "text" | "doc" | null>(null);
  const [docName, setDocName] = useState("");
  const [result, setResult] = useState<AiBuildResult | null>(null);
  const [filterStat, setFilterStat] = useState<RuleFilterStat | null>(null);
  const [wbSel, setWbSel] = useState<Record<string, boolean>>({});
  const [ruleSel, setRuleSel] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<"wb" | "rules" | null>(null);
  const docInputRef = useRef<HTMLInputElement>(null);

  const [mem, setMem] = useState<AiMemoryData | null>(null);
  const [memErr, setMemErr] = useState("");
  const [pairs, setPairs] = useState<AiDiff[] | null>(null);
  const [pairMsg, setPairMsg] = useState("");
  const [learning, setLearning] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"wordbanks-csv" | "wordbanks-txt" | "rules-csv" | "rules-txt" | null>(null);

  const loadMem = () => {
    api
      .aiMemory()
      .then(setMem)
      .catch((e: Error) => setMemErr(e.message));
  };
  useEffect(() => {
    if (sub === "learning") loadMem();
  }, [sub]);

  /* ---------- AI 创建（对话式 / 文本式 / 文档式） ---------- */
  const reportFilter = (f: RuleFilterStat | undefined, title: string) => {
    if (!f) return;
    setFilterStat(f);
    if (f.filtered_rules + f.filtered_entries === 0) {
      setFilterStat(null);
      toast(`${title}：原始规则 ${f.generated_rules} 条、词条 ${f.generated_entries} 条，全部通过校验`);
    }
  };

  const pick = (r: AiBuildResult) => {
    setResult(r);
    const ws: Record<string, boolean> = {};
    r.wordbanks.forEach((w, wi) => w.entries.forEach((_, ei) => { ws[`w${wi}-${ei}`] = true; }));
    const rs: Record<string, boolean> = {};
    r.rules.forEach((_, i) => { rs[`r${i}`] = true; });
    setWbSel(ws);
    setRuleSel(rs);
  };

  const runBuild = async (kind: "dialogue" | "text" | "doc", file?: File) => {
    setBusy(kind);
    try {
      const r = kind === "dialogue"
        ? await api.aiBuildDialogue(dialogue.trim())
        : kind === "text"
          ? await api.aiBuildText(text.trim())
          : await api.aiBuildDoc(file!);
      if (!r.ok) {
        toast(r.message, "err");
        setResult(null);
        return;
      }
      pick(r.result);
      reportFilter(r.filter, kind === "dialogue" ? "对话式创建完成" : kind === "text" ? "文本式创建完成" : `已阅读「${file!.name}」`);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  };

  const addWordbanks = async () => {
    if (!result) return;
    setSaving("wb");
    try {
      const cur: WordbanksData = (await api.wordbanks()).groups ? { groups: (await api.wordbanks()).groups } : { groups: [] };
      const picked: AiBuildWordbank[] = [];
      result.wordbanks.forEach((w, wi) => {
        const entries = w.entries.filter((_, ei) => wbSel[`w${wi}-${ei}`]);
        if (entries.length) picked.push({ name: w.name, entries });
      });
      if (!picked.length) {
        toast("未勾选任何词条", "warn");
        return;
      }
      const next = {
        groups: [
          ...cur.groups,
          ...picked.map((w) => ({
            id: genId("w"),
            name: w.name,
            module: "text_word",
            scope: "all",
            enabled: true,
            entries: w.entries.map((e) => ({ id: genId("e"), keyword: e.keyword, tag: e.tag, suggestion: e.suggestion, enabled: true })),
          })),
        ],
      };
      const res = await api.saveWordbanks(next);
      const n = picked.reduce((t, w) => t + w.entries.length, 0);
      const flt = res.filtered_entries ?? 0;
      toast(flt > 0
        ? `已加入词库 ${n - flt} 条词条，后端过滤无效词条 ${flt} 条（见日志）`
        : `已加入词库：${n} 条词条`);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(null);
    }
  };

  const addRules = async () => {
    if (!result) return;
    setSaving("rules");
    try {
      const cur: CustomRulesData = await api.customRules();
      const picked: AiBuildRule[] = result.rules.filter((_, i) => ruleSel[`r${i}`]);
      if (!picked.length) {
        toast("未勾选任何规则", "warn");
        return;
      }
      const next = {
        groups: [
          ...(cur.groups || []),
          {
            id: genId("g"),
            name: "AI 生成规则",
            category: "expression",
            scope: "all",
            enabled: true,
            rules: picked.map((r) => ({
              id: genId("r"),
              name: r.name,
              enabled: true,
              match_mode: r.match_mode as "keyword" | "regex",
              pattern: r.pattern,
              severity: r.severity as "low" | "medium" | "high",
              tag: "",
              suggestion: r.suggestion,
            })),
          },
        ],
      };
      const res = await api.saveCustomRules(next);
      const flt = res.filtered_rules ?? 0;
      toast(flt > 0
        ? `已加入规则 ${picked.length - flt} 条，后端过滤无效规则 ${flt} 条（见日志）`
        : `已加入规则：${picked.length} 条`);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(null);
    }
  };

  /* ---------- 自学习记忆（成对样本） ---------- */
  const toggleEnabled = async () => {
    if (!mem) return;
    try {
      const r = await api.aiMemoryToggle(!mem.enabled);
      toast(r.enabled ? "本地AI自学习已开启" : "本地AI自学习已关闭（已有记忆保留）");
      loadMem();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const uploadRevised = async (file: File) => {
    try {
      const r = await api.aiMemoryPair(file);
      if (!r.ok) {
        toast(r.message || "上传失败", "err");
        return;
      }
      setPairMsg(r.message || "修订文档已上传，可比对差异");
      toast(`修订文档「${file.name}」已配对，点击「比对差异」提取错误→正确片段`);
      setPairs(null);
      loadMem();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const computeDiffs = async () => {
    try {
      const r = await api.aiMemoryDiffs();
      if (!r.ok) {
        toast(r.message || "比对失败", "err");
        setPairs(null);
        return;
      }
      setPairs(r.diffs);
      toast(r.diffs.length
        ? `比对完成：提取到 ${r.diffs.length} 处差异，请确认后加入记忆样本库`
        : "比对完成：未发现可学习的差异片段");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const addSample = async () => {
    if (!mem || !pairs?.length) return;
    const st = mem.source_status;
    try {
      const r = await api.aiMemoryAddSample({
        diffs: pairs,
        source_doc: st.files.map((f) => f.name).join("、") || "—",
        revised_doc: st.revised || "—",
      });
      if (!r.ok) {
        toast(r.message || "加入失败", "err");
        return;
      }
      toast(`已确认并存入本地记忆库：${pairs.length} 个差异片段（完整文档已释放，仅保留差异文本）`);
      setPairs(null);
      setPairMsg("");
      loadMem();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const learn = async (s: AiMemorySample) => {
    if (learning) return;
    setLearning(s.id);
    try {
      const r = await api.aiMemoryLearn(s.id);
      if (r.ok) {
        const st = r.stats || {};
        const flt = st.filtered ?? 0;
        toast(flt > 0
          ? `本次学习生成 ${st.entries ?? 0} 条词库、${st.rules ?? 0} 条规则，后端过滤无效 ${flt} 条`
          : `本次学习生成 ${st.entries ?? 0} 条词库、${st.rules ?? 0} 条规则`);
      } else {
        toast(r.message || "学习失败", "err");
      }
      loadMem();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setLearning(null);
    }
  };

  const clearAll = async () => {
    if (!mem) return;
    if (!window.confirm("确定批量清空本地AI学习记忆？\n将删除全部学习样本与学习产出的词条/规则，不影响您手动导入或编写的规则词库。")) return;
    try {
      const r = await api.aiMemoryClear();
      toast(`已清空 ${r.removed ?? 0} 条学习记忆`);
      loadMem();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const exportLearned = async (format: "csv" | "txt", kind: "wordbanks" | "rules") => {
    const key = `${kind}-${format}` as const;
    setExporting(key);
    try {
      const blob = await api.aiMemoryExport(format, kind);
      download(blob, `ai_learning_${kind}_${Date.now()}.${format}`);
      toast(kind === "wordbanks" ? "学习产出的词库已导出" : "学习产出的规则已导出");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setExporting(null);
    }
  };

  const selWbCount = result
    ? result.wordbanks.reduce((n, w, wi) => n + w.entries.filter((_, ei) => wbSel[`w${wi}-${ei}`]).length, 0)
    : 0;
  const ruleCount = result ? result.rules.filter((_, i) => ruleSel[`r${i}`]).length : 0;

  const sampleStatus: Record<string, { tone: "gray" | "ok" | "warn" | "danger"; label: string }> = {
    pending: { tone: "gray", label: "待学习" },
    learning: { tone: "warn", label: "学习中" },
    done: { tone: "ok", label: "已学习" },
    failed: { tone: "danger", label: "失败" },
  };

  return (
    <div className="space-y-4">
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <TabBar tabs={SUB_TABS as unknown as { key: string; name: string; icon?: string }[]} active={sub} onChange={setSub} />
      </HoloCard>

      {sub === "dialogue" && (
        <HoloCard className="p-4" glow="sm">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-semibold text-white">💬 对话式创建</span>
            <span className="text-xs text-white/40">用一句话描述你的规则/词库需求</span>
          </div>
          <HoloTextarea
            className="h-28"
            placeholder={"例如：\n把「截止日期」标为不规范，建议改为「截至日期」；\n检测「我们立马搞定」这类口语化表达；\n金额单位必须写「万元」。"}
            value={dialogue}
            onChange={(e) => setDialogue(e.target.value)}
          />
          <div className="mt-2.5 flex items-center gap-2">
            <HoloButton variant="primary" disabled={busy !== null} onClick={() => runBuild("dialogue")}>
              {busy === "dialogue" ? "AI 生成中…（本地推理较慢，可能需要 5-20 分钟）" : "生成规则与词库"}
            </HoloButton>
            <span className="text-[11px] text-white/35">来源标记：AI对话创建</span>
          </div>
        </HoloCard>
      )}

      {sub === "text" && (
        <HoloCard className="p-4" glow="sm">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-semibold text-white">📝 文本式创建</span>
            <span className="text-xs text-white/40">粘贴准则 / 规范 / 范本文本，AI 批量提炼</span>
          </div>
          <HoloTextarea
            className="h-28"
            placeholder="粘贴一段准则、规范或范本文本（如《资产评估执业准则》条文、公司文书规范、正确范本段落）…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <HoloButton variant="primary" disabled={busy !== null} onClick={() => runBuild("text")}>
              {busy === "text" ? "AI 生成中…（本地推理较慢，可能需要 5-20 分钟）" : "生成规则与词库"}
            </HoloButton>
            <HoloButton variant="ghost" disabled={busy !== null} onClick={() => docInputRef.current?.click()}>
              {busy === "doc" ? "AI 阅读中…" : "或上传文档自建"}
            </HoloButton>
            <input
              ref={docInputRef}
              type="file"
              accept=".txt,.md,.csv,.docx,.pdf"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) {
                  setDocName(f.name);
                  runBuild("doc", f);
                }
                e.target.value = "";
              }}
            />
            {docName && <span className="text-xs text-white/40">{docName}</span>}
            <span className="text-[11px] text-white/35">来源标记：AI文本创建</span>
          </div>
        </HoloCard>
      )}

      {result && (
        <HoloCard className="p-4" glow="sm">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-sm font-semibold text-white">生成结果预览</span>
            <span className="text-xs text-white/40">勾选要加入的条目，确认后写入自定义词库 / 自定义规则</span>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <HoloButton size="sm" variant="primary" disabled={saving !== null} onClick={addWordbanks}>
                {saving === "wb" ? "加入中…" : `加入词库（${selWbCount}）`}
              </HoloButton>
              <HoloButton size="sm" variant="primary" disabled={saving !== null} onClick={addRules}>
                {saving === "rules" ? "加入中…" : `加入规则（${ruleCount}）`}
              </HoloButton>
            </div>
          </div>

          {result.wordbanks.length > 0 && (
            <div className="mt-3 space-y-3">
              {result.wordbanks.map((w, wi) => (
                <div key={`${wi}-${w.name}`} className="overflow-hidden rounded-2xl border border-white/10">
                  <div className="border-b border-white/10 bg-white/[0.04] px-3 py-2 text-[13px] font-semibold text-white/85">
                    📚 {w.name}
                    <span className="ml-2 text-[11px] font-normal text-white/40">{w.entries.length} 条</span>
                  </div>
                  <table className="holo-table w-full text-[13px]">
                    <thead>
                      <tr className="text-xs text-white/60">
                        <th className="w-10 px-3 py-1.5 text-left" />
                        <th className="px-3 py-1.5 text-left font-semibold">关键词</th>
                        <th className="px-3 py-1.5 text-left font-semibold">标签</th>
                        <th className="px-3 py-1.5 text-left font-semibold">建议替换</th>
                      </tr>
                    </thead>
                    <tbody>
                      {w.entries.map((e, ei) => (
                        <tr key={`${wi}-${ei}`} className="border-b border-white/5 text-white/85">
                          <td className="px-3 py-1.5">
                            <HoloSwitch checked={wbSel[`w${wi}-${ei}`]} onChange={(v) => setWbSel((s) => ({ ...s, [`w${wi}-${ei}`]: v }))} />
                          </td>
                          <td className="px-3 py-1.5">{e.keyword}</td>
                          <td className="px-3 py-1.5">{e.tag || "—"}</td>
                          <td className="px-3 py-1.5">{e.suggestion || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}

          {result.rules.length > 0 && (
            <div className="mt-3 overflow-hidden rounded-2xl border border-white/10">
              <div className="border-b border-white/10 bg-white/[0.04] px-3 py-2 text-[13px] font-semibold text-white/85">
                🧩 规则
                <span className="ml-2 text-[11px] font-normal text-white/40">{result.rules.length} 条</span>
              </div>
              <table className="holo-table w-full text-[13px]">
                <thead>
                  <tr className="text-xs text-white/60">
                    <th className="w-10 px-3 py-1.5 text-left" />
                    <th className="px-3 py-1.5 text-left font-semibold">规则名</th>
                    <th className="px-3 py-1.5 text-left font-semibold">模式</th>
                    <th className="px-3 py-1.5 text-left font-semibold">匹配式</th>
                    <th className="px-3 py-1.5 text-left font-semibold">级别</th>
                    <th className="px-3 py-1.5 text-left font-semibold">建议</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rules.map((r, i) => (
                    <tr key={i} className="border-b border-white/5 text-white/85">
                      <td className="px-3 py-1.5">
                        <HoloSwitch checked={ruleSel[`r${i}`]} onChange={(v) => setRuleSel((s) => ({ ...s, [`r${i}`]: v }))} />
                      </td>
                      <td className="px-3 py-1.5">{r.name}</td>
                      <td className="px-3 py-1.5">
                        <HoloBadge tone={r.match_mode === "regex" ? "sev-high" : "sev-medium"}>{r.match_mode === "regex" ? "正则" : "子串"}</HoloBadge>
                      </td>
                      <td className="max-w-[260px] truncate px-3 py-1.5 font-mono text-xs" title={r.pattern}>{r.pattern}</td>
                      <td className="px-3 py-1.5">
                        <HoloBadge tone={r.severity === "high" ? "sev-high" : r.severity === "medium" ? "sev-medium" : "sev-low"}>
                          {SEV_LABEL[r.severity] ?? r.severity}
                        </HoloBadge>
                      </td>
                      <td className="px-3 py-1.5">{r.suggestion || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.wordbanks.length === 0 && result.rules.length === 0 && (
            <p className="mt-3 text-xs text-white/40">AI 认为当前输入没有适合落地的规则或词库，可换个说法重试。</p>
          )}
        </HoloCard>
      )}

      {sub === "learning" && (
        <LearningSection
          mem={mem}
          memErr={memErr}
          pairs={pairs}
          pairMsg={pairMsg}
          learning={learning}
          exporting={exporting}
          sampleStatus={sampleStatus}
          onToggle={toggleEnabled}
          onUploadRevised={uploadRevised}
          onComputeDiffs={computeDiffs}
          onAddSample={addSample}
          onLearn={learn}
          onClear={clearAll}
          onExport={exportLearned}
          onReload={loadMem}
          toast={toast}
        />
      )}

      {/* AI 创建完成弹窗：告知无效规则被拦截 */}
      <HoloModal open={filterStat !== null} title="生成结果校验" onClose={() => setFilterStat(null)} width={560}>
        {filterStat && (
          <div className="space-y-3">
            <div className="rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] px-4 py-3 text-[13px] leading-relaxed text-white/85">
              本次生成原始规则 <b className="text-white">{filterStat.generated_rules}</b> 条、词条{" "}
              <b className="text-white">{filterStat.generated_entries}</b> 条；
              后端过滤无效规则 <b className="text-amber-300">{filterStat.filtered_rules}</b> 条、词条{" "}
              <b className="text-amber-300">{filterStat.filtered_entries}</b> 条；
              实际生效 <b className="text-emerald-300">{filterStat.accepted_rules}</b> 条规则、{" "}
              <b className="text-emerald-300">{filterStat.accepted_entries}</b> 条词条。
            </div>
            {filterStat.rejected.length > 0 && (
              <div className="max-h-[260px] space-y-1.5 overflow-y-auto">
                {filterStat.rejected.map((r, i) => (
                  <div key={i} className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2">
                    <div className="flex items-start justify-between gap-3 text-xs">
                      <span className="font-mono text-white/85">{r.pattern || r.name}</span>
                      <span className="shrink-0 text-rose-300/90">{r.reason}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="text-[11px] text-white/35">被过滤的规则不会出现在规则列表，详情已记录于后台日志（logs/rule_filter.log）。</p>
          </div>
        )}
      </HoloModal>
    </div>
  );
}

/* ---------- 自学习记忆子模块 ---------- */
interface LearningProps {
  mem: AiMemoryData | null;
  memErr: string;
  pairs: AiDiff[] | null;
  pairMsg: string;
  learning: string | null;
  exporting: string | null;
  sampleStatus: Record<string, { tone: "gray" | "ok" | "warn" | "danger"; label: string }>;
  onToggle: () => void;
  onUploadRevised: (f: File) => void;
  onComputeDiffs: () => void;
  onAddSample: () => void;
  onLearn: (s: AiMemorySample) => void;
  onClear: () => void;
  onExport: (format: "csv" | "txt", kind: "wordbanks" | "rules") => void;
  onReload: () => void;
  toast: (msg: string, tone?: "ok" | "err" | "warn") => void;
}

function LearningSection(p: LearningProps) {
  const revisedRef = useRef<HTMLInputElement>(null);
  if (p.memErr)
    return (
      <HoloCard className="p-6">
        <EmptyState text={`加载失败：${p.memErr}`} icon="⚠️" />
      </HoloCard>
    );
  if (!p.mem) return null;
  const mem = p.mem;

  const st = mem.stats;
  const src = mem.source_status;
  const pairing = src.available && src.valid;

  return (
    <div className="space-y-4">
      <HoloCard className="p-6" glow="sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-[720px]">
            <SectionTitle>本地 AI 模型自学习记忆（全程离线）</SectionTitle>
            <p className="mt-2 text-xs leading-relaxed text-white/55">
              检测任务完成后系统留存原始待检测文档（AI 不自动采集，仅您确认的样本用于学习）；您上传人工修改后的修订文档，系统比对出
              <b className="text-white/80"> 错误 → 正确</b> 差异片段，确认后由本地模型提炼标准表述，
              自动生成词库条目与校验规则并合并至「自定义词库 / 自定义正则规则」，来源标记为
              <b className="text-white/80"> 本地AI自学习生成-人工校对样本</b>。记忆数据全部保存在本机，不进入检测导出报告。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-white/60">总开关</span>
            <HoloSwitch checked={mem.enabled} onChange={p.onToggle} label={mem.enabled ? "已开启" : "已关闭"} />
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {[
            ["样本总数", st.samples, "🧾"],
            ["待学习", st.pending, "⏳"],
            ["已学习", st.done, "✅"],
            ["学习失败", st.failed, "⚠️"],
            ["产出词条", st.entries, "📚"],
            ["产出规则", st.rules, "🧩"],
          ].map(([k, v, ic]) => (
            <div key={k as string} className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
              <div className="text-[11px] text-white/45">
                {ic} {k}
              </div>
              <div className="mt-1 font-mono text-xl font-bold text-white/90">{v as number}</div>
            </div>
          ))}
        </div>
      </HoloCard>

      {/* 配对学习：原始文档 + 修订文档 → 差异确认 */}
      <HoloCard className="p-6">
        <SectionTitle>配对人工校对样本（原始文档 + 修订文档）</SectionTitle>
        {!mem.enabled && (
          <p className="mt-2 text-xs text-[#ffb454]">总开关已关闭，请先开启自学习才能配对学习。</p>
        )}
        {!pairing ? (
          <div className="mt-3 flex items-start gap-2 rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] px-4 py-3 text-[13px] text-white/80">
            <span>⚠️</span>
            <span>{src.message || "当前无可配对的原始文档，请先执行一次文档检测流程（上传/扫描文档并完成核验）。"}</span>
          </div>
        ) : (
          <div className="mt-3 space-y-3">
            <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
              <div className="text-[11px] text-white/45">系统留存原始文档（{src.files.length} 份）</div>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {src.files.map((f, i) => (
                  <span key={i} className="rounded-xl border border-white/10 bg-white/[0.06] px-3 py-1.5 text-xs text-white/75">
                    📄 {f.name}
                  </span>
                ))}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <HoloButton variant="primary" disabled={!mem.enabled} onClick={() => revisedRef.current?.click()}>
                上传修订文档
              </HoloButton>
              <input
                ref={revisedRef}
                type="file"
                accept=".txt,.md,.csv,.docx,.pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) {
                    p.onUploadRevised(f);
                  }
                  e.target.value = "";
                }}
              />
              {src.revised && <span className="text-xs text-white/40">已配对修订文档：{src.revised}</span>}
              <HoloButton variant="ghost" disabled={!mem.enabled || !src.revised} onClick={p.onComputeDiffs}>
                比对差异
              </HoloButton>
            </div>
            {p.pairMsg && <p className="text-xs text-white/40">{p.pairMsg}</p>}
            {p.pairs !== null && (
              <div className="space-y-2">
                {p.pairs.length ? (
                  <>
                    <div className="max-h-[320px] space-y-2 overflow-y-auto">
                      {p.pairs.map((d, i) => (
                        <div key={i} className="rounded-2xl border border-white/10 bg-white/[0.04] p-3">
                          <div className="text-[11px] text-rose-300/80">错误片段 {i + 1}</div>
                          <p className="mt-1 break-all text-[13px] text-white/85">{d.old || "（无）"}</p>
                          <div className="mt-2 text-[11px] text-emerald-300/80">修订后正确片段</div>
                          <p className="mt-1 break-all text-[13px] text-white/85">{d.new || "（无）"}</p>
                        </div>
                      ))}
                    </div>
                    <HoloButton variant="primary" onClick={p.onAddSample}>
                      确认正确，加入本地记忆样本库（{p.pairs.length} 处差异）
                    </HoloButton>
                  </>
                ) : (
                  <p className="text-xs text-white/40">未发现可学习的差异片段。</p>
                )}
              </div>
            )}
          </div>
        )}
      </HoloCard>

      {/* 样本列表 */}
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <div className="flex items-center justify-between px-6 pt-5">
          <SectionTitle>学习样本（{mem.samples.length}）</SectionTitle>
          <HoloButton variant="danger" size="sm" onClick={p.onClear} disabled={!mem.samples.length && !mem.learned.length}>
            批量清空学习记忆
          </HoloButton>
        </div>
        {mem.samples.length ? (
          <div className="space-y-3 p-6 pt-4">
            {mem.samples.map((s) => {
              const stb = p.sampleStatus[s.status] ?? p.sampleStatus.pending;
              return (
                <div key={s.id} className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <HoloBadge tone={stb.tone}>{stb.label}</HoloBadge>
                    {s.enabled ? (
                      <HoloBadge tone="accent">启用</HoloBadge>
                    ) : (
                      <HoloBadge tone="gray">已禁用</HoloBadge>
                    )}
                    <span className="text-[11px] text-white/40">来源：{s.source_doc} → {s.revised_doc}</span>
                    <span className="text-[11px] text-white/30">{s.created_at}</span>
                    {s.status === "done" && s.result_count > 0 && (
                      <span className="text-[11px] text-[#4fd6c9]">产出 {s.result_count} 条</span>
                    )}
                  </div>
                  <div className="mt-2 space-y-2">
                    {(s.diffs || []).map((d, i) => (
                      <div key={i} className="flex flex-wrap gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-[13px]">
                        <span className="max-w-[46%] break-all text-rose-300/90">❌ {d.old || "（无）"}</span>
                        <span className="text-white/25">→</span>
                        <span className="max-w-[46%] break-all text-emerald-300/90">✅ {d.new || "（待补充）"}</span>
                      </div>
                    ))}
                  </div>
                  {s.note && <p className="mt-2 text-xs text-white/40">备注：{s.note}</p>}
                  {s.error && (
                    <p className="mt-2 rounded-xl border border-[rgba(255,107,125,0.3)] bg-[rgba(255,107,125,0.08)] px-3 py-2 text-xs text-[#ffb3bd]">
                      学习失败：{s.error}
                    </p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <HoloButton size="sm" variant="primary" onClick={() => p.onLearn(s)} disabled={!!p.learning || !mem.enabled || !s.enabled}>
                      {p.learning === s.id ? "学习中…（本地推理较慢）" : "立即学习"}
                    </HoloButton>
                    <HoloButton
                      size="sm"
                      onClick={async () => {
                        try {
                          await api.aiMemorySampleToggle(s.id, !s.enabled);
                          p.onReload();
                        } catch (e) {
                          p.toast((e as Error).message, "err");
                        }
                      }}
                    >
                      {s.enabled ? "禁用" : "启用"}
                    </HoloButton>
                    <HoloButton
                      size="sm"
                      variant="danger"
                      onClick={async () => {
                        if (!window.confirm("删除该样本？其已学习的产出条目不受影响（可在下方列表中管理）。")) return;
                        try {
                          await api.aiMemorySampleDelete(s.id);
                          p.toast("样本已删除");
                          p.onReload();
                        } catch (e) {
                          p.toast((e as Error).message, "err");
                        }
                      }}
                    >
                      删除
                    </HoloButton>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="p-6 pt-2">
            <EmptyState text="暂无学习样本：执行检测 → 上传修订文档 → 比对差异 → 确认加入" icon="🧠" />
          </div>
        )}
      </HoloCard>

      {/* 学习产出（词库条目 + 校验规则，可导出备份） */}
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <div className="px-6 pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <SectionTitle>学习产出（{mem.learned.length}）</SectionTitle>
              <p className="mt-1 text-[11px] text-white/40">
                已自动合并至「自定义词库 / 自定义正则规则」（来源标记：本地AI自学习生成-人工校对样本）；启用状态即时参与检测，可在对应管理页二次编辑。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <HoloButton size="sm" variant="ghost" disabled={p.exporting !== null} onClick={() => p.onExport("csv", "wordbanks")}>
                {p.exporting === "wordbanks-csv" ? "导出中…" : "导出词库 CSV"}
              </HoloButton>
              <HoloButton size="sm" variant="ghost" disabled={p.exporting !== null} onClick={() => p.onExport("txt", "wordbanks")}>
                导出词库 TXT
              </HoloButton>
              <HoloButton size="sm" variant="ghost" disabled={p.exporting !== null} onClick={() => p.onExport("csv", "rules")}>
                导出规则 CSV
              </HoloButton>
              <HoloButton size="sm" variant="ghost" disabled={p.exporting !== null} onClick={() => p.onExport("txt", "rules")}>
                导出规则 TXT
              </HoloButton>
            </div>
          </div>
        </div>
        {mem.learned.length ? (
          <div className="space-y-3 p-6 pt-4">
            {mem.learned.map((l) => (
              <div key={l.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
                <HoloBadge tone={l.kind === "wordbank" ? "accent" : "warn"}>{l.kind === "wordbank" ? "词库条目" : "校验规则"}</HoloBadge>
                <span className="min-w-[120px] text-sm font-semibold text-white/85">
                  {l.kind === "wordbank" ? l.keyword : l.name}
                </span>
                {l.kind === "rule" && (
                  <span className="font-mono text-[11px] text-white/40">
                    [{l.match_mode}] {l.pattern}
                  </span>
                )}
                <span className="max-w-[320px] truncate text-xs text-white/45">{l.suggestion}</span>
                <span className="text-[10px] text-white/30">本地AI自学习生成-人工校对样本 · {l.created_at}</span>
                <span className="ml-auto flex items-center gap-2">
                  <HoloSwitch
                    checked={l.enabled}
                    onChange={async () => {
                      try {
                        await api.aiMemoryLearnedToggle(l.id, !l.enabled);
                        p.onReload();
                      } catch (e) {
                        p.toast((e as Error).message, "err");
                      }
                    }}
                    label={l.enabled ? "启用" : "禁用"}
                  />
                  <HoloButton
                    size="sm"
                    variant="danger"
                    onClick={async () => {
                      if (!window.confirm("删除该学习产出？（从词库/自定义规则中移除）")) return;
                      try {
                        await api.aiMemoryLearnedDelete(l.id);
                        p.toast("已删除");
                        p.onReload();
                      } catch (e) {
                        p.toast((e as Error).message, "err");
                      }
                    }}
                  >
                    删除
                  </HoloButton>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-6 pt-2">
            <EmptyState text="暂无学习产出：添加样本后点击「立即学习」即可生成" icon="📚" />
          </div>
        )}
      </HoloCard>
    </div>
  );
}
