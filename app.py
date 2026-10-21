# -*- coding: utf-8 -*-
"""
文档低级错误检查工具 —— FastAPI 后端（离线保密版）
=========================================================================
默认纯本地运行：仅监听 127.0.0.1，无任何外部网络请求、无遥测、无数据上传。
前端为 static/index.html（深色玻璃拟态单页应用），所有检测逻辑复用既有
checkers / config / reporting 模块。

多用户（单主机局域网共享）：
    config/settings.json -> security：
        access_token  访问口令（非空时局域网用户需输入口令才能使用）
    listen        "127.0.0.1"=仅本机（默认） / "0.0.0.0"=开放局域网
    开启 0.0.0.0 时程序自动放行 Windows 防火墙入站 TCP 8501（需管理员权限）。

启动：python app.py   （默认 http://127.0.0.1:8501）

代码结构：
    server/main.py        FastAPI 装配（CORS / 路由注册 / 静态资源）
    server/state.py       路径常量 + 工作区 / 扫描 / 任务内存态 + 并发闸门
    server/security.py    访问口令门禁 + /api/security/ping
    server/services/      业务执行层（检测执行 / AI 核验 / 缓存 / 序列化）
    server/routes/        按业务域拆分的 APIRouter 模块
"""

import os

from server.main import app  # noqa: F401  (供 uvicorn app:app 使用)
from config.settings_manager import load_settings


def _ensure_firewall_rule(port: int, name: str = "文档核验中心 8501") -> bool:
    """非回环监听时，尽力自动放行 Windows 防火墙入站 TCP 端口（需管理员权限）。"""
    try:
        import subprocess

        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if f"name={name}" in (r.stdout or "") or f"name = {name}" in (r.stdout or ""):
            return True
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={name}", "dir=in", "action=allow", "protocol=TCP",
             f"localport={port}", "profile=private,domain"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001 - 防火墙配置失败仅提示，不阻塞启动
        return False


if __name__ == "__main__":
    import uvicorn

    _settings = load_settings()
    _sec = _settings.get("security") or {}
    # 监听地址优先级：环境变量 DOCCHECK_HOST > settings.security.listen > 默认仅本机
    host = (os.environ.get("DOCCHECK_HOST", "") or str(_sec.get("listen", "127.0.0.1")) or "127.0.0.1").strip()
    if host in ("*", ""):
        host = "0.0.0.0"
    _is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if not _is_loopback and _ensure_firewall_rule(8501):
        print(f"[防火墙] 已放行入站 TCP 8501（监听 {host}，局域网多用户访问）")
    uvicorn.run(app, host=host, port=8501)
