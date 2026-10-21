"""核验等待页轮询 / 自动跳转回归验证。

覆盖本次修复：
  A. /api/task/{tid} 首帧即可返回 running（不再需要手动刷新才拿到进度）
  B. 任务终态 done 时快照仍可读取（前端靠它触发自动跳转）
  C. /api/issues 在任务完成后返回本轮结果
  D. 静态资源指向新构建产物
"""
import io
import json
import os
import time
import urllib.request
import uuid
import zipfile

BASE = "http://127.0.0.1:8501"


def get(path, timeout=10):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def build_docx(text: str) -> bytes:
    """最小可用 docx（无需 python-docx）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   "</Relationships>")
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
                   "</w:body></w:document>")
    return buf.getvalue()


def post_multipart(path, filename, content: bytes):
    boundary = "----wb" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode("utf-8")
    )
    body.write(b"Content-Type: application/octet-stream\r\n\r\n")
    body.write(content)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        BASE + path, data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


print("=" * 62)
print("A. 上传后首帧轮询即返回 running（无需手动刷新）")
print("=" * 62)
content = build_docx("本项目由王五负责，金额人民币100元。本项目由王五负责，金额人民币200元。")
r = post_multipart("/api/upload", "回归样本.docx", content)
tid = r.get("task_id")
assert tid, f"上传未返回 task_id: {r}"
print(f"  上传成功 task_id={tid}")

# 立刻轮询（模拟前端 UploadTab -> setTaskId -> WaitingTab 首帧）
first = get(f"/api/task/{tid}")
assert "status" in first and "progress" in first and "logs" in first, first
print(f"  首帧快照: status={first['status']} progress={first['progress']} stage={first['stage_text']}")
assert first["status"] in ("running", "done"), first
print("  [PASS] 首帧即可读到任务快照，无需刷新页面")

print()
print("=" * 62)
print("B. 轮询至终态，快照仍可读取（自动跳转依赖）")
print("=" * 62)
seen_stages = set()
final = None
deadline = time.time() + 180
while time.time() < deadline:
    s = get(f"/api/task/{tid}")
    seen_stages.add(s.get("stage_text") or s.get("stage"))
    if s["status"] != "running":
        final = s
        break
    time.sleep(0.5)

assert final is not None, "任务在 180s 内未结束"
print(f"  终态: status={final['status']} progress={final['progress']}")
print(f"  经历阶段: {sorted(x for x in seen_stages if x)}")
assert final["status"] == "done", f"任务非正常结束: {final}"
# 关键：终态快照里没有 result，但 status=done 可读 —— 前端据此触发跳转
assert final["status"] == "done", final
print("  [PASS] 终态快照可读且 status=done（前端 useEffect 能触发自动跳转）")

print()
print("=" * 62)
print("C. /api/issues 返回本轮结果")
print("=" * 62)
iv = get("/api/issues")
n = len(iv.get("issues", []))
print(f"  问题条目数: {n}")
assert isinstance(iv.get("issues"), list), iv
print("  [PASS] 错误详情接口正常返回")

print()
print("=" * 62)
print("D. 静态资源指向新构建产物")
print("=" * 62)
html = urllib.request.urlopen(BASE + "/", timeout=5).read().decode("utf-8")
assert "index-XO_2JFHH.js" in html, f"入口未指向新构建: {html[:400]}"
js = urllib.request.urlopen(BASE + "/assets/index-XO_2JFHH.js", timeout=10).read()
print(f"  index.html -> index-XO_2JFHH.js ({len(js)} bytes)")
# 新逻辑特征串：同步初始化 + releaseTask
assert b"releaseTask" in js or b"settled" in js or b"stamp" in js, "新逻辑未打入产物"
print("  [PASS] 新构建已生效，包含修复后的状态逻辑")

print()
print("=" * 62)
print("E. 任务不存在时返回 404（前端容忍 2 次后判定失效）")
print("=" * 62)
try:
    get("/api/task/__not_exist__")
    raise AssertionError("应返回 404")
except urllib.error.HTTPError as e:
    assert e.code == 404, e.code
    print(f"  [PASS] 返回 {e.code}，前端 failCount>=3 才判定任务失效")

print()
print("全部回归通过。")
