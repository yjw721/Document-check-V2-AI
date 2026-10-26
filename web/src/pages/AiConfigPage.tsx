import { useEffect, useRef, useState, type ReactNode, type ChangeEvent } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import { HoloInput, HoloSelect, FieldLabel } from "../components/ui/HoloInput";
import HoloSwitch from "../components/ui/HoloSwitch";
import SectionTitle from "../components/common/SectionTitle";
import HoloBadge from "../components/ui/HoloBadge";
import { api } from "../lib/api";
import { useToast } from "../components/ui/Toast";
import type { AiPromptPreset, AiRef, AiSettings, AiSkill, SettingsData } from "../lib/types";

/* AI 配置：独立后台管理模块（仅管理员可见）
 * 子标签：基础参数配置 / 高级模型设置 / 自定义提示词管理 / 知识库配置 / AI 日志
 * 数据全部来自 config/settings.json 的 ai 组（含新增的温度 / 上下文 / 角色 / 风格 / 限流 / 缓存），
 * 以及参考资料（ai_refs）与 AI 活动日志，迁移自原「导入与检测 → AI 配置」弹窗。 */

const AI_TABS = [
  { key: "basic", name: "基础参数配置", ic: "🔧" },
  { key: "advanced", name: "高级模型设置", ic: "🧪" },
  { key: "prompt", name: "自定义提示词管理", ic: "📝" },
  { key: "skill", name: "专项技能 SKILL", ic: "🎯" },
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
      {tab === "skill" && (
        <SkillTab ai={ai} setAi={setAi} saving={saving} onSave={save} onCommit={commitAi} />
      )}
      {tab === "kb" && <KbTab />}
      {tab === "log" && <LogTab />}
    </div>
  );
}

/* ---------------- 基础参数配置 ---------------- */
const LOCAL_DEFAULT_BASE = "http://127.0.0.1:11434";
/* 看起来像联网地址的特征子串（命中时切换为本地时自动覆盖 base_url） */
const ONLINE_BASE_HINTS = [
  "openai.com",
  "deepseek",
  "bigmodel",
  "dashscope",
  "xf-yun",
  "spark-api",
  "api.openai",
  "/v1/chat",
  "/chat/completions",
];

