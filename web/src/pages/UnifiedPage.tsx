import HoloCard from "../components/ui/HoloCard";
import TabBar from "../components/common/TabBar";
import { UNI_TABS } from "../lib/constants";
import RulesOverviewTab from "./unified/RulesOverviewTab";
import WordbanksOverviewTab from "./unified/WordbanksOverviewTab";
import TemplateImportTab from "./unified/TemplateImportTab";
import AiCreateTab from "./unified/AiCreateTab";
import DraftsTab from "./unified/DraftsTab";
import ScanTab from "./unified/ScanTab";
import { navGuard, LEAVE_CONFIRM_MSG } from "../lib/unsaved";

/* 规则与词库统一管理：规则总览（内置+自定义）/ 词库总览（内置+自定义）/
    批量导入 / AI 规则词库智能生成 / 一键扫描清理 */
export default function UnifiedPage({ tab, onTab }: { tab: string; onTab: (k: string) => void }) {
  const handleTab = (k: string) => {
    if (navGuard.dirty && k !== tab && !window.confirm(LEAVE_CONFIRM_MSG)) return;
    onTab(k);
  };
  return (
    <div className="space-y-4">
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <TabBar tabs={UNI_TABS as unknown as { key: string; name: string; icon?: string }[]} active={tab} onChange={handleTab} />
      </HoloCard>
      <div className="tab-pane">
        {tab === "rules" && <RulesOverviewTab />}
        {tab === "wordbanks" && <WordbanksOverviewTab />}
        {tab === "template" && <TemplateImportTab />}
        {tab === "aiCreate" && <AiCreateTab />}
        {tab === "drafts" && <DraftsTab />}
        {tab === "scan" && <ScanTab />}
      </div>
    </div>
  );
}
