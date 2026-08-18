# -*- coding: utf-8 -*-
"""范本解析模块端到端 API 验证（本地 127.0.0.1，不触碰外网）。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8501"


def req(method, path, body=None, files=None):
    if files:
        boundary = "----wb" + "x" * 20
        chunks = []
        for name, fname, data in files:
            head = ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n") % (boundary, name, fname)
            chunks.append(head.encode("utf-8"))
            chunks.append(data)
            chunks.append(b"\r\n")
        chunks.append(("--%s--\r\n" % boundary).encode("utf-8"))
        data = b"".join(chunks)
        req_obj = urllib.request.Request(BASE + path, data=data, method="POST")
        req_obj.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    else:
        req_obj = urllib.request.Request(BASE + path, method=method)
        if body is not None:
            req_obj.add_header("Content-Type", "application/json")
            req_obj.data = json.dumps(body).encode("utf-8")
    with urllib.request.urlopen(req_obj, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) 上传范本（2 docx + 1 pdf）
files = [
    ("files", "template_report.docx", open("tests/_tpl/template_report.docx", "rb").read()),
    ("files", "template_standard.docx", open("tests/_tpl/template_standard.docx", "rb").read()),
    ("files", "sample_pdf_bad.pdf", open("tests/samples/sample_pdf_bad.pdf", "rb").read()),
]
draft = req("POST", "/api/template/upload", files=files)
print("== 上传解析 ==")
print("docs:", [(d["name"], d["ok"], d["chunks"]) for d in draft["docs"]])
print("rules:", len(draft["rules"]), "entries:", len(draft["entries"]),
      "conflicts:", len(draft["conflicts"]), "refs:", len(draft["references"]))
from collections import Counter
print("rule tags:", dict(Counter(r["tag"] for r in draft["rules"])))
print("entry tags:", dict(Counter(e["tag"] for e in draft["entries"])))
assert draft["docs"] and all(d["ok"] for d in draft["docs"]), "部分范本解析失败"
assert len(draft["rules"]) > 0 and len(draft["entries"]) > 0, "未生成规则/词条"
assert any(r["tag"] == "执业风险警示" for r in draft["rules"]), "缺少执业风险警示规则"
assert any(e["tag"] == "执业风险警示" for e in draft["entries"]), "缺少执业风险警示词条"
assert draft["conflicts"], "未检测到术语冲突"

# 展示关键样本
print("\n== 规则样本 ==")
for r in draft["rules"][:5]:
    print(" -", r["tag"], "|", r["name"], "|", r["match_mode"], "|", r["pattern"][:60])
print("\n== 冲突样本 ==")
for c in draft["conflicts"][:3]:
    print(" -", c["topic"], "->", c["suggestion"][:80])

# 2) 勾选：规则全选，词条选一半
rule_ids = [r["id"] for r in draft["rules"]]
entry_ids = [e["id"] for e in draft["entries"][::2]]
req("POST", "/api/template/select", {"rule_ids": rule_ids, "entry_ids": entry_ids})

# 3) 导入
imp = req("POST", "/api/template/import", {"rule_ids": rule_ids, "entry_ids": entry_ids})
print("\n== 导入 ==", imp)
assert imp["imported_rules"] == len(rule_ids)
assert imp["imported_entries"] == len(entry_ids)

# 4) 校验自定义规则 / 词库已追加（不覆盖）
cr = req("GET", "/api/custom_rules")
wb = req("GET", "/api/wordbanks")
tp_groups = [g for g in cr["groups"] if g["name"].startswith("范本生成")]
tp_wb = [g for g in wb["groups"] if g["name"].startswith("范本生成")]
print("\n追加的自定义规则组:", [(g["name"], len(g["rules"])) for g in tp_groups])
print("追加的词库组:", [(g["name"], len(g["entries"])) for g in tp_wb])
assert tp_groups and tp_groups[0]["rules"][0]["tag"], "规则组缺少 tag"
assert all("tag" in r for g in tp_groups for r in g["rules"]), "部分规则缺少 tag"
print("\nE2E 范本解析链路验证通过 ✔")
