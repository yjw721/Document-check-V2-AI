import HoloCard from "../components/ui/HoloCard";
import TabBar from "../components/common/TabBar";
import { UNI_TABS } from "../lib/constants";
import CustomRulesTab from "./unified/CustomRulesTab";
import WordbanksTab from "./unified/WordbanksTab";
import TemplateImportTab from "./unified/TemplateImportTab";
import BuiltinRulesTab from "./unified/BuiltinRulesTab";
import BuiltinDictTab from "./unified/BuiltinDictTab";
import AiCreateTab from "./unified/AiCreateTab";
import ScanTab from "./unified/ScanTab";

/* 规则与词库统一管理：自定义正则规则 / 自定义词库 / 批量导入 / 内置标准规则 / 内置词库 / AI 规则词库智能生成 */
export default function UnifiedPage({ tab, onTab }: { tab: string; onTab: (k: string) => void }) {
  return (
    <div className="space-y-4">
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <TabBar tabs={UNI_TABS as unknown as { key: string; name: string; icon?: string }[]} active={tab} onChange={onTab} />
      </HoloCard>
      <div className="tab-pane">
        {tab === "customRules" && <CustomRulesTab />}
        {tab === "wordbanks" && <WordbanksTab />}
        {tab === "template" && <TemplateImportTab />}
        {tab === "builtinRules" && <BuiltinRulesTab />}
        {tab === "builtinDicts" && <BuiltinDictTab />}
        {tab === "aiCreate" && <AiCreateTab />}
        {tab === "scan" && <ScanTab />}
      </div>
    </div>
  );
}
