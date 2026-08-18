import { useEffect, useMemo, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import HoloSwitch from "../components/ui/HoloSwitch";
import { HoloInput, HoloSelect } from "../components/ui/HoloInput";
import HoloBadge from "../components/ui/HoloBadge";
import SectionTitle from "../components/common/SectionTitle";
import { api } from "../lib/api";
import { useToast } from "../components/ui/Toast";
import type { RulesData, SettingsData } from "../lib/types";
import { ACCENT_PRESETS, applyTheme, isThemeScheme, resetTheme, THEME_SCHEMES } from "../lib/theme";

/* 后台设置：检测全局限制（config/rules.json 的 global 段）+ 全局运行设置（config/settings.json）
 * 规则词条的启停 / 级别 / 建议 / 新增编辑导入，已统一归集到「规则与词库统一管理」 */

/** 按路径不可变更新嵌套对象 */
function setAt<T>(obj: T, path: (string | number)[], value: unknown): T {
  const next: Record<string, unknown> = { ...(obj as Record<string, unknown>) };
  let cur = next;
  for (let i = 0; i < path.length - 1; i++) {
    const k = path[i] as string;
    const child = (cur[k] ?? {}) as Record<string, unknown>;
    cur[k] = { ...child };
    cur = cur[k] as Record<string, unknown>;
  }
  cur[path[path.length - 1] as string] = value;
  return next as T;
}

/* 通用布尔 / 数值 / 文本 / 下拉字段 */
function SettingField({
  label,
  type,
  value,
  options,
  onChange,
}: {
  label: string;
  type: "bool" | "num" | "text" | "select";
  value: unknown;
  options?: [string, string][];
  onChange: (v: unknown) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/5 py-2.5 last:border-0">
      <span className="min-w-0 text-[13px] text-white/70">{label}</span>
      {type === "bool" ? (
        <HoloSwitch checked={Boolean(value)} onChange={onChange} />
      ) : type === "select" ? (
        <HoloSelect
          className="w-[150px] py-1.5"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          {(options ?? []).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </HoloSelect>
      ) : (
        <HoloInput
          className="w-[170px] py-1.5 text-right"
          type={type === "num" ? "number" : "text"}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(type === "num" ? Number(e.target.value) : e.target.value)}
        />
      )}
    </div>
  );
}

export default function AdminPage() {
  const toast = useToast();
  const [rules, setRules] = useState<RulesData | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState<"rules" | "settings" | null>(null);

  useEffect(() => {
    Promise.all([api.rules(), api.settings()])
      .then(([r, s]) => {
        setRules(r);
        setSettings(s);
      })
      .catch((e: Error) => setErr(e.message));
  }, []);

  /* 检测全局限制：meta / global 说明 */
  const meta = (rules?.meta ?? {}) as Record<string, unknown>;
  const global = (rules?.global ?? {}) as Record<string, unknown>;

  /* 全局设置分组：key → { label, icon }，字段在组件内按类型渲染 */
  const SET_GROUPS: { key: string; label: string; ic: string; fields: [string, string][] }[] = useMemo(
    () => [
      {
        key: "ui",
        label: "界面",
        ic: "🎨",
        fields: [
          ["sidebar_default_collapsed", "侧栏默认折叠"],
          ["animation_enabled", "启用动画"],
          ["table_row_height", "表格行高"],
          ["page_size", "每页条数"],
        ],
      },
      {
        key: "detection",
        label: "检测",
        ic: "🔍",
        fields: [
          ["concurrency", "并发数"],
          ["parse_timeout", "解析超时（秒）"],
          ["auto_ignore_blank", "自动忽略空行"],
          ["abnormal_popup", "异常弹窗提示"],
        ],
      },
      {
        key: "report",
        label: "报告",
        ic: "📝",
        fields: [
          ["default_dir", "默认导出目录"],
          ["include_cover", "包含封面"],
        ],
      },
      {
        key: "parse",
        label: "解析",
        ic: "📄",
        fields: [
          ["enable_pdf", "启用 PDF 解析"],
          ["enable_legacy", "启用旧格式"],
          ["scan_pdf_skip", "扫描跳过 PDF"],
        ],
      },
      {
        key: "log_cache",
        label: "缓存与日志",
        ic: "🗂️",
        fields: [
          ["cache_expire_days", "缓存保留天数"],
          ["run_log", "记录运行日志"],
        ],
      },
    ],
    [],
  );

  /* 按字段名给出渲染参数 */
  const fieldMeta = (key: string, value: unknown): { type: "bool" | "num" | "text" | "select"; options?: [string, string][] } => {
    if (typeof value === "boolean") return { type: "bool" };
    if (typeof value === "number") return { type: "num" };
    if (key === "table_row_height")
      return {
        type: "select",
        options: [
          ["compact", "紧凑"],
          ["cozy", "舒适"],
          ["comfortable", "宽松"],
        ],
      };
    if (key === "scan_pdf_skip")
      return {
        type: "select",
        options: [
          ["auto", "自动"],
          ["always", "总是跳过"],
          ["never", "从不跳过"],
        ],
      };
    return { type: "text" };
  };

  if (err) {
    return (
      <HoloCard className="p-6">
        <div className="py-10 text-center text-white/40">加载失败：{err}</div>
        <div className="pb-6 text-center text-xs text-white/30">请确认后端服务已在 http://127.0.0.1:8501 启动</div>
      </HoloCard>
    );
  }
  if (!rules || !settings) return null;

  const saveRules = async () => {
    setSaving("rules");
    try {
      await api.saveRules(rules);
      toast("检测全局限制已保存，下次检测即时生效");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(null);
    }
  };

  const restoreRules = async () => {
    try {
      const r = await api.restoreRules();
      setRules(r.data);
      toast("检测全局限制已恢复默认");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const saveSettings = async () => {
    setSaving("settings");
    try {
      await api.saveSettings(settings);
      toast("全局设置已保存");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(null);
    }
  };

  const restoreSettings = async () => {
    try {
      const s = await api.restoreSettings();
      setSettings(s);
      toast("全局设置已恢复默认");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  /* ---- 界面主题配置（实时预览：编辑即 applyTheme，保存后全局持久化） ---- */
  const ui = (settings?.ui ?? {}) as Record<string, unknown>;
  const tScheme = isThemeScheme(ui.theme_scheme) ? ui.theme_scheme : "holographic";
  const tAccent = typeof ui.accent_color === "string" ? ui.accent_color : "";
  const previewAccent = /^#?[0-9a-f]{6}$/i.test(tAccent.trim()) ? tAccent : THEME_SCHEMES[tScheme].defaultAccent;

  const setUiField = (k: string, v: unknown) => {
    if (!settings) return;
    const next = setAt(settings, ["ui", k], v);
    setSettings(next);
    applyTheme(next.ui?.theme_scheme, next.ui?.accent_color);
  };

  const resetThemeDefaults = async () => {
    if (!settings) return;
    const def = resetTheme();
    const next = setAt(setAt(settings, ["ui", "theme_scheme"], def.scheme), ["ui", "accent_color"], def.accent);
    setSettings(next);
    applyTheme(next.ui?.theme_scheme, next.ui?.accent_color);
    try {
      await api.saveSettings(next);
      toast("已恢复全息渐变默认主题配色");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div className="space-y-4">
      {/* ---------- 检测全局限制（规则启停/级别/建议已归集至统一管理） ---------- */}
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">检测全局限制</SectionTitle>
          <HoloBadge tone="gray">config/rules.json · global</HoloBadge>
          <span className="ml-auto flex gap-2.5">
            <HoloButton size="sm" onClick={() => (window.location.hash = "#unified/builtinRules")}>
              前往规则与词库统一管理 →
            </HoloButton>
            <HoloButton variant="primary" size="sm" disabled={saving === "rules"} onClick={saveRules}>
              {saving === "rules" ? "保存中…" : "保存限制"}
            </HoloButton>
            <HoloButton size="sm" onClick={restoreRules}>
              恢复默认
            </HoloButton>
          </span>
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-white/50">
          {String(meta.description ?? "")} 具体规则的启用开关、严重级别、整改建议与新增/导入，请前往「规则与词库统一管理」维护；此处修改保存后，下次检测即时生效。
        </p>

        {/* global 限制 */}
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="mb-1 text-sm font-semibold text-white/90">全局限制（对所有文档类型生效）</div>
          {(["max_file_size_mb", "max_issues_per_file", "skip_hidden_files"] as const).map((k) => (
            <SettingField
              key={k}
              label={{ max_file_size_mb: "单文件上限（MB）", max_issues_per_file: "单文件问题上限", skip_hidden_files: "跳过隐藏文件" }[k]}
              type={typeof global[k] === "boolean" ? "bool" : "num"}
              value={global[k]}
              onChange={(v) => setRules((r) => (r ? setAt(r, ["global", k], v) : r))}
            />
          ))}
        </div>
      </HoloCard>

      {/* ---------- 全局运行设置 ---------- */}
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">全局运行设置</SectionTitle>
          <HoloBadge tone="gray">config/settings.json</HoloBadge>
          <span className="ml-auto flex gap-2.5">
            <HoloButton variant="primary" size="sm" disabled={saving === "settings"} onClick={saveSettings}>
              {saving === "settings" ? "保存中…" : "保存设置"}
            </HoloButton>
            <HoloButton size="sm" onClick={restoreSettings}>
              恢复默认
            </HoloButton>
          </span>
        </div>

        {/* ---------- 界面主题配置 ---------- */}
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="text-sm font-semibold text-white/90">🎨 界面主题配置</div>
            <HoloBadge tone="gray">theme_scheme + accent_color</HoloBadge>
            <span className="ml-auto">
              <HoloButton size="sm" onClick={resetThemeDefaults}>
                恢复全息渐变默认主题
              </HoloButton>
            </span>
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-white/45">
            切换配色方案与强调色即时预览全站生效；点击「保存设置」持久化，刷新页面自动读取。强调色仅作用于光晕 / 高亮 / 状态边框 / 进度条，不破坏深色宇宙基底。
          </p>

          {/* 配色方案 */}
          <div className="mt-3 text-[12px] text-white/55">配色方案</div>
          <div className="mt-1.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {(Object.keys(THEME_SCHEMES) as (keyof typeof THEME_SCHEMES)[]).map((key) => {
              const meta = THEME_SCHEMES[key];
              const active = tScheme === key;
              return (
                <button
                  key={key}
                  onClick={() => setUiField("theme_scheme", key)}
                  className={`rounded-2xl border p-3 text-left transition-all duration-500 select-none ${
                    active
                      ? "border-[var(--border-accent)] bg-white/[0.08] shadow-[0_0_20px_var(--glow-tab)]"
                      : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.06]"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`h-3.5 w-3.5 rounded-full ${active ? "bg-[var(--accent)] shadow-[0_0_8px_var(--accent)]" : "border border-white/30"}`} />
                    <b className="text-[13px] text-white/90">{meta.name}</b>
                    {active && <HoloBadge tone="accent">当前</HoloBadge>}
                  </div>
                  <div className="mt-1.5 text-[11px] leading-relaxed text-white/45">{meta.desc}</div>
                  {/* 方案缩略 */}
                  <div
                    className={`mt-2 h-1.5 rounded-full ${key === "holographic" ? "holo-bg" : "bg-gradient-to-r from-[#6366f1] via-[#1e1b4b] to-[#38bdf8]"}`}
                  />
                </button>
              );
            })}
          </div>

          {/* 强调色 */}
          <div className="mt-3.5 flex flex-wrap items-center gap-3">
            <span className="text-[12px] text-white/55">自定义强调色</span>
            <div className="flex items-center gap-1.5">
              <button
                title="跟随方案默认强调色"
                onClick={() => setUiField("accent_color", "")}
                className={`grid h-7 w-7 place-items-center rounded-full border text-[11px] transition-all duration-500 ${
                  !tAccent.trim() ? "border-[var(--border-accent)] bg-white/[0.1] text-white" : "border-white/20 text-white/50 hover:border-white/40"
                }`}
              >
                默
              </button>
              {ACCENT_PRESETS.map((c) => (
                <button
                  key={c}
                  title={c}
                  onClick={() => setUiField("accent_color", c)}
                  style={{ backgroundColor: c }}
                  className={`h-7 w-7 rounded-full transition-all duration-500 hover:scale-110 ${
                    tAccent.toLowerCase() === c ? "ring-2 ring-white/80 ring-offset-2 ring-offset-transparent shadow-[0_0_10px_var(--accent)]" : ""
                  }`}
                />
              ))}
              <label
                title="自定义取色"
                className="relative grid h-7 w-7 cursor-pointer place-items-center overflow-hidden rounded-full border border-white/25 transition-all duration-500 hover:scale-110"
                style={{ background: `conic-gradient(#f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00)` }}
              >
                <input
                  type="color"
                  value={previewAccent}
                  onChange={(e) => setUiField("accent_color", e.target.value)}
                  className="absolute inset-0 cursor-pointer opacity-0"
                />
              </label>
            </div>
            <span className="text-[11px] text-white/40">
              当前：<code className="text-white/60">{previewAccent}</code>
              {tAccent.trim() ? "（自定义）" : "（方案默认）"}
            </span>
          </div>

          {/* 实时预览 */}
          <div className="mt-3.5 rounded-2xl border border-white/10 bg-[#0a0a1f]/60 p-3.5">
            <div className="text-[11px] text-white/45">实时预览（切换即时生效）</div>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <span className="holo-text text-[15px] font-bold">全息渐变预览</span>
              <HoloButton variant="primary" size="sm" icon={<span>✦</span>}>
                光晕按钮
              </HoloButton>
              <HoloSwitch checked onChange={() => {}} label="高亮开关" />
              <div className="w-36">
                <div className="holo-progress">
                  <i style={{ width: "60%" }} />
                </div>
                <div className="mt-1 text-[10px] text-white/35">进度条 60%</div>
              </div>
              <span className="rounded-full border border-[var(--border-accent)] bg-[var(--glow-accent-soft)] px-2.5 py-1 text-[11px] text-white/80">
                强调状态边框
              </span>
            </div>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
          {SET_GROUPS.map((g) => {
            const grp = (settings[g.key] ?? {}) as Record<string, unknown>;
            return (
              <div key={g.key} className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
                <div className="mb-1 text-sm font-semibold text-white/90">
                  {g.ic} {g.label}
                </div>
                {g.fields.map(([fk, label]) => {
                  const v = grp[fk];
                  if (Array.isArray(v)) return null; // 复杂数组字段只读跳过
                  const meta = fieldMeta(fk, v);
                  return (
                    <SettingField
                      key={fk}
                      label={label}
                      type={meta.type}
                      options={meta.options}
                      value={v}
                      onChange={(nv) => setSettings((s) => (s ? setAt(s, [g.key, fk], nv) : s))}
                    />
                  );
                })}
              </div>
            );
          })}
        </div>
      </HoloCard>
    </div>
  );
}
