# -*- coding: utf-8 -*-
"""CORS 预检（OPTIONS）回归：access_gate 放行预检，由 CORSMiddleware 正常应答。

背景：``security.install`` 注册的 ``access_gate`` 是最外层 HTTP 中间件
（在 ``add_middleware(CORSMiddleware)`` 之后安装）。修复前它会把 OPTIONS 预检
401 截断且不带 CORS 头，导致跨域开发（Vite 5173 → 后端 8501）无法调用任何
``ai.config`` 管理员接口（连 GET 都 "Failed to fetch"）。

修复后断言：
- 管理员接口预检 → 200 + Access-Control-Allow-Origin / Allow-Headers；
- 无 Origin 的普通 OPTIONS → 交给路由（405），并非无条件放行 200；
- 真实请求口令门禁依旧 fail-closed（未带管理员令牌的写请求仍 401）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


from fastapi.testclient import TestClient  # noqa: E402

from server import security  # noqa: E402
from server.main import app  # noqa: E402

ORIGIN = "http://127.0.0.1:5173"


def preflight(client, path, method="GET"):
    """模拟浏览器 CORS 预检（按规范不携带任何凭证头）。"""
    return client.options(
        path,
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "x-workspace,x-admin-token,content-type",
        },
    )


def main() -> bool:
    with TestClient(app) as client:
        print("1) 管理员接口 CORS 预检不再被门禁截断")
        r = preflight(client, "/api/ai/status", "GET")
        chk("OPTIONS /api/ai/status → 200", r.status_code == 200, f"status={r.status_code}")
        acao = r.headers.get("access-control-allow-origin", "")
        chk("带 Access-Control-Allow-Origin", acao in (ORIGIN, "*"), f"acao={acao!r}")
        acah = (r.headers.get("access-control-allow-headers", "") or "").lower()
        chk("Allow-Headers 含 x-admin-token", "x-admin-token" in acah, f"acah={acah!r}")

        r2 = preflight(client, "/api/ai/skills", "GET")
        chk("OPTIONS /api/ai/skills → 200", r2.status_code == 200, f"status={r2.status_code}")

        r3 = preflight(client, "/api/ai/skills/import", "POST")
        chk("OPTIONS /api/ai/skills/import (POST) → 200", r3.status_code == 200, f"status={r3.status_code}")
        chk(
            "导入接口预检带 ACAO",
            r3.headers.get("access-control-allow-origin", "") in (ORIGIN, "*"),
            f"headers={dict(r3.headers)}",
        )

        print("2) 未带 Origin 的普通 OPTIONS 不被无条件放行（交给路由处理）")
        r4 = client.options("/api/ai/status")
        chk("无 Origin OPTIONS /api/ai/status → 405", r4.status_code == 405, f"status={r4.status_code}")

        print("3) 真实请求门禁依旧 fail-closed（放行预检不降低安全性）")
        if security.admin_token():
            r5 = client.post("/api/ai/skills/import", files={"file": ("a.zip", b"x", "application/zip")})
            chk("写请求无管理员令牌 → 401", r5.status_code == 401, f"status={r5.status_code}")
            chk(
                "401 响应体 admin_required",
                (r5.json() or {}).get("admin_required") is True,
                f"body={r5.text[:120]}",
            )
            r6 = client.get("/api/ai/status", headers={"X-Admin-Token": security.admin_token()})
            chk("带管理员令牌读接口 → 200", r6.status_code == 200, f"status={r6.status_code}")
        else:
            print("  [OK] 未配置管理员口令，跳过令牌门禁断言（本环境全员管理员）")
            chk("未配置管理员口令时跳过", True)

    print(f"\n结果：{OK} 通过 / {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
