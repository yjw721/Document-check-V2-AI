import { useState } from "react";
import { api, setAccessToken } from "../../lib/api";

/* 访问口令门禁（局域网多用户模式）：输入口令解锁整个工具。
   无需口令时后端 auth_required=false，本组件不会挂载。 */

export function AccessGate({ onAuthed }: { onAuthed: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setAccessToken(token.trim());
      const r = await api.securityPing();
      if (r.authorized) {
        onAuthed();
        return;
      }
      setAccessToken("");
      setError("口令错误，请重新输入");
    } catch (err) {
      setAccessToken("");
      setError(err instanceof Error ? err.message : "无法连接服务，请稍后再试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="holo-gate fixed inset-0 z-[200] flex items-center justify-center bg-[#0b1020]/95">
      <div className="pointer-events-none absolute inset-0 holo-grid opacity-40" />
      <form
        onSubmit={submit}
        className="relative w-[min(92vw,400px)] rounded-3xl border border-white/10 bg-white/5 p-8 backdrop-blur-2xl shadow-2xl"
      >
        <div className="mb-1 text-2xl font-semibold tracking-tight bg-gradient-to-r from-sky-300 via-violet-300 to-fuchsia-300 bg-clip-text text-transparent">
          文档核验中心
        </div>
        <div className="mb-6 text-xs text-white/55">
          服务已设置访问口令，请输入后使用。
        </div>
        <input
          type="password"
          autoFocus
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="请输入访问口令"
          className="w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm text-white placeholder:text-white/55 backdrop-blur-xl transition-all outline-none hover:border-white/20 focus:border-[var(--border-accent)] focus:shadow-[0_0_20px_var(--glow-input)] focus:bg-white/[0.08]"
        />
        {error && <div className="mt-3 text-xs text-rose-400">{error}</div>}
        <button
          type="submit"
          disabled={busy || !token.trim()}
          className="mt-5 w-full rounded-2xl bg-gradient-to-r from-sky-500 to-violet-600 py-2.5 text-sm font-medium text-white shadow-lg shadow-violet-900/30 transition active:scale-[0.99] disabled:opacity-50"
        >
          {busy ? "验证中…" : "进入系统"}
        </button>
      </form>
    </div>
  );
}