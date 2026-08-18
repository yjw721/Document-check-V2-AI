import { useEffect, useRef, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import { HoloInput, HoloSelect, FieldLabel } from "../../components/ui/HoloInput";
import HoloSwitch from "../../components/ui/HoloSwitch";
import HoloModal from "../../components/ui/HoloModal";
import FileChip from "../../components/common/FileChip";
import SectionTitle from "../../components/common/SectionTitle";
import DropZone, { type DropZoneHandle } from "../../components/common/DropZone";
import { api } from "../../lib/api";
import type { AiRef, AiSettings, SettingsData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

/* 标签1 · 导入与检测：文件上传 + 文件夹扫描 + AI 智能核验（本地/联网）选择 */
export default function UploadTab() {
  const toast = useToast();
  const dropRef = useRef<DropZoneHandle>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [folder, setFolder] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [cfgOpen, setCfgOpen] = useState(false);
  const [aiDraft, setAiDraft] = useState<AiSettings>({});
  const [testing, setTesting] = useState(false);
  const [refs, setRefs] = useState<AiRef[]>([]);
  const [refBusy, setRefBusy] = useState(false);
  const refInputRef = useRef<HTMLInputElement>(null);
  const [localModels, setLocalModels] = useState<string[]>([]);
  const [modelMsg, setModelMsg] = useState("");
  const [modelCustom, setModelCustom] = useState(false);

  useEffect(() => {
    api.settings().then(setSettings).catch(() => setSettings(null));
  }, []);

  const ai: AiSettings = settings?.ai ?? {};
  const aiMode: "off" | "local" | "online" = !ai.enabled ? "off" : ai.mode === "online" ? "online" : "local";

  const addFiles = (list: File[]) => {
    setFiles((prev) => [...prev, ...list]);
  };

  const saveAi = (patch: Partial<AiSettings>) => {
    if (!settings) return;
    const next = { ...settings, ai: { ...ai, ...patch } };
    setSettings(next);
    api.saveSettings(next).then(() => toast("AI 核验设置已保存")).catch((e) => toast((e as Error).message, "err"));
  };

  const setAiMode = (v: string) => {
    if (v === "off") saveAi({ enabled: false });
    else saveAi({ enabled: true, mode: v });
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

  const loadRefs = () => api.aiRefs().then((r) => setRefs(r.refs)).catch(() => setRefs([]));

  const uploadRef = async (file: File) => {
    setRefBusy(true);
    try {
      const r = await api.aiRefUpload(file);
      setRefs(r.refs);
      toast(r.ok ? r.message : r.message, r.ok ? undefined : "err");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setRefBusy(false);
    }
  };

  const loadLocalModels = (current?: string) => {
    setModelMsg("");
    api.aiModels().then((r) => {
      setLocalModels(r.models);
      if (current && r.models.length && !r.models.includes(current)) setModelCustom(true);
      else if (!current && r.models.length && aiDraft.mode !== "online") {
        setAiDraft((d) => ({ ...d, model: r.models[0] }));
      }
      if (!r.ok) setModelMsg(r.message || "无法获取本地模型列表");
    }).catch((e) => {
      setLocalModels([]);
      setModelMsg((e as Error).message);
    });
  };

  const testConn = async () => {
    setTesting(true);
    try {
      const r = await api.aiTest(aiDraft);
      toast(r.ok ? r.message : r.message, r.ok ? undefined : "err");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setTesting(false);
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
              className="rounded-xl text-[#a78bfa] underline-offset-2 transition-all duration-500 hover:underline"
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
          <HoloSwitch checked={recursive} onChange={setRecursive} label="递归子目录" />
          <HoloButton onClick={startFolder}>扫描并检测</HoloButton>
        </div>
      </HoloCard>

      <HoloCard className="p-6">
        <SectionTitle>AI 智能核验</SectionTitle>
        <p className="mb-3 text-xs text-white/45">
          规则检测（格式 / 文字规范 / 通顺度）完成后，对文档内容做 AI 语义级二次核验（前后矛盾、表意不清、逻辑不通等）。
          可在「本地 AI（Ollama，零联网）」与「联网 AI（OpenAI 兼容接口）」之间选择；默认关闭，保持离线保密。
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <HoloSelect className="w-[220px]" value={aiMode} onChange={(e) => setAiMode(e.target.value)}>
            <option value="off">不启用 AI 核验</option>
            <option value="local">本地 AI（Ollama）</option>
            <option value="online">联网 AI（OpenAI 兼容）</option>
          </HoloSelect>
          {ai.enabled && (
            <span className="text-xs text-white/50">
              {ai.mode === "local" ? `模型：${ai.model || "qwen2.5:7b"}` : `接口：${ai.base_url || "未配置"} · 模型：${ai.model || ""}`}
            </span>
          )}
          <HoloButton size="sm" onClick={() => { setAiDraft({ ...ai }); setModelCustom(false); setCfgOpen(true); loadLocalModels(ai.model); loadRefs(); }}>
            AI 配置…
          </HoloButton>
        </div>
      </HoloCard>

      <HoloModal
        open={cfgOpen}
        title="AI 智能核验配置"
        onClose={() => setCfgOpen(false)}
        width={620}
        footer={
          <>
            <HoloButton variant="ghost" onClick={testConn} disabled={testing} icon={<span>⚡</span>}>
              {testing ? "测试中…" : "测试连接"}
            </HoloButton>
            <HoloButton
              variant="primary"
              onClick={() => {
                saveAi({ ...aiDraft });
                setCfgOpen(false);
              }}
            >
              保存配置
            </HoloButton>
          </>
        }
      >
        <div className="space-y-3.5">
          <div>
            <FieldLabel>核验模式</FieldLabel>
            <HoloSelect className="w-full" value={aiDraft.mode ?? "local"} onChange={(e) => setAiDraft({ ...aiDraft, mode: e.target.value })}>
              <option value="local">本地 AI（Ollama，零联网）</option>
              <option value="online">联网 AI（OpenAI 兼容接口）</option>
            </HoloSelect>
          </div>
          <div>
            <FieldLabel>接口地址 base_url</FieldLabel>
            <HoloInput
              placeholder={aiDraft.mode === "online" ? "如 https://api.deepseek.com/v1" : "Ollama 默认 http://127.0.0.1:11434"}
              value={aiDraft.base_url ?? ""}
              onChange={(e) => setAiDraft({ ...aiDraft, base_url: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <FieldLabel>模型名 model</FieldLabel>
              {aiDraft.mode === "local" && !modelCustom ? (
                <div className="flex items-center gap-2">
                  <HoloSelect
                    className="flex-1"
                    value={localModels.includes(aiDraft.model ?? "") ? aiDraft.model : ""}
                    onChange={(e) => {
                      if (e.target.value === "__custom__") setModelCustom(true);
                      else setAiDraft({ ...aiDraft, model: e.target.value });
                    }}
                  >
                    <option value="">{localModels.length ? "请选择本地模型…" : "暂无本地模型（Ollama 未运行？）"}</option>
                    {localModels.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                    <option value="__custom__">手动输入模型名…</option>
                  </HoloSelect>
                  <HoloButton size="sm" onClick={() => loadLocalModels(aiDraft.model)} title="刷新模型列表">
                    刷新
                  </HoloButton>
                </div>
              ) : (
                <HoloInput
                  placeholder={aiDraft.mode === "online" ? "如 deepseek-chat / qwen-max" : "如 qwen2.5:7b"}
                  value={aiDraft.model ?? ""}
                  onChange={(e) => setAiDraft({ ...aiDraft, model: e.target.value })}
                />
              )}
              {aiDraft.mode === "local" && modelMsg && (
                <p className="mt-1 text-[11px] text-amber-300/70">{modelMsg}</p>
              )}
              {aiDraft.mode === "local" && !modelCustom && localModels.length > 0 && (
                <p className="mt-1 text-[11px] text-white/35">已同步扫描本机 Ollama：{localModels.length} 个模型可供选择</p>
              )}
            </div>
            <div>
              <FieldLabel>API Key（仅联网 AI 需要）</FieldLabel>
              <HoloInput
                type="password"
                placeholder="sk-…"
                value={aiDraft.api_key ?? ""}
                onChange={(e) => setAiDraft({ ...aiDraft, api_key: e.target.value })}
              />
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div>
              <FieldLabel>单次超时（秒）</FieldLabel>
              <HoloInput type="number" value={aiDraft.timeout ?? 60} onChange={(e) => setAiDraft({ ...aiDraft, timeout: Number(e.target.value) || 60 })} />
            </div>
            <div>
              <FieldLabel>每段字数上限</FieldLabel>
              <HoloInput type="number" value={aiDraft.max_chars ?? 3000} onChange={(e) => setAiDraft({ ...aiDraft, max_chars: Number(e.target.value) || 3000 })} />
            </div>
            <div>
              <FieldLabel>单文件调用上限</FieldLabel>
              <HoloInput type="number" value={aiDraft.max_requests ?? 10} onChange={(e) => setAiDraft({ ...aiDraft, max_requests: Number(e.target.value) || 10 })} />
            </div>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
            <div className="flex flex-wrap items-center gap-3">
              <HoloSwitch
                checked={aiDraft.create_enabled ?? true}
                onChange={(v) => setAiDraft({ ...aiDraft, create_enabled: v })}
                label="启用本地AI智能生成&自学习（规则词库智能生成统一模块总开关）"
              />
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-white/35">
              控制「规则与词库 → AI 规则词库智能生成」的对话式/文本式/文档式创建与本地自学习配对学习；
              关闭后相关入口将被拦截，已有规则词库与学习记忆不受影响。
            </p>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
            <div className="flex flex-wrap items-center gap-3">
              <HoloSwitch
                checked={aiDraft.ref_enabled ?? true}
                onChange={(v) => setAiDraft({ ...aiDraft, ref_enabled: v })}
                label="核验时携带参考资料"
              />
              <span className="text-[11px] text-white/40">参考文本上限（字符）</span>
              <HoloInput
                className="w-[110px]"
                type="number"
                value={aiDraft.ref_max_chars ?? 2000}
                onChange={(e) => setAiDraft({ ...aiDraft, ref_max_chars: Number(e.target.value) || 2000 })}
              />
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-white/35">
              上传行业标准 / 术语定义 / 书写规范文件（.txt / .md / .csv / .docx / .pdf），
              AI 核验时将自动携带并按这些标准核查（文档表述与参考标准不符视为问题）。
            </p>
            <div className="mt-2.5 flex flex-col gap-1.5">
              {refs.map((r) => (
                <div key={r.name} className="flex items-center gap-2 rounded-lg bg-white/[0.04] px-2.5 py-1.5">
                  <HoloSwitch checked={r.enabled} onChange={(v) => api.aiRefToggle(r.name, v).then((x) => setRefs(x.refs))} />
                  <span className="min-w-0 flex-1 truncate text-xs text-white/80">{r.name}</span>
                  <span className="shrink-0 text-[10px] text-white/35">{r.chars} 字符 · {r.updated}</span>
                  <button
                    className="shrink-0 rounded-md px-1.5 text-xs text-red-300/80 hover:bg-red-400/10"
                    onClick={() => { if (window.confirm(`删除参考资料「${r.name}」？`)) api.aiRefDelete(r.name).then((x) => setRefs(x.refs)); }}
                  >
                    删除
                  </button>
                </div>
              ))}
              {refs.length === 0 && <span className="text-[11px] text-white/30">暂无参考资料，AI 将按通用知识核验。</span>}
            </div>
            <div className="mt-2.5 flex items-center gap-2">
              <HoloButton size="sm" variant="ghost" disabled={refBusy} onClick={() => refInputRef.current?.click()}>
                {refBusy ? "上传中…" : "上传参考资料"}
              </HoloButton>
              <input
                ref={refInputRef}
                type="file"
                accept=".txt,.md,.csv,.docx,.pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) uploadRef(f);
                  e.target.value = "";
                }}
              />
            </div>
          </div>
          <p className="text-[11px] leading-relaxed text-white/35">
            本地 AI：需先在本机安装并启动 Ollama（ollama serve），并已拉取模型（如 ollama pull qwen2.5:7b），零联网。
            联网 AI：需配置服务商接口地址与 API Key（如 DeepSeek / 通义 / Kimi / OpenAI 等 OpenAI 兼容接口），文本将发送至该接口。
          </p>
        </div>
      </HoloModal>
    </div>
  );
}