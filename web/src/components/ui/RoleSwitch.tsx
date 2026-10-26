import { useState } from "react";
import HoloButton from "./HoloButton";
import HoloModal from "./HoloModal";

/* 角色切换 / 管理员登录：
   - 未配置管理员口令（adminRequired=false）：全员即管理员，仅展示徽标，无需登录/登出
   - 已配置：普通用户可点击「管理员登录」输入口令提权（会话级，关闭标签页自动登出）
             管理员可「退出管理员」回到普通用户视角 */
interface RoleSwitchProps {
  isAdmin: boolean;
  adminRequired: boolean;
  onLogin: (token: string) => Promise<boolean>;
  onLogout: () => void;
}

export default function RoleSwitch({ isAdmin, adminRequired, onLogin, onLogout }: RoleSwitchProps) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  if (!adminRequired) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-accent-soft)] bg-white/5 px-3.5 py-1.5 text-xs text-white/70 backdrop-blur-xl">
        <span className="h-2 w-2 rounded-full bg-[#46d39a] shadow-[0_0_8px_#46d39a]" />
        管理员
      </span>
    );
  }

  const submit = async () => {
    if (!token) {
      setErr("请输入管理员口令");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const ok = await onLogin(token);
      if (ok) {
        setOpen(false);
        setToken("");
      } else {
        setErr("管理员口令错误");
      }
    } catch (e) {
      setErr((e as Error).message || "校验失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3.5 py-1.5 text-xs backdrop-blur-xl">
        <span
          className={`h-2 w-2 rounded-full ${
            isAdmin
              ? "bg-[#46d39a] shadow-[0_0_8px_#46d39a]"
              : "bg-[#f5a623] shadow-[0_0_8px_#f5a623]"
          }`}
        />
        <span className="text-white/70">{isAdmin ? "管理员" : "普通用户"}</span>
      </span>
      {isAdmin ? (
        <HoloButton size="sm" onClick={onLogout}>
          退出管理员
        </HoloButton>
      ) : (
        <HoloButton size="sm" variant="primary" onClick={() => setOpen(true)}>
          管理员登录
        </HoloButton>
      )}

      <HoloModal open={open} title="管理员登录" onClose={() => setOpen(false)} width={420}>
        <p className="mb-3 text-xs leading-relaxed text-white/55">
          输入管理员口令以提权：可访问「AI 配置 / 设置 / 后台」等模块并保存后台配置。关闭标签页即自动登出。
        </p>
        <input
          type="password"
          autoFocus
          value={token}
          onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder="管理员口令"
          className="w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm text-white placeholder:text-white/55 backdrop-blur-xl outline-none transition-all hover:border-white/20 focus:border-[var(--border-accent)] focus:shadow-[0_0_20px_var(--glow-input)] focus:bg-white/[0.08]"
        />
        {err && <div className="mt-2 text-xs text-[var(--tone-danger)]">{err}</div>}
        <div className="mt-5 flex justify-end gap-2.5">
          <HoloButton onClick={() => setOpen(false)}>取消</HoloButton>
          <HoloButton variant="primary" loading={busy} onClick={submit}>
            登录
          </HoloButton>
        </div>
      </HoloModal>
    </>
  );
}
