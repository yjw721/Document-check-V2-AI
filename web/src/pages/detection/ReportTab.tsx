import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import { HoloInput, HoloSelect } from "../../components/ui/HoloInput";
import HoloSwitch from "../../components/ui/HoloSwitch";
import SectionTitle from "../../components/common/SectionTitle";
import { FieldLabel } from "../../components/ui/HoloInput";
import { api } from "../../lib/api";
import type { OverviewData } from "../../lib/types";
import { useToast } from "../../components/ui/Toast";
import { useOverlay } from "../../lib/ui";

/* 标签4 · 报告导出：报告配置 + 生成下载 Word 核查报告（支持按语句通顺类筛选导出） */
export default function ReportTab() {
  const toast = useToast();
  const overlay = useOverlay();
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [operator, setOperator] = useState("");
  const [org, setOrg] = useState("");
  const [cover, setCover] = useState(true);
  const [filter, setFilter] = useState<"all" | "fluency">("all");

  useEffect(() => {
    api
      .overview()
      .then(setOverview)
      .catch(() => setOverview(null));
  }, []);

  const generate = async () => {
    overlay.show("正在生成报告…");
    try {
      const blob = await api.report({ operator, org, include_cover: cover, report_filter: filter });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `文档低级错误检查报告_${new Date().toISOString().slice(0, 10)}.docx`;
      a.click();
      URL.revokeObjectURL(url);
      overlay.hide();
      toast("报告已生成");
    } catch (e) {
      overlay.hide();
      toast((e as Error).message, "err");
    }
  };

  const hasFiles = (overview?.summary.total_files ?? 0) > 0;

  return (
    <HoloCard className="max-w-[560px] p-6">
      <SectionTitle>生成 Word 检测报告</SectionTitle>
      {hasFiles ? (
        <div className="mb-3 text-sm text-white/70">
          当前共 <b className="text-white">{overview?.summary.total_files}</b> 个文件、
          <b className="text-white">{overview?.summary.total_issues}</b> 处问题，将一并写入报告。
        </div>
      ) : (
        <div className="mb-3 text-sm text-[#ffb454]">当前暂无检测结果，请先导入并检测文件。</div>
      )}

      <div className="space-y-4">
        <div>
          <FieldLabel>检测人</FieldLabel>
          <HoloInput placeholder="选填" value={operator} onChange={(e) => setOperator(e.target.value)} />
        </div>
        <div>
          <FieldLabel>所属单位</FieldLabel>
          <HoloInput placeholder="选填" value={org} onChange={(e) => setOrg(e.target.value)} />
        </div>
        <div>
          <FieldLabel>导出范围</FieldLabel>
          <HoloSelect className="w-full" value={filter} onChange={(e) => setFilter(e.target.value as "all" | "fluency")}>
            <option value="all">全部问题</option>
            <option value="fluency">仅语句通顺类问题（逻辑断裂 / 成分残缺 / 语序 / 赘述 / 关联词 / 杂糅）</option>
          </HoloSelect>
        </div>
        <HoloSwitch checked={cover} onChange={setCover} label="包含封面与落款" />
        <HoloButton variant="primary" disabled={!hasFiles} onClick={generate} icon={<span>📝</span>}>
          生成并下载报告
        </HoloButton>
      </div>
    </HoloCard>
  );
}
