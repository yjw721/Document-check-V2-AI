# -*- coding: utf-8 -*-
"""验证：zipskill 标准技能包导入（Agent Skills 开放标准 SKILL.md → 本系统 AiSkill）。

覆盖：frontmatter 拆分、字段映射（name→id、metadata 扩展→file_types/severity、正文→prompt）、
      非法字段消毒、错误分支，以及 POST /api/ai/skills/import 的 ZIP 端到端解析与防护。
"""
import asyncio
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server.routes.ai_config import (  # noqa: E402
    _split_frontmatter,
    _parse_skill_md,
    api_ai_skills_import,
)
from fastapi import UploadFile  # noqa: E402

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


# --- 1) frontmatter 拆分 ---
y, b = _split_frontmatter("---\nname: pdf-processing\ndescription: 提取PDF\n---\n这是指令正文\n")
chk("拆出 yaml frontmatter", bool(y) and "name: pdf-processing" in y, y)
chk("拆出 Markdown 正文", bool(b) and "这是指令正文" in b, b)
chk("无 frontmatter 返回 (None,None)", _split_frontmatter("#只有正文\n") == (None, None))

# --- 2) 标准 SKILL.md（slug name）解析 ---
s1, e1 = _parse_skill_md(
    "pdf-processing/SKILL.md",
    "---\nname: pdf-processing\ndescription: Extract PDF text. Use when handling PDFs.\n---\n额外重点核查：PDF 表单字段。\n",
)
chk("标准包解析无错误", e1 is None, e1)
chk("slug name → id", bool(s1) and s1["id"] == "pdf-processing", s1)
chk("正文 → prompt", bool(s1) and s1["prompt"] == "额外重点核查：PDF 表单字段。", s1)
chk("缺省 file_types=all", bool(s1) and s1["file_types"] == "all", s1)
chk("缺省 severity 为空", bool(s1) and s1["default_severity"] == "", s1)
chk("description 取 frontmatter", bool(s1) and s1["description"].startswith("Extract PDF"), s1)

# --- 3) metadata 扩展字段（display-name / file-types / default-severity） ---
s2, e2 = _parse_skill_md(
    "finance-hookup/SKILL.md",
    "---\nname: finance-hookup\ndescription: 勾稽核对\nmetadata:\n"
    "  display-name: 财务勾稽\n  file-types: doc,xls\n  default-severity: high\n---\n勾稽指令。\n",
)
chk("metadata 扩展解析无错误", e2 is None, e2)
chk("display-name → 显示名", bool(s2) and s2["name"] == "财务勾稽", s2)
chk("file-types → file_types", bool(s2) and s2["file_types"] == "doc,xls", s2)
chk("default-severity → severity", bool(s2) and s2["default_severity"] == "high", s2)

# --- 4) 中文 name（非 slug）：作为显示名 + 生成 id ---
s3, e3 = _parse_skill_md("合同/SKILL.md", "---\nname: 合同风险\ndescription: 合同核查\n---\n合同指令。\n")
chk("中文 name 解析无错误", e3 is None, e3)
chk("中文 name → 显示名", bool(s3) and s3["name"] == "合同风险", s3)
chk("中文 name 生成非空 id", bool(s3) and bool(s3["id"]), s3)
chk("中文包 prompt=正文", bool(s3) and s3["prompt"] == "合同指令。", s3)

# --- 5) 非法字段消毒 ---
s4, e4 = _parse_skill_md(
    "a/SKILL.md",
    "---\nname: a\ndescription: d\nmetadata:\n  file-types: xyz\n  default-severity: critical\n---\nbody\n",
)
chk("非法 file-types 归一为 all", bool(s4) and s4["file_types"] == "all", s4)
chk("非法 severity 清空", bool(s4) and s4["default_severity"] == "", s4)

# --- 6) 错误分支 ---
_, errA = _parse_skill_md("x/SKILL.md", "只有正文没有 frontmatter")
chk("缺 frontmatter 报错", bool(errA) and "frontmatter" in errA, errA)
_, errB = _parse_skill_md("x/SKILL.md", "---\nname: x\ndescription: d\n---\n   \n")
chk("正文为空报错", bool(errB) and "正文" in errB, errB)


# --- 7) ZIP 端点端到端 ---
def build_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


zip_bytes = build_zip({
    "pack/pdf-processing/SKILL.md": "---\nname: pdf-processing\ndescription: PDF 提取\n---\nPDF指令。\n",
    "pack/合同/SKILL.md": "---\nname: 合同风险\ndescription: 合同核查\n"
                          "metadata:\n  display-name: 合同风险点\n  default-severity: high\n---\n合同指令。\n",
    "__MACOSX/SKILL.md": "---\nname: junk\ndescription: x\n---\njunk\n",  # 应跳过
    "pack/README.md": "# 说明",                                            # 应忽略
})

resp = asyncio.run(api_ai_skills_import(UploadFile(file=io.BytesIO(zip_bytes), filename="pack.zip")))
chk("ZIP 端点 ok", resp.get("ok") is True, resp.get("message"))
chk("解析到 2 个技能", resp.get("count") == 2, resp.get("count"))
ids = {s["id"] for s in resp.get("skills", [])}
names = {s["name"] for s in resp.get("skills", [])}
chk("含 slug id pdf-processing", "pdf-processing" in ids, ids)
chk("__MACOSX 的 SKILL.md 已跳过", "junk" not in ids, ids)
chk("含 metadata 中文显示名「合同风险点」", "合同风险点" in names, names)
contract = next((s for s in resp.get("skills", []) if s["name"] == "合同风险点"), None)
chk("合同技能 default-severity=high", bool(contract) and contract.get("default_severity") == "high", contract)
chk("合同技能正文为 prompt", bool(contract) and contract.get("prompt") == "合同指令。", contract)

# 非 ZIP
r_nf = asyncio.run(api_ai_skills_import(UploadFile(file=io.BytesIO(b"not a zip"), filename="a.txt")))
chk("非 ZIP 返回失败", r_nf.get("ok") is False and r_nf.get("count") == 0, r_nf.get("message"))

# 无 SKILL.md 的 ZIP
r_es = asyncio.run(api_ai_skills_import(UploadFile(file=io.BytesIO(build_zip({"pack/README.md": "# hi"})), filename="e.zip")))
chk("无 SKILL.md 返回失败", r_es.get("ok") is False, r_es.get("message"))

# 空文件
r_empty = asyncio.run(api_ai_skills_import(UploadFile(file=io.BytesIO(b""), filename="x.zip")))
chk("空文件返回失败", r_empty.get("ok") is False, r_empty.get("message"))

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
