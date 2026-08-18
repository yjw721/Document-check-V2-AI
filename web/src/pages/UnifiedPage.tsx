import HoloCard from "../components/ui/HoloCard";
import TabBar from "../components/common/TabBar";
import { UNI_TABS } from "../lib/constants";
import CustomRulesTab from "./unified/CustomRulesTab";
import WordbanksTab from "./unified/WordbanksTab";
import TemplateImportTab from "./unified/TemplateImportTab";
import BuiltinRulesTab from "./unified/BuiltinRulesTab";
import BuiltinDictTab from "./unified/BuiltinDictTab";
import AiBuildTab from "./unified/AiBuildTab";
import AiMemoryTab from "./unified/AiMemoryTab";

/* 规则与词库统一管理：自定义正则规则 / 自定义词库 / 批量导入 / 内置标准规则 / 内置词库 / AI 智能创建 / 本地AI自学习 */
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
        {tab === "aiBuild" && <AiBuildTab />}
        {tab === "aiMemory" && <AiMemoryTab />}
      </div>
    </div>
  );
}
