import { useEffect, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloBadge from "../../components/ui/HoloBadge";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { FilesData } from "../../lib/types";
import { FT_TABLE_ICON } from "../../lib/constants";
import { useToast } from "../../components/ui/Toast";

/* 标签2 · 文件列表：已导入文档清单 + 清空数据 */
export default function FilesTab() {
  const toast = useToast();
  const [data, setData] = useState<FilesData | null>(null);
  const [err, setErr] = useState("");

  const load = () => {
    api
      .files()
      .then(setData)
      .catch((e: Error) => setErr(e.message));
  };
  useEffect(load, []);

  const clearData = async () => {
    if (!window.confirm("确认清空内存中的全部检测结果？")) return;
    await api.clearData();
    toast("已清空");
    load();
  };

  if (err)
    return (
      <HoloCard className="p-6">
        <EmptyState text={`加载失败：${err}`} icon="⚠️" />
      </HoloCard>
    );
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <HoloButton
          variant="primary"
          icon={<span>＋</span>}
          onClick={() => (window.location.hash = "#detection/upload")}
        >
          导入文件
        </HoloButton>
        <HoloButton variant="danger" onClick={clearData}>
          清空检测数据
        </HoloButton>
        <span className="ml-auto text-xs text-white/40">共 {data.results.length} 个文件</span>
      </div>

      <HoloCard className="overflow-hidden p-0" glow="sm">
        <div className="overflow-x-auto">
          <table className="holo-table w-full text-[13.5px]">
            <thead>
              <tr className="text-xs text-white/60">
                <th className="px-4 py-3 text-left font-semibold">文件名</th>
                <th className="px-4 py-3 text-left font-semibold">类型</th>
                <th className="px-4 py-3 text-left font-semibold">大小</th>
                <th className="px-4 py-3 text-left font-semibold">状态</th>
                <th className="px-4 py-3 text-left font-semibold">问题数</th>
                <th className="px-4 py-3 text-left font-semibold">路径</th>
              </tr>
            </thead>
            <tbody>
              {data.results.length ? (
                data.results.map((r, i) => (
                  <tr key={i} className="border-b border-white/5 text-white/85">
                    <td className="px-4 py-3">
                      <span className="mr-1.5">{FT_TABLE_ICON[r.file_type] ?? "📄"}</span>
                      {r.file_name}
                    </td>
                    <td className="px-4 py-3">
                      <HoloBadge tone="gray">{r.file_type}</HoloBadge>
                    </td>
                    <td className="px-4 py-3 text-white/70">{r.size_text}</td>
                    <td className="px-4 py-3">
                      {r.status === "error" ? (
                        <HoloBadge tone="danger">无法解析</HoloBadge>
                      ) : r.active_issue_count > 0 ? (
                        <HoloBadge tone="warn">存在问题</HoloBadge>
                      ) : (
                        <HoloBadge tone="ok">检测通过</HoloBadge>
                      )}
                    </td>
                    <td className="px-4 py-3 font-bold">{r.active_issue_count}</td>
                    <td className="max-w-[220px] truncate px-4 py-3 text-xs text-white/40" title={r.file_path}>
                      {r.file_path}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6}>
                    <EmptyState text="暂无检测结果，请先到「导入与检测」上传文件" icon="📄" />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </HoloCard>
    </div>
  );
}
