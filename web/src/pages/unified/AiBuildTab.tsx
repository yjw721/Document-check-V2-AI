import { useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloSwitch from "../../components/ui/HoloSwitch";
import { HoloTextarea } from "../../components/ui/HoloInput";
import HoloBadge from "../../components/ui/HoloBadge";
import { api } from "../../lib/api";
import type { AiBuildResult, AiBuildRule, AiBuildWordbank, CustomRulesData, WordbanksData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";
import { SEV_LABEL } from "../../lib/constants";

const genId = (p: string) => p + Math.random().toString(16).slice(2, 10);

/* 标签6 · AI 智能创建：对话式生成规则/词库 + 上传文档自建（阅读后提取） */
export default function AiBuildTab() {
  const toast = useToast();
  const [dialogue, setDialogue] = useState("");
  const [busy, setBusy] = useState<"dialogue" | "doc" | null>(null);
  const [docName, setDocName] = useState("");
  const [result, setResult] = useState<AiBuildResult | null>(null);
  const [wbSel, setWbSel] = useState<Record<string, boolean>>({});
  const [ruleSel, setRuleSel] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<"wb" | "rules" | null>(null);
  const docInputRef = useRef<HTMLInputElement>(null);

  const pick = (r: AiBuildResult) => {
    setResult(r);
    const ws: Record<string, boolean> = {};
    r.wordbanks.forEach((w, wi) => w.entries.forEach((_, ei) => { ws[`w${wi}-${ei}`] = true; }));
    const rs: Record<string, boolean> = {};
    r.rules.forEach((_, i) => { rs[`r${i}`] = true; });
    setWbSel(ws);
    setRuleSel(rs);
  };

  const genDialogue = async () => {
    const t = dialogue.trim();
    if (!t) {
      toast("请先描述你的规则/词库需求", "warn");
      return;
    }
    setBusy("dialogue");
    try {
      const r = await api.aiBuildDialogue(t);
      if (!r.ok) {
        toast(r.message, "err");
        setResult(null);
        return;
      }
      pick(r.result);
      toast(`生成完成：${r.result.wordbanks.length} 个词库分组 · ${r.result.rules.length} 条规则，请确认后加入`);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  };

  const genDoc = async (file: File) => {
    setBusy("doc");
    try {
      const r = await api.aiBuildDoc(file);
      if (!r.ok) {
        toast(r.message, "err");
        setResult(null);
        return;
      }
      pick(r.result);
      toast(`已阅读「${file.name}」并提取：${r.result.wordbanks.length} 个词库分组 · ${r.result.rules.length} 条规则`);
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
      await api.saveWordbanks(next);
      toast(`已加入词库：${picked.reduce((n, w) => n + w.entries.length, 0)} 条词条`);
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
      await api.saveCustomRules(next);
      toast(`已加入规则：${picked.length} 条`);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(null);
    }
  };

  const selWbCount = result
    ? result.wordbanks.reduce((n, w, wi) => n + w.entries.filter((_, ei) => wbSel[`w${wi}-${ei}`]).length, 0)
    : 0;
  const ruleCount = result ? result.rules.filter((_, i) => ruleSel[`r${i}`]).length : 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* 对话式创建 */}
        <HoloCard className="p-4" glow="sm">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-semibold text-white">💬 对话式创建</span>
            <span className="text-xs text-white/40">用一句话描述需求</span>
          </div>
          <HoloTextarea
            className="h-28"
            placeholder={"例如：\n把「截止日期」标为不规范，建议改为「截至日期」；\n检测「我们立马搞定」这类口语化表达；\n金额单位必须写「万元」。"}
            value={dialogue}
            onChange={(e) => setDialogue(e.target.value)}
          />
          <div className="mt-2.5 flex items-center gap-2">
            <HoloButton variant="primary" disabled={busy !== null} onClick={genDialogue}>
              {busy === "dialogue" ? "AI 生成中…（本地推理较慢，可能需要 5-20 分钟）" : "生成规则与词库"}
            </HoloButton>
          </div>
        </HoloCard>

        {/* 文档自建 */}
        <HoloCard className="p-4" glow="sm">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-semibold text-white">📄 上传文档自建</span>
            <span className="text-xs text-white/40">AI 阅读后提取术语与规范</span>
          </div>
          <p className="mb-3 text-[11px] leading-relaxed text-white/35">
            上传规范 / 标准 / 示例 / 词表文档（.txt / .md / .csv / .docx / .pdf），
            AI 通读全文后提取其中隐含的检测规则与词库（术语、禁用表述、书写要求等），供你确认后加入。
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <HoloButton variant="primary" disabled={busy !== null} onClick={() => docInputRef.current?.click()}>
              {busy === "doc" ? "AI 阅读中…（本地推理较慢，可能需要 5-20 分钟）" : "选择文档并生成"}
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
                  genDoc(f);
                }
                e.target.value = "";
              }}
            />
            {docName && <span className="text-xs text-white/40">{docName}</span>}
          </div>
        </HoloCard>
      </div>

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
    </div>
  );
}