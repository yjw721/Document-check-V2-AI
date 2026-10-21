import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import HoloBadge from "../components/ui/HoloBadge";
import { HoloSelect, HoloTextarea } from "../components/ui/HoloInput";
import HoloSwitch from "../components/ui/HoloSwitch";
import SectionTitle from "../components/common/SectionTitle";
import EmptyState from "../components/common/EmptyState";
import { api } from "../lib/api";
import { useToast } from "../components/ui/Toast";
import type {
  KbAskResp,
  KbConfig,
  KbDoc,
  KbFolder,
  KbPermissions,
  KbSearchResp,
  KbStatus,
  KbTreeNode,
  KbVerifyPreviewResp,
  SettingsData,
} from "../lib/types";

/* 本地知识库（RAG）管理台
 * - 树形目录导航：按目录归集资料，检索 / 问答可限定到某个目录子树
 * - 可插拔向量库：默认 Chroma 本地持久化，缺依赖时自动降级为内存后端
 * - 多格式解析：PDF（含扫描件 OCR）/ Word / Excel / 纯文本 / 图片 OCR
 * - 混合检索：BM25 + 向量双路 RRF 融合；问答强制标注来源并做防幻觉审计
 * - 权限：与核验系统 settings.role 打通，服务端强制校验 */

const ACCEPT =
  ".docx,.doc,.pdf,.txt,.md,.csv,.xlsx,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp";
const CHUNK_SIZES = [300, 500, 800, 1200, 2000];
const OVERLAPS = [0, 60, 100, 150, 200];
const ROOT = "";

const MODE_LABEL: Record<string, string> = {
  hybrid: "向量 + 关键词",
  bm25: "关键词 BM25",
  none: "空库",
};

