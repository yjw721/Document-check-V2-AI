import { useEffect, useMemo, useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloModal from "../../components/ui/HoloModal";
import HoloBadge from "../../components/ui/HoloBadge";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { emitRulesChanged } from "../../lib/events";
import type { ScanItem, ScanResult } from "../../lib/types";

/* 标签5 · 一键扫描清理：扫描全部来源规则词库，分类识别无效/重复/正常条目，
   勾选删除 + 一键清理无效 + 一键处理重复 + 二次确认 + 备份导出 + 进度提示。
   内置标准规则 / 词库（dictionary 来源）只读，禁止删除，仅作诊断提示。 */
export default function ScanTab() {
  const toast = useToast();
  const [result, setResult] = useState<ScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [prog, setProg] = useState({ pct: 0, text: "" });
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<{ open: boolean; ids: string[]; label: string }>({
    open: false,
    ids: [],
    label: "",
  });
  const [busy, setBusy] = useState(false);
  const [foldNormal, setFoldNormal] = useState(true);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    api
      .scanLast()
      .then((r) => r.result && setResult(r.result))
      .catch(() => {});
    return () => {
      mounted.current = false;
    };
  }, []);

  const items = useMemo(() => result?.items ?? [], [result]);
  const invalidItems = useMemo(() => items.filter((i) => i.category === "invalid"), [items]);
  const dupItems = useMemo(() => items.filter((i) => i.category === "duplicate"), [items]);
  const dupCleanable = useMemo(() => dupItems.filter((i) => !i.keep), [dupItems]);
  const normalItems = useMemo(() => items.filter((i) => i.category === "normal"), [items]);
  const stats = result?.stats;

  /* ---------- 勾选（内置只读条目不可勾选） ---------- */
  const readonlyItem = (it: ScanItem) => it.source === "dictionary";
  const toggle = (it: ScanItem) => {
    if (it.keep || readonlyItem(it)) return;
    setChecked((s) => {
      const n = new Set(s);
      if (n.has(it.item_id)) n.delete(it.item_id);
      else n.add(it.item_id);
      return n;
    });
  };

  const setGroup = (list: ScanItem[], on: boolean) =>
    setChecked((s) => {
      const n = new Set(s);
      for (const it of list) {
        if (it.keep || readonlyItem(it)) continue;
        if (on) n.add(it.item_id);
        else n.delete(it.item_id);
      }
      return n;
    });

  const selectable = (list: ScanItem[]) => list.filter((i) => !i.keep && !readonlyItem(i));
  const groupAllOn = (list: ScanItem[]) =>
    list.filter((i) => !i.keep && !readonlyItem(i)).length > 0 &&
    selectable(list).every((i) => checked.has(i.item_id));
  const cleanableIn = (list: ScanItem[]) => list.filter((i) => !i.keep && !readonlyItem(i));

  /* ---------- 扫描（进度轮询） ---------- */
  const startScan = async () => {
    if (scanning) return;
    setScanning(true);
    setChecked(new Set());
    setProg({ pct: 0, text: "任务启动…" });
    try {
      const r = await api.scanStart();
      await poll(r.task_id);
    } catch (e) {
      if (mounted.current) {
        toast((e as Error).message, "err");
        setScanning(false);
      }
    }
  };

  const poll = async (tid: string) => {
    while (mounted.current) {
      let cont = false;
      try {
        const s = await api.taskPoll(tid);
        if (s.status === "running") {
          setProg({ pct: s.progress, text: s.stage_text });
          cont = true;
        } else if (s.status === "done") {
          setProg({ pct: 100, text: "扫描完成" });
          setResult(s.result ?? null);
          setScanning(false);
          const st = s.result?.stats;
          toast(
            `扫描完成：共 ${st?.scanned ?? 0} 条，无效 ${st?.invalid ?? 0} / 重复 ${st?.duplicate ?? 0} / 正常 ${st?.normal ?? 0}`,
          );
        } else {
          setScanning(false);
          toast(s.error || "扫描失败", "err");
        }
      } catch {
        try {
          const last = await api.scanLast();
          if (mounted.current && last.result) setResult(last.result);
        } catch {
          /* 忽略回退失败 */
        }
        if (mounted.current) {
          setScanning(false);
          toast("扫描任务已过期，请重新扫描", "warn");
        }
      }
      if (!cont || !mounted.current) break;
      await new Promise((r) => setTimeout(r, 700));
    }
  };

  /* ---------- 导出备份 ---------- */
  const download = (blob: Blob, name: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const exportBackup = async (ids: string[], fmt: "txt" | "csv" = "txt") => {
    try {
      const blob = await api.scanExport({ ids, format: fmt });
      const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
      download(blob, `scan_backup_${stamp}.${fmt}`);
      toast(`已导出 ${ids.length} 条备份（${fmt.toUpperCase()}）`);
    } catch (e) {
      toast((e as Error).message, "err");
      throw e;
    }
  };

  /* ---------- 清理（二次确认 → 清理 → 广播刷新 → 重新扫描） ---------- */
  const openClean = (ids: string[], label: string) => {
    if (!ids.length) {
      toast("没有可清理的条目", "warn");
      return;
    }
    setConfirm({ open: true, ids, label });
  };

  const doClean = async (withExport: boolean) => {
    const ids = confirm.ids;
    if (!ids.length) return;
    setBusy(true);
    try {
      if (withExport) await exportBackup(ids, "txt");
      const r = await api.scanClean({ ids });
      toast(r.message || "清理完成");
      setConfirm({ open: false, ids: [], label: "" });
      setChecked(new Set());
      emitRulesChanged();
      startScan();
    } catch (e) {
      toast((e as Error).message, "err");
      setConfirm({ open: false, ids: [], label: "" });
    } finally {
      setBusy(false);
    }
  };

  /* ---------- 定位原条目：跳转到对应管理页 ---------- */
  const jumpTo = (it: ScanItem) => {
    const tab = it.source === "custom_rules" ? "rules" : "wordbanks";
    window.location.hash = `#unified/${tab}`;
  };

  const confirmInvalid = confirm.ids.filter((id) => {
    const it = items.find((i) => i.item_id === id);
    return it?.category === "invalid";
  }).length;
  const confirmDup = confirm.ids.length - confirmInvalid;

  const srcTone = (s: string): "accent" | "sev-low" | "gray" =>
    s === "custom_rules" ? "accent" : s === "wordbanks" ? "sev-low" : "gray";

  const headCls: Record<string, string> = {
    invalid: "text-[var(--tone-danger)]",
    duplicate: "text-[var(--tone-warn)]",
    normal: "text-[var(--tone-ok)]",
  };

  return (
    <div className="space-y-4">
      {/* ===== 顶部：扫描操作 + 进度 + 统计 ===== */}
      <HoloCard className="p-5" glow="sm">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="mr-auto">
            <div className="text-sm font-bold">一键扫描管理</div>
            <div className="mt-0.5 text-xs text-white/50">
              扫描全部来源的规则与词库，自动识别无效 / 重复 / 正常条目；清理前自动备份，正常条目与 AI 学习样本不受影响；内置标准规则 / 词库只读，仅诊断提示、禁止删除
            </div>
          </div>
          <HoloButton
            size="sm"
            variant="primary"
            icon={<span>🧹</span>}
            onClick={startScan}
            disabled={scanning}
          >
            {scanning ? "扫描中…" : "一键扫描全部"}
          </HoloButton>
          <HoloButton
            size="sm"
            icon={<span>📤</span>}
            onClick={() => exportBackup(invalidItems.concat(dupCleanable).map((i) => i.item_id))}
            disabled={scanning || !result || !stats?.cleanable}
            title="导出全部无效项与重复项备份"
          >
            备份导出
          </HoloButton>
        </div>

        {/* 扫描进度 */}
        {scanning && (
          <div className="mt-4">
            <div className="holo-progress">
              <i style={{ width: `${prog.pct}%` }} />
            </div>
            <div className="mt-1.5 flex items-center justify-between text-xs">
              <span className="text-white/60">{prog.text}</span>
              <span className="text-white/70">{Math.round(prog.pct)}%</span>
            </div>
          </div>
        )}

        {/* 统计 */}
        {stats && (
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {[
              { label: "扫描来源", val: stats.sources, cls: "" },
              { label: "共扫描条目", val: stats.scanned, cls: "" },
              { label: "无效条目", val: stats.invalid, cls: "text-[var(--tone-danger)]" },
              { label: "重复条目", val: stats.duplicate, cls: "text-[var(--tone-warn)]" },
              { label: "待清理（自定义）", val: stats.cleanable, cls: "text-[var(--tone-cyan)]" },
              { label: "内置只读诊断", val: stats.readonly ?? 0, cls: "text-[var(--tone-gray)]" },
              { label: "正常条目", val: stats.normal, cls: "text-[var(--tone-ok)]" },
            ].map((s) => (
              <div key={s.label} className="rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5">
                <div className="text-[11px] text-white/50">{s.label}</div>
                <div className={`mt-0.5 text-lg font-bold ${s.cls}`}>{s.val}</div>
              </div>
            ))}
          </div>
        )}

        {/* 按来源分布 */}
        {stats && (
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.values(stats.by_source).map((b) => (
              <span
                key={b.label}
                className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] text-white/60"
              >
                {b.label}：{b.scanned} 条
                {b.invalid ? <b className="mx-0.5 text-[var(--tone-danger)]">· 无效 {b.invalid}</b> : null}
                {b.duplicate ? <b className="mx-0.5 text-[var(--tone-warn)]">· 重复 {b.duplicate}</b> : null}
              </span>
            ))}
          </div>
        )}
      </HoloCard>

      {!result && !scanning ? (
        <HoloCard className="p-6">
          <EmptyState text="尚未扫描，点击上方「一键扫描全部」开始检查规则与词库" icon="🧹" />
        </HoloCard>
      ) : (
        <>
          {/* ===== 无效条目 ===== */}
          <HoloCard className="p-4" glow="sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`text-sm font-bold ${headCls.invalid}`}>⛔ 无效条目</span>
              <HoloBadge tone="danger">{invalidItems.length} 条</HoloBadge>
              <span className="text-[11px] text-white/40">
                匹配=建议 / 空内容 / 正则语法错误 / 格式错误等；内置条目只读仅提示，不可清理
              </span>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <HoloButton size="sm" onClick={() => setGroup(cleanableIn(invalidItems), !groupAllOn(invalidItems))}>
                  {groupAllOn(invalidItems) ? "取消全选" : "全选无效"}
                </HoloButton>
                <HoloButton
                  size="sm"
                  variant="danger"
                  onClick={() => openClean(cleanableIn(invalidItems).map((i) => i.item_id), "全部无效项")}
                  disabled={!cleanableIn(invalidItems).length}
                >
                  一键清理全部无效项
                </HoloButton>
              </div>
            </div>

            {invalidItems.length ? (
              <div className="mt-3 overflow-x-auto rounded-2xl border border-white/10">
                <table className="holo-table w-full text-[13px]">
                  <thead>
                    <tr className="text-xs text-white/60">
                      <th className="w-10 px-3 py-2"></th>
                      <th className="px-3 py-2 text-left font-semibold">来源 / 定位</th>
                      <th className="px-3 py-2 text-left font-semibold">匹配式</th>
                      <th className="px-3 py-2 text-left font-semibold">建议</th>
                      <th className="px-3 py-2 text-left font-semibold">原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {invalidItems.map((it) => (
                      <tr key={it.item_id} className="border-b border-white/5 text-white/85">
                        <td className="px-3 py-1.5">
                          <input
                            type="checkbox"
                            className="accent-[var(--accent)]"
                            checked={checked.has(it.item_id)}
                            onChange={() => toggle(it)}
                            disabled={readonlyItem(it)}
                            title={readonlyItem(it) ? "内置内容只读，禁止删除" : undefined}
                          />
                        </td>
                        <td className="px-3 py-1.5">
                          <div className="flex items-center gap-1.5">
                            <HoloBadge tone={srcTone(it.source)}>{it.source_label}</HoloBadge>
                            {readonlyItem(it) && (
                              <HoloBadge tone="gray">内置只读</HoloBadge>
                            )}
                            <button
                              onClick={() => jumpTo(it)}
                              title="跳转到对应管理页定位该条目"
                              className="rounded-lg px-1.5 py-0.5 text-white/70 transition-all duration-300 hover:bg-white/10 hover:text-white"
                            >
                              {it.group_label} ↗
                            </button>
                          </div>
                        </td>
                        <td className="px-3 py-1.5 font-mono text-[12px] text-white/90">{it.pattern}</td>
                        <td className="max-w-[280px] px-3 py-1.5 text-white/60">{it.suggestion}</td>
                        <td className="px-3 py-1.5 text-[var(--tone-danger)]">{it.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="mt-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-xs text-[var(--tone-ok)]">
                未发现无效条目 ✓
              </div>
            )}
          </HoloCard>

          {/* ===== 重复条目 ===== */}
          <HoloCard className="p-4" glow="sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`text-sm font-bold ${headCls.duplicate}`}>🔁 重复条目</span>
              <HoloBadge tone="warn">{dupItems.length} 条</HoloBadge>
              <span className="text-[11px] text-white/40">
                重复项保留首次出现，其余可一键清理（勾选默认不含保留项）；内置条目只读仅提示
              </span>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <HoloButton
                  size="sm"
                  onClick={() => setGroup(dupCleanable, !groupAllOn(dupCleanable))}
                  disabled={!dupCleanable.length}
                >
                  {groupAllOn(dupCleanable) ? "取消全选重复" : "全选重复"}
                </HoloButton>
                <HoloButton
                  size="sm"
                  variant="danger"
                  onClick={() => openClean(cleanableIn(dupCleanable).map((i) => i.item_id), "重复项（保留首次出现）")}
                  disabled={!cleanableIn(dupCleanable).length}
                >
                  一键处理重复项
                </HoloButton>
              </div>
            </div>

            {dupItems.length ? (
              <div className="mt-3 overflow-x-auto rounded-2xl border border-white/10">
                <table className="holo-table w-full text-[13px]">
                  <thead>
                    <tr className="text-xs text-white/60">
                      <th className="w-10 px-3 py-2"></th>
                      <th className="px-3 py-2 text-left font-semibold">来源 / 定位</th>
                      <th className="px-3 py-2 text-left font-semibold">匹配式</th>
                      <th className="px-3 py-2 text-left font-semibold">建议</th>
                      <th className="px-3 py-2 text-left font-semibold">处理</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dupItems.map((it) => (
                      <tr key={it.item_id} className="border-b border-white/5 text-white/85">
                        <td className="px-3 py-1.5">
                          <input
                            type="checkbox"
                            className="accent-[var(--accent)]"
                            checked={checked.has(it.item_id)}
                            onChange={() => toggle(it)}
                            disabled={it.keep || readonlyItem(it)}
                            title={it.keep ? "保留首次出现项，不可删除" : readonlyItem(it) ? "内置内容只读，禁止删除" : undefined}
                          />
                        </td>
                        <td className="px-3 py-1.5">
                          <div className="flex items-center gap-1.5">
                            <HoloBadge tone={srcTone(it.source)}>{it.source_label}</HoloBadge>
                            {readonlyItem(it) && (
                              <HoloBadge tone="gray">内置只读</HoloBadge>
                            )}
                            <button
                              onClick={() => jumpTo(it)}
                              title="跳转到对应管理页定位该条目"
                              className="rounded-lg px-1.5 py-0.5 text-white/70 transition-all duration-300 hover:bg-white/10 hover:text-white"
                            >
                              {it.group_label} ↗
                            </button>
                          </div>
                        </td>
                        <td className="px-3 py-1.5 font-mono text-[12px] text-white/90">{it.pattern}</td>
                        <td className="max-w-[280px] px-3 py-1.5 text-white/60">{it.suggestion}</td>
                        <td className="px-3 py-1.5">
                          {it.keep ? (
                            <HoloBadge tone="ok">保留首次出现</HoloBadge>
                          ) : (
                            <span className="text-xs text-[var(--tone-warn)]">{it.reason}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="mt-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-xs text-[var(--tone-ok)]">
                未发现重复条目 ✓
              </div>
            )}
          </HoloCard>

          {/* ===== 正常条目（只读摘要） ===== */}
          <HoloCard className="p-4" glow="sm">
            <button className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setFoldNormal((f) => !f)}>
              <span className={`text-sm font-bold ${headCls.normal}`}>✅ 正常可用条目</span>
              <HoloBadge tone="ok">{normalItems.length} 条</HoloBadge>
              <span className="text-[11px] text-white/40">不参与清理，仅作健康度查看</span>
              <span className="ml-auto text-xs text-white/50">{foldNormal ? "▸ 展开" : "▾ 收起"}</span>
            </button>
            {!foldNormal && (
              <div className="mt-3 max-h-[420px] overflow-auto rounded-2xl border border-white/10">
                <table className="holo-table w-full text-[13px]">
                  <thead className="sticky top-0 z-10">
                    <tr className="text-xs text-white/60">
                      <th className="px-3 py-2 text-left font-semibold">来源</th>
                      <th className="px-3 py-2 text-left font-semibold">分组 / 文件</th>
                      <th className="px-3 py-2 text-left font-semibold">条目</th>
                      <th className="px-3 py-2 text-left font-semibold">建议</th>
                    </tr>
                  </thead>
                  <tbody>
                    {normalItems.slice(0, 300).map((it) => (
                      <tr key={it.item_id} className="border-b border-white/5 text-white/80">
                        <td className="px-3 py-1.5">
                          <HoloBadge tone={srcTone(it.source)}>{it.source_label}</HoloBadge>
                        </td>
                        <td className="px-3 py-1.5 text-white/60">{it.group_label}</td>
                        <td className="px-3 py-1.5 font-mono text-[12px]">{it.pattern}</td>
                        <td className="max-w-[280px] px-3 py-1.5 text-white/50">{it.suggestion}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {normalItems.length > 300 && (
                  <div className="px-3 py-2 text-center text-xs text-white/40">
                    仅展示前 300 条，其余 {normalItems.length - 300} 条正常条目未列出
                  </div>
                )}
              </div>
            )}
          </HoloCard>

          {/* ===== 底部批量操作栏 ===== */}
          {checked.size > 0 && (
            <div className="sticky bottom-4 z-20 flex flex-wrap items-center gap-2 rounded-2xl border border-[var(--border-accent-soft)] bg-[rgba(10,10,31,0.9)] px-4 py-3 shadow-[0_0_30px_var(--glow-btn)] backdrop-blur-xl">
              <span className="text-sm text-white/80">
                已勾选 <b className="text-[var(--accent)]">{checked.size}</b> 条
              </span>
              <div className="ml-auto flex flex-wrap gap-2">
                <HoloButton size="sm" icon={<span>📤</span>} onClick={() => exportBackup([...checked])}>
                  导出选中备份
                </HoloButton>
                <HoloButton
                  size="sm"
                  variant="danger"
                  onClick={() => openClean([...checked], "勾选条目")}
                  disabled={busy}
                >
                  删除选中 ({checked.size})
                </HoloButton>
              </div>
            </div>
          )}
        </>
      )}

      {/* ===== 删除二次确认 ===== */}
      <HoloModal
        open={confirm.open}
        title="确认清理？"
        onClose={() => setConfirm({ open: false, ids: [], label: "" })}
        width={520}
        footer={
          <>
            <HoloButton onClick={() => setConfirm({ open: false, ids: [], label: "" })} disabled={busy}>
              取消
            </HoloButton>
            <HoloButton
              icon={<span>📤</span>}
              onClick={() => doClean(true)}
              disabled={busy}
            >
              导出备份再清理
            </HoloButton>
            <HoloButton variant="danger" onClick={() => doClean(false)} disabled={busy}>
              {busy ? "处理中…" : "确认清理"}
            </HoloButton>
          </>
        }
      >
        <div className="space-y-3 text-sm leading-relaxed">
          <p className="text-white/80">
            即将删除 <b className="text-[var(--tone-danger)]">{confirm.ids.length}</b> 条条目
            （无效 <b className="text-[var(--tone-danger)]">{confirmInvalid}</b> 条 / 重复{" "}
            <b className="text-[var(--tone-warn)]">{confirmDup}</b> 条）。
          </p>
          <p className="text-white/60">
            正常条目与 AI 学习记忆样本 <b>不受影响</b>；删除前会自动备份到本地
            <code className="mx-1 rounded bg-white/10 px-1.5 py-0.5 text-xs">reports/scan_backups/</code>
            目录，也可先「导出备份再清理」留存副本。
          </p>
          <p className="text-xs text-[var(--tone-warn)]">此操作不可撤销，请确认后继续。</p>
        </div>
      </HoloModal>
    </div>
  );
}