# -*- coding: utf-8 -*-
"""设置 / AI 配置敏感字段脱敏回归测试。

覆盖：
- GET 设置与 AI 状态不再回传真实 API Key / 访问口令；
- 前端回传 ``********`` 时保存不会覆盖真实密钥；
- 输入新密钥 / 清空密钥时按预期更新；
- AI 连接测试遇到掩码时还原真实密钥。

隔离策略：patch 路由模块的 ``load_settings`` / ``save_settings`` /
``ai_test_connection``，不读写真实 config/settings.json。
"""
import copy
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

from config.settings_manager import (  # noqa: E402
    SECRET_MASK, mask_secrets, merge_settings_patch,
)
from server import security  # noqa: E402
from server.main import app  # noqa: E402
from server.routes import ai_config, settings as settings_route  # noqa: E402

FAKE = {
    "ui": {"page_size": 20},
    "role": "admin",
    "ai": {"enabled": True, "api_key": "sk-real-secret", "model": "qwen3:8b"},
    "security": {"access_token": "local-token", "listen": "127.0.0.1"},
}


def test_helpers():
    print("1) 脱敏 / 合并辅助函数")
    masked = mask_secrets(FAKE)
    chk("API Key 被替换为掩码", masked["ai"]["api_key"] == SECRET_MASK)
    chk("访问口令被替换为掩码", masked["security"]["access_token"] == SECRET_MASK)
    chk("脱敏不修改原配置", FAKE["ai"]["api_key"] == "sk-real-secret")

    merged = merge_settings_patch(FAKE, {"ai": {"api_key": SECRET_MASK, "model": "new"}})
    chk("掩码保存保留原 API Key", merged["ai"]["api_key"] == "sk-real-secret")
    chk("非敏感字段正常更新", merged["ai"]["model"] == "new")
    chk("未提交字段保持原值", merged["security"]["access_token"] == "local-token")

    merged = merge_settings_patch(FAKE, {"ai": {"api_key": "sk-new"}})
    chk("输入新 API Key 时更新", merged["ai"]["api_key"] == "sk-new")
    merged = merge_settings_patch(FAKE, {"security": {"access_token": ""}})
    chk("显式清空访问口令时更新为空", merged["security"]["access_token"] == "")


def test_api():
    fake = copy.deepcopy(FAKE)
    saved = []

    def fake_load():
        return copy.deepcopy(fake)

    def fake_save(data):
        saved.append(copy.deepcopy(data))
        return True

    def fake_test(cfg):
        saved.append({"__test_cfg__": copy.deepcopy(cfg)})
        return True, "ok"

    orig = (settings_route.load_settings, settings_route.save_settings,
            ai_config.load_settings, ai_config.ai_test_connection,
            security.access_token)
    settings_route.load_settings = fake_load
    settings_route.save_settings = fake_save
    ai_config.load_settings = fake_load
    ai_config.ai_test_connection = fake_test
    security.access_token = lambda: ""
    client = TestClient(app)
    try:
        print("2) HTTP 接口")
        r = client.get("/api/settings")
        body = r.json()
        chk("GET /api/settings 不泄露 API Key",
            r.status_code == 200 and body["ai"]["api_key"] == SECRET_MASK,
            f"status={r.status_code}")
        chk("GET /api/settings 不泄露访问口令",
            body["security"]["access_token"] == SECRET_MASK)

        r = client.get("/api/ai/status")
        chk("GET /api/ai/status 不泄露 API Key",
            r.status_code == 200 and r.json()["ai"]["api_key"] == SECRET_MASK,
            f"status={r.status_code}")

        r = client.post("/api/settings", json={"ai": {"api_key": SECRET_MASK, "model": "new-model"}})
        chk("保存掩码设置成功", r.status_code == 200 and r.json().get("ok") is True,
            f"status={r.status_code}")
        last = saved[-1]
        chk("保存掩码时后端保留真实 API Key", last["ai"]["api_key"] == "sk-real-secret")
        chk("保存掩码时其他字段正常落盘", last["ai"]["model"] == "new-model")

        r = client.post("/api/settings", json={"ai": {"api_key": "sk-new"}})
        chk("保存新 API Key 成功", r.status_code == 200 and saved[-1]["ai"]["api_key"] == "sk-new",
            f"status={r.status_code}")

        r = client.post("/api/ai/test", json={"api_key": SECRET_MASK, "model": "qwen3:8b"})
        test_cfg = saved[-1].get("__test_cfg__") or {}
        chk("AI 连接测试成功", r.status_code == 200 and r.json().get("ok") is True,
            f"status={r.status_code}")
        chk("AI 测试遇到掩码时使用真实 API Key", test_cfg.get("api_key") == "sk-real-secret")

        print("3) 非管理员字段级保护")
        fake["role"] = "operator"
        r = client.post("/api/settings", json={"ai": {"api_key": "sk-evil"}})
        chk("非管理员改 AI Key 被 403 拒绝", r.status_code == 403, f"status={r.status_code}")
        r = client.post("/api/settings", json={"role": "admin"})
        chk("非管理员提权被 403 拒绝", r.status_code == 403, f"status={r.status_code}")
        r = client.post("/api/settings", json={"ai": {"api_key": SECRET_MASK}})
        chk("非管理员回传未改动掩码可正常保存", r.status_code == 200, f"status={r.status_code}")
        r = client.post("/api/settings", json={"ui": {"page_size": 30}})
        chk("非管理员仍可保存普通偏好", r.status_code == 200 and saved[-1]["ui"]["page_size"] == 30,
            f"status={r.status_code}")
    finally:
        (settings_route.load_settings, settings_route.save_settings,
         ai_config.load_settings, ai_config.ai_test_connection,
         security.access_token) = orig


def main():
    test_helpers()
    test_api()
    print(f"\n敏感字段脱敏测试：通过 {OK} / 失败 {FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
