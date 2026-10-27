# -*- coding: utf-8 -*-
"""草稿箱鉴权回归：操作员（未提权）应可采纳 / 丢弃草稿、可发起 AI 生成；
仅真正的「后台配置写」接口（如 /api/ai/test）才需管理员口令。

防止 regressions：曾因管理员写保护前缀误用 `/api/ai/`（过宽），
导致 /api/ai/drafts/{id}/accept、/api/ai/build/* 等操作员生产动作被 401 拦截。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from fastapi.testclient import TestClient
from checkers import datastore
import server.routes.ai_drafts as ad

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def setup_store():
    """把数据层指向临时文件，并在其中植入一条草稿，避免污染真实 config。"""
    store = tempfile.mktemp(suffix=".json")
    datastore.STORE_PATH = store
    did = datastore.add_draft(
        "b_regression", "dialogue",
        {"rules": [{"name": "r1", "match_mode": "keyword", "pattern": "测试", "severity": "medium", "suggestion": ""}],
         "wordbanks": [{"name": "w1", "entries": [{"keyword": "k1"}]}]},
        "ai_dialogue", "AI对话创建", {"generated_rules": 1, "filtered_rules": 0}, "demo.docx",
    )
    # accept/reject 不应写入真实规则库 / 词库
    empty = {"version": 1, "groups": []}
    ad.load_custom_rules = lambda: dict(empty)
    ad.save_custom_rules = lambda d: True
    ad.load_wordbanks = lambda: dict(empty)
    ad.save_wordbanks = lambda d: True
    return did


def main():
    from server.main import app

    did = setup_store()
    client = TestClient(app)

    # 1) 草稿读取：所有已认证用户可读
    r = client.get("/api/ai/drafts")
    check("GET /api/ai/drafts 200（操作员可读）", r.status_code == 200, str(r.status_code))

    # 2) 草稿采纳：操作员（不携带 X-Admin-Token）应可成功
    r = client.post(f"/api/ai/drafts/{did}/accept")
    check("POST /api/ai/drafts/{id}/accept 200（无需管理员口令）", r.status_code == 200, str(r.status_code))
    check("采纳返回 ok=true", (r.json() or {}).get("ok") is True)

    # 3) 草稿丢弃（新草稿）：同样无需管理员口令
    did2 = setup_store()
    r = client.post(f"/api/ai/drafts/{did2}/reject")
    check("POST /api/ai/drafts/{id}/reject 200（无需管理员口令）", r.status_code == 200, str(r.status_code))

    # 4) AI 生成：操作员可发起（不应被管理员令牌拦截）
    r = client.post("/api/ai/build/dialogue", json={"text": "生成一条规则"})
    check("POST /api/ai/build/dialogue 非 401（操作员可生成）", r.status_code != 401, str(r.status_code))

    # 5) 真正的后台配置写：仍需管理员口令（无 token 应 401）
    r = client.post("/api/ai/test", json={})
    check("POST /api/ai/test 401（后台配置写需管理员口令）", r.status_code == 401, str(r.status_code))
    check("401 带 admin_required 标记", (r.json() or {}).get("admin_required") is True)

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
