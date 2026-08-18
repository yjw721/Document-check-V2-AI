import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloBadge from "../../components/ui/HoloBadge";
import { HoloInput } from "../../components/ui/HoloInput";
import HoloSwitch from "../../components/ui/HoloSwitch";
import SectionTitle from "../../components/common/SectionTitle";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { AiMemoryData, AiMemorySample } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";

/* 标签7 · 本地AI自学习：人工确认样本 → 本地提炼标准表述 → 词库条目+校验规则
   全程离线；仅学习用户主动确认正确的内容；记忆数据本机保存、不进入导出报告 */
export default function AiMemoryTab() {
  const toast = useToast();
  const [data, setData] = useState<AiMemoryData | null>(null);
  const [err, setErr] = useState("");
  const [content, setContent] = useState("");
  const [src, setSrc] = useState("");
  const [learning, setLearning] = useState<string | null>(null);

  const load = () => {
    api
      .aiMemory()
      .then(setData)
      .catch((e: Error) => setErr(e.message));
  };
  useEffect(load, []);

  const toggleEnabled = async () => {
    if (!data) return;
    try {
      const r = await api.aiMemoryToggle(!data.enabled);
      toast(r.enabled ? "本地AI自学习已开启" : "本地AI自学习已关闭（已有记忆保留）");
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const addSample = async () => {
    const c = content.trim();
    if (!c) {
      toast("请输入人工确认正确的样本内容", "warn");
      return;
    }
    try {
      const r = await api.aiMemoryAddSample({ content: c, source: src.trim() });
      if (!r.ok) {
        toast(r.message || "添加失败", "err");
        return;
      }
      toast("样本已加入本地学习库（仅保存在本机）");
      setContent("");
      setSrc("");
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const learn = async (s: AiMemorySample) => {
    if (learning) return;
    setLearning(s.id);
    try {
      const r = await api.aiMemoryLearn(s.id);
      if (r.ok) {
        const st = r.stats || {};
        toast(`学习完成：词库条目 ${st.entries ?? 0} 条、校验规则 ${st.rules ?? 0} 条`);
      } else {
        toast(r.message || "学习失败", "err");
      }
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setLearning(null);
    }
  };

  const clearAll = async () => {
    if (!data) return;
    if (!window.confirm("确定批量清空本地AI学习记忆？\n将删除全部学习样本与学习产出的词条/规则，不影响您手动导入或编写的规则词库。")) return;
    try {
      const r = await api.aiMemoryClear();
      toast(`已清空 ${r.removed ?? 0} 条学习记忆`);
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (err)
    return (
      <HoloCard className="p-6">
        <EmptyState text={`加载失败：${err}`} icon="⚠️" />
      </HoloCard>
    );
  if (!data) return null;

  const st = data.stats;
  const sampleStatus: Record<string, { tone: "gray" | "ok" | "warn" | "danger"; label: string }> = {
    pending: { tone: "gray", label: "待学习" },
    learning: { tone: "warn", label: "学习中" },
    done: { tone: "ok", label: "已学习" },
    failed: { tone: "danger", label: "失败" },
  };

  return (
    <div className="space-y-4">
      {/* 说明 + 总开关 */}
      <HoloCard className="p-6" glow="sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-[720px]">
            <SectionTitle>本地 AI 模型自学习记忆（全程离线）</SectionTitle>
            <p className="mt-2 text-xs leading-relaxed text-white/55">
              仅学习您人工确认正确的文档内容（AI 不自动采集任何文档）。基于确认内容，本地模型提炼标准表述，
              自动生成词库条目与校验规则并合并至「自定义词库 / 自定义正则规则」模块，来源标记为
              <b className="text-white/80"> 本地AI学习生成</b>。记忆数据全部保存在本机，不进入检测导出报告。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-white/60">总开关</span>
            <HoloSwitch checked={data.enabled} onChange={toggleEnabled} label={data.enabled ? "已开启" : "已关闭"} />
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {[
            ["样本总数", st.samples, "🧾"],
            ["待学习", st.pending, "⏳"],
            ["已学习", st.done, "✅"],
            ["学习失败", st.failed, "⚠️"],
            ["产出词条", st.entries, "📚"],
            ["产出规则", st.rules, "🧩"],
          ].map(([k, v, ic]) => (
            <div key={k as string} className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
              <div className="text-[11px] text-white/45">
                {ic} {k}
              </div>
              <div className="mt-1 font-mono text-xl font-bold text-white/90">{v as number}</div>
            </div>
          ))}
        </div>
      </HoloCard>

      {/* 添加人工确认样本 */}
      <HoloCard className="p-6">
        <SectionTitle>添加人工确认样本（正确内容）</SectionTitle>
        <textarea
          className="mt-3 min-h-[110px] w-full resize-y rounded-2xl border border-white/10 bg-white/[0.05] px-3.5 py-3 text-sm text-white/90 outline-none backdrop-blur-xl transition-colors placeholder:text-white/30 focus:border-[var(--border-accent-soft)] focus:shadow-[0_0_18px_var(--glow-btn)]"
          placeholder="粘贴一段您人工确认【正确】的文档内容（如规范表述、标准术语用法、正确的格式示例）…"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <HoloInput
            className="w-[280px]"
            placeholder="来源（选填，如：资产评估报告.docx / 第3页）"
            value={src}
            onChange={(e) => setSrc(e.target.value)}
          />
          <HoloButton variant="primary" onClick={addSample} icon={<span>＋</span>} disabled={!data.enabled}>
            加入学习库
          </HoloButton>
          {!data.enabled && <span className="text-xs text-[#ffb454]">总开关已关闭，请先开启自学习</span>}
        </div>
      </HoloCard>

      {/* 样本列表 */}
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <div className="flex items-center justify-between px-6 pt-5">
          <SectionTitle>学习样本（{data.samples.length}）</SectionTitle>
          <HoloButton variant="danger" size="sm" onClick={clearAll} disabled={!data.samples.length && !data.learned.length}>
            批量清空学习记忆
          </HoloButton>
        </div>
        {data.samples.length ? (
          <div className="space-y-3 p-6 pt-4">
            {data.samples.map((s) => {
              const stb = sampleStatus[s.status] ?? sampleStatus.pending;
              return (
                <div key={s.id} className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <HoloBadge tone={stb.tone}>{stb.label}</HoloBadge>
                    {s.enabled ? (
                      <HoloBadge tone="accent">启用</HoloBadge>
                    ) : (
                      <HoloBadge tone="gray">已禁用</HoloBadge>
                    )}
                    {s.source && <span className="text-[11px] text-white/40">来源：{s.source}</span>}
                    <span className="text-[11px] text-white/30">{s.created_at}</span>
                    {s.status === "done" && s.result_count > 0 && (
                      <span className="text-[11px] text-[#4fd6c9]">产出 {s.result_count} 条</span>
                    )}
                  </div>
                  <p className="mt-2 break-all text-sm leading-relaxed text-white/80">{s.content}</p>
                  {s.error && (
                    <p className="mt-2 rounded-xl border border-[rgba(255,107,125,0.3)] bg-[rgba(255,107,125,0.08)] px-3 py-2 text-xs text-[#ffb3bd]">
                      学习失败：{s.error}
                    </p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <HoloButton size="sm" variant="primary" onClick={() => learn(s)} disabled={!!learning || !data.enabled || !s.enabled}>
                      {learning === s.id ? "学习中…" : "立即学习"}
                    </HoloButton>
                    <HoloButton
                      size="sm"
                      onClick={async () => {
                        try {
                          await api.aiMemorySampleToggle(s.id, !s.enabled);
                          load();
                        } catch (e) {
                          toast((e as Error).message, "err");
                        }
                      }}
                    >
                      {s.enabled ? "禁用" : "启用"}
                    </HoloButton>
                    <HoloButton
                      size="sm"
                      variant="danger"
                      onClick={async () => {
                        if (!window.confirm("删除该样本？其已学习的产出条目不受影响（可在下方列表中管理）。")) return;
                        try {
                          await api.aiMemorySampleDelete(s.id);
                          toast("样本已删除");
                          load();
                        } catch (e) {
                          toast((e as Error).message, "err");
                        }
                      }}
                    >
                      删除
                    </HoloButton>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="p-6 pt-2">
            <EmptyState text="暂无学习样本：在检测结果中标记内容为正确，或在上方粘贴确认过的正确内容" icon="🧠" />
          </div>
        )}
      </HoloCard>

      {/* 学习产出（词库条目 + 校验规则） */}
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <div className="px-6 pt-5">
          <SectionTitle>学习产出（{data.learned.length}）</SectionTitle>
          <p className="mt-1 text-[11px] text-white/40">
            已自动合并至「自定义词库 / 自定义正则规则」（来源标记：本地AI学习生成）；启用状态即时参与检测，可在对应管理页二次编辑。
          </p>
        </div>
        {data.learned.length ? (
          <div className="space-y-3 p-6 pt-4">
            {data.learned.map((l) => (
              <div key={l.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
                <HoloBadge tone={l.kind === "wordbank" ? "accent" : "warn"}>{l.kind === "wordbank" ? "词库条目" : "校验规则"}</HoloBadge>
                <span className="min-w-[120px] text-sm font-semibold text-white/85">
                  {l.kind === "wordbank" ? l.keyword : l.name}
                </span>
                {l.kind === "rule" && (
                  <span className="font-mono text-[11px] text-white/40">
                    [{l.match_mode}] {l.pattern}
                  </span>
                )}
                <span className="max-w-[320px] truncate text-xs text-white/45">{l.suggestion}</span>
                <span className="text-[10px] text-white/30">本地AI学习生成 · {l.created_at}</span>
                <span className="ml-auto flex items-center gap-2">
                  <HoloSwitch
                    checked={l.enabled}
                    onChange={async () => {
                      try {
                        await api.aiMemoryLearnedToggle(l.id, !l.enabled);
                        load();
                      } catch (e) {
                        toast((e as Error).message, "err");
                      }
                    }}
                    label={l.enabled ? "启用" : "禁用"}
                  />
                  <HoloButton
                    size="sm"
                    variant="danger"
                    onClick={async () => {
                      if (!window.confirm("删除该学习产出？（从词库/自定义规则中移除）")) return;
                      try {
                        await api.aiMemoryLearnedDelete(l.id);
                        toast("已删除");
                        load();
                      } catch (e) {
                        toast((e as Error).message, "err");
                      }
                    }}
                  >
                    删除
                  </HoloButton>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-6 pt-2">
            <EmptyState text="暂无学习产出：添加样本后点击「立即学习」即可生成" icon="📚" />
          </div>
        )}
      </HoloCard>
    </div>
  );
}
