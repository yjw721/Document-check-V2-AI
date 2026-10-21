import { useCallback, useEffect, useMemo, useState } from "react";
import AppLayout from "./components/layout/AppLayout";
import ErrorBoundary from "./components/ErrorBoundary";
import OverviewPage from "./pages/OverviewPage";
import DetectionPage from "./pages/DetectionPage";
import UnifiedPage from "./pages/UnifiedPage";
import AdminPage from "./pages/AdminPage";
import SettingsPage from "./pages/SettingsPage";
import AiConfigPage from "./pages/AiConfigPage";
import KbPage from "./pages/KbPage";
import { ToastProvider } from "./components/ui/Toast";
import { OverlayProvider } from "./lib/ui";
import { TaskProvider } from "./lib/taskState";
import { DET_TABS, NAV, UNI_TABS } from "./lib/constants";
import { api, clearAccessToken } from "./lib/api";
import { AccessGate } from "./components/ui/AccessGate";
import { navGuard, LEAVE_CONFIRM_MSG } from "./lib/unsaved";
import { applyTheme } from "./lib/theme";

/* ---------- hash 解析：兼容旧路由自动映射 ---------- */
function parseHash(hash: string): { view: string; detTab: string; uniTab: string } {
  let v = (hash || "#overview").replace(/^#\/?/, "");
  let detTab = "upload";
  let uniTab = "rules";

  // 检测管理旧路由：upload/files/issues/report
  if (v === "upload" || v === "files" || v === "issues" || v === "report") {
    detTab = v;
    v = "detection";
  }
  // 规则词库旧路由
  else if (v === "rules") {
    v = "unified";
  }
  // 检测管理子路由 #detection/<tab>
  else if (v === "detection" || v.startsWith("detection/")) {
    const sub = v.split("/")[1] ?? "";
    if (DET_TABS.some((t) => t.key === sub)) detTab = sub;
    v = "detection";
  }
  // 统一管理旧路由：customRules→rules、wordbanks、builtinRules→rules、builtinDicts→wordbanks、template
  if (v === "customRules" || v === "builtinRules") {
    uniTab = "rules";
    v = "unified";
  } else if (v === "wordbanks" || v === "builtinDicts") {
    uniTab = "wordbanks";
    v = "unified";
  } else if (v === "template") {
    uniTab = v;
    v = "unified";
  }
  // 统一管理子路由 #unified/<tab>
  else if (v === "unified" || v.startsWith("unified/")) {
    const sub = v.split("/")[1] ?? "";
    if (UNI_TABS.some((t) => t.key === sub)) uniTab = sub;
    v = "unified";
  }

  if (!NAV.some((n) => n.key === v)) v = "overview";
  return { view: v, detTab, uniTab };
}

function App() {
  const [hash, setHash] = useState(() => window.location.hash);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("docchk_side") === "1");
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    localStorage.getItem("docchk_theme") === "light" ? "light" : "dark",
  );
  const [counts, setCounts] = useState<{ files: number; issues: number }>({ files: 0, issues: 0 });
  const [role, setRole] = useState<"admin" | "user">("admin");
  const [auth, setAuth] = useState<"checking" | "locked" | "open">("checking");

  /* 访问口令门禁：启动探针 + 全局 401 兜底 */
  useEffect(() => {
    let alive = true;
    api
      .securityPing()
      .then((r) => {
        if (!alive) return;
        setAuth(!r.auth_required || r.authorized ? "open" : "locked");
      })
      .catch(() => alive && setAuth("locked"));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const onUnauthorized = () => {
      clearAccessToken();
      setAuth("locked");
    };
    window.addEventListener("docchk:unauthorized", onUnauthorized);
    return () => window.removeEventListener("docchk:unauthorized", onUnauthorized);
  }, []);

  const { view, detTab, uniTab } = useMemo(() => parseHash(hash), [hash]);
  /* 普通用户不可见 / 不可进入「AI 配置」后台模块 */
  const effView = view === "aiconfig" && role !== "admin" ? "overview" : view;

  /* 哈希变化监听 */
  useEffect(() => {
    const h = () => setHash(window.location.hash);
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  /* 主题同步：写入 <html data-theme> + localStorage */
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("docchk_theme", theme);
  }, [theme]);

  /* 配色方案 / 强调色：启动与刷新时读取已持久化主题参数，全局统一应用 */
  useEffect(() => {
    api
      .settings()
      .then((s) => {
        applyTheme(s.ui?.theme_scheme, s.ui?.accent_color);
        setRole(s.role === "user" ? "user" : "admin");
      })
      .catch(() => applyTheme("holographic", ""));
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  /* 导航 */
  const navigate = useCallback((key: string) => {
    if (navGuard.dirty && key !== effView && !window.confirm(LEAVE_CONFIRM_MSG)) return;
    window.location.hash = "#" + key;
  }, [effView]);


  /* 顶部 chips 刷新 */
  useEffect(() => {
    api
      .overview()
      .then((d) => setCounts({ files: d.summary.total_files, issues: d.summary.total_issues }))
      .catch(() => {});
  }, [view]);

  const toggleSide = useCallback(() => {
    setCollapsed((c) => {
      localStorage.setItem("docchk_side", c ? "0" : "1");
      return !c;
    });
  }, []);

  /* 标题 / 面包屑 */
  const navItem = NAV.find((n) => n.key === effView) ?? NAV[0];
  const title = navItem.name;
  const crumb =
    effView === "detection"
      ? (DET_TABS.find((t) => t.key === detTab) ?? DET_TABS[0]).name
      : effView === "unified"
        ? (UNI_TABS.find((t) => t.key === uniTab) ?? UNI_TABS[0]).name
        : effView === "aiconfig"
          ? "后台管理 · AI 配置"
          : "本地离线 · 零联网";

  /* 口令校验完成前：不挂载业务模块，避免未鉴权请求刷屏 */
  if (auth === "checking") {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-[var(--bg)]">
        <div className="text-sm text-[var(--text-dim)]">正在连接服务…</div>
      </div>
    );
  }
  if (auth === "locked") {
    return <AccessGate onAuthed={() => setAuth("open")} />;
  }

  return (
    <ToastProvider>
      <OverlayProvider>
        <TaskProvider>
          <AppLayout
          collapsed={collapsed}
          onToggle={toggleSide}
          active={effView}
          onNavigate={navigate}
          title={title}
          crumb={crumb}
          fileCount={counts.files}
          issueCount={counts.issues}
          theme={theme}
          onToggleTheme={toggleTheme}
          hiddenNav={role !== "admin" ? ["aiconfig"] : []}
        >
          {/* 页面级错误边界：单个模块崩溃只降级为错误卡片，不再整站白屏 */}
          <ErrorBoundary label={navItem.name}>
            {effView === "overview" && <OverviewPage />}
            {effView === "detection" && <DetectionPage tab={detTab} onTab={(t) => (window.location.hash = `#detection/${t}`)} />}
            {effView === "unified" && <UnifiedPage tab={uniTab} onTab={(t) => (window.location.hash = `#unified/${t}`)} />}
            {effView === "kb" && <KbPage />}
            {effView === "aiconfig" && <AiConfigPage />}
            {effView === "admin" && <AdminPage />}
            {effView === "settings" && <SettingsPage />}
          </ErrorBoundary>
        </AppLayout>
        </TaskProvider>
      </OverlayProvider>
    </ToastProvider>
  );
}

export default App;
