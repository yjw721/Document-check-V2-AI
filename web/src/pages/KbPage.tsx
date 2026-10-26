import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import HoloBadge from "../components/ui/HoloBadge";
import { HoloInput, HoloSelect, HoloTextarea } from "../components/ui/HoloInput";
import HoloSwitch from "../components/ui/HoloSwitch";
import SectionTitle from "../components/common/SectionTitle";
import EmptyState from "../components/common/EmptyState";
import { api } from "../lib/api";
import { useToast } from "../components/ui/Toast";
import KnowledgeGraph from "../components/kb/KnowledgeGraph";
import type {
  KbAskResp,
  KbConfig,
  KbDoc,
  KbFolder,
  KbGraphNode,
  KbGraphResp,
  KbPermissions,
  KbRecord,
  KbRecordType,
  KbRecordStats,
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
 * - 权限：与核验系统角色体系打通（settings.role 默认角色 + X-Admin-Token 提权），服务端强制校验 */

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

/* 图谱选中节点详情 */
function GraphDetail({
  graph,
  sel,
  onPick,
  onClose,
}: {
  graph: KbGraphResp;
  sel: KbGraphNode | null;
  onPick: (id: string) => void;
  onClose: () => void;
}) {
  if (!sel) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-[12px] leading-relaxed text-white/45">
        点击图谱中的任意节点查看详情：章节路径、所属文档、包含段落数与样例文本，以及与之
        <span className="text-cyan-200/70">概念关联</span>的其它章节（基于向量语义相似或关键词共现）。
      </div>
    );
  }
  const related = graph.edges
    .filter((e) => e.kind === "related" && (e.source === sel.id || e.target === sel.id))
    .map((e) => {
      const oid = e.source === sel.id ? e.target : e.source;
      const other = graph.nodes.find((n) => n.id === oid);
      return other ? { node: other, w: e.weight } : null;
    })
    .filter(Boolean)
    .sort((a, b) => b!.w - a!.w) as { node: KbGraphNode; w: number }[];
  const docLabels = (sel.doc_ids || [])
    .map((did) => graph.nodes.find((n) => n.id === `doc:${did}`)?.label || did);

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-white/90">{sel.label}</div>
          <div className="mt-0.5 text-[11px] text-white/45">
            {sel.type === "doc" ? "文档" : `章节 · 层级 ${sel.level}`} · {sel.chunks} 段
            {sel.rel_count > 0 && ` · ${sel.rel_count} 条关联`}
          </div>
        </div>
        <button
          className="rounded-md px-1.5 text-white/40 transition-all hover:text-white/80"
          onClick={onClose}
          title="关闭"
        >
          ✕
        </button>
      </div>
      {sel.full_path !== sel.label && (
        <div className="mt-2 break-words text-[11px] text-white/40">{sel.full_path}</div>
      )}
      <div className="mt-2 max-h-[180px] overflow-y-auto rounded-xl border border-white/10 bg-black/20 p-2 text-[11px] leading-relaxed text-white/70">
        {sel.sample || "（无样例文本）"}
      </div>
      {docLabels.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1 text-[11px] text-white/45">
          <span className="text-white/35">所属文档：</span>
          {docLabels.map((d, i) => (
            <span key={i} className="rounded bg-white/10 px-1.5 py-0.5 text-white/60">
              {d}
            </span>
          ))}
        </div>
      )}
      {related.length > 0 && (
        <>
          <div className="mb-1 mt-3 text-[11px] font-medium text-white/55">
            概念关联（{related.length}）
          </div>
          <div className="flex flex-col gap-1">
            {related.map((r) => (
              <button
                key={r.node.id}
                onClick={() => onPick(r.node.id)}
                className="flex items-center gap-2 rounded-lg border border-white/10 px-2 py-1 text-left text-[11px] text-white/65 transition-all hover:bg-white/10 hover:text-white/90"
              >
                <span className="flex-1 truncate">{r.node.label}</span>
                <span className="text-[10px] text-cyan-200/70">{r.w.toFixed(2)}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
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

  /* 检索 / 问答使用记录（逐条 + 整体管理） */
  const [recs, setRecs] = useState<KbRecord[]>([]);
  const [recTotal, setRecTotal] = useState(0);
  const [recStats, setRecStats] = useState<KbRecordStats>({ total: 0, ask: 0, search: 0 });
  const [recLoading, setRecLoading] = useState(false);
  const [recType, setRecType] = useState<"" | KbRecordType>("");
  const [recKw, setRecKw] = useState("");
  const [recOffset, setRecOffset] = useState(0);
  const [recDetail, setRecDetail] = useState<KbRecord | null>(null);
  const REC_LIMIT = 15;
  const recPage = Math.floor(recOffset / REC_LIMIT) + 1;
  const recPageTotal = Math.max(1, Math.ceil(recTotal / REC_LIMIT));
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

  /* 思维图谱 */
  const [graph, setGraph] = useState<KbGraphResp | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphCurOnly, setGraphCurOnly] = useState(false);
  const [showRelated] = useState(true);
  const [selNode, setSelNode] = useState<KbGraphNode | null>(null);

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

  /* 思维图谱：随「仅当前目录」开关与目录选择刷新 */
  const loadGraph = useCallback(async () => {
    setGraphLoading(true);
    try {
      const g = await api.kbGraph(graphCurOnly ? curFolder : "", graphCurOnly ? includeSub : true);
      setGraph(g);
      setSelNode(null);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setGraphLoading(false);
    }
  }, [graphCurOnly, curFolder, includeSub]);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

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

  /* ---------- 检索 / 问答记录：加载、筛选、删除、清空、导出、重跑 ---------- */
  const loadRecs = useCallback(async () => {
    setRecLoading(true);
    try {
      const r = await api.kbRecords({
        type: recType,
        keyword: recKw || undefined,
        limit: REC_LIMIT,
        offset: recOffset,
      });
      if (r.ok) {
        setRecs(r.items);
        setRecTotal(r.total);
        setRecStats(r.stats);
      }
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setRecLoading(false);
    }
  }, [recType, recKw, recOffset]);

  useEffect(() => {
    loadRecs();
  }, [loadRecs]);

  const openDetail = async (id: string) => {
    try {
      const r = await api.kbRecord(id);
      if (r.ok) setRecDetail(r as KbRecord);
    } catch (ex) {
      toast((ex as Error).message, "err");
    }
  };

  const deleteRecord = async (id: string, closeAfter = false) => {
    if (!confirm("确定删除这条记录？此操作不可恢复。")) return;
    try {
      const r = await api.kbRecordDelete(id);
      if (r.ok) {
        if (closeAfter) setRecDetail(null);
        loadRecs();
        toast("已删除该记录");
      }
    } catch (ex) {
      toast((ex as Error).message, "err");
    }
  };

  const clearRecs = async () => {
    const label = recType ? (recType === "ask" ? "问答" : "检索") : "全部";
    if (!confirm(`确定清空${label}记录？此操作不可恢复。`)) return;
    try {
      const r = await api.kbRecordsClear(recType || undefined);
      if (r.ok) {
        setRecOffset(0);
        loadRecs();
        toast(`已清空${label}记录`);
      }
    } catch (ex) {
      toast((ex as Error).message, "err");
    }
  };

  const exportRecs = async (fmt: "json" | "md") => {
    try {
      const r = await api.kbRecordsExport({
        type: recType || undefined,
        keyword: recKw || undefined,
        fmt,
      });
      if (r.ok) {
        downloadText(r.content, r.filename, fmt === "md" ? "text/markdown" : "application/json");
        toast(`已导出 ${r.filename}`);
      }
    } catch (ex) {
      toast((ex as Error).message, "err");
    }
  };

  const rerunAsk = async (rec: KbRecord) => {
    const q = rec.query;
    if (q.length < 2) {
      toast("记录中的问题为空", "err");
      return;
    }
    setAsking(true);
    try {
      const resp = await api.kbAsk({ question: q, top_k: rec.params.top_k || 3, folder: curFolder || undefined });
      setQa((list) => [{ q, resp }, ...list].slice(0, 20));
      toast("已重新提问，结果见「知识库问答」");
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setAsking(false);
    }
  };

  const rerunSearch = async (rec: KbRecord) => {
    const q = rec.query;
    if (q.length < 2) {
      toast("记录中的检索词为空", "err");
      return;
    }
    setSearching(true);
    try {
      setDbg(
        await api.kbSearch({
          question: q,
          top_k: rec.params.top_k || 5,
          folder: includeSub && curFolder ? curFolder : undefined,
        }),
      );
      toast("已重新检索，结果见「检索诊断」");
    } catch (ex) {
      toast((ex as Error).message, "err");
    } finally {
      setSearching(false);
    }
  };

  const goPrevPage = () => {
    if (recOffset >= REC_LIMIT) setRecOffset(recOffset - REC_LIMIT);
  };
  const goNextPage = () => {
    if (recOffset + REC_LIMIT < recTotal) setRecOffset(recOffset + REC_LIMIT);
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

      {/* 思维图谱 */}
      <HoloCard className="p-6">
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">知识思维图谱</SectionTitle>
          <span className="text-[11px] text-white/40">文档 → 章节层级 + 跨章节概念关联</span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-[11px] text-white/55">
              <input
                type="checkbox"
                className="accent-[var(--accent-ink)]"
                checked={graphCurOnly}
                onChange={(e) => setGraphCurOnly(e.target.checked)}
              />
              仅当前目录
            </label>
            {graph?.stats && (
              <span className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/50">
                {graph.stats.nodes} 节点 · {graph.stats.edges} 边 · 关联 {graph.stats.related}
                {graph.stats.has_vectors ? "" : " · 关键词共现"}
              </span>
            )}
            <HoloButton variant="ghost" size="sm" loading={graphLoading} onClick={loadGraph}>
              ↻ 重新生成
            </HoloButton>
          </div>
        </div>

        {graphLoading && !graph ? (
          <div className="flex h-[520px] items-center justify-center text-[12px] text-white/40">
            图谱生成中…
          </div>
        ) : graph && graph.nodes.length ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
            <KnowledgeGraph
              data={graph}
              selectedId={selNode?.id}
              onSelect={setSelNode}
              showRelated={showRelated}
            />
            <GraphDetail
              graph={graph}
              sel={selNode}
              onPick={(id) => {
                const n = graph.nodes.find((x) => x.id === id);
                if (n) setSelNode(n);
              }}
              onClose={() => setSelNode(null)}
            />
          </div>
        ) : (
          <EmptyState text={graph?.message || "暂无可用图谱，请先上传文档并建立索引"} icon="🕸️" />
        )}
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

      {/* 检索与问答记录（逐条 + 整体管理） */}
      <HoloCard className="p-6">
        <SectionTitle className="!mb-0">检索与问答记录</SectionTitle>
        <div className="mt-1 text-[11px] text-white/35">
          自动留存每次「检索诊断」与「知识库问答」，支持逐条查看 / 删除 / 重新执行 / 导出，以及整体筛选、统计、清空与导出。
        </div>

        {/* 工具栏：筛选 + 整体操作 */}
        <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="flex flex-wrap items-center gap-2">
            <HoloSelect
              value={recType}
              onChange={(e) => {
                setRecType(e.target.value as "" | KbRecordType);
                setRecOffset(0);
              }}
            >
              <option value="">全部类型</option>
              <option value="ask">问答</option>
              <option value="search">检索</option>
            </HoloSelect>
            <HoloInput
              className="w-56"
              placeholder="按问题 / 检索词筛选"
              value={recKw}
              onChange={(e) => setRecKw(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") setRecOffset(0);
              }}
            />
            <HoloButton variant="primary" onClick={() => setRecOffset(0)}>
              筛选
            </HoloButton>
          </div>
          <div className="flex flex-wrap items-center gap-2 lg:ml-auto">
            <span className="text-[11px] text-white/40">
              共 {recStats.total} 条 · 问答 {recStats.ask} · 检索 {recStats.search}
            </span>
            <HoloButton onClick={() => exportRecs("json")}>导出 JSON</HoloButton>
            <HoloButton onClick={() => exportRecs("md")}>导出 Markdown</HoloButton>
            {can("kb.delete") && (
              <HoloButton
                variant="danger"
                onClick={clearRecs}
              >
                清空{recType ? (recType === "ask" ? "问答" : "检索") : "全部"}
              </HoloButton>
            )}
          </div>
        </div>

        {/* 列表 */}
        <div className="mt-4 space-y-2">
          {recLoading ? (
            <div className="text-[12px] text-white/40">加载中…</div>
          ) : recs.length === 0 ? (
            <EmptyState text="暂无记录：在「检索诊断」或「知识库问答」中发起查询后，会自动留存到这里。" />
          ) : (
            recs.map((rec) => (
              <RecordRow
                key={rec.id}
                rec={rec}
                onDetail={openDetail}
                onDelete={(id) => deleteRecord(id)}
                onRerun={rec.type === "ask" ? rerunAsk : rerunSearch}
                canDelete={can("kb.delete")}
              />
            ))
          )}
        </div>

        {/* 分页 */}
        {recTotal > REC_LIMIT && (
          <div className="mt-4 flex items-center justify-between text-[11px] text-white/45">
            <span>
              第 {recPage} / {recPageTotal} 页 · 共 {recTotal} 条
            </span>
            <div className="flex gap-2">
              <HoloButton disabled={recOffset <= 0} onClick={goPrevPage}>
                上一页
              </HoloButton>
              <HoloButton disabled={recOffset + REC_LIMIT >= recTotal} onClick={goNextPage}>
                下一页
              </HoloButton>
            </div>
          </div>
        )}
      </HoloCard>

      {recDetail && (
        <RecordDetailModal
          rec={recDetail}
          onClose={() => setRecDetail(null)}
          onRerun={(r) => {
            (r.type === "ask" ? rerunAsk : rerunSearch)(r);
            setRecDetail(null);
          }}
          onDelete={(id) => deleteRecord(id, true)}
          canDelete={can("kb.delete")}
        />
      )}

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

/* ---------- 检索 / 问答记录：单条行 + 详情弹窗 ---------- */
function downloadText(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function RecordRow({
  rec,
  onDetail,
  onDelete,
  onRerun,
  canDelete,
}: {
  rec: KbRecord;
  onDetail: (id: string) => void;
  onDelete: (id: string) => void;
  onRerun: (rec: KbRecord) => void;
  canDelete: boolean;
}) {
  const s = rec.summary;
  const isAsk = rec.type === "ask";
  const conf = Math.round((s.confidence ?? 0) * 100);
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <HoloBadge tone={isAsk ? "accent" : "gray"}>{isAsk ? "问答" : "检索"}</HoloBadge>
        <span className="text-[11px] text-white/40">{rec.created_at}</span>
        {isAsk ? (
          <span className="flex flex-wrap items-center gap-1.5 text-[11px]">
            {s.refused ? (
              <HoloBadge tone="warn">资料不足·已拒答</HoloBadge>
            ) : (
              <HoloBadge tone={s.grounded ? "ok" : "danger"}>{s.grounded ? "可溯源" : "可疑引用"}</HoloBadge>
            )}
            <span className="text-white/40">可信度 {conf}%</span>
            <span className="text-white/40">来源 {s.sources_count ?? 0}</span>
            {s.elapsed != null && <span className="text-white/30">{s.elapsed}s</span>}
          </span>
        ) : (
          <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-white/40">
            <span>命中 {s.hits_count ?? 0} 段</span>
            <span>· 模式 {MODE_LABEL[s.mode || ""] || s.mode || "-"}</span>
            {s.top_file && <span>· 最佳 {s.top_file}</span>}
          </span>
        )}
        {s.has_error && <HoloBadge tone="danger">执行出错</HoloBadge>}
        <div className="ml-auto flex gap-1.5">
          <button
            onClick={() => onRerun(rec)}
            className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/55 transition-all hover:bg-white/10 hover:text-white/80"
          >
            重新执行
          </button>
          <button
            onClick={() => onDetail(rec.id)}
            className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-white/55 transition-all hover:bg-white/10 hover:text-white/80"
          >
            详情
          </button>
          {canDelete && (
            <button
              onClick={() => onDelete(rec.id)}
              className="rounded-lg border border-white/10 px-2 py-0.5 text-[11px] text-rose-300/80 transition-all hover:bg-rose-500/10"
            >
              删除
            </button>
          )}
        </div>
      </div>
      <div className="mt-1.5 truncate text-[13px] text-white/80" title={rec.query}>
        {rec.query || "（空查询）"}
      </div>
    </div>
  );
}

function RecordDetailModal({
  rec,
  onClose,
  onRerun,
  onDelete,
  canDelete,
}: {
  rec: KbRecord;
  onClose: () => void;
  onRerun: (rec: KbRecord) => void;
  onDelete: (id: string) => void;
  canDelete: boolean;
}) {
  const isAsk = rec.type === "ask";
  const s = rec.summary;
  const res = (rec.result || {}) as KbAskResp & KbSearchResp & Record<string, any>;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[88vh] w-full max-w-3xl overflow-auto rounded-3xl border border-white/15 bg-[#14122b] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-center gap-2">
          <HoloBadge tone={isAsk ? "accent" : "gray"}>{isAsk ? "知识库问答" : "检索诊断"}</HoloBadge>
          <span className="text-[11px] text-white/40">
            {rec.created_at} · {rec.id}
          </span>
          <div className="ml-auto flex gap-1.5">
            <button
              onClick={() => onRerun(rec)}
              className="rounded-lg border border-white/15 px-2.5 py-1 text-[12px] text-white/70 transition-all hover:bg-white/10"
            >
              重新执行
            </button>
            {canDelete && (
              <button
                onClick={() => onDelete(rec.id)}
                className="rounded-lg border border-rose-400/30 px-2.5 py-1 text-[12px] text-rose-300/80 transition-all hover:bg-rose-500/10"
              >
                删除
              </button>
            )}
            <button
              onClick={onClose}
              className="rounded-lg border border-white/15 px-2.5 py-1 text-[12px] text-white/70 transition-all hover:bg-white/10"
            >
              关闭
            </button>
          </div>
        </div>

        <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.03] p-3">
          <div className="text-[11px] text-white/40">问题 / 检索词</div>
          <div className="mt-1 text-[14px] text-white/90">{rec.query || "（空）"}</div>
          <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-white/45">
            <span>Top K：{rec.params.top_k ?? "-"}</span>
            <span>范围：{rec.params.scope || "全库"}</span>
          </div>
        </div>

        {isAsk ? (
          <div className="mt-3 space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <HoloBadge tone={s.grounded ? "ok" : "danger"}>
                {s.grounded ? "引用可溯源" : "存在可疑引用"}
              </HoloBadge>
              <HoloBadge tone="gray">可信度 {Math.round((s.confidence ?? 0) * 100)}%</HoloBadge>
              {s.refused && <HoloBadge tone="warn">资料不足·已拒答</HoloBadge>}
            </div>
            <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-white/80">{res.answer}</div>
            {s.has_error && res.error && (
              <div className="rounded-xl border border-rose-400/25 bg-rose-400/[0.07] px-3 py-2 text-[11px] text-rose-200/85">
                ⚠️ {res.error}
              </div>
            )}
            {Array.isArray(res.sources) && res.sources.length > 0 && (
              <div>
                <div className="mb-1 text-[11px] text-white/45">来源（{res.sources.length}）</div>
                <div className="flex flex-wrap gap-1.5">
                  {res.sources.map((src: any, j: number) => (
                    <span
                      key={j}
                      className="rounded-full border border-[var(--border-accent-soft)] bg-white/[0.04] px-2.5 py-1 text-[11px] text-cyan-200/90"
                    >
                      {src.file} · {src.section}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/45">
              <HoloBadge tone="gray">模式 {MODE_LABEL[res.mode || ""] || res.mode || "-"}</HoloBadge>
              <span>命中 {Array.isArray(res.hits) ? res.hits.length : 0} 段</span>
            </div>
            {Array.isArray(res.hits) && res.hits.length > 0 ? (
              res.hits.map((h: any) => (
                <div key={h.chunk_id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="rounded-md bg-white/10 px-1.5 py-0.5 text-white/60">#{h.rank}</span>
                    <span className="text-cyan-200/85">
                      RRF {h.rrf} · BM25 {h.bm25}
                      {h.cos ? ` · 向量 ${h.cos}` : ""}
                    </span>
                    <span className="ml-auto text-white/45">{h.file}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-white/35">
                    {h.path || h.section}
                    {h.loc ? ` · ${h.loc}` : ""}
                  </div>
                  <div className="mt-2 whitespace-pre-wrap text-[12px] leading-relaxed text-white/65">{h.text}</div>
                </div>
              ))
            ) : (
              <div className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-[12px] text-white/45">
                未命中任何片段。
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
