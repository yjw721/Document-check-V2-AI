import { useEffect, useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import { HoloInput, FieldLabel } from "../../components/ui/HoloInput";
import FileChip from "../../components/common/FileChip";
import SectionTitle from "../../components/common/SectionTitle";
import DropZone, { type DropZoneHandle } from "../../components/common/DropZone";
import { api, type AiTaskOverride } from "../../lib/api";
import { useTaskState } from "../../lib/taskState";
import type { AiSettings, SettingsData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

/* 标签1 · 导入与检测：文件上传 + 文件夹扫描。
 * AI 智能核验的全部后台参数已迁移至独立「AI 配置」模块（仅管理员可见），
 * 本页提供「本次任务」级的 AI 核验快速开关与范围选择（不修改全局设置）。 */
export default function UploadTab() {
  const toast = useToast();
  const dropRef = useRef<DropZoneHandle>(null);
  const { setTaskId } = useTaskState();
  const [files, setFiles] = useState<File[]>([]);
  const [folder, setFolder] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [busy, setBusy] = useState(false);
  /* 本次任务的 AI 覆盖：auto=按全局设置 on=强制启用 off=强制停用 */
  const [aiOverride, setAiOverride] = useState<"auto" | "on" | "off">("auto");
  const [aiScope, setAiScope] = useState<"all" | "issues">("all");

  useEffect(() => {
    api.settings().then(setSettings).catch(() => setSettings(null));
  }, []);

  const ai: AiSettings = settings?.ai ?? {};
  const aiEffectiveOn = aiOverride === "on" ? true : aiOverride === "off" ? false : !!ai.enabled;

  const addFiles = (list: File[]) => {
    setFiles((prev) => [...prev, ...list]);
  };

  const buildOverride = (): AiTaskOverride | undefined => {
    const over: AiTaskOverride = {};
    if (aiOverride !== "auto") over.enabled = aiOverride === "on";
    if (aiEffectiveOn) over.scope = aiScope;
    return over.enabled != null || over.scope ? over : undefined;
  };

  const startUpload = async () => {
    if (!files.length || busy) return;
    setBusy(true);
    try {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f, f.name));
      const r = await api.upload(fd, buildOverride());
      if (!r.task_id) {
        toast("任务创建失败，请重试", "err");
        return;
      }
      /* 写入全局状态（同步落 localStorage）+ 立即切页，等待页首帧即开始轮询 */
      setTaskId(r.task_id);
      toast("检测任务已启动");
      window.location.hash = "#detection/waiting";
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const startFolder = async () => {
    const f = folder.trim();
    if (!f) {
      toast("请填写文件夹路径", "warn");
      return;
    }
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.scanFolder({ folder: f, recursive, ai: buildOverride() });
      if (!r.task_id) {
        toast("任务创建失败，请重试", "err");
        return;
      }
      setTaskId(r.task_id);
      toast("检测任务已启动");
      window.location.hash = "#detection/waiting";
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <HoloCard className="p-6">
        <SectionTitle>上传文件检测</SectionTitle>
        <DropZone ref={dropRef} accept=".docx,.xlsx,.xlsm,.pdf" onFiles={addFiles}>
          <div className="text-3xl">📥</div>
          <div className="mt-2">
            拖拽 .docx / .xlsx / .pdf 到此处，或{" "}
            <button
              className="rounded-xl text-[var(--tone-violet)] underline-offset-2 transition-all duration-500 hover:underline"
              onClick={() => dropRef.current?.pick()}
            >
              点击选择
            </button>
          </div>
          <div className="mt-2 text-xs text-white/40">
            {aiEffectiveOn
              ? ai.mode === "local"
                ? "本地 AI 核验已开启：文档仅在本地解析与本地模型核验，零联网"
                : "联网 AI 核验已开启：文本将发送至所配置的接口地址"
              : "文件仅在本地解析，全程零联网"}
          </div>
        </DropZone>
        {files.length > 0 && (
          <div className="mt-3.5 flex flex-col gap-2">
            {files.map((f, i) => (
              <FileChip
                key={`${f.name}-${i}`}
                name={f.name}
                sizeText={`${(f.size / 1024).toFixed(1)} KB`}
                onRemove={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
              />
            ))}
          </div>
        )}

        {/* 本次任务 · AI 智能核验快速控制（不修改全局设置） */}
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <FieldLabel className="!mb-0">本次任务 · AI 智能核验</FieldLabel>
            <select
              value={aiOverride}
              onChange={(e) => setAiOverride(e.target.value as "auto" | "on" | "off")}
              className="rounded-xl border border-white/10 bg-black/30 px-3 py-1.5 text-[13px] text-white/90 outline-none focus:border-[var(--accent)]"
            >
              <option value="auto">按全局设置</option>
              <option value="on">强制启用</option>
              <option value="off">不启用</option>
            </select>
            {aiEffectiveOn && (
              <label className="flex items-center gap-2 text-[13px] text-white/70">
                核验范围
                <select
                  value={aiScope}
                  onChange={(e) => setAiScope(e.target.value as "all" | "issues")}
                  className="rounded-xl border border-white/10 bg-black/30 px-3 py-1.5 text-[13px] text-white/90 outline-none focus:border-[var(--accent)]"
                >
                  <option value="all">全部可解析文件</option>
                  <option value="issues">仅检测出问题的文件（更快）</option>
                </select>
              </label>
            )}
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-white/35">
            仅影响本次任务，不改动「AI 配置」中的全局参数。本地 CPU 推理较慢，范围选「仅检测出问题的文件」可显著加快整体耗时。
          </p>
        </div>

        <div className="mt-3 flex items-center gap-3">
          <HoloButton variant="primary" disabled={!files.length || busy} onClick={startUpload}>
            {busy ? "提交中…" : "开始检测"}
          </HoloButton>
          <span className="text-xs text-white/40">{files.length ? `已选择 ${files.length} 个文件` : "尚未选择文件"}</span>
        </div>
      </HoloCard>

      <HoloCard className="p-6">
        <SectionTitle>扫描本地文件夹</SectionTitle>
        <FieldLabel>文件夹路径（如 D:\合同\2026）</FieldLabel>
        <HoloInput placeholder="请输入本地绝对路径" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <div className="mt-4 flex flex-wrap items-center gap-4">
          <label className="flex cursor-pointer items-center gap-2 text-[13px] text-white/70">
            <input
              type="checkbox"
              checked={recursive}
              onChange={(e) => setRecursive(e.target.checked)}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            递归子目录
          </label>
          <HoloButton onClick={startFolder} disabled={busy}>
            {busy ? "提交中…" : "扫描并检测"}
          </HoloButton>
        </div>
      </HoloCard>
    </div>
  );
}