function fmtSize(n?: number) {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function fmtTime(s?: string) {
  if (!s) return "--";
  try {
    return new Date(s.replace(" ", "T")).toLocaleString();
  } catch {
    return s;
  }
}

interface Qa {
  q: string;
  resp: KbAskResp;
}

/* 递归渲染目录树 */
function TreeItem({
  node,
  depth,
  activeId,
  onPick,
}: {
  node: KbTreeNode;
  depth: number;
  activeId: string;
  onPick: (id: string) => void;
}) {
  const [open, setOpen] = useState(depth < 2);
  const active = node.id === activeId;
  return (
    <div>
      <div
        className={`flex cursor-pointer items-center gap-1.5 rounded-xl px-2 py-1.5 text-[12px] transition-all ${
          active
            ? "bg-[var(--glow-accent-soft)] text-[var(--accent-ink)]"
            : "text-white/65 hover:bg-white/[0.07] hover:text-white/90"
        }`}
        style={{ paddingLeft: 8 + depth * 12 }}
        onClick={() => onPick(node.id)}
      >
        {node.folder_count ? (
          <button
            className="w-3 shrink-0 text-[9px] text-white/35"
            onClick={(e) => {
              e.stopPropagation();
              setOpen((v) => !v);
            }}
            title={open ? "收起" : "展开"}
          >
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <span className="shrink-0">{depth === 0 ? "🗂️" : "📁"}</span>
        <span className="truncate">{node.name}</span>
        {!!node.doc_count && (
          <span className="ml-auto shrink-0 rounded-full bg-white/10 px-1.5 text-[10px] text-white/45">
            {node.doc_count}
          </span>
        )}
      </div>
      {open &&
        node.folders.map((f) => (
          <TreeItem key={f.id} node={f} depth={depth + 1} activeId={activeId} onPick={onPick} />
        ))}
    </div>
  );
}

export default function KbPage() {
  const toast = useToast();
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [err, setErr] = useState("");
  const [uploading, setUploading] = useState(false);
  const [fileInputKey, setFileInputKey] = useState(0);

  /* 树形目录 */
  const [tree, setTree] = useState<KbTreeNode | null>(null);
  const [folders, setFolders] = useState<KbFolder[]>([]);
  const [curFolder, setCurFolder] = useState<string>(ROOT);
  const [includeSub, setIncludeSub] = useState(true);
  const [busyId, setBusyId] = useState("");

  /* 权限 */
  const [perms, setPerms] = useState<KbPermissions | null>(null);
  const can = useCallback((cap: string) => Boolean((perms?.caps ?? {})[cap]), [perms]);

  /* 问答 */
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(3);
  const [asking, setAsking] = useState(false);
  const [qa, setQa] = useState<Qa[]>([]);

  /* 检索诊断 */
  const [dbgQ, setDbgQ] = useState("");
  const [dbgK, setDbgK] = useState(5);
  const [searching, setSearching] = useState(false);
  const [dbg, setDbg] = useState<KbSearchResp | null>(null);

  /* 配置 */
  const [cfg, setCfg] = useState<KbConfig | null>(null);
  const [savingCfg, setSavingCfg] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);

  /* 核验预检 */
  const [vpText, setVpText] = useState("");
  const [vpLoading, setVpLoading] = useState(false);
  const [vp, setVp] = useState<KbVerifyPreviewResp | null>(null);

  /* AI 注入设置 */
  const [kbEnabled, setKbEnabled] = useState(false);
  const [kbDense, setKbDense] = useState(false);
  const settingsRef = useRef<SettingsData | null>(null);
  const [saving, setSaving] = useState(false);

  const loadStatus = useCallback(() => {
    api
      .kbStatus()
      .then((s) => {
        setStatus(s);
        if (s.config) setCfg(s.config as KbConfig);
        setErr("");
      })
      .catch((e: Error) => setErr(e.message));
  }, []);

  const loadTree = useCallback(() => {
    api.kbTree().then((r) => setTree(r.tree ?? null)).catch(() => setTree(null));
    api.kbFolders().then((r) => setFolders(r.folders ?? [])).catch(() => setFolders([]));
  }, []);

  const loadPerms = useCallback(() => {
    api.kbPermissions().then(setPerms).catch(() => setPerms(null));
  }, []);

  const load = useCallback(() => {
    loadStatus();
    loadTree();
  }, [loadStatus, loadTree]);

  useEffect(() => {
    load();
    loadPerms();
    api
      .settings()
      .then((s) => {
        settingsRef.current = s;
        setKbEnabled(Boolean(s.ai?.kb_enabled));
        setKbDense(Boolean(s.ai?.kb_dense_in_verify));
        if (s.ai?.kb_top_k) setTopK(Number(s.ai.kb_top_k));
      })
      .catch(() => {});
  }, [load, loadPerms]);

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

  /* ---------- 目录操作 ---------- */
  const createFolder = async () => {
    const name = window.prompt("新目录名称");
    if (!name?.trim()) return;
    try {
      const r = await api.kbCreateFolder(name.trim(), curFolder);
      if (!r.ok) throw new Error(r.message || "新建失败");
      loadTree();
      toast("目录已创建");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const renameFolder = async () => {
    if (!curFolder) return;
    const cur = folders.find((f) => f.id === curFolder);
    const name = window.prompt("重命名目录", cur?.name ?? "");
    if (!name?.trim()) return;
    try {
      const r = await api.kbRenameFolder(curFolder, name.trim());
      if (!r.ok) throw new Error(r.message || "重命名失败");
      loadTree();
      toast("已重命名");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const deleteFolder = async () => {
    if (!curFolder) return;
    const cur = folders.find((f) => f.id === curFolder);
    const cascade = window.confirm(
      `删除目录「${cur?.name ?? ""}」？\n\n【确定】连同子目录与文档一并删除\n【取消】保留资料，仅把内容上移到上级目录`,
    );
    try {
      const r = await api.kbDeleteFolder(curFolder, cascade);
      if (!r.ok) throw new Error(r.message || "删除失败");
      setCurFolder(ROOT);
      load();
      toast(cascade ? "目录及其内容已删除" : "目录已删除，资料已上移");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const moveDocs = async (ids: string[]) => {
    const opts = folders.map((f) => `${f.id}  ${f.path}`).join("\n");
    const target = window.prompt(
      `把 ${ids.length} 个文档移动到（填目录 id，留空 = 根目录）：\n${opts}`,
      curFolder,
    );
    if (target === null) return;
    try {
      const r = await api.kbMoveDocs(ids, target.trim());
      if (!r.ok) throw new Error(r.message || "移动失败");
      load();
      toast(`已移动 ${r.moved} 个文档`);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  /* ---------- 上传 / 文档操作 ---------- */
  const onPickFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    setUploading(true);
    try {
      const r = await api.kbUpload(files, curFolder);
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

  const reindexDoc = async (d: KbDoc) => {
    setBusyId(d.id);
    try {
      const r = await api.kbReindexDoc(d.id);
      if (!r.ok) throw new Error(String(r.message || "重建失败"));
      load();
      toast(`「${d.name}」已重新切片并向量化（${String(r.chunks ?? 0)} 段）`);
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setBusyId("");
    }
  };

  const removeDoc = async (d: KbDoc) => {
    if (!window.confirm(`从知识库删除「${d.name}」？`)) return;
    setBusyId(d.id);
    try {
      await api.kbDelete([d.id]);
      load();
      toast("已删除");
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setBusyId("");
    }
  };

  /* ---------- 问答 / 检索 ---------- */
  const ask = async () => {
    const q = question.trim();
    if (q.length < 2) {
      toast("请输入要查询的问题", "err");
      return;
    }
    setAsking(true);
    try {
      const resp = await api.kbAsk({ question: q, top_k: topK, folder: curFolder || undefined });
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
      setDbg(
        await api.kbSearch({
          question: q,
          top_k: dbgK,
          folder: includeSub && curFolder ? curFolder : undefined,
        }),
      );
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setSearching(false);
    }
  };

  const saveCfg = async (patch: Partial<KbConfig>) => {
    if (!cfg) return;
    const next = { ...cfg, ...patch };
    setCfg(next);
    setSavingCfg(true);
    try {
      const r = await api.kbSaveConfig(next);
      if (r.config) setCfg(r.config as KbConfig);
      loadStatus();
      toast("配置已保存（对已入库文档需点「重建索引」生效）");
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
      setVp(await api.kbVerifyPreview({ text: t, top_k: topK }));
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

  /* ---------- 派生数据 ---------- */
  const allDocs = status?.docs ?? [];
  const docs = useMemo(() => {
    if (!curFolder) return allDocs;
    const curPath = folders.find((f) => f.id === curFolder)?.path ?? "根目录";
    if (includeSub) return allDocs.filter((d) => (d.path ?? "根目录").startsWith(curPath));
    return allDocs.filter((d) => (d.folder ?? ROOT) === curFolder);
  }, [allDocs, curFolder, includeSub, folders]);

  const curPath = curFolder
    ? folders.find((f) => f.id === curFolder)?.path ?? "根目录"
    : "根目录";
  const totalChars = allDocs.reduce((s, d) => s + (d.chars || 0), 0);
  const ocr = status?.parsers?.ocr;
  const storeName = status?.store?.backend === "chroma" ? "Chroma" : "内存 JSON";

  return (
    <div className="space-y-4">
      {/* 概览 */}
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">本地知识库 · RAG 检索问答</SectionTitle>
          <HoloBadge tone={status?.embed_available ? "ok" : "warn"}>
            {status?.embed_available ? `向量检索可用 · ${status.embed_model}` : "关键词检索模式"}
          </HoloBadge>
          <HoloBadge tone="accent">向量库：{storeName}</HoloBadge>
          {!!ocr && (
            <HoloBadge tone={ocr.available ? "ok" : "gray"}>
              OCR：{ocr.available ? ocr.engine : "未安装"}
            </HoloBadge>
          )}
          {!!perms && <HoloBadge tone="gray">当前角色：{perms.role_label}</HoloBadge>}
          <span className="text-[11px] text-white/40">
            {allDocs.length} 个文档 · {status?.total_chunks ?? 0} 个片段 ·{" "}
            {fmtSize(totalChars)} 文本 · 索引更新于 {fmtTime(status?.updated_at)}
          </span>
        </div>

        {!!status?.store_error && (
          <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-[12px] text-amber-200/85">
            向量库已自动降级：{status.store_error}
          </div>
        )}
        {!status?.embed_available && (
          <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-[12px] text-amber-200/85">
            未在 Ollama 检测到嵌入模型，当前使用 BM25 关键词检索（无需额外依赖）。
            安装 bge-m3（<code className="text-amber-100">ollama pull bge-m3</code>）
            并在「AI 配置」开启嵌入后点「重建索引」，即可启用向量检索。
          </div>
        )}
      </HoloCard>

      {/* 目录导航 + 文档区 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
        <HoloCard className="p-4">
          <div className="mb-3 flex items-center gap-2">
            <span className="text-[12px] font-medium text-white/70">目录导航</span>
            {can("kb.manage") && (
              <button
                className="ml-auto rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/55 transition-all hover:bg-white/10 hover:text-white/90"
                onClick={createFolder}
                title="在当前目录下新建子目录"
              >
                ＋ 目录
              </button>
            )}
          </div>
          <div className="max-h-[420px] overflow-y-auto pr-1">
            {tree ? (
              <TreeItem node={tree} depth={0} activeId={curFolder} onPick={setCurFolder} />
            ) : (
              <EmptyState text="暂无目录" icon="🗂️" />
            )}
          </div>
          {!!curFolder && (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-white/10 pt-3">
              {can("kb.manage") && (
                <button
                  className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/55 transition-all hover:bg-white/10 hover:text-white/90"
                  onClick={renameFolder}
                >
                  重命名
                </button>
              )}
              {can("kb.delete") && (
                <button
                  className="rounded-lg border border-[rgba(255,107,125,0.35)] px-2 py-0.5 text-[11px] text-[var(--tone-danger-soft)] transition-all hover:bg-[rgba(255,107,125,0.16)]"
                  onClick={deleteFolder}
                >
                  删除目录
                </button>
              )}
            </div>
          )}
        </HoloCard>

        <HoloCard className="p-6">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[12px] text-white/45">
              当前位置：<span className="text-white/75">{curPath}</span>
            </span>
            <label className="flex items-center gap-1.5 text-[11px] text-white/45">
              <input
                type="checkbox"
                className="accent-[var(--accent-ink)]"
                checked={includeSub}
                onChange={(e) => setIncludeSub(e.target.checked)}
              />
              含子目录
            </label>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {can("kb.upload") && (
                <>
                  <HoloButton
                    variant="primary"
                    size="sm"
                    loading={uploading}
                    onClick={() => document.getElementById("kb-file-input")?.click()}
                  >
                    📤 上传到当前目录
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
                </>
              )}
              {can("kb.reindex") && (
                <HoloButton
                  variant="ghost"
                  size="sm"
                  loading={rebuilding}
                  disabled={!allDocs.length || rebuilding}
                  onClick={rebuild}
                  title="按已存原文重新切片并全量重嵌向量"
                >
                  🔄 重建索引
                </HoloButton>
              )}
              {can("kb.delete") && allDocs.length > 0 && (
                <HoloButton
                  variant="danger"
                  size="sm"
                  onClick={() => {
                    if (window.confirm(`确定清空知识库全部 ${allDocs.length} 个文档吗？此操作不可恢复。`)) {
                      api.kbClear().then(() => {
                        load();
                        setCurFolder(ROOT);
                        toast("知识库已清空");
                      });
                    }
                  }}
                >
                  🗑️ 清空
                </HoloButton>
              )}
            </div>
          </div>
          <div className="mt-1 text-[11px] text-white/30">
            支持 Word / PDF（含扫描件 OCR）/ Excel / 纯文本 / 图片 OCR，单文件 ≤ 30MB
          </div>
          {err && <div className="mt-2 text-[12px] text-red-300/90">提示：{err}</div>}

          {docs.length === 0 ? (
            <EmptyState text="当前目录暂无文档" icon="📄" />
          ) : (
            <div className="mt-3 grid grid-cols-1 gap-2 xl:grid-cols-2">
              {docs.map((d) => (
                <div
                  key={d.id}
                  className="rounded-2xl border border-white/10 bg-white/[0.03] p-3 transition-all hover:border-[var(--border-accent-soft)]"
                >
                  <div className="flex items-start gap-2">
                    <span className="text-lg leading-none">📄</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium text-white/90" title={d.name}>
                        {d.name}
                      </div>
                      <div className="mt-1 text-[11px] text-white/45">
                        {fmtSize(d.size)} · {d.chunks} 段 · {fmtTime(d.added_at)}
                      </div>
                      {!!d.path && d.path !== curPath && (
                        <div className="mt-0.5 truncate text-[10px] text-white/30">{d.path}</div>
                      )}
                    </div>
                    <button
                      className="shrink-0 rounded-lg px-1.5 py-0.5 text-[11px] text-red-300/70 transition-all hover:bg-red-400/10 hover:text-red-200 disabled:opacity-40"
                      disabled={!can("kb.delete") || busyId === d.id}
                      onClick={() => removeDoc(d)}
                      title={can("kb.delete") ? "删除" : "当前角色无删除权限"}
                    >
                      ✕
                    </button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <button
                      className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/55 transition-all hover:bg-white/10 hover:text-white/90 disabled:opacity-40"
                      disabled={busyId === d.id}
                      onClick={() => downloadDoc(d.id, d.name)}
                    >
                      ⬇️ 原文
                    </button>
                    <button
                      className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/55 transition-all hover:bg-white/10 hover:text-white/90 disabled:opacity-40"
                      disabled={!can("kb.reindex") || busyId === d.id}
                      onClick={() => reindexDoc(d)}
                      title={can("kb.reindex") ? "重新切片并重向量化本文档" : "当前角色无重向量化权限"}
                    >
                      {busyId === d.id ? "处理中…" : "🔁 重新向量化"}
                    </button>
                    <button
                      className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/55 transition-all hover:bg-white/10 hover:text-white/90 disabled:opacity-40"
                      disabled={!can("kb.upload") || busyId === d.id}
                      onClick={() => moveDocs([d.id])}
                      title="移动到其他目录"
                    >
                      ➡️ 移动
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </HoloCard>
      </div>

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
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </HoloSelect>
            </div>
            <HoloButton loading={searching} onClick={runSearch} disabled={searching || !can("kb.search")}>
              🔍 检索
            </HoloButton>
          </div>
        </div>

        {dbg && (
          <div className="mt-4 space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <HoloBadge tone="accent">{MODE_LABEL[dbg.mode] || dbg.mode}</HoloBadge>
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
                  <div className="mt-1 text-[11px] text-white/35">
                    {h.path || h.section}
                    {h.loc ? ` · ${h.loc}` : ""}
                  </div>
                  <div className="mt-2 whitespace-pre-wrap text-[12px] leading-relaxed text-white/65">
                    {h.text}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </HoloCard>

      {/* 问答（带溯源与防幻觉审计） */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">知识库问答（带溯源 · 防幻觉）</SectionTitle>
        <div className="mt-1 text-[11px] text-white/35">
          模型只能依据命中的资料作答并逐句标注来源；系统会自动回扫引用，
          把「资料中不存在的来源」标为可疑并提示复核。
        </div>
        <div className="mt-3 flex flex-col gap-3 md:flex-row">
          <HoloTextarea
            className="flex-1"
            rows={2}
            placeholder="输入问题，如：应收账款账龄 1-3 年的坏账准备计提比例是多少？"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask();
            }}
          />
          <div className="flex flex-row items-end gap-2">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] text-white/40">检索前 N</span>
              <HoloSelect className="w-[76px]" value={topK} onChange={(e) => setTopK(Number(e.target.value))}>
                {[1, 2, 3, 4, 5, 8].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </HoloSelect>
            </div>
            <HoloButton variant="primary" loading={asking} onClick={ask} disabled={asking || !can("kb.search")}>
              💬 提问
            </HoloButton>
          </div>
        </div>
        <div className="mt-1 text-[11px] text-white/30">
          Ctrl / ⌘ + Enter 快捷提问{!!curFolder && ` · 范围限定为「${curPath}」`}
        </div>

        {qa.length > 0 && (
          <div className="mt-4 space-y-3">
            {qa.map((item, i) => {
              const a = item.resp.audit;
              const conf = Math.round((a?.confidence ?? 0) * 100);
              return (
                <div key={qa.length - i} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex items-start gap-2">
                    <span className="text-sm font-semibold text-white/90">Q{i + 1}</span>
                    <span className="text-[13px] text-white/85">{item.q}</span>
                    <span className="ml-auto shrink-0 text-[11px] text-white/35">
                      {MODE_LABEL[item.resp.mode || "bm25"] || "关键词"} ·{" "}
                      {item.resp.elapsed.toFixed(1)}s
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                    {a?.refused ? (
                      <HoloBadge tone="warn">资料不足 · 已按规则拒答</HoloBadge>
                    ) : (
                      <HoloBadge tone={a?.grounded ? "ok" : "danger"}>
                        {a?.grounded ? "引用可溯源" : "存在可疑引用"}
                      </HoloBadge>
                    )}
                    <HoloBadge tone="gray">可信度 {conf}%</HoloBadge>
                    {!!a?.citations?.length && (
                      <span className="text-white/35">引用 {a.citations.length} 处</span>
                    )}
                  </div>
                  <div className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-white/75">
                    {item.resp.answer}
                  </div>
                  {!!a?.warnings?.length && (
                    <div className="mt-2 rounded-xl border border-amber-400/25 bg-amber-400/[0.07] px-3 py-2 text-[11px] text-amber-200/85">
                      ⚠️ {a.warnings.join("；")}
                    </div>
                  )}
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
              );
            })}
          </div>
        )}
      </HoloCard>

      {/* 与核验流程打通 */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">与检测 / AI 核验流程打通</SectionTitle>
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
          <HoloSwitch
            checked={kbEnabled}
            disabled={saving}
            onChange={(v) => {
              setKbEnabled(v);
              commitKbSetting(
                { kb_enabled: v },
                v ? "已开启：AI 核验与生成会参考知识库" : "已关闭：AI 核验不再注入知识库",
              );
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
                <option key={n} value={n}>
                  {n} 段
                </option>
              ))}
            </HoloSelect>
          </label>
          <HoloSwitch
            checked={kbDense}
            disabled={saving || !status?.embed_resolved}
            onChange={(v) => {
              setKbDense(v);
              commitKbSetting(
                { kb_dense_in_verify: v },
                v
                  ? "已开启：核验时启用向量检索（更准，但本地推理更慢）"
                  : "已关闭：核验仅用关键词检索（更快）",
              );
            }}
            label="核验时启用向量（稠密）检索"
          />
          <span className="text-[11px] text-white/35">
            命中依据会随问题条目回传，可在「问题明细」中查看「知识库依据」。
          </span>
        </div>

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

      {/* 配置：切片 / 向量后端 / OCR */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">切片 · 向量库 · OCR 配置</SectionTitle>
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
          <label className="flex items-center gap-2 text-[12px] text-white/50">
            片段目标长度
            <HoloSelect
              className="w-[110px]"
              value={cfg?.chunk_size ?? 500}
              disabled={savingCfg || !cfg || !can("kb.manage")}
              onChange={(e) => saveCfg({ chunk_size: Number(e.target.value) })}
            >
              {CHUNK_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n} 字
                </option>
              ))}
            </HoloSelect>
          </label>
          <label className="flex items-center gap-2 text-[12px] text-white/50">
            片段重叠
            <HoloSelect
              className="w-[110px]"
              value={cfg?.chunk_overlap ?? 100}
              disabled={savingCfg || !cfg || !can("kb.manage")}
              onChange={(e) => saveCfg({ chunk_overlap: Number(e.target.value) })}
            >
              {OVERLAPS.map((n) => (
                <option key={n} value={n}>
                  {n} 字
                </option>
              ))}
            </HoloSelect>
          </label>
          <label className="flex items-center gap-2 text-[12px] text-white/50">
            向量库后端
            <HoloSelect
              className="w-[160px]"
              value={cfg?.vector_backend ?? "chroma"}
              disabled={savingCfg || !cfg || !can("kb.manage")}
              onChange={(e) => saveCfg({ vector_backend: e.target.value })}
            >
              {(status?.backends ?? [{ id: "chroma", label: "Chroma（推荐）", available: true }]).map((b) => (
                <option key={b.id} value={b.id} disabled={!b.available}>
                  {b.label}
                  {b.available ? "" : "（不可用）"}
                </option>
              ))}
            </HoloSelect>
          </label>
          <HoloSwitch
            checked={cfg?.ocr_enabled ?? true}
            disabled={savingCfg || !can("kb.manage")}
            onChange={(v) => saveCfg({ ocr_enabled: v })}
            label="启用 OCR（扫描件 PDF 与图片）"
          />
        </div>
        <div className="mt-2 text-[11px] text-white/30">
          切换后端后需点「重建索引」把已有向量迁到新后端；OCR 关闭后图片与扫描件将直接拒绝入库。
        </div>

        {!!status?.parsers?.formats?.length && (
          <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
            {status.parsers.formats.map((f) => (
              <div key={f.ext} className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-white/80">{f.label}</span>
                  <span
                    className={`ml-auto rounded-full border px-2 py-0.5 text-[10px] ${
                      f.available
                        ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                        : "border-white/10 bg-white/5 text-white/40"
                    }`}
                  >
                    {f.available ? "可用" : "需安装依赖"}
                  </span>
                </div>
                <div className="mt-0.5 text-[10px] text-white/35">{f.note}</div>
              </div>
            ))}
          </div>
        )}

        {ocr && !ocr.available && (
          <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-[12px] text-amber-200/85">
            未检测到 OCR 引擎，扫描件 PDF 与图片无法识别。安装命令：
            <code className="ml-1 text-amber-100">pip install rapidocr-onnxruntime</code>
          </div>
        )}
      </HoloCard>

      {/* 权限矩阵（仅管理员可见） */}
      {perms?.role === "admin" && (
        <HoloCard className="p-6">
          <SectionTitle className="!mb-0">角色权限矩阵</SectionTitle>
          <div className="mt-1 text-[11px] text-white/35">
            与「设置 → 角色与权限」共用同一份角色；此处可微调各角色的知识库能力。
            拦截在服务端执行，前端隐藏按钮不等于放行。
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[560px] text-[12px]">
              <thead>
                <tr className="text-left text-white/45">
                  <th className="py-1.5 pr-3 font-normal">能力</th>
                  {perms.roles.map((r) => (
                    <th key={r.id} className="py-1.5 pr-3 font-normal">
                      {r.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {perms.cap_defs.map((c) => (
                  <tr key={c.id} className="border-t border-white/5">
                    <td className="py-1.5 pr-3 text-white/70">
                      {c.label}
                      <span className="ml-1 text-[10px] text-white/30">{c.desc}</span>
                    </td>
                    {perms.roles.map((r) => (
                      <td key={r.id} className="py-1.5 pr-3">
                        <input
                          type="checkbox"
                          className="accent-[var(--accent-ink)]"
                          checked={Boolean(r.caps[c.id])}
                          onChange={(e) => {
                            const next = { ...r.caps, [c.id]: e.target.checked };
                            api
                              .kbSavePermissions({ [r.id]: Object.keys(next).filter((k) => next[k]) })
                              .then((p) => {
                                setPerms(p);
                                toast("权限已保存");
                              })
                              .catch((ex: Error) => toast(ex.message, "err"));
                          }}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3">
            <HoloButton
              size="sm"
              variant="ghost"
              onClick={() => {
                api
                  .kbSavePermissions({ reset: true })
                  .then((p) => {
                    setPerms(p);
                    toast("已恢复默认权限矩阵");
                  })
                  .catch((ex: Error) => toast(ex.message, "err"));
              }}
            >
              恢复默认
            </HoloButton>
          </div>
        </HoloCard>
      )}
    </div>
  );
}
