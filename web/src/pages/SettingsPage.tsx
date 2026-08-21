import { useCallback, useEffect, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import HoloModal from "../components/ui/HoloModal";
import HoloSwitch from "../components/ui/HoloSwitch";
import StatCard from "../components/common/StatCard";
import SectionTitle from "../components/common/SectionTitle";
import { api } from "../lib/api";
import { useToast } from "../components/ui/Toast";
import type { OverviewData } from "../lib/types";

/* 缓存与系统：系统信息 / 数据概览 / 维护操作（清缓存、清空检测数据） */

export default function SettingsPage() {
  const toast = useToast();
  const [data, setData] = useState<OverviewData | null>(null);
  const [err, setErr] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState<"cache" | "data" | null>(null);

  const refresh = useCallback(() => {
    api
      .overview()
      .then(setData)
      .catch((e: Error) => setErr(e.message));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const doClearCache = async () => {
    setBusy("cache");
    try {
      const r = await api.clearCache();
      toast(`已清理 ${r.count} 个缓存文件`);
      refresh();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  };

  const doClearData = async () => {
    setConfirmOpen(false);
    setBusy("data");
    try {
      await api.clearData();
      toast("检测数据已清空");
      refresh();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  };

  if (err) {
    return (
      <HoloCard className="p-6">
        <div className="py-10 text-center text-white/40">加载失败：{err}</div>
        <div className="pb-6 text-center text-xs text-white/30">请确认后端服务已在 http://127.0.0.1:8501 启动</div>
      </HoloCard>
    );
  }

  const s = data?.summary;
  const statusMap: Record<string, string> = {};
  (data?.status ?? []).forEach(([k, v]) => (statusMap[k] = v));

  /* 当前应用角色（管理员 / 普通用户）：控制后台模块可见性 */
  const [role, setRoleState] = useState<"admin" | "user">("admin");
  useEffect(() => {
    api.settings().then((st) => setRoleState(st.role === "user" ? "user" : "admin")).catch(() => {});
  }, []);
  const setRole = async (r: "admin" | "user") => {
    setRoleState(r);
    try {
      const cur = await api.settings();
      await api.saveSettings({ ...cur, role: r });
      toast(r === "user" ? "已切换为普通用户：AI 配置等后台模块已隐藏" : "已切换为管理员：后台模块可见");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div className="space-y-4">
      {/* 统计卡 */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="检测文件" value={s?.total_files ?? "—"} icon="📄" />
        <StatCard label="发现问题" value={s?.total_issues ?? "—"} icon="🐞" tone="danger" />
        <StatCard label="缓存文件" value={data?.cache.count ?? "—"} icon="🗂️" />
        <StatCard label="缓存大小" value={data?.cache.size_text ?? "—"} icon="💾" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* 系统信息 */}
        <HoloCard className="p-6">
          <SectionTitle>系统信息</SectionTitle>
          <div className="space-y-2.5 text-[13px]">
            {[
              ["应用", "文档核验中心"],
              ["版本", "2.0.0 · 全息渐变版"],
              ["运行模式", "本地离线 · 零联网"],
              ["后端地址", "http://127.0.0.1:8501"],
              ["前端", "React 18 + TypeScript + Tailwind"],
              ["上次检测", statusMap["上次检测"] ?? "—"],
              ["网络状态", statusMap["网络状态"] ?? "离线（零请求）"],
            ].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-3 border-b border-white/5 pb-2.5 last:border-0 last:pb-0">
                <span className="text-white/60">{k}</span>
                <span className="min-w-0 truncate text-right text-white/90">{v}</span>
              </div>
            ))}
          </div>
        </HoloCard>

        {/* 维护操作 */}
        <HoloCard className="p-6">
          <SectionTitle>维护操作</SectionTitle>
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-white/90">清理缓存文件</div>
                <div className="mt-0.5 text-xs text-white/50">删除本地解析缓存与临时文件（不影响规则与词库配置）</div>
              </div>
              <HoloButton size="sm" disabled={busy === "cache"} onClick={doClearCache}>
                {busy === "cache" ? "清理中…" : "立即清理"}
              </HoloButton>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[rgba(255,107,125,0.35)] bg-[rgba(255,107,125,0.08)] p-4">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-white/90">清空检测数据</div>
                <div className="mt-0.5 text-xs text-white/50">删除全部已导入文件与检测结果，不可恢复</div>
              </div>
              <HoloButton variant="danger" size="sm" disabled={busy === "data"} onClick={() => setConfirmOpen(true)}>
                {busy === "data" ? "清空中…" : "清空数据"}
              </HoloButton>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
              <div className="text-sm font-semibold text-white/90">关于离线安全</div>
              <p className="mt-1.5 text-xs leading-relaxed text-white/50">
                本工具全程本地运行：文件解析、规则匹配、报告生成均在本机完成，无任何网络请求、无遥测上报。
                所有配置（规则 / 词库 / 设置）与缓存均保存在本项目 config/ 目录与系统临时缓存目录中。
              </p>
            </div>
          </div>
        </HoloCard>
      </div>

      {/* 角色与权限 */}
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">角色与权限</SectionTitle>
          <span className="text-xs text-white/45">本地离线工具：角色仅用于区分后台管理可见性，不做账号校验</span>
        </div>
        <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-white/90">
                当前角色：{role === "admin" ? "管理员" : "普通用户"}
              </div>
              <div className="mt-0.5 text-xs text-white/50">
                管理员可见「AI 配置」等后台模块并可修改；切换为普通用户后这些入口自动隐藏且直链不可进入。
              </div>
            </div>
            <HoloSwitch
              checked={role === "admin"}
              onChange={(v) => setRole(v ? "admin" : "user")}
              label={role === "admin" ? "管理员" : "普通用户"}
            />
          </div>
        </div>
      </HoloCard>

      {/* 确认弹窗 */}
      <HoloModal open={confirmOpen} title="确认清空检测数据？" onClose={() => setConfirmOpen(false)} width={440}>
        <div className="text-sm leading-relaxed text-white/70">
          此操作将删除全部 <b className="text-[var(--tone-danger)]">{s?.total_files ?? 0}</b> 个已导入文件与{" "}
          <b className="text-[var(--tone-danger)]">{s?.total_issues ?? 0}</b> 条检测结果，且不可恢复。
          <br />
          规则、词库与系统设置不会受影响。确定继续吗？
        </div>
        <div className="mt-5 flex justify-end gap-2.5">
          <HoloButton onClick={() => setConfirmOpen(false)}>取消</HoloButton>
          <HoloButton variant="danger" onClick={doClearData}>
            确认清空
          </HoloButton>
        </div>
      </HoloModal>
    </div>
  );
}