function looksLikeOnlineBase(url: string | undefined | null): boolean {
  const s = String(url || "").toLowerCase();
  if (!s) return false;
  return ONLINE_BASE_HINTS.some((h) => s.includes(h));
}

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
  const [scanning, setScanning] = useState(false);
  const [testing, setTesting] = useState(false);
  const toast2 = useToast();
  const aiMode: "off" | "local" | "online" = !ai.enabled ? "off" : ai.mode === "online" ? "online" : "local";
  /* 用 ref 跟踪上一次的 aiMode，避免初次挂载时的副作用把空状态填错 */
  const prevModeRef = useRef<AiTabKey | "off" | "local" | "online">("off");

  /* 拉取本地模型列表：
   *  - baseOverride：调用时使用的 base_url（不写回 settings，仅用于探测）
   *  - fillModel：返回后是否把 ai.model 设为第一个可用模型
   */
  const loadLocalModels = (baseOverride?: string, fillModel = true) => {
    setScanning(true);
    setModelMsg("");
    api
      .aiModels(baseOverride)
      .then((r) => {
        setLocalModels(r.models);
        if (!r.ok) {
          setModelMsg(r.message || "无法获取本地模型列表");
          return;
        }
        if (fillModel && r.models.length) {
          const cur = ai.model;
          if (!cur || !r.models.includes(cur)) {
            setAi({ model: r.models[0] });
          } else if (modelCustom) {
            /* 之前手动输入、但恰好在自动列表里 → 切回下拉模式 */
            setModelCustom(false);
          }
        }
        if (!r.models.length) {
          setModelMsg("未发现已安装的本地模型，请先在 Ollama 拉取模型（如 ollama pull qwen2.5:7b）");
        }
      })
      .catch((e) => {
        setLocalModels([]);
        setModelMsg((e as Error).message);
      })
      .finally(() => setScanning(false));
  };

  /* 初次挂载：仅在已是 local/online 时拉一次（保留原行为） */
  useEffect(() => {
    if (aiMode !== "off") {
      const probeUrl = aiMode === "local" ? (ai.base_url || LOCAL_DEFAULT_BASE) : undefined;
      loadLocalModels(probeUrl, false);
    }
    prevModeRef.current = aiMode;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* 模式切换为「本地」时自动填入：base_url 用本地默认 + 触发模型扫描与首项选择 */
  useEffect(() => {
    if (aiMode === "local" && prevModeRef.current !== "local") {
      const curUrl = (ai.base_url || "").trim();
      const needFill = !curUrl || looksLikeOnlineBase(curUrl);
      if (needFill) {
        setAi({ base_url: LOCAL_DEFAULT_BASE });
      }
      const probeUrl = needFill ? LOCAL_DEFAULT_BASE : (curUrl || LOCAL_DEFAULT_BASE);
      loadLocalModels(probeUrl, true);
    }
    prevModeRef.current = aiMode;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiMode]);

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
              else if (v === "local") {
                /* 切换到本地：base_url 空/联网形态 → 自动填本地默认 */
                const curUrl = (ai.base_url || "").trim();
                const nextUrl = !curUrl || looksLikeOnlineBase(curUrl) ? LOCAL_DEFAULT_BASE : curUrl;
                setAi({ enabled: true, mode: "local", base_url: nextUrl });
                /* 立即用新地址探测一次（不依赖 useEffect，避免与表单同步） */
                loadLocalModels(nextUrl, true);
              } else {
                setAi({ enabled: true, mode: "online" });
              }
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
              onBlur={() => {
                /* 失焦时若是 local 模式，按当前填写的 base_url 重新探测一次 */
                if (aiMode === "local" && (ai.base_url || "").trim()) {
                  loadLocalModels(ai.base_url, true);
                }
              }}
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
                  disabled={scanning}
                >
                  <option value="">
                    {scanning
                      ? "正在扫描本地模型…"
                      : localModels.length
                        ? "请选择本地模型…"
                        : "暂无本地模型（Ollama 未运行？）"}
                  </option>
                  {localModels.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  <option value="__custom__">手动输入模型名…</option>
                </HoloSelect>
                <HoloButton size="sm" disabled={scanning} onClick={() => loadLocalModels(ai.base_url, true)}>
                  {scanning ? "扫描中…" : "刷新"}
                </HoloButton>
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
          <p className="text-[11px] text-white/35">
            已设置时仅显示 ********；不改动会保留原 Key，清空后保存可删除。
          </p>
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
          <p className="mb-2 text-[11px] leading-relaxed text-white/35">本地 CPU 推理较慢：即使配置较小，本地模式也会自动放宽到不小于 300 秒，避免误报「连接失败/超时」。</p>
          <Field label="每段字数上限">
            <HoloInput type="number" className="w-[120px]" value={ai.max_chars ?? 3000} onChange={(e) => setAi({ max_chars: Number(e.target.value) || 3000 })} />
          </Field>
          <Field label="单文件调用上限">
            <HoloInput type="number" className="w-[120px]" value={ai.max_requests ?? 10} onChange={(e) => setAi({ max_requests: Number(e.target.value) || 10 })} />
          </Field>
          <Field label="失败重试次数">
            <HoloInput type="number" className="w-[120px]" value={ai.retry ?? 1} min={0} max={5} onChange={(e) => setAi({ retry: Math.max(0, Math.min(5, Number(e.target.value) || 0)) })} />
          </Field>
          <p className="mt-1 text-[11px] leading-relaxed text-white/35">单段调用超时/连接失败时的自动重试次数（默认 1，会放大网上波动/服务未就绪）。</p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-2 text-sm font-semibold text-white/90">核验行为（可控性）</div>
          <Field label="核验范围 verify_scope">
            <HoloSelect className="w-[240px]" value={ai.verify_scope ?? "all"} onChange={(e) => setAi({ verify_scope: e.target.value })}>
              <option value="all">全部可解析文件</option>
              <option value="issues">仅基础检测出问题的文件（更快）</option>
            </HoloSelect>
          </Field>
          <Field label="阶段失败写入问题列表 fail_as_issue">
            <HoloSwitch checked={ai.fail_as_issue ?? false} onChange={(v) => setAi({ fail_as_issue: v })} />
          </Field>
          <p className="mt-1 text-[11px] leading-relaxed text-white/35">
            默认关闭：AI 阶段失败（如本地服务未启动 / 超时）只在文件列表的 AI 状态徽标与任务日志提示，不再作为「AI 链接失败」之类的伪问题写入结果，不污染问题统计与报告。开启后恢复旧行为（以低级别问题标记）。
          </p>
          <Field label="深度复核（每段追加一轮补漏复查）">
            <HoloSwitch checked={ai.deep_review ?? true} onChange={(v) => setAi({ deep_review: v })} />
          </Field>
          <p className="mt-1 text-[11px] leading-relaxed text-white/35">
            增强审核力度：每个片段在首轮核查后再发起一轮「只输出遗漏问题」的复核请求，重点补漏表格勾稽重算、跨段落数值/日期一致性、金额大小写、单位口径等。开启后 AI 核验耗时约翻倍，可在文档量大或本地 CPU 推理较慢时关闭。
          </p>
          <Field label="严格取证（引用须逐字命中原文）">
            <HoloSwitch checked={ai.strict_grounding ?? true} onChange={(v) => setAi({ strict_grounding: v })} />
          </Field>
          <Field label="置信度门槛（低于则丢弃）">
            <HoloInput
              type="number"
              className="w-[120px]"
              step={0.1}
              min={0}
              max={1}
              value={ai.min_confidence ?? 0.6}
              onChange={(e) => setAi({ min_confidence: Math.max(0, Math.min(1, Number(e.target.value) || 0)) })}
            />
          </Field>
          <p className="mt-1 text-[11px] leading-relaxed text-white/35">
            严格取证会要求 AI 引用的原文逐字命中（短引用须完整命中），置信度门槛会丢弃模型把握不足的问题，二者共同抑制误报与幻觉；长文档自动采用「首段 + 末段 + 等距抽样」的全篇覆盖，不再只检前几段。
          </p>
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
        <HoloButton variant="primary" disabled={saving} onClick={() => onSave()}>
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
        <HoloButton variant="primary" disabled={saving} onClick={() => onSave()}>
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
        <HoloButton variant="primary" disabled={saving} onClick={() => onSave()}>
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

/* ---------------- 专项技能 SKILL ---------------- */
const FILE_TYPE_OPTS: [string, string][] = [
  ["all", "全部文件"],
  ["doc", "Word 文档"],
  ["xls", "Excel 表格"],
  ["pdf", "PDF"],
  ["txt", "文本文件"],
];
const SEVERITY_OPTS: [string, string][] = [
  ["", "不指定（由模型判断）"],
  ["high", "高（high）"],
  ["medium", "中（medium）"],
  ["low", "低（low）"],
];

function SkillTab({
  ai, setAi, saving, onSave, onCommit,
}: {
  ai: AiSettings;
  setAi: (p: Partial<AiSettings>) => void;
  saving: boolean;
  onSave: () => void;
  onCommit: (patch: Partial<AiSettings>, msg?: string) => void;
}) {
  const toast = useToast();
  const [builtin, setBuiltin] = useState<AiSkill[]>([]);
  const [editing, setEditing] = useState<AiSkill | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const packRef = useRef<HTMLInputElement>(null);
  const [packImporting, setPackImporting] = useState(false);

  const active: string[] = ai.active_skills ?? [];
  const custom: AiSkill[] = ai.custom_skills ?? [];
  const activeSet = new Set(active);

  useEffect(() => {
    api.aiSkills()
      .then((r) => setBuiltin(r.builtin || []))
      .catch(() => setBuiltin([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleBuiltin = (id: string, on: boolean) => {
    const cur = new Set(ai.active_skills ?? []);
    if (on) cur.add(id); else cur.delete(id);
    setAi({ active_skills: [...cur] });
  };
  const upsertCustom = (s: AiSkill) => {
    const exists = custom.some((x) => x.id === s.id);
    const nextCustom = exists ? custom.map((x) => (x.id === s.id ? s : x)) : [...custom, s];
    const cur = new Set(ai.active_skills ?? []);
    if (!exists) cur.add(s.id); // 新建技能默认启用
    setAi({ custom_skills: nextCustom, active_skills: [...cur] });
  };
  const removeCustom = (id: string) => {
    const nextCustom = custom.filter((x) => x.id !== id);
    const cur = new Set(ai.active_skills ?? []);
    cur.delete(id);
    setAi({ custom_skills: nextCustom, active_skills: [...cur] });
  };

  // 判定技能字段是否齐全（id / name / prompt 必为字符串）——JSON 导入与技能包导入共用
  const isValidSkill = (s: AiSkill | undefined | null): s is AiSkill =>
    !!s && typeof s.id === "string" && typeof s.name === "string" && typeof s.prompt === "string";

  // 合并导入的技能（同 id 覆盖、新 id 追加）并提交保存；未提供启用列表时默认全部启用
  const commitImport = async (valid: AiSkill[], pkgActive?: string[]) => {
    const byId = new Map(custom.map((s) => [s.id, s]));
    for (const s of valid) byId.set(s.id, s);
    const nextCustom = [...byId.values()];
    const cur = new Set(active);
    const nextActive: string[] = Array.isArray(pkgActive)
      ? [...new Set([...cur, ...pkgActive.filter((x) => byId.has(x))])]
      : [...new Set([...cur, ...valid.map((s) => s.id)])];
    await onCommit({ custom_skills: nextCustom, active_skills: nextActive }, `已导入 ${valid.length} 个技能`);
    return valid.length;
  };

  // 导出：将自定义技能库（含启用状态）序列化为 JSON 文件下载
  const handleExport = () => {
    const payload = {
      version: 1,
      kind: "ai-skill-library",
      exported_at: new Date().toISOString(),
      custom_skills: custom,
      active_skills: active,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ai-skills-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast(`已导出 ${custom.length} 个自定义技能`);
  };

  // 导入：读取 JSON 文件，按 id 合并进自定义库，并默认启用导入项
  const handleImportFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 允许重复选择同一文件
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const incoming: AiSkill[] = Array.isArray(data?.custom_skills) ? data.custom_skills : [];
      if (!incoming.length) {
        toast("导入文件未包含自定义技能", "err");
        return;
      }
      const valid = incoming.filter(isValidSkill);
      if (valid.length !== incoming.length) toast("部分技能格式不合法，已跳过", "err");
      if (!valid.length) {
        toast("导入文件中无有效技能", "err");
        return;
      }
      await commitImport(valid, Array.isArray(data?.active_skills) ? data.active_skills : undefined);
    } catch {
      toast("导入失败：文件不是有效的技能 JSON", "err");
    }
  };

  // 导入 zipskill 标准技能包（Agent Skills 开放标准 ZIP）：上传后端解析，再合并进自定义库
  const handleImportPackFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 允许重复选择同一文件
    if (!file) return;
    setPackImporting(true);
    try {
      const res = await api.aiImportSkillPack(file);
      const incoming: AiSkill[] = Array.isArray(res.skills) ? res.skills : [];
      const valid = incoming.filter(isValidSkill);
      if (res.errors?.length) toast(res.errors.join("；"), "err");
      if (!valid.length) {
        toast(res.message || "技能包中无有效技能", "err");
        return;
      }
      await commitImport(valid); // 技能包无启用标记，默认全部启用
    } catch (err) {
      toast(`导入失败：${(err as Error)?.message || "无法解析技能包"}`, "err");
    } finally {
      setPackImporting(false);
    }
  };

  return (
    <HoloCard className="p-6">
      <SectionTitle>专项技能 SKILL</SectionTitle>
      <p className="mt-2 text-xs leading-relaxed text-white/45">
        专项技能与「角色设定 / 提示词预设」并存生效：预设定义 AI 的「人设与风格」，技能定义 AI 的「专项关注点」，
        二者同时注入系统提示、互不替代，共同收紧审核力度。内置技能可一键开关；自定义技能库支持新建 / 编辑 / 删除 / 启用，
        并可将整套技能库导出为 JSON 备份或与他人共享、再导入合并；亦支持「导入技能包」——上传 zipskill 标准技能包
        （Agent Skills 开放标准的 .zip / .zipskill，包内每个含 SKILL.md 的目录解析为一个技能，正文即专项指令）。
      </p>

      <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
        <div className="mb-2 text-sm font-semibold text-white/90">内置专项技能（{builtin.length}）</div>
        <div className="flex flex-col gap-2">
          {builtin.length === 0 && (
            <span className="text-[11px] text-white/30">加载内置技能中…若长时间为空请确认后端已启动。</span>
          )}
          {builtin.map((s) => (
            <div key={s.id} className="flex items-start gap-3 rounded-xl bg-white/[0.04] p-3">
              <div className="pt-0.5">
                <HoloSwitch checked={activeSet.has(s.id)} onChange={(v) => toggleBuiltin(s.id, v)} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-white/90">{s.name}</div>
                <div className="mt-0.5 text-[11px] leading-relaxed text-white/40">{s.description}</div>
              </div>
              {s.default_on && (
                <span className="shrink-0 rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[10px] text-white/40">默认开</span>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
        <div className="mb-2 flex items-center justify-between text-sm font-semibold text-white/90">
          <span>自定义技能库（{custom.length}）</span>
          <div className="flex items-center gap-1.5">
            <HoloButton size="sm" variant="ghost" onClick={() => importRef.current?.click()}>导入</HoloButton>
            <HoloButton size="sm" variant="ghost" onClick={() => packRef.current?.click()} disabled={packImporting}>
              {packImporting ? "解析中…" : "导入技能包"}
            </HoloButton>
            <HoloButton size="sm" variant="ghost" onClick={handleExport} disabled={custom.length === 0}>导出</HoloButton>
            <HoloButton size="sm" onClick={() => setEditing({ id: genId("sk"), name: "", description: "", prompt: "", file_types: "all", default_severity: "" })}>
              ＋ 新建技能
            </HoloButton>
          </div>
        </div>
        <div className="flex max-h-72 flex-col gap-1.5 overflow-y-auto">
          {custom.length === 0 && (
            <span className="text-[11px] text-white/30">暂无自定义技能，点击「新建技能」添加专属核验关注点（如「评估准则口径」「合同风险点」）。</span>
          )}
          {custom.map((s) => (
            <div key={s.id} className="flex items-center gap-2 rounded-lg bg-white/[0.04] px-2.5 py-1.5">
              <HoloSwitch checked={activeSet.has(s.id)} onChange={(v) => toggleBuiltin(s.id, v)} />
              <span className="min-w-0 flex-1 truncate text-xs text-white/85">{s.name || "（未命名）"}</span>
              <span className="shrink-0 text-[10px] text-white/35">
                {FILE_TYPE_OPTS.find(([k]) => k === (s.file_types || "all"))?.[1] ?? s.file_types}
                {s.default_severity ? ` · ${s.default_severity}` : ""}
              </span>
              <button className="shrink-0 rounded-md px-1.5 text-xs text-sky-300/80 hover:bg-sky-400/10" onClick={() => setEditing(s)}>编辑</button>
              <button className="shrink-0 rounded-md px-1.5 text-xs text-red-300/80 hover:bg-red-400/10" onClick={() => removeCustom(s.id)}>删除</button>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2.5">
        <HoloButton variant="primary" disabled={saving} onClick={onSave}>
          {saving ? "保存中…" : "保存配置"}
        </HoloButton>
        <span className="text-[11px] text-white/35">
          当前启用 {active.length} 项技能（内置 {builtin.filter((s) => activeSet.has(s.id)).length} / 自定义 {custom.filter((s) => activeSet.has(s.id)).length}）。
        </span>
      </div>

      <input ref={importRef} type="file" accept="application/json,.json" className="hidden" onChange={handleImportFile} />
      <input ref={packRef} type="file" accept=".zip,.zipskill,application/zip" className="hidden" onChange={handleImportPackFile} />

      {editing && (
        <SkillEditor
          skill={editing}
          onClose={() => setEditing(null)}
          onSave={(s) => {
            upsertCustom(s);
            setEditing(null);
            toast("自定义技能已保存（请点击「保存配置」落盘）");
          }}
        />
      )}
    </HoloCard>
  );
}

function SkillEditor({
  skill, onClose, onSave,
}: {
  skill: AiSkill;
  onClose: () => void;
  onSave: (s: AiSkill) => void;
}) {
  const [name, setName] = useState(skill.name ?? "");
  const [description, setDescription] = useState(skill.description ?? "");
  const [prompt, setPrompt] = useState(skill.prompt ?? "");
  const [fileTypes, setFileTypes] = useState(skill.file_types || "all");
  const [severity, setSeverity] = useState(skill.default_severity || "");
  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/55 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl" onClick={(e) => e.stopPropagation()}>
        <HoloCard className="w-full p-6">
          <SectionTitle>编辑自定义技能</SectionTitle>
          <div className="mt-3 space-y-3">
            <div>
              <FieldLabel>技能名称</FieldLabel>
              <HoloInput value={name} onChange={(e) => setName(e.target.value)} placeholder="如：评估准则口径校验" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <FieldLabel>适用文件类型</FieldLabel>
                <HoloSelect className="w-full" value={fileTypes} onChange={(e) => setFileTypes(e.target.value)}>
                  {FILE_TYPE_OPTS.map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </HoloSelect>
              </div>
              <div>
                <FieldLabel>建议严重度</FieldLabel>
                <HoloSelect className="w-full" value={severity} onChange={(e) => setSeverity(e.target.value)}>
                  {SEVERITY_OPTS.map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </HoloSelect>
              </div>
            </div>
            <div>
              <FieldLabel>技能说明（界面展示）</FieldLabel>
              <HoloInput value={description} onChange={(e) => setDescription(e.target.value)} placeholder="简要描述该技能关注什么" />
            </div>
            <div>
              <FieldLabel>技能提示词（注入 AI 系统提示）</FieldLabel>
              <textarea
                className="mt-1 h-40 w-full resize-none rounded-2xl border border-white/10 bg-black/30 p-3 text-[13px] text-white/90 outline-none focus:border-[var(--border-accent)]"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="描述该技能要额外重点核查的维度，例如：核对资产账面价值与评估申报值是否一致，关注折旧/摊销口径……"
              />
            </div>
          </div>
          <div className="mt-4 flex justify-end gap-2.5">
            <HoloButton onClick={onClose}>取消</HoloButton>
            <HoloButton
              variant="primary"
              onClick={() => {
                onSave({
                  ...skill,
                  name: name.trim() || "未命名技能",
                  description,
                  prompt: prompt.trim(),
                  file_types: fileTypes,
                  default_severity: severity,
                });
              }}
            >
              保存技能
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
