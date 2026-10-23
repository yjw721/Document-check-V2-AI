# -*- coding: utf-8 -*-
"""检索记录 HTTP 接口权限回归测试。

验证服务端强制点，而不仅是前端按钮：
- viewer / operator 可查看、导出记录，但不能删除或清空；
- admin 可以删除单条与清空全部；
- 记录删除不能再用 kb.search 越权。

隔离策略：记录文件指向 ``.tmp_kbrecords_api/``，角色通过 patch
``checkers.rag.permissions`` 模拟，不修改真实设置与知识库。
"""
import os
import shutil
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

from checkers.rag import records as rec  # noqa: E402
from checkers.rag import permissions  # noqa: E402
from server.main import app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_DIR = os.path.join(ROOT, ".tmp_kbrecords_api")


def setup_records():
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    rec.RECORDS_PATH = os.path.join(TMP_DIR, "records.json")
    rec._cache = None
    rec._seq = 0


def as_role(role):
    caps = permissions.DEFAULT_CAPS[role]
    permissions.current_role = lambda: role
    permissions.caps_of = lambda _role: list(caps)


def main():
    setup_records()
    orig_role, orig_caps = permissions.current_role, permissions.caps_of
    client = TestClient(app)
    try:
        rec.add_record("search", "记录权限测试", {"top_k": 3}, {"hits_count": 1}, {"hits": []})
        rid = rec.list_records(limit=1)["items"][0]["id"]

        print("1) viewer 只读")
        as_role("viewer")
        r = client.get("/api/rag/records")
        chk("viewer 可列出记录", r.status_code == 200 and r.json().get("total") == 1,
            f"status={r.status_code}")
        r = client.get("/api/rag/records/export", params={"fmt": "json"})
        chk("viewer 可导出记录", r.status_code == 200 and r.json().get("ok") is True,
            f"status={r.status_code}")
        r = client.get(f"/api/rag/records/{rid}")
        chk("viewer 可看详情", r.status_code == 200 and r.json().get("ok") is True,
            f"status={r.status_code}")
        r = client.delete(f"/api/rag/records/{rid}")
        chk("viewer 删除单条被 403 拒绝", r.status_code == 403, f"status={r.status_code}")
        r = client.delete("/api/rag/records")
        chk("viewer 清空被 403 拒绝", r.status_code == 403, f"status={r.status_code}")
        chk("viewer 越权后记录仍存在", rec.stats()["total"] == 1)

        print("2) operator 仍不可删除")
        as_role("operator")
        r = client.delete(f"/api/rag/records/{rid}")
        chk("operator 删除单条被 403 拒绝", r.status_code == 403, f"status={r.status_code}")
        r = client.delete("/api/rag/records")
        chk("operator 清空被 403 拒绝", r.status_code == 403, f"status={r.status_code}")
        chk("operator 越权后记录仍存在", rec.stats()["total"] == 1)

        print("3) admin 可删除 / 清空")
        as_role("admin")
        r = client.delete(f"/api/rag/records/{rid}")
        chk("admin 删除单条成功", r.status_code == 200 and r.json().get("deleted") == 1,
            f"status={r.status_code} body={r.text[:120]}")
        chk("单条删除后列表为空", rec.stats()["total"] == 0)

        rec.add_record("ask", "清空测试", {"top_k": 3}, {"grounded": True}, {"answer": "x"})
        rec.add_record("search", "清空测试 2", {"top_k": 3}, {"hits_count": 0}, {"hits": []})
        r = client.delete("/api/rag/records")
        chk("admin 清空全部成功", r.status_code == 200 and r.json().get("ok") is True,
            f"status={r.status_code}")
        chk("清空后列表为空", rec.stats()["total"] == 0)
    finally:
        permissions.current_role, permissions.caps_of = orig_role, orig_caps
        rec._cache = None
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    print(f"\n记录接口权限测试：通过 {OK} / 失败 {FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
