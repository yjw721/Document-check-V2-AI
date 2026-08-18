# -*- coding: utf-8 -*-
"""DCS 变体 SCEL 真实文件 · 端到端上传验证（临时诊断脚本，可删除）"""
import io, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app import app

SCEL = r"C:\Users\86135\Downloads\财务会计 财会词汇大全【官方推荐】.scel"

client = TestClient(app)

with open(SCEL, "rb") as f:
    data = f.read()
print(f"文件大小: {len(data)} 字节 = 0x{len(data):X}")

# 1) 上传
with open(SCEL, "rb") as f:
    resp = client.post(
        "/api/template/upload",
        files=[("files", (os.path.basename(SCEL), f, "application/octet-stream"))],
        data={"category": "industry"},
    )
print("HTTP:", resp.status_code)
r = resp.json()

docs = r.get("docs", [])
entries = r.get("entries", [])
rules = r.get("rules", [])
print(f"docs: {len(docs)}  rules: {len(rules)}  entries(草案): {len(entries)}")

for d in docs:
    print("  doc:", d.get("name"), "| ok:", d.get("ok"), "| chunks:", d.get("chunks"),
          "| tags:", sorted(set(t for t in d.get("tags", []) if t)),
          "| error:", d.get("error"))

# 2) 词条内容与标签抽查
sample_terms = ["固定资产", "股东资金", "关联交易", "组织章程", "审计报告",
                "现金流量表", "长期借款", "盈余公积", "未分配利润", "资本公积"]
terms = [e.get("keyword") for e in entries]
termset = set(terms)
for t in sample_terms:
    print(f"  抽查 {t}: {'OK' if t in termset else 'MISS'}")

if entries:
    print("首条:", entries[0])
    print("末条:", entries[-1])
    from collections import Counter
    print("标签分布:", dict(Counter(e.get("tag") for e in entries)))

# 3) 导入链路（全部勾选词条）
entry_ids = [e["id"] for e in entries]
if entry_ids:
    s = client.post("/api/template/select", json={"rule_ids": [], "entry_ids": entry_ids})
    print("select:", s.status_code, s.json())
    imp = client.post("/api/template/import", json={"rule_ids": [], "entry_ids": entry_ids})
    print("import:", imp.status_code, imp.json())
