import { useCallback, useEffect, useRef, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import { HoloSelect, HoloTextarea } from "../components/ui/HoloInput";
import HoloSwitch from "../components/ui/HoloSwitch";
import SectionTitle from "../components/common/SectionTitle";
import { api } from "../lib/api";
import { useToast } from "../components/ui/Toast";
import type {
  KbAskResp,
  KbSearchResp,
  KbStatus,
  KbVerifyPreviewResp,
  SettingsData,
} from "../lib/types";

/* 本地知识库（RAG）：
 * 上传行业规范 / 术语定义 / 书写规范文档 → 按标题层级切片（带重叠）建立索引
 * （BM25 + 可选向量，向量化时带「文档名 / 章节路径」上下文前缀）→
 * 检索采用双路 RRF 融合 + MMR 去冗余；问答强制标注来源。
 * 开启「注入知识库」后，核验链路按每个待核验片段各自检索，命中依据随问题回传。 */

const ACCEPT = ".docx,.pdf,.txt,.md,.csv,.xlsx";
const CHUNK_SIZES = [300, 500, 800, 1200, 2000];
const OVERLAPS = [0, 60, 100, 150, 200];

function fmtSize(n?: number) {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

interface Qa {
  q: string;
  resp: KbAskResp;
}

const MODE_LABEL: Record<string, string> = {
  hybrid: "向量 + BM25",
  bm25: "BM25",
  none: "空库",
};

export default function KbPage() {
  const toast = useToast();
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [err, setErr] = useState("");
  const [uploading, setUploading] = useState(false);
  const [fileInputKey, setFileInputKey] = useState(0);

  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(3);
  const [asking, setAsking] = useState(false);
  const [qa, setQa] = useState<Qa[]>([]);

  /* 检索诊断 */
  const [dbgQ, setDbgQ] = useState("");
  const [dbgK, setDbgK] = useState(5);
  const [searching, setSearching] = useState(false);
  const [dbg, setDbg] = useState<KbSearchResp | null>(null);

  /* 切片参数 */
  const [cfg, setCfg] = useState<{ chunk_size: number; chunk_overlap: number } | null>(null);
  const [savingCfg, setSavingCfg] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);

  /* 核验预检：回显某段待核验文本会被注入哪些知识库片段 */
  const [vpText, setVpText] = useState("");
  const [vpLoading, setVpLoading] = useState(false);
  const [vp, setVp] = useState<KbVerifyPreviewResp | null>(null);

  /* AI 注入知识库设置（persist 到 ai 组） */
  const [kbEnabled, setKbEnabled] = useState(false);
  const [kbDense, setKbDense] = useState(false);
  const settingsRef = useRef<SettingsData | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    api
      .kbStatus()
      .then((s) => {
        setStatus(s);
        if (s.config) setCfg(s.config);
        setErr("");
      })
      .catch((e: Error) => setErr(e.message));
  }, []);

  useEffect(() => {
    load();
    api
      .settings()
      .then((s) => {
        settingsRef.current = s;
        setKbEnabled(Boolean(s.ai?.kb_enabled));
        setKbDense(Boolean(s.ai?.kb_dense_in_verify));
        if (s.ai?.kb_top_k) setTopK(Number(s.ai.kb_top_k));
      })
      .catch(() => {});
  }, [load]);

  const commitKbSetting = async (patch: Partial<SettingsData["ai"]>, msg: string) => {
    const cur = settingsRef.current;
    if (!cur) return;
    const next = { ...cur, ai: { ...(cur.ai ?? {}), ...patch } } as SettingsData;
    settingsRef.current = next;
    setSaving(true);
    try {
      await api.saveSettings(next);
      toast(msg);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };

  const onPickFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    setUploading(true);
    try {
      const r = await api.kbUpload(files);
      load();
      const reused = (r.docs || []).filter((d) => d.reused).length;
      toast(
        r.added > 0
          ? `成功入库 ${r.added} 个文档${reused ? `（${reused} 个内容未变，已复用索引）` : ""}`
          : "未成功入库任何文档",
        r.added > 0 ? "ok" : "err",
      );
      if (r.errors?.length) setErr(r.errors.join("；"));
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setUploading(false);
      setFileInputKey((k) => k + 1);
    }
  };

  const ask = async () => {
    const q = question.trim();
    if (q.length < 2) {
      toast("请输入要查询的问题", "err");
      return;
    }
    setAsking(true);
    try {
      const resp = await api.kbAsk({ question: q, top_k: topK });
      setQa((list) => [{ q, resp }, ...list].slice(0, 20));
      setQuestion("");
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setAsking(false);
    }
  };

  const runSearch = async () => {
    const q = dbgQ.trim();
    if (q.length < 2) {
      toast("请输入检索关键词", "err");
      return;
    }
    setSearching(true);
    try {
      const r = await api.kbSearch({ question: q, top_k: dbgK });
      setDbg(r);
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setSearching(false);
    }
  };

  const saveCfg = async (patch: Partial<{ chunk_size: number; chunk_overlap: number }>) => {
    if (!cfg) return;
    const next = { ...cfg, ...patch };
    setCfg(next);
    setSavingCfg(true);
    try {
      const r = await api.kbSaveConfig(next);
      if (r.config) setCfg(r.config);
      toast("切片参数已保存（对已入库文档需点「重建索引」生效）");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSavingCfg(false);
    }
  };

  const runVerifyPreview = async () => {
    const t = vpText.trim();
    if (t.length < 4) {
      toast("请输入待核验的文本", "err");
      return;
    }
    setVpLoading(true);
    try {
      const r = await api.kbVerifyPreview({ text: t, top_k: topK });
      setVp(r);
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setVpLoading(false);
    }
  };

  const rebuild = async () => {
    setRebuilding(true);
    try {
      const r = await api.kbRebuild();
      load();
      toast(
        `索引重建完成：${r.rebuilt} 个文档 / ${r.chunks} 个片段` +
          (r.skipped ? `（${r.skipped} 个无原文跳过）` : ""),
      );
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setRebuilding(false);
    }
  };

  /* 带访问口令头下载原文（new window 无法携带 X-Access-Token） */
  const downloadDoc = async (docId: string, name: string) => {
    try {
      const blob = await api.kbDownload(docId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (ex) {
      toast((ex as Error).message, "err");
    }
  };

  const docs = status?.docs ?? [];
  const totalChars = docs.reduce((s, d) => s + (d.chars || 0), 0);
  return (
    <div className="space-y-4">
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">本地知识库 · RAG 检索问答</SectionTitle>
          <span className={`rounded-full border px-2.5 py-0.5 text-[11px] ${status?.embed_available ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300" : "border-amber-400/30 bg-amber-400/10 text-amber-300"}`}>
            {status?.embed_available ? `向量检索可用 · ${status.embed_model}` : "BM25 检索模式"}
          </span>
          {!!status?.embed_resolved_model && status.embed_resolved_model !== status.embed_model && (
            <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[11px] text-white/45">
              本机可用嵌入模型：{status.embed_resolved_model}
            </span>
          )}
          <span className="text-[11px] text-white/40">
            {docs.length} 个文档 · {status?.total_chunks ?? 0} 个片段 · {fmtSize(totalChars)} 文本 · 索引更新于 {status?.updated_at || "--"}
          </span>
        </div>

        <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <div className="flex flex-wrap items-center gap-3">
            <HoloButton variant="primary" size="sm" loading={uploading} onClick={() => document.getElementById("kb-file-input")?.click()}>
              📤 上传文档入库
            </HoloButton>
            <input
              id="kb-file-input"
              key={fileInputKey}
              type="file"
              multiple
              accept={ACCEPT}
              className="hidden"
              onChange={onPickFiles}
            />
            <HoloButton variant="ghost" size="sm" loading={rebuilding} disabled={!docs.length || rebuilding} onClick={rebuild} title="按已存原文重新切片并全量重嵌向量">
              🔄 重建索引
            </HoloButton>
            {docs.length > 0 && (
              <HoloButton
                variant="danger"
                size="sm"
                onClick={() => {
                  if (window.confirm(`确定清空知识库全部 ${docs.length} 个文档吗？此操作不可恢复。`)) {
                    api.kbClear().then(() => { load(); toast("知识库已清空"); });
                  }
                }}
              >
                🗑️ 清空知识库
              </HoloButton>
            )}
            <span className="ml-auto text-[11px] text-white/35">
              支持 .docx / .pdf / .txt / .md / .csv / .xlsx，单文件 ≤ 30MB，按标题层级自动切片
            </span>
          </div>
          {!status?.embed_available && (
            <div className="mt-2 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-[12px] text-amber-200/80">
              提示：未在 Ollama 检测到嵌入模型，当前使用 BM25 关键词检索（无需额外依赖）。
              若在 Ollama 安装 bge-m3（<code className="text-amber-100">ollama pull bge-m3</code>）
              并在「AI 配置」开启嵌入，然后点「重建索引」，即可启用向量检索提升相关性。
            </div>
          )}
          {err && <div className="mt-2 text-[12px] text-red-300/90">提示：{err}</div>}
        </div>

        {/* 文档列表 */}
        {docs.length > 0 && (
          <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
            {docs.map((d) => (
              <div key={d.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3 transition-all hover:border-[var(--border-accent-soft)]">
                <div className="flex items-start gap-2">
                  <span className="text-lg leading-none">📄</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-white/90" title={d.name}>{d.name}</div>
                  <div className="mt-1 text-[11px] text-white/45">
                    {fmtSize(d.size)} · {d.chunks} 段 · {d.added_at ? new Date(d.added_at.replace(" ", "T")).toLocaleString() : "--"}
                  </div>
                  </div>
                  <button
                    className="shrink-0 rounded-lg px-1.5 py-0.5 text-[11px] text-red-300/70 transition-all hover:bg-red-400/10 hover:text-red-200"
                    onClick={() => {
                      if (window.confirm(`从知识库删除「${d.name}」？`)) {
                        api.kbDelete([d.id]).then(() => load());
                      }
                    }}
                    title="删除"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </HoloCard>

      {/* 切片与索引配置 */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">切片与索引参数</SectionTitle>
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
          <label className="flex items-center gap-2 text-[12px] text-white/50">
            片段目标长度
            <HoloSelect
              className="w-[110px]"
              value={cfg?.chunk_size ?? 500}
              disabled={savingCfg || !cfg}
              onChange={(e) => saveCfg({ chunk_size: Number(e.target.value) })}
            >
              {CHUNK_SIZES.map((n) => (
                <option key={n} value={n}>{n} 字</option>
              ))}
            </HoloSelect>
          </label>
          <label className="flex items-center gap-2 text-[12px] text-white/50">
            片段重叠
            <HoloSelect
              className="w-[110px]"
              value={cfg?.chunk_overlap ?? 100}
              disabled={savingCfg || !cfg}
              onChange={(e) => saveCfg({ chunk_overlap: Number(e.target.value) })}
            >
              {OVERLAPS.map((n) => (
                <option key={n} value={n}>{n} 字</option>
              ))}
            </HoloSelect>
          </label>
          <span className="text-[11px] text-white/35">
            重叠可缓解跨片段语义断裂；参数改动后点「重建索引」对全部文档生效。
          </span>
        </div>
      </HoloCard>

      {/* AI 核验注入 */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">与检测 / AI 生成模块打通</SectionTitle>
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
          <HoloSwitch
            checked={kbEnabled}
            disabled={saving}
            onChange={(v) => {
              setKbEnabled(v);
              commitKbSetting({ kb_enabled: v }, v ? "已开启：AI 核验与生成会参考知识库" : "已关闭：AI 核验不再注入知识库");
            }}
            label="AI 核验与报告生成时注入知识库参考资料"
          />
          <label className="flex items-center gap-2 text-[12px] text-white/50">
            每次注入片段数
            <HoloSelect
              className="w-[140px]"
              value={topK}
              disabled={saving}
              onChange={(e) => {
                const v = Number(e.target.value);
                setTopK(v);
                commitKbSetting({ kb_top_k: v }, "片段数已保存");
              }}
            >
              {[1, 2, 3, 4, 5, 8].map((n) => (
                <option key={n} value={n}>{n} 段</option>
              ))}
            </HoloSelect>
          </label>
          <HoloSwitch
            checked={kbDense}
            disabled={saving || !status?.embed_resolved}
            onChange={(v) => {
              setKbDense(v);
              commitKbSetting({ kb_dense_in_verify: v },
                              v ? "已开启：核验时启用向量检索（更准，但本地推理更慢）"
                                 : "已关闭：核验仅用 BM25 关键词检索（更快）");
            }}
            label="核验时启用向量（稠密）检索"
          />
          <span className="text-[11px] text-white/35">仅对已上传的知识库文档生效；核验时按片段核查『规范不符』并注明出处。</span>
        </div>

        {/* 核验预检 */}
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <div className="text-[12px] font-medium text-white/70">核验预检</div>
          <div className="mt-1 text-[11px] text-white/35">
            粘贴一段待核验文本，查看核验时会被注入的知识库片段——用于确认联动生效与命中质量。
          </div>
          <div className="mt-3 flex flex-col gap-3 md:flex-row">
            <HoloTextarea
              className="flex-1"
              rows={3}
              placeholder="如：应收账款账面余额 23,381,372.12 元，账龄主要为 1 至 2 年，坏账准备按 5% 计提。"
              value={vpText}
              onChange={(e) => setVpText(e.target.value)}
            />
            <div className="flex flex-row items-end">
              <HoloButton loading={vpLoading} onClick={runVerifyPreview} disabled={vpLoading}>
                🧪 预检命中
              </HoloButton>
            </div>
          </div>

          {vp && (
            <div className="mt-3 space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="rounded-md bg-white/10 px-1.5 py-0.5 text-white/60">
                  切分 {vp.segments} 段
                </span>
                <span className="text-white/40">注入 {vp.refs.length} 个知识片段</span>
                {vp.refs.length === 0 && (
                  <span className="text-amber-200/75">未命中：请确认已上传相关资料文档</span>
                )}
              </div>
              {vp.refs.map((r, j) => (
                <div key={j} className="rounded-xl border border-white/10 bg-white/[0.02] p-2.5">
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="rounded-md bg-cyan-400/15 px-1.5 py-0.5 text-cyan-100/85">
                      相关度 {r.score}
                    </span>
                    <span className="text-white/70">
                      {r.file}
                      {r.path ? ` / ${r.path}` : ""}
                    </span>
                    <button
                      className="ml-auto rounded-lg border border-white/10 px-2 py-0.5 text-white/50 transition-all hover:bg-white/10 hover:text-white/80"
                      onClick={() => downloadDoc(r.doc_id, r.file)}
                      title="下载原文核对"
                    >
                      下载原文
                    </button>
                  </div>
                  <div className="mt-1.5 whitespace-pre-wrap text-[11px] leading-relaxed text-white/55">
                    {r.snippet}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </HoloCard>

      {/* 检索诊断 */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">检索诊断（仅检索，不调用大模型）</SectionTitle>
        <div className="mt-4 flex flex-col gap-3 md:flex-row">
          <HoloTextarea
            className="flex-1"
            rows={2}
            placeholder="输入关键词，检查知识库能否命中相关资料，如：坏账准备 计提比例"
            value={dbgQ}
            onChange={(e) => setDbgQ(e.target.value)}
          />
          <div className="flex flex-row items-end gap-2">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] text-white/40">Top N</span>
              <HoloSelect className="w-[76px]" value={dbgK} onChange={(e) => setDbgK(Number(e.target.value))}>
                {[3, 5, 8, 10].map((n) => (
                  <option key={n} value={n}>{n}</option>
              ))}
              </HoloSelect>
            </div>
            <HoloButton loading={searching} onClick={runSearch} disabled={searching}>
              🔍 检索
            </HoloButton>
          </div>
        </div>

        {dbg && (
          <div className="mt-4 space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="rounded-full border border-cyan-300/25 bg-cyan-400/10 px-2.5 py-0.5 text-cyan-200/90">
                {MODE_LABEL[dbg.mode] || dbg.mode}
              </span>
              <span className="text-white/40">
                片段 {dbg.stats.chunks} · 向量 {dbg.stats.vectors} · 文档 {dbg.stats.docs}
              </span>
              {dbg.query_terms.length > 0 && (
                <span className="text-white/30">检索词：{dbg.query_terms.join(" / ")}</span>
              )}
            </div>
            {dbg.hits.length === 0 ? (
              <div className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-[12px] text-white/45">
                未命中任何片段：请确认已上传相关文档，或改用资料中的原始表述重试。
              </div>
            ) : (
              dbg.hits.map((h) => (
                <div key={h.chunk_id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="rounded-md bg-white/10 px-1.5 py-0.5 text-white/60">#{h.rank}</span>
                    <span className="text-cyan-200/85">
                      RRF {h.rrf} · BM25 {h.bm25}
                      {h.cos ? ` · 向量 ${h.cos}` : ""}
                    </span>
                    <button
                      className="ml-auto rounded-lg border border-white/10 px-2 py-0.5 text-white/50 transition-all hover:bg-white/10 hover:text-white/80"
                      onClick={() => downloadDoc(h.doc_id, h.file)}
                      title="下载原文核对"
                    >
                      📄 {h.file}
                    </button>
                  </div>
                  <div className="mt-1 text-[11px] text-white/35">{h.path || h.section}{h.loc ? ` · ${h.loc}` : ""}</div>
                  <div className="mt-2 whitespace-pre-wrap text-[12px] leading-relaxed text-white/65">{h.text}</div>
                </div>
              ))
            )}
          </div>
        )}
      </HoloCard>

      {/* 问答 */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">知识库问答</SectionTitle>
        <div className="mt-4 flex flex-col gap-3 md:flex-row">
          <HoloTextarea
            className="flex-1"
            rows={2}
            placeholder="输入问题，如：应收账款账龄 1-3 年的坏账准备计提比例是多少？"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask(); }}
          />
          <div className="flex flex-row items-end gap-2">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] text-white/40">检索前 N</span>
              <HoloSelect className="w-[76px]" value={topK} onChange={(e) => setTopK(Number(e.target.value))}>
                {[1, 2, 3, 4, 5, 8].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </HoloSelect>
            </div>
            <HoloButton variant="primary" loading={asking} onClick={ask} disabled={asking}>
              💬 提问
            </HoloButton>
          </div>
        </div>
        <div className="mt-1 text-[11px] text-white/30">Ctrl / ⌘ + Enter 快捷提问</div>

        {qa.length > 0 && (
          <div className="mt-4 space-y-3">
            {qa.map((item, i) => (
              <div key={qa.length - i} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                <div className="flex items-start gap-2">
                  <span className="text-sm font-semibold text-white/90">Q{i + 1}</span>
                  <span className="text-[13px] text-white/85">{item.q}</span>
                  <span className="ml-auto shrink-0 text-[11px] text-white/35">
                    {MODE_LABEL[item.resp.mode || "bm25"] || "BM25"} · {item.resp.elapsed.toFixed(1)}s
                  </span>
                </div>
                <div className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-white/75">
                  {item.resp.answer}
                </div>
                {item.resp.sources.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {item.resp.sources.map((s, j) => (
                      <button
                        key={j}
                        className="rounded-full border border-[var(--border-accent-soft)] bg-white/[0.04] px-2.5 py-1 text-[11px] text-cyan-200/90 transition-all hover:bg-white/[0.1]"
                        onClick={() => downloadDoc(s.doc_id, s.file)}
                        title={`${s.path || s.section}${s.loc ? `\n${s.loc}` : ""}\n${s.snippet ?? ""}\n（点击下载原文核对）`}
                      >
                        📄 {s.file} · {s.section}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </HoloCard>
    </div>
  );
}
