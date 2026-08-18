import HoloCard from "../components/ui/HoloCard";
import TabBar from "../components/common/TabBar";
import { DET_TABS } from "../lib/constants";
import { taskState } from "../lib/taskState";
import UploadTab from "./detection/UploadTab";
import FilesTab from "./detection/FilesTab";
import IssuesTab from "./detection/IssuesTab";
import WaitingTab from "./detection/WaitingTab";
import ReportTab from "./detection/ReportTab";
import { useToast } from "../components/ui/Toast";

/* 检测管理：导入与检测 / 文件列表 / 错误详情 / 核验等待 / 报告导出 */
export default function DetectionPage({ tab, onTab }: { tab: string; onTab: (k: string) => void }) {
  const toast = useToast();
  const handleTab = (k: string) => {
    // 核验未完成时不允许跳转报告页面（真实任务状态由等待页轮询维护）
    if (k === "report" && taskState.status === "running") {
      toast("核验尚未完成，请等待核验结束或先取消任务", "warn");
      return;
    }
    onTab(k);
  };
  return (
    <div className="space-y-4">
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <TabBar tabs={DET_TABS as unknown as { key: string; name: string; icon?: string }[]} active={tab} onChange={handleTab} />
      </HoloCard>
      <div className="tab-pane">
        {tab === "upload" && <UploadTab />}
        {tab === "files" && <FilesTab />}
        {tab === "issues" && <IssuesTab />}
        {tab === "waiting" && <WaitingTab />}
        {tab === "report" && <ReportTab />}
      </div>
    </div>
  );
}
