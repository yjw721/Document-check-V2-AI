import { useCallback, useEffect, useMemo, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloModal from "../../components/ui/HoloModal";
import HoloBadge from "../../components/ui/HoloBadge";
import { HoloSelect } from "../../components/ui/HoloInput";
import SectionTitle from "../../components/common/SectionTitle";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { DraftItem, DraftDetail, DataStoreStats } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { SEV_LABEL } from "../../lib/constants";

/* 统一数据层 · AI 产出草稿箱
 * AI 规则/词库智能生成的产出会先落到这里（pending），人工确认后「采纳」即并入正式
 * 规则库 / 词库并立即作用于检测链路；「丢弃」则不入库。全部动作写入操作日志可溯源。 */

const STATUS_META: Record<string, { label: string; tone: "warn" | "ok" | "gray"; ic: string }> = {
  pending: { label: "待确认", tone: "warn", ic: "⏳" },
  accepted: { label: "已采纳", tone: "ok", ic: "✅" },
  rejected: { label: "已丢弃", tone: "gray", ic: "🗑️" },
};

interface Entry {
  keyword?: string;
  tag?: string;
  suggestion?: string;
  [k: string]: unknown;
}

export default function DraftsTab() {
  const toast = useToast();
  const [items, setItems] = useState<DraftItem[]>([]);
  const [stats, setStats] = useState<DataStoreStats | null>(null);
  const [status, setStatus] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [actId, setActId] = useState("");
  const [detail, setDetail] = useState<DraftDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [confirm, setConfirm] = useState<{ id: string; kind: "accept" | "reject" } | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [d, s] = await Promise.all([
        api.drafts({ status: status || undefined }),
        api.dataStats(),
      ]);
      setItems(d.items || []);
      setStats(s.stats || null);
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openDetail = async (id: string) => {
    setActId(id);
    try {
      const r = await api.draft(id);
      setDetail(r.draft);
      setDetailOpen(true);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setActId("");
    }
  };

  const doHandle = async () => {
    if (!confirm) return;
    const { id, kind } = confirm;
    setActId(id);
    try {
      const r = kind === "accept" ? await api.draftAccept(id) : await api.draftReject(id);
      toast(r.message || (kind === "accept" ? "已采纳并入正式库" : "已丢弃"), kind === "reject" ? "warn" : "ok");
      setDetailOpen(false);
      setDetail(null);
      await reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setActId("");
      setConfirm(null);
    }
  };

  /* 详情内的规则 / 词条分组预览 */
  const ruleList = useMemo(() => (detail?.rules ?? []) as Record<string, unknown>[], [detail]);
  const wbList = useMemo(() => (detail?.wordbanks ?? []) as Record<string, unknown>[], [detail]);

  if (err) {
    return (
      <HoloCard className="p-6">
        <div className="py-10 text-center text-white/40">加载失败：{err}</div>
        <div className="pb-6 text-center text-xs text-white/30">请确认后端服务已在 http://127.0.0.1:8501 启动</div>
      </HoloCard>
    );
  }

  return (
    <div className="space-y-4">
      {/* ---------- 数据层总览 ---------- */}
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">统一数据层 · AI 产出草稿箱</SectionTitle>
          <HoloBadge tone="gray">config/data_store.json</HoloBadge>
          <span className="ml-auto flex gap-2.5">
            <HoloButton size="sm" onClick={() => (window.location.hash = "#unified/aiCreate")}>
              前往 AI 智能生成 →
            </HoloButton>
            <HoloButton size="sm" disabled={loading} onClick={() => void reload()}>
              {loading ? "刷新中…" : "刷新"}
            </HoloButton>
          </span>
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-white/50">
          AI 规则 / 词库智能生成的产出会先落到草稿箱永久保存（不会再因任务回收而丢失）。
          逐条确认后点「采纳」即并入正式规则库 / 词库并立即作用于检测链路；「丢弃」则仅标记不入库。
          采纳与丢弃均写入操作日志，检测报告可溯源。
        </p>

        {stats && (
          <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
            {[
              { k: "草稿总数", v: stats.total, ic: "🗄️" },
              { k: "待确认", v: stats.by_status?.pending ?? 0, ic: "⏳" },
              { k: "已采纳", v: stats.by_status?.accepted ?? 0, ic: "✅" },
              { k: "已入库规则", v: stats.accepted_rules, ic: "🧩" },
              { k: "已入库词条", v: stats.accepted_entries, ic: "📚" },
            ].map((c) => (
              <div key={c.k} className="rounded-2xl border border-white/10 bg-white/[0.04] px-3.5 py-3">
                <div className="text-[11px] text-white/45">
                  {c.ic} {c.k}
                </div>
                <div className="holo-text mt-1 text-xl font-bold">{c.v}</div>
              </div>
            ))}
          </div>
        )}
      </HoloCard>

      {/* ---------- 草稿列表 ---------- */}
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">草稿列表</SectionTitle>
          <span className="text-[11px] text-white/35">共 {items.length} 条</span>
          <span className="ml-auto flex items-center gap-2">
            <span className="text-[12px] text-white/55">状态筛选</span>
            <HoloSelect
              className="w-[130px] py-1.5"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">全部</option>
              <option value="pending">待确认</option>
              <option value="accepted">已采纳</option>
              <option value="rejected">已丢弃</option>
            </HoloSelect>
          </span>
        </div>

        {loading && !items.length ? (
          <div className="py-10 text-center text-sm text-white/40">加载中…</div>
        ) : !items.length ? (
          <EmptyState text="暂无草稿。前往「AI 规则词库智能生成」产出后会自动出现在这里" icon="🗄️" />
        ) : (
          <div className="mt-3 space-y-2.5">
            {items.map((d) => {
              const m = STATUS_META[d.status] ?? STATUS_META.pending;
              const s = d.stats || { rules: 0, wordbanks: 0, entries: 0 };
              const busy = actId === d.id;
              return (
                <div
                  key={d.id}
                  className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 transition-all duration-500 hover:border-white/20 hover:bg-white/[0.06]"
                >
                  <div className="flex flex-wrap items-center gap-2.5">
                    <HoloBadge tone={m.tone}>
                      {m.ic} {m.label}
                    </HoloBadge>
                    <b className="text-[13px] text-white/90">{d.source_label || "AI生成"}</b>
                    <span className="text-[11px] text-white/40">
                      {d.filename ? `来源文件：${d.filename} · ` : ""}
                      {d.created_at}
                    </span>
                    <code className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-white/40">{d.id}</code>
                    <span className="ml-auto flex flex-wrap items-center gap-2">
                      <HoloButton size="sm" disabled={busy} onClick={() => void openDetail(d.id)}>
                        查看
                      </HoloButton>
                      {d.status === "pending" && (
                        <>
                          <HoloButton
                            variant="primary"
                            size="sm"
                            disabled={busy}
                            onClick={() => setConfirm({ id: d.id, kind: "accept" })}
                          >
                            采纳入库
                          </HoloButton>
                          <HoloButton
                            variant="danger"
                            size="sm"
                            disabled={busy}
                            onClick={() => setConfirm({ id: d.id, kind: "reject" })}
                          >
                            丢弃
                          </HoloButton>
                        </>
                      )}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-3 text-[12px] text-white/55">
                    <span>规则 {s.rules} 条</span>
                    <span>词库 {s.wordbanks} 组</span>
                    <span>词条 {s.entries} 条</span>
                    {d.filter && (d.filter as Record<string, number>).filtered_rules ? (
                      <span className="text-[var(--tone-warn)]">
                        校验过滤规则 {(d.filter as Record<string, number>).filtered_rules} 条
                      </span>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </HoloCard>

      {/* ---------- 详情 ---------- */}
      <HoloModal
        open={detailOpen}
        title="草稿详情"
        width={720}
        onClose={() => {
          setDetailOpen(false);
          setDetail(null);
        }}
        footer={
          detail && (
            <>
              <HoloButton
                onClick={() => {
                  setDetailOpen(false);
                  setDetail(null);
                }}
              >
                关闭
              </HoloButton>
              {detail.status === "pending" && (
                <>
                  <HoloButton variant="danger" onClick={() => setConfirm({ id: detail.id, kind: "reject" })}>
                    丢弃
                  </HoloButton>
                  <HoloButton variant="primary" onClick={() => setConfirm({ id: detail.id, kind: "accept" })}>
                    采纳并入库
                  </HoloButton>
                </>
              )}
            </>
          )
        }
      >
        {detail && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2.5">
              <HoloBadge tone={(STATUS_META[detail.status] ?? STATUS_META.pending).tone}>
                {(STATUS_META[detail.status] ?? STATUS_META.pending).label}
              </HoloBadge>
              <b className="text-white/90">{detail.source_label || "AI生成"}</b>
              <span className="text-[11px] text-white/40">{detail.created_at}</span>
              {detail.filename && <span className="text-[11px] text-white/40">文件：{detail.filename}</span>}
            </div>

            {/* 规则 */}
            <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-3.5">
              <div className="mb-2 text-sm font-semibold text-white/90">🧩 规则（{ruleList.length} 条）</div>
              {!ruleList.length ? (
                <div className="py-3 text-center text-[12px] text-white/35">无</div>
              ) : (
                <div className="max-h-[240px] space-y-2 overflow-auto pr-1">
                  {ruleList.map((r, i) => (
                    <div key={i} className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-semibold text-white/85">
                          {String(r.name ?? "未命名规则")}
                        </span>
                        <HoloBadge tone="gray">{String(r.match_mode ?? "keyword")}</HoloBadge>
                        <HoloBadge
                          tone={
                            String(r.severity ?? "medium") === "high"
                              ? "sev-high"
                              : String(r.severity ?? "medium") === "low"
                                ? "sev-low"
                                : "sev-medium"
                          }
                        >
                          {SEV_LABEL[String(r.severity ?? "medium")] ?? String(r.severity ?? "一般")}
                        </HoloBadge>
                      </div>
                      <div className="mt-1 break-all font-mono text-[11px] text-white/55">
                        {String(r.pattern ?? "")}
                      </div>
                      {r.suggestion ? (
                        <div className="mt-1 text-[11px] text-white/45">建议：{String(r.suggestion)}</div>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 词库 */}
            <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-3.5">
              <div className="mb-2 text-sm font-semibold text-white/90">📚 词库（{wbList.length} 组）</div>
              {!wbList.length ? (
                <div className="py-3 text-center text-[12px] text-white/35">无</div>
              ) : (
                <div className="max-h-[260px] space-y-2.5 overflow-auto pr-1">
                  {wbList.map((w, wi) => {
                    const es = (w.entries ?? []) as Entry[];
                    return (
                      <div key={wi} className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
                        <div className="text-[13px] font-semibold text-white/85">
                          {String(w.name ?? "未命名词库")}（{es.length} 条）
                        </div>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {es.slice(0, 60).map((e, ei) => (
                            <span
                              key={ei}
                              title={e.suggestion ? String(e.suggestion) : undefined}
                              className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-white/70"
                            >
                              {String(e.keyword ?? "")}
                            </span>
                          ))}
                          {es.length > 60 && (
                            <span className="px-1 text-[11px] text-white/35">等 {es.length} 条…</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {detail.note ? (
              <div className="text-[11px] text-white/40">处置记录：{String(detail.note)}</div>
            ) : null}
          </div>
        )}
      </HoloModal>

      {/* ---------- 处置确认 ---------- */}
      <HoloModal
        open={!!confirm}
        title={confirm?.kind === "accept" ? "确认采纳该草稿？" : "确认丢弃该草稿？"}
        width={480}
        onClose={() => setConfirm(null)}
        footer={
          <>
            <HoloButton onClick={() => setConfirm(null)}>取消</HoloButton>
            <HoloButton variant={confirm?.kind === "accept" ? "primary" : "danger"} onClick={() => void doHandle()}>
              {confirm?.kind === "accept" ? "确认采纳" : "确认丢弃"}
            </HoloButton>
          </>
        }
      >
        <div className="leading-relaxed text-white/70">
          {confirm?.kind === "accept" ? (
            <>
              采纳后将把草稿中的规则与词条并入正式
              <code className="mx-1 rounded bg-white/5 px-1 text-[11px] text-white/60">custom_rules.json</code>/
              <code className="mx-1 rounded bg-white/5 px-1 text-[11px] text-white/60">wordbanks.json</code>，
              并<b className="text-white/90">立即作用于后续检测</b>。无效或重复条目会在入库时自动过滤。
            </>
          ) : (
            <>丢弃后该草稿标记为已丢弃，不会并入规则库 / 词库；记录仍保留以便追溯。</>
          )}
        </div>
      </HoloModal>
    </div>
  );
}
