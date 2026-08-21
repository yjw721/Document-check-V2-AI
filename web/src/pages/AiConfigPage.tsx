import { useEffect, useRef, useState, type ReactNode } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import { HoloInput, HoloSelect, FieldLabel } from "../components/ui/HoloInput";
import HoloSwitch from "../components/ui/HoloSwitch";
import SectionTitle from "../components/common/SectionTitle";
import HoloBadge from "../components/ui/HoloBadge";
import { api } from "../lib/api";
import { useToast } from "../components/ui/Toast";
import type { AiPromptPreset, AiRef, AiSettings, SettingsData } from "../lib/types";

/* AI 配置：独立后台管理模块（仅管理员可见）
 * 子标签：基础参数配置 / 高级模型设置 / 自定义提示词管理 / 知识库配置 / AI 日志
 * 数据全部来自 config/settings.json 的 ai 组（含新增的温度 / 上下文 / 角色 / 风格 / 限流 / 缓存），
 * 以及参考资料（ai_refs）与 AI 活动日志，迁移自原「导入与检测 → AI 配置」弹窗。 */

const AI_TABS = [
  { key: "basic", name: "基础参数配置", ic: "🔧" },
  { key: "advanced", name: "高级模型设置", ic: "🧪" },
  { key: "prompt", name: "自定义提示词管理", ic: "📝" },
  { key: "kb", name: "知识库配置", ic: "📚" },
  { key: "log", name: "AI 日志", ic: "📜" },
] as const;
type AiTabKey = (typeof AI_TABS)[number]["key"];

const STYLE_OPTS: [string, string][] = [
  ["precise", "严谨精确"],
  ["concise", "简洁直击"],
  ["balance", "均衡（默认）"],
  ["friendly", "友好易读"],
  ["formal", "正式书面"],
];

