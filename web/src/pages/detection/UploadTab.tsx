import { useEffect, useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import { HoloInput, FieldLabel } from "../../components/ui/HoloInput";
import FileChip from "../../components/common/FileChip";
import SectionTitle from "../../components/common/SectionTitle";
import DropZone, { type DropZoneHandle } from "../../components/common/DropZone";
import { api } from "../../lib/api";
import type { AiSettings, SettingsData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

/* 标签1 · 导入与检测：文件上传 + 文件夹扫描。
 * AI 智能核验的全部后台参数已迁移至独立「AI 配置」模块（仅管理员可见）。 */
export default function UploadTab() {
  const toast = useToast();
  const dropRef = useRef<DropZoneHandle>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [folder, setFolder] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [settings, setSettings] = useState<SettingsData | null>(null);

  useEffect(() => {
    api.settings().then(setSettings).catch(() => setSettings(null));
  }, []);

  const ai: AiSettings = settings?.ai ?? {};

  const addFiles = (list: File[]) => {
    setFiles((prev) => [...prev, ...list]);
  };

  const startUpload = async () => {
    if (!files.length) return;
    try {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f, f.name));
      const r = await api.upload(fd);
      if (r.task_id) {
        try {
          localStorage.setItem("doc_checker_task_id", r.task_id);
        } catch {
          /* ignore */
        }
      }
      toast("检测任务已启动");
      window.location.hash = "#detection/waiting";
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const startFolder = async () => {
    const f = folder.trim();
    if (!f) {
      toast("请填写文件夹路径", "warn");
      return;
    }
    try {
      const r = await api.scanFolder({ folder: f, recursive });
      if (r.task_id) {
        try {
          localStorage.setItem("doc_checker_task_id", r.task_id);
        } catch {
          /* ignore */
        }
      }
      toast("检测任务已启动");
      window.location.hash = "#detection/waiting";
    } catch (e) {
      toast((e as Error).message, "err");
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
            {ai.enabled
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
        <div className="mt-3 flex items-center gap-3">
          <HoloButton variant="primary" disabled={!files.length} onClick={startUpload}>
            开始检测
          </HoloButton>
          <span className="text-xs text-white/40">{files.length ? `已选择 ${files.length} 个文件` : "尚未选择文件"}</span>
        </div>
      </HoloCard>

      <HoloCard className="p-6">
        <SectionTitle>扫描本地文件夹</SectionTitle>
        <FieldLabel>文件夹路径（如 D:\合同\2026）</FieldLabel>
        <HoloInput placeholder="请输入本地绝对路径" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <div className="mt-4 flex flex-wrap items-center gap-4">
          <HoloSwitchLocal checked={recursive} onChange={setRecursive} label="递归子目录" />
          <HoloButton onClick={startFolder}>扫描并检测</HoloButton>
        </div>
      </HoloCard>

      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">AI 智能核验</SectionTitle>
          <span className="text-xs text-white/45">
            {ai.enabled
              ? ai.mode === "local"
                ? `本地 AI 已开启（${ai.model || "qwen2.5:7b"}，零联网）`
                : `联网 AI 已开启（${ai.model || "未配置模型"}）`
              : "当前未启用"}
          </span>
          <HoloButton
            size="sm"
            className="ml-auto"
            onClick={() => (window.location.hash = "#aiconfig")}
          >
            前往 AI 配置 →
          </HoloButton>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-white/45">
          AI 核验的模型、温度、上下文、角色风格、提示词预设、知识库、限流与缓存等全部后台参数，
          已统一归入独立「AI 配置」模块（后台管理 · 仅管理员可见）。
        </p>
      </HoloCard>
    </div>
  );
}

/* 本地开关（避免与已移除的 AI 配置弹窗耦合） */
function HoloSwitchLocal({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-[13px] text-white/70">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-[var(--accent)]"
      />
      {label}
    </label>
  );
}
