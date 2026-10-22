# -*- coding: utf-8 -*-
"""检索 / 问答使用记录（records.py）回归测试。

重点覆盖两类缺陷：
1. **赋值导致变量变局部**：``add_record`` / ``delete_record`` / ``clear_records``
   内部对 ``_cache`` 重新赋值，若不声明 ``global _cache``，先前对 ``_cache``
   的读取会抛 ``UnboundLocalError``，且被 ``except`` 静默吞掉 —— 表现为
   「接口返回正常但记录永远写不进去」。此处用多条断言锁死。
2. **列表省流量 / 详情给全文**：列表不含 ``result``，详情接口必须含。

隔离策略：把 ``RECORDS_PATH`` 改到项目内 ``.tmp_kbrecords/``，不污染真实文件。
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


from checkers.rag import records as r  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_DIR = os.path.join(ROOT, ".tmp_kbrecords")


def clean_start():
    """重置到干净状态（指向隔离文件 + 清空进程内缓存）。"""
    os.makedirs(TMP_DIR, exist_ok=True)
    r.RECORDS_PATH = os.path.join(TMP_DIR, "records.json")
    try:
        if os.path.isfile(r.RECORDS_PATH):
            os.remove(r.RECORDS_PATH)
    except OSError:
        pass
    r._cache = None


def main():
    clean_start()

    print("1) 写入与列表")
    rec_s = r.add_record("search", "重置成本法的适用前提", {"top_k": 5, "scope": "全库"},
                         {"hits_count": 2, "mode": "bm25", "top_file": "a.pdf"},
                         {"hits": [{"rank": 1, "file": "a.pdf"}]})
    rec_a = r.add_record("ask", "坏账准备计提比例是多少", {"top_k": 3, "scope": "全库"},
                         {"grounded": True, "confidence": 0.72, "sources_count": 3, "elapsed": 1.2},
                         {"answer": "依据资料……", "sources": [{"file": "a.pdf"}]})
    chk("add_record 返回记录（若缺 global _cache 会返回空 dict）", bool(rec_s) and bool(rec_a),
        f"got search={bool(rec_s)} ask={bool(rec_a)}")
    chk("记录 id 唯一", rec_s.get("id") != rec_a.get("id"), f"{rec_s.get('id')} vs {rec_a.get('id')}")
    chk("type 正确写入", rec_s.get("type") == "search" and rec_a.get("type") == "ask")

    lst = r.list_records(limit=10)
    chk("列出 2 条", lst["total"] == 2 and len(lst["items"]) == 2, f"total={lst['total']}")
    chk("列表不含 result（省流量）", all("result" not in it for it in lst["items"]))
    chk("列表含 summary", all("summary" in it for it in lst["items"]))
    chk("最新记录在最前", lst["items"][0]["id"] == rec_a.get("id"))

    print("2) 整体统计")
    st = r.stats()
    chk("统计 total=2", st["total"] == 2, f"{st}")
    chk("分类计数正确", st["ask"] == 1 and st["search"] == 1, f"ask={st['ask']} search={st['search']}")

    print("3) 持久化（重启缓存后仍在）")
    r._cache = None
    lst2 = r.list_records(limit=10)
    chk("缓存清空后仍从文件读出 2 条", lst2["total"] == 2, f"total={lst2['total']}")

    print("4) 详情")
    d = r.get_record(rec_a["id"])
    chk("详情 ok=True", d.get("ok") is True)
    chk("详情含完整 result", "result" in d and bool(d["result"].get("answer")))
    chk("详情找工作不到返回 ok=False", r.get_record("nonexistent").get("ok") is False)

    print("5) 筛选")
    chk("按类型筛选 search", r.list_records(rec_type="search", limit=10)["total"] == 1)
    chk("按类型筛选 ask", r.list_records(rec_type="ask", limit=10)["total"] == 1)
    chk("关键词命中 query", r.list_records(keyword="坏账", limit=10)["total"] == 1)
    chk("关键词无命中返回 0", r.list_records(keyword="zzz-not-exist", limit=10)["total"] == 0)

    print("6) 导出")
    md = r.export_records(fmt="md")
    chk("Markdown 导出含标题", "记录导出" in md["content"])
    chk("Markdown 含问答内容", "坏账准备计提比例是多少" in md["content"])
    chk("Markdown 含检索类型标记", "检索诊断" in md["content"])
    chk("Markdown 文件名正确", md["filename"] == "rag-records.md")
    js = r.export_records(fmt="json")
    chk("JSON 导出可解析且 2 条", len(__import__("json").loads(js["content"])) == 2)
    chk("JSON 文件名正确", js["filename"] == "rag-records.json")

    print("7) 逐条删除")
    r._cache = None
    dr = r.delete_record(rec_s["id"])
    chk("删除返回 ok 且计数 1", dr.get("ok") is True and dr.get("deleted") == 1, f"{dr}")
    chk("删除后仅剩 1 条", r.list_records(limit=10)["total"] == 1)
    chk("删除不存在的记录返回 ok=False", r.delete_record("nope").get("ok") is False)

    print("8) 整体清空")
    r.add_record("search", "临时检索词", {"top_k": 3}, {"hits_count": 0}, {"hits": []})
    chk("清空前共 2 条（1 ask + 1 search）", r.stats()["total"] == 2)
    cr = r.clear_records("search")
    chk("按类型清空返回 cleared_type=search", cr.get("cleared_type") == "search", f"{cr}")
    chk("按类型清空后仅剩 ask", r.stats()["ask"] == 1 and r.stats()["search"] == 0)
    r.clear_records()
    chk("清空全部后为 0", r.stats()["total"] == 0)

    print("9) 上限裁剪（直接触发 _cache = _cache[:MAX_RECORDS] 分支）")
    clean_start()
    orig_max = r.MAX_RECORDS
    try:
        r.MAX_RECORDS = 3
        for i in range(5):
            r.add_record("search", f"查询{i}", {"top_k": 3}, {"hits_count": 0}, {"hits": []})
        chk("超过上限只保留最新 3 条", r.stats()["total"] == 3, f"total={r.stats()['total']}")
        items = r.list_records(limit=10)["items"]
        chk("保留的是最新三条", [it["query"] for it in items] == ["查询4", "查询3", "查询2"],
            f"{[it['query'] for it in items]}")
    finally:
        r.MAX_RECORDS = orig_max

    print("10) 隔离目录确实产出文件")
    chk("records.json 已落盘", os.path.isfile(r.RECORDS_PATH))

    # 清理
    try:
        shutil.rmtree(TMP_DIR)
    except OSError:
        pass


if __name__ == "__main__":
    main()
    print(f"\n通过 {OK} / {OK + FAIL}")
    sys.exit(1 if FAIL else 0)
