import { useEffect, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import HoloBadge from "../components/ui/HoloBadge";
import StatCard from "../components/common/StatCard";
import SectionTitle from "../components/common/SectionTitle";
import HoloProgress from "../components/common/HoloProgress";
import EmptyState from "../components/common/EmptyState";
import { api } from "../lib/api";
import type { OverviewData } from "../lib/types";
import { SEV_LABEL } from "../lib/constants";

/* 总览面板：统计卡 + 严重度/类型分布 + 运行状态 + 高频问题 */
export default function OverviewPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .overview()
      .then(setData)
      .catch((e: Error) => setErr(e.message));
  }, []);

  if (err) {
    return (
      <HoloCard className="p-6">
        <div className="py-10 text-center text-white/40">加载失败：{err}</div>
        <div className="pb-6 text-center text-xs text-white/30">
          请确认后端服务已在 http://127.0.0.1:8501 启动
        </div>
      </HoloCard>
    );
  }
  if (!data) return null;

  const s = data.summary;
  const bt = s.by_type;

  return (
    <div className="space-y-4">
      {/* 统计卡 */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="检测文件" value={s.total_files} icon="📄" />
        <StatCard label="发现问题" value={s.total_issues} icon="🐞" tone="danger" />
        <StatCard label="检测通过" value={s.pass_files} icon="✅" tone="ok" />
        <StatCard label="无法解析" value={s.error_files} icon="⚠️" tone="warn" />
      </div>

      <div className="flex flex-col gap-4 xl:flex-row">
        {/* 左列 */}
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <HoloCard className="p-6">
            <SectionTitle>问题严重度分布</SectionTitle>
            <HoloProgress high={s.severity.high} medium={s.severity.medium} low={s.severity.low} />
            <div className="mt-3 flex flex-wrap gap-4">
              <span className="inline-flex items-center gap-2 text-sm text-white/80">
                <HoloBadge tone="sev-high">严重</HoloBadge> {s.severity.high}
              </span>
              <span className="inline-flex items-center gap-2 text-sm text-white/80">
                <HoloBadge tone="sev-medium">一般</HoloBadge> {s.severity.medium}
              </span>
              <span className="inline-flex items-center gap-2 text-sm text-white/80">
                <HoloBadge tone="sev-low">轻微</HoloBadge> {s.severity.low}
              </span>
            </div>
          </HoloCard>

          <HoloCard className="p-6">
            <SectionTitle>文件类型分布</SectionTitle>
            <div className="space-y-2.5 text-[13px]">
              {[
                ["Word 文档", bt.Word || 0, "📘"],
                ["Excel 文档", bt.Excel || 0, "📗"],
                ["PDF 文档", bt.PDF || 0, "📕"],
                ["不支持格式", bt["不支持"] || 0, "⚠️"],
              ].map(([k, v, ic]) => (
                <div key={k as string} className="flex items-center justify-between border-b border-white/5 pb-2.5 last:border-0 last:pb-0">
                  <span className="text-white/60">
                    {ic} {k}
                  </span>
                  <span className="font-bold text-white">{v}</span>
                </div>
              ))}
            </div>
          </HoloCard>
        </div>

        {/* 右列 */}
        <div className="flex w-full flex-col gap-4 xl:w-[340px] xl:shrink-0">
          <HoloCard className="p-6">
            <SectionTitle>运行状态</SectionTitle>
            <div className="space-y-2.5 text-[13px]">
              {data.status.map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-3 border-b border-white/5 pb-2.5 last:border-0 last:pb-0">
                  <span className="text-white/60">{k}</span>
                  <span className="min-w-0 truncate text-right text-white/90">{v}</span>
                </div>
              ))}
            </div>
          </HoloCard>

          <HoloCard className="p-6">
            <SectionTitle>高频问题（Top 10）</SectionTitle>
            {data.recent_issues.length ? (
              <div className="max-h-[300px] space-y-3 overflow-y-auto">
                {data.recent_issues.map((i, idx) => (
                  <div key={idx} className="border-b border-white/5 pb-2.5 last:border-0 last:pb-0">
                    <div className="flex items-center gap-2">
                      <HoloBadge tone={i.severity as "sev-high" | "sev-medium" | "sev-low"}>
                        {SEV_LABEL[i.severity]}
                      </HoloBadge>
                      <b className="text-sm text-white">{i.rule_title}</b>
                    </div>
                    <div className="mt-1 truncate text-xs text-white/40">
                      {i.file_name} · {i.location}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState text="暂无问题" icon="✨" />
            )}
          </HoloCard>

          <div className="flex gap-2.5">
            <HoloButton
              variant="primary"
              icon={<span>📥</span>}
              onClick={() => (window.location.hash = "#detection/upload")}
            >
              导入检测
            </HoloButton>
            <HoloButton icon={<span>🐞</span>} onClick={() => (window.location.hash = "#detection/issues")}>
              查看问题
            </HoloButton>
          </div>
        </div>
      </div>
    </div>
  );
}
