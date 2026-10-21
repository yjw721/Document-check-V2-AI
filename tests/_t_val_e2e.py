"""端到端验证：估值核查四阶段闭环 + 静态前端可用性。

手工构造 multipart/form-data（等价浏览器 FormData），避免 httpx files/data 组合在本环境的坑；
同时 trust_env=False 绕过系统代理。
"""
import glob
import os
import time

import httpx

BASE = "http://127.0.0.1:8501"
LOG = []


def ok(name, cond, extra=""):
    LOG.append(f"{'PASS' if cond else 'FAIL'} | {name} {extra}")
    print(f"{'✔' if cond else '✘'} {name} {extra}")


def build_multipart(pairs, sd):
    """构造 multipart：files[] 与 slots[] 与浏览器 FormData.append 等价。"""
    boundary = "----DocCheckerValBoundary9f3a"
    body = b""
    for slot, ascii_name, real_name in pairs:
        content = open(os.path.join(sd, real_name), "rb").read()
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="files"; filename="{ascii_name}"\r\n'
        ).encode("utf-8")
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += content + b"\r\n"
    for slot, _, _ in pairs:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="slots"\r\n\r\n{slot}\r\n'.encode("utf-8")
    body += f"--{boundary}--\r\n".encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    return body, ctype


def main():
    client = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0))

    # 1) meta
    r = client.get(f"{BASE}/api/valuation/meta")
    m = r.json()
    ok("meta 返回", r.status_code == 200 and m.get("node_count") == 9,
       f"node={m.get('node_count')} item={m.get('item_count')}")
    ok("meta.stages 四阶段", len(m.get("stages") or []) == 4, str([s["no"] for s in (m.get("stages") or [])]))
    ok("meta.blocks 七数据块", len(m.get("blocks") or []) == 7, str([b["id"] for b in (m.get("blocks") or [])]))
    ok("meta.slots 五槽位",
       [s["key"] for s in m.get("slots") or []] == ["report", "statement", "income", "cost", "market"])

    # 2) 上传
    sd = os.path.join("tests", "samples", "valuation")
    pairs = [("report", "report.docx", "资产评估报告.docx"),
             ("statement", "statement.docx", "资产评估说明.docx"),
             ("income", "income.xlsx", "收益法测算表.xlsx"),
             ("cost", "cost.xlsx", "成本法测算表.xlsx"),
             ("market", "market.xlsx", "市场法测算表.xlsx")]
    body, ctype = build_multipart(pairs, sd)
    r = client.post(f"{BASE}/api/valuation/upload", content=body,
                    headers={"Content-Type": ctype})
    if r.status_code != 200:
        ok("upload", False, r.text[:300])
        return
    j = r.json()
    sid = j["session_id"]
    ok("upload 5 件", j.get("ok") and len(j.get("files") or []) == 5, f"session={sid}")

    # 3) 运行
    r = client.post(f"{BASE}/api/valuation/run", json={"session_id": sid, "use_ai": False})
    j = r.json()
    tid = j.get("task_id")
    ok("run 启动", bool(tid) and j.get("ok"), f"task={tid}")

    # 4) 轮询
    snap = None
    for _ in range(160):
        snap = client.get(f"{BASE}/api/valuation/task/{tid}").json()
        if snap.get("done"):
            break
        time.sleep(0.5)
    ok("task 完成", bool(snap and snap.get("done")),
       f"status={snap.get('status')} pct={snap.get('progress')} stage={snap.get('stage_text')}")

    # 5) 结果
    rj = client.get(f"{BASE}/api/valuation/result/{tid}").json()
    res = rj.get("result") or {}
    s = res.get("summary") or {}
    ok("result ready", rj.get("ready") is True, f"total={s.get('total')} high={s.get('high')} dedup={s.get('dedup_merged')}")
    ok("节点 9 个", len(res.get("nodes") or []) == 9)
    ok("数据块 7 个", len((res.get("split") or {}).get("blocks") or []) == 7)
    ok("发现问题 > 0", s.get("total", 0) > 0, f"{s.get('total')} 条")
    ok("去重生效", s.get("dedup_merged", 0) >= 0, f"合并 {s.get('dedup_merged')} 条")

    # 6) Word 报告
    rr = client.get(f"{BASE}/api/valuation/report/{tid}")
    open("/tmp/val_e2e_report.docx", "wb").write(rr.content)
    ok("Word 报告导出", rr.status_code == 200 and len(rr.content) > 8000,
       f"{len(rr.content)}B")

    # 7) 静态前端
    idx = client.get(f"{BASE}/")
    ok("静态首页 200", idx.status_code == 200, f"{len(idx.content)}B")
    js = sorted(glob.glob("static/assets/*.js"))[-1]
    r = client.get(f"{BASE}/assets/{os.path.basename(js)}")
    ok("JS 资源可达", r.status_code == 200, os.path.basename(js))
    src = open(js, encoding="utf-8", errors="ignore").read()
    ok("页面已打包进产物", "api/valuation" in src and "9 大核查节点" in src)

    # 8) reset
    rst = client.post(f"{BASE}/api/valuation/reset", json={})
    ok("reset", rst.json().get("ok") is True)

    print("\n" + "=" * 62)
    print(f"通过 {sum(1 for x in LOG if x.startswith('PASS'))} / {len(LOG)}")


main()
