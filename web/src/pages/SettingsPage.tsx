import { useCallback, useEffect, useState } from "react";
import HoloCard from "../components/ui/HoloCard";
import HoloButton from "../components/ui/HoloButton";
import HoloModal from "../components/ui/HoloModal";
import { HoloSelect } from "../components/ui/HoloInput";
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

  const [sec, setSec] = useState<{ access_token: string; admin_token: string; listen: string }>({
    access_token: "",
    admin_token: "",
    listen: "127.0.0.1",
  });
  const [secBusy, setSecBusy] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [showAdminToken, setShowAdminToken] = useState(false);
  useEffect(() => {
    api
      .settings()
      .then((st) => {
        const sc = (st as { security?: { access_token?: string; admin_token?: string; listen?: string } }).security || {};
        setSec({
          access_token: typeof sc.access_token === "string" ? sc.access_token : "",
          admin_token: typeof sc.admin_token === "string" ? sc.admin_token : "",
          listen: typeof sc.listen === "string" && sc.listen ? sc.listen : "127.0.0.1",
        });
      })
      .catch(() => {});
  }, []);
  const saveSecurity = async () => {
    setSecBusy(true);
    try {
      const cur = await api.settings();
      await api.saveSettings({ ...cur, security: sec });
      toast(sec.listen === "0.0.0.0" ? "已保存：重启服务后开放局域网（自动放行防火墙 8501）" : "已保存：重启服务后生效");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSecBusy(false);
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

      {/* 访问安全（多用户 / 局域网共享） */}
      <HoloCard className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <SectionTitle className="!mb-0">访问安全 · 多用户共享</SectionTitle>
          <span className="text-xs text-white/45">单机默认无需配置；局域网多用户请先设置口令再开放端口</span>
        </div>
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="mb-1.5 text-xs text-white/60">访问口令（为空 = 免密，单机默认）</div>
            <div className="relative">
              <input
                type={showToken ? "text" : "password"}
                value={sec.access_token}
                onChange={(e) => setSec({ ...sec, access_token: e.target.value })}
                placeholder="留空表示不校验口令"
                className="w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5 pr-14 text-sm text-white placeholder:text-white/55 backdrop-blur-xl transition-all outline-none hover:border-white/20 focus:border-[var(--border-accent)] focus:shadow-[0_0_20px_var(--glow-input)] focus:bg-white/[0.08]"
              />
              <button
                type="button"
                onClick={() => setShowToken((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg px-2 py-1 text-xs text-white/50 hover:bg-white/10 hover:text-white/80"
              >
                {showToken ? "隐藏" : "显示"}
              </button>
            </div>
            <div className="mt-1 text-[11px] text-white/35">
              已设置时仅显示 ********；不改动会保留原口令，清空后保存可取消口令。
            </div>
          </div>
          <div>
            <div className="mb-1.5 text-xs text-white/60">监听范围</div>
            <HoloSelect
              value={sec.listen}
              onChange={(e) => setSec({ ...sec, listen: e.target.value })}
            >
              <option value="127.0.0.1" className="bg-[#161a2b]">仅本机（127.0.0.1）</option>
              <option value="0.0.0.0" className="bg-[#161a2b]">局域网多用户（0.0.0.0）</option>
            </HoloSelect>
          </div>
          <div>
            <div className="mb-1.5 text-xs text-white/60">管理员口令（可选 · 后台写保护）</div>
            <div className="relative">
              <input
                type={showAdminToken ? "text" : "password"}
                value={sec.admin_token}
                onChange={(e) => setSec({ ...sec, admin_token: e.target.value })}
                placeholder="留空表示不启用管理员校验"
                className="w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5 pr-14 text-sm text-white placeholder:text-white/55 backdrop-blur-xl transition-all outline-none hover:border-white/20 focus:border-[var(--border-accent)] focus:shadow-[0_0_20px_var(--glow-input)] focus:bg-white/[0.08]"
              />
              <button
                type="button"
                onClick={() => setShowAdminToken((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg px-2 py-1 text-xs text-white/50 hover:bg-white/10 hover:text-white/80"
              >
                {showAdminToken ? "隐藏" : "显示"}
              </button>
            </div>
            <div className="mt-1 text-[11px] text-white/35">
              已设置时仅显示 ********；填新口令即覆盖，清空保存可取消管理员校验。
            </div>
          </div>
        </div>
        <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4 text-xs leading-relaxed text-white/50">
          设置<b className="text-white/75">访问口令</b>后，<b className="text-white/75">本机与局域网任何用户打开页面都需输入口令</b>才能使用（防他人读取/删除检测数据）。
          设置<b className="text-white/75">管理员口令</b>后，后台写接口（设置保存 / AI 配置 / 知识库权限等）须额外在请求头携带正确令牌，
          普通用户仅可只读与使用；提权登录在页面右上角「管理员登录」完成，关闭标签页即自动登出。
          开放《0.0.0.0 监听》后重启服务即生效，程序会自动放行 Windows 防火墙入站 TCP 8501（Linux/防火墙受阻时需手动放行）。
          局域网访问地址：<code className="rounded bg-white/10 px-1.5 py-0.5 text-[var(--tone-primary)]">http://&lt;本机局域网IP&gt;:8501</code>
        </div>
        <div className="mt-4 flex justify-end">
          <HoloButton size="sm" disabled={secBusy} onClick={saveSecurity}>
            {secBusy ? "保存中…" : "保存设置"}
          </HoloButton>
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