function genId(prefix = "p"): string {
  return prefix + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

export default function AiConfigPage() {
  const toast = useToast();
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<AiTabKey>("basic");

  const ai: AiSettings = settings?.ai ?? {};
  const setAi = (patch: Partial<AiSettings>) => {
    if (!settings) return;
    setSettings({ ...settings, ai: { ...ai, ...patch } });
  };
  /* 始终保存最新 settings（避免同一 tick 内先 setAi 再 save 读到旧值导致覆盖丢失） */
  const settingsRef = useRef<SettingsData | null>(null);
  settingsRef.current = settings;
  const save = async (msg = "AI 配置已保存") => {
    const cur = settingsRef.current;
    if (!cur) return;
    setSaving(true);
    try {
      await api.saveSettings(cur);
      toast(msg);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };
  /* 即时落盘：同时更新 state 与 ref，保证后续保存读到最新值 */
  const commitAi = async (patch: Partial<AiSettings>, msg = "AI 配置已保存") => {
    const cur = settingsRef.current;
    if (!cur) return;
    const next = { ...cur, ai: { ...(cur.ai ?? {}), ...patch } } as SettingsData;
    settingsRef.current = next;
    setSettings(next);
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

  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch((e: Error) => setErr(e.message));
  }, []);

  if (err) {
    return (
      <HoloCard className="p-6">
        <div className="py-10 text-center text-white/40">加载失败：{err}</div>
        <div className="pb-6 text-center text-xs text-white/30">请确认后端服务已在 http://127.0.0.1:8501 启动</div>
      </HoloCard>
    );
  }
  if (!settings) return null;

  return (
    <div className="space-y-4">
      <HoloCard className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">AI 配置</SectionTitle>
          <HoloBadge tone="accent">后台管理 · 仅管理员</HoloBadge>
          <span className="ml-auto text-[11px] text-white/40">所有 AI 参数集中管理，切换模块不丢失</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {AI_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] transition-all duration-300 ${
                tab === t.key
                  ? "border-[var(--border-accent)] bg-white/[0.09] text-white shadow-[0_0_14px_var(--glow-tab)]"
                  : "border-white/10 bg-white/[0.03] text-white/55 hover:text-white hover:border-white/25"
              }`}
            >
              <span>{t.ic}</span>
              {t.name}
            </button>
          ))}
        </div>
      </HoloCard>

      {tab === "basic" && (
        <BasicTab ai={ai} setAi={setAi} saving={saving} onSave={save} />
      )}
      {tab === "advanced" && (
        <AdvancedTab ai={ai} setAi={setAi} saving={saving} onSave={save} />
      )}
      {tab === "prompt" && (
        <PromptTab ai={ai} setAi={setAi} saving={saving} onSave={save} onCommit={commitAi} />
      )}
      {tab === "kb" && <KbTab />}
      {tab === "log" && <LogTab />}
    </div>
  );
}

/* ---------------- 基础参数配置 ---------------- */
function BasicTab({
  ai, setAi, saving, onSave,
}: {
  ai: AiSettings;
  setAi: (p: Partial<AiSettings>) => void;
  saving: boolean;
  onSave: () => void;
}) {
  const [localModels, setLocalModels] = useState<string[]>([]);
  const [modelMsg, setModelMsg] = useState("");
  const [modelCustom, setModelCustom] = useState(false);
  const [testing, setTesting] = useState(false);
  const toast2 = useToast();
  const aiMode: "off" | "local" | "online" = !ai.enabled ? "off" : ai.mode === "online" ? "online" : "local";

  const loadLocalModels = (current?: string) => {
    setModelMsg("");
    api
      .aiModels()
      .then((r) => {
        setLocalModels(r.models);
        if (current && r.models.length && !r.models.includes(current)) setModelCustom(true);
        else if (!current && r.models.length && ai.mode !== "online") {
          if (!ai.model || !r.models.includes(ai.model)) setAi({ model: r.models[0] });
        }
        if (!r.ok) setModelMsg(r.message || "无法获取本地模型列表");
      })
      .catch((e) => {
        setLocalModels([]);
        setModelMsg((e as Error).message);
      });
  };

  useEffect(() => {
    if (aiMode !== "off") loadLocalModels(ai.model);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const testConn = async () => {
    setTesting(true);
    try {
      const r = await api.aiTest(ai);
      toast2(r.ok ? r.message : r.message, r.ok ? undefined : "err");
    } catch (e) {
      toast2((e as Error).message, "err");
    } finally {
      setTesting(false);
    }
  };

  return (
    <HoloCard className="p-6">
      <SectionTitle>基础参数配置</SectionTitle>
      <p className="mt-2 text-xs leading-relaxed text-white/45">
        AI 智能核验与本地AI生成的总开关、模型与接口配置。默认关闭保持离线保密；开启后可选择本地 Ollama（零联网）或联网 OpenAI 兼容接口。
      </p>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 text-sm font-semibold text-white/90">总开关与模式</div>
          <Field label="AI 智能核验">
            <HoloSelect className="w-[200px]" value={aiMode} onChange={(e) => {
              const v = e.target.value;
              if (v === "off") setAi({ enabled: false });
              else setAi({ enabled: true, mode: v });
            }}>
              <option value="off">不启用 AI 核验</option>
              <option value="local">本地 AI（Ollama）</option>
              <option value="online">联网 AI（OpenAI 兼容）</option>
            </HoloSelect>
          </Field>
          <Field label="本地AI生成&自学习总开关">
            <HoloSwitch checked={ai.create_enabled ?? true} onChange={(v) => setAi({ create_enabled: v })} />
          </Field>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 text-sm font-semibold text-white/90">接口与模型</div>
          <Field label="接口地址 base_url">
            <HoloInput
              className="flex-1"
              placeholder={ai.mode === "online" ? "如 https://api.deepseek.com/v1 或 https://open.bigmodel.cn/api/paas/v4（含版本前缀）" : "Ollama 默认 http://127.0.0.1:11434"}
              value={ai.base_url ?? ""}
              onChange={(e) => setAi({ base_url: e.target.value })}
            />
          </Field>
          <Field label="模型名 model">
            {ai.mode === "local" && !modelCustom ? (
              <div className="flex flex-1 items-center gap-2">
                <HoloSelect
                  className="flex-1"
                  value={localModels.includes(ai.model ?? "") ? ai.model : ""}
                  onChange={(e) => {
                    if (e.target.value === "__custom__") setModelCustom(true);
                    else setAi({ model: e.target.value });
                  }}
                >
                  <option value="">{localModels.length ? "请选择本地模型…" : "暂无本地模型（Ollama 未运行？）"}</option>
                  {localModels.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  <option value="__custom__">手动输入模型名…</option>
                </HoloSelect>
                <HoloButton size="sm" onClick={() => loadLocalModels(ai.model)}>刷新</HoloButton>
              </div>
            ) : (
              <HoloInput
                className="flex-1"
                placeholder={ai.mode === "online" ? "如 deepseek-chat / qwen-max" : "如 qwen2.5:7b"}
                value={ai.model ?? ""}
                onChange={(e) => setAi({ model: e.target.value })}
              />
            )}
          </Field>
          <Field label="API Key（仅联网需要）">
            <HoloInput
              className="flex-1"
              type="password"
              placeholder="sk-…"
              value={ai.api_key ?? ""}
              onChange={(e) => setAi({ api_key: e.target.value })}
            />
          </Field>
          {ai.mode === "local" && modelMsg && <p className="text-[11px] text-amber-300/70">{modelMsg}</p>}
          {ai.mode === "local" && !modelCustom && localModels.length > 0 && (
            <p className="text-[11px] text-white/35">已同步扫描本机 Ollama：{localModels.length} 个模型</p>
          )}
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 text-sm font-semibold text-white/90">调用与超时</div>
          <Field label="单次超时（秒）">
            <HoloInput type="number" className="w-[120px]" value={ai.timeout ?? 60} onChange={(e) => setAi({ timeout: Number(e.target.value) || 60 })} />
          </Field>
          <Field label="每段字数上限">
            <HoloInput type="number" className="w-[120px]" value={ai.max_chars ?? 3000} onChange={(e) => setAi({ max_chars: Number(e.target.value) || 3000 })} />
          </Field>
          <Field label="单文件调用上限">
            <HoloInput type="number" className="w-[120px]" value={ai.max_requests ?? 10} onChange={(e) => setAi({ max_requests: Number(e.target.value) || 10 })} />
          </Field>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 text-sm font-semibold text-white/90">参考资料（知识库）绑定</div>
          <Field label="核验时携带参考资料">
            <HoloSwitch checked={ai.ref_enabled ?? true} onChange={(v) => setAi({ ref_enabled: v })} />
          </Field>
          <Field label="参考文本上限（字符）">
            <HoloInput type="number" className="w-[120px]" value={ai.ref_max_chars ?? 2000} onChange={(e) => setAi({ ref_max_chars: Number(e.target.value) || 2000 })} />
          </Field>
          <p className="mt-1 text-[11px] leading-relaxed text-white/35">参考资料在「知识库配置」标签页上传与管理，AI 核验时将自动携带并按标准核查。</p>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2.5">
        <HoloButton variant="ghost" onClick={testConn} disabled={testing} icon={<span>⚡</span>}>
          {testing ? "测试中…" : "测试连接"}
        </HoloButton>
        <HoloButton variant="primary" disabled={saving} onClick={onSave}>
          {saving ? "保存中…" : "保存配置"}
        </HoloButton>
        <span className="text-[11px] text-white/35">本地 AI 需先安装并启动 Ollama（ollama serve）并拉取模型，零联网。</span>
      </div>
    </HoloCard>
  );
}

/* ---------------- 高级模型设置 ---------------- */
function AdvancedTab({
  ai, setAi, saving, onSave,
}: {
  ai: AiSettings;
  setAi: (p: Partial<AiSettings>) => void;
  saving: boolean;
  onSave: () => void;
}) {
  const rl = ai.rate_limit ?? {};
  const cc = ai.cache ?? {};
  const setRl = (p: Partial<NonNullable<AiSettings["rate_limit"]>>) =>
    setAi({ rate_limit: { ...rl, ...p } });
  const setCc = (p: Partial<NonNullable<AiSettings["cache"]>>) =>
    setAi({ cache: { ...cc, ...p } });

  return (
    <HoloCard className="p-6">
      <SectionTitle>高级模型设置</SectionTitle>
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 text-sm font-semibold text-white/90">生成与上下文</div>
          <Field label="温度 temperature（0 严谨 ~ 1 发散）">
            <HoloInput type="number" step="0.1" className="w-[120px]" value={ai.temperature ?? 0.7} onChange={(e) => setAi({ temperature: Number(e.target.value) })} />
          </Field>
          <Field label="上下文长度 num_ctx">
            <HoloInput type="number" step="256" className="w-[120px]" value={ai.num_ctx ?? 4096} onChange={(e) => setAi({ num_ctx: Number(e.target.value) || 4096 })} />
          </Field>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white/90">
            限流参数
            <HoloSwitch checked={rl.enabled ?? false} onChange={(v) => setRl({ enabled: v })} label="启用" />
          </div>
          <Field label="每分钟请求上限 rpm">
            <HoloInput type="number" className="w-[120px]" value={rl.rpm ?? 20} onChange={(e) => setRl({ rpm: Number(e.target.value) || 20 })} />
          </Field>
          <Field label="并发调用上限">
            <HoloInput type="number" className="w-[120px]" value={rl.concurrency ?? 1} onChange={(e) => setRl({ concurrency: Number(e.target.value) || 1 })} />
          </Field>
          <p className="mt-1 text-[11px] text-white/35">本地模型建议并发 1，避免排队打爆；开启后对高频调用做节流与并发约束。</p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4 lg:col-span-2">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white/90">
            响应缓存配置
            <HoloSwitch checked={cc.enabled ?? false} onChange={(v) => setCc({ enabled: v })} label="启用" />
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="缓存有效期（秒）">
              <HoloInput type="number" className="w-[120px]" value={cc.ttl ?? 3600} onChange={(e) => setCc({ ttl: Number(e.target.value) || 3600 })} />
            </Field>
            <Field label="最大缓存条目">
              <HoloInput type="number" className="w-[120px]" value={cc.max_entries ?? 200} onChange={(e) => setCc({ max_entries: Number(e.target.value) || 200 })} />
            </Field>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-white/35">
            对完全相同的 mode+model+提示词命中缓存直接返回，降低重复调用开销；密钥/随机内容不受影响。缓存仅保存在本机内存，重启后清空。
          </p>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2.5">
        <HoloButton variant="primary" disabled={saving} onClick={onSave}>
          {saving ? "保存中…" : "保存配置"}
        </HoloButton>
      </div>
    </HoloCard>
  );
}

/* ---------------- 自定义提示词管理 ---------------- */
function PromptTab({
  ai, setAi, saving, onSave, onCommit,
}: {
  ai: AiSettings;
  setAi: (p: Partial<AiSettings>) => void;
  saving: boolean;
  onSave: () => void;
  onCommit: (patch: Partial<AiSettings>, msg?: string) => void;
}) {
  const presets = ai.prompt_presets ?? [];
  const [editing, setEditing] = useState<AiPromptPreset | null>(null);

  const upsertAndCommit = (p: AiPromptPreset) => {
    const exists = presets.some((x) => x.id === p.id);
    const next = exists ? presets.map((x) => (x.id === p.id ? p : x)) : [...presets, p];
    onCommit({ prompt_presets: next });
  };
  const removeAndCommit = (id: string) => {
    const next = presets.filter((x) => x.id !== id);
    const patch: Partial<AiSettings> = { prompt_presets: next };
    if (ai.active_preset === id) patch.active_preset = "";
    onCommit(patch, "预设已删除");
  };

  return (
    <HoloCard className="p-6">
      <SectionTitle>自定义提示词管理</SectionTitle>
      <p className="mt-2 text-xs leading-relaxed text-white/45">
        AI 角色设定与响应风格作为系统前缀统一生效；提示词预设可保存多套，并指定一套默认套用于 AI 核验（生成同样继承角色与风格）。
      </p>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 text-sm font-semibold text-white/90">角色与风格（全局）</div>
          <Field label="AI 角色设定">
            <HoloInput className="flex-1" placeholder="如：资深资产评估师 / 公文审核专家" value={ai.role ?? ""} onChange={(e) => setAi({ role: e.target.value })} />
          </Field>
          <Field label="响应风格">
            <HoloSelect className="w-[180px]" value={ai.response_style ?? "balance"} onChange={(e) => setAi({ response_style: e.target.value })}>
              {STYLE_OPTS.map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </HoloSelect>
          </Field>
          <Field label="默认套用预设（AI 核验）">
            <HoloSelect className="w-[200px]" value={ai.active_preset ?? ""} onChange={(e) => setAi({ active_preset: e.target.value })}>
              <option value="">不套用预设</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </HoloSelect>
          </Field>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 flex items-center justify-between text-sm font-semibold text-white/90">
            <span>提示词预设（{presets.length}）</span>
            <HoloButton size="sm" onClick={() => setEditing({ id: genId(), name: "", content: "", scope: "all" })}>＋ 新建预设</HoloButton>
          </div>
          <div className="flex max-h-72 flex-col gap-1.5 overflow-y-auto">
            {presets.length === 0 && <span className="text-[11px] text-white/30">暂无预设，点击「新建预设」保存常用提示词。</span>}
            {presets.map((p) => (
              <div key={p.id} className="flex items-center gap-2 rounded-lg bg-white/[0.04] px-2.5 py-1.5">
                <span className="min-w-0 flex-1 truncate text-xs text-white/85">{p.name || "（未命名）"}</span>
                <span className="shrink-0 text-[10px] text-white/35">{p.scope ?? "all"}</span>
                <button className="shrink-0 rounded-md px-1.5 text-xs text-sky-300/80 hover:bg-sky-400/10" onClick={() => setEditing(p)}>编辑</button>
                <button className="shrink-0 rounded-md px-1.5 text-xs text-red-300/80 hover:bg-red-400/10" onClick={() => removeAndCommit(p.id)}>删除</button>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2.5">
        <HoloButton variant="primary" disabled={saving} onClick={onSave}>
          {saving ? "保存中…" : "保存配置"}
        </HoloButton>
      </div>

      {editing && (
        <PresetEditor
          preset={editing}
          onClose={() => setEditing(null)}
          onSave={(p) => {
            upsertAndCommit(p);
            setEditing(null);
          }}
        />
      )}
    </HoloCard>
  );
}

function PresetEditor({
  preset, onClose, onSave,
}: {
  preset: AiPromptPreset;
  onClose: () => void;
  onSave: (p: AiPromptPreset) => void;
}) {
  const [name, setName] = useState(preset.name);
  const [content, setContent] = useState(preset.content);
  const [scope, setScope] = useState(preset.scope ?? "all");
  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/55 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl" onClick={(e) => e.stopPropagation()}>
        <HoloCard className="w-full p-6">
        <SectionTitle>编辑提示词预设</SectionTitle>
        <div className="mt-3 space-y-3">
          <div>
            <FieldLabel>预设名称</FieldLabel>
            <HoloInput value={name} onChange={(e) => setName(e.target.value)} placeholder="如：资产评估准则核验口径" />
          </div>
          <div>
            <FieldLabel>适用范畴</FieldLabel>
            <HoloSelect className="w-[200px]" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="all">全部（核验+生成）</option>
              <option value="verify">仅 AI 核验</option>
              <option value="build">仅 AI 生成</option>
            </HoloSelect>
          </div>
          <div>
            <FieldLabel>提示词内容（套用在系统提示前）</FieldLabel>
            <textarea
              className="mt-1 h-44 w-full resize-none rounded-2xl border border-white/10 bg-black/30 p-3 text-[13px] text-white/90 outline-none focus:border-[var(--border-accent)]"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="例如：核查时严格对照《资产评估执业准则》，对金额单位、禁用词、编号连续性重点把关……"
            />
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2.5">
          <HoloButton onClick={onClose}>取消</HoloButton>
          <HoloButton
            variant="primary"
            onClick={() => {
              if (!name.trim()) {
                setName("未命名预设");
              }
              onSave({ id: preset.id, name: name.trim() || "未命名预设", content, scope });
            }}
          >
             保存预设
          </HoloButton>
        </div>
      </HoloCard>
      </div>
    </div>
  );
}

/* ---------------- 知识库配置（参考资料） ---------------- */
function KbTab() {
  const toast = useToast();
  const [refs, setRefs] = useState<AiRef[]>([]);
  const [busy, setBusy] = useState(false);
  const refInputRef = useRef<HTMLInputElement>(null);

  const load = () => api.aiRefs().then((r) => setRefs(r.refs)).catch(() => setRefs([]));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const upload = async (file: File) => {
    setBusy(true);
    try {
      const r = await api.aiRefUpload(file);
      setRefs(r.refs);
      toast(r.ok ? r.message : r.message, r.ok ? undefined : "err");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <HoloCard className="p-6">
      <SectionTitle>知识库配置</SectionTitle>
      <p className="mt-2 text-xs leading-relaxed text-white/45">
        上传行业标准 / 术语定义 / 书写规范文件（.txt / .md / .csv / .docx / .pdf），AI 核验时自动携带并按这些标准核查（文档表述与参考标准不符视为问题）。
      </p>
      <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
        <div className="flex flex-col gap-1.5">
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
          <HoloButton size="sm" variant="ghost" disabled={busy} onClick={() => refInputRef.current?.click()}>
            {busy ? "上传中…" : "上传参考资料"}
          </HoloButton>
          <input
            ref={refInputRef}
            type="file"
            accept=".txt,.md,.csv,.docx,.pdf"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload(f);
              e.target.value = "";
            }}
          />
        </div>
      </div>
    </HoloCard>
  );
}

/* ---------------- AI 日志 ---------------- */
function LogTab() {
  const toast = useToast();
  const [logs, setLogs] = useState<ReturnType<typeof Object>[]>([]);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .aiLogs(200, 0)
      .then((r) => setLogs(r.logs as never[]))
      .catch((e: Error) => toast(e.message, "err"))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const EVENT_LABEL: Record<string, string> = {
    verify: "AI 核验",
    build_dialogue: "对话生成",
    build_text: "文本生成",
    build_doc: "文档生成",
    test: "连接测试",
    learn: "自学习",
  };

  return (
    <HoloCard className="p-6">
      <div className="flex flex-wrap items-center gap-3">
        <SectionTitle className="!mb-0">AI 日志</SectionTitle>
        <HoloBadge tone="gray">logs/ai_activity.jsonl</HoloBadge>
        <span className="ml-auto flex gap-2.5">
          <HoloButton size="sm" disabled={loading} onClick={load}>刷新</HoloButton>
          <HoloButton
            size="sm"
            onClick={() => {
              if (window.confirm("确认清空 AI 活动日志？")) {
                api.aiLogsClear().then(() => { toast("AI 日志已清空"); load(); }).catch((e: Error) => toast(e.message, "err"));
              }
            }}
          >
            清空日志
          </HoloButton>
        </span>
      </div>
      <div className="mt-4 overflow-hidden rounded-2xl border border-white/10">
        <table className="w-full text-[12px]">
          <thead className="bg-white/[0.06] text-white/55">
            <tr>
              <th className="px-3 py-2 text-left">时间</th>
              <th className="px-3 py-2 text-left">事件</th>
              <th className="px-3 py-2 text-left">模型</th>
              <th className="px-3 py-2 text-left">结果</th>
              <th className="px-3 py-2 text-left">说明</th>
            </tr>
          </thead>
          <tbody>
            {logs.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-white/30">暂无 AI 活动记录</td></tr>
            )}
            {logs.map((l, i) => {
              const e = l as Record<string, unknown>;
              return (
                <tr key={i} className="border-t border-white/[0.06]">
                  <td className="whitespace-nowrap px-3 py-2 text-white/55">{String(e.ts ?? "")}</td>
                  <td className="px-3 py-2">{EVENT_LABEL[String(e.event ?? "")] ?? String(e.event ?? "")}</td>
                  <td className="px-3 py-2 text-white/70">{String(e.model ?? "—")}</td>
                  <td className="px-3 py-2">
                    {e.ok ? <span className="text-[var(--tone-ok)]">成功</span> : <span className="text-[var(--tone-danger)]">失败</span>}
                  </td>
                  <td className="px-3 py-2 text-white/70">{String(e.detail ?? "")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </HoloCard>
  );
}

/* 通用字段行 */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/5 py-2.5 last:border-0">
      <span className="min-w-0 text-[13px] text-white/70">{label}</span>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}
