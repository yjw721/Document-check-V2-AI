# -*- coding: utf-8 -*-
"""集成验证：本地知识库（RAG）检索内核与核验联动。

覆盖：
    1. 多格式入库与按标题层级切片（含章节路径、块间重叠）
    2. 稀疏 / 稠密双路 RRF 融合检索（离线：稠密路强制关闭）
    3. 增量索引：内容未变的文档复用旧索引，不触发重嵌
    4. 核验联动：逐片段检索并回传「知识依据」（Issue.evidence）
    5. 检索诊断 / 配置读写 / 删除 / 清空

隔离策略：把引擎的路径常量临时指向项目内的 ``.tmp_ragtest_kb/``，
全程不触碰真实的 config/knowledge/（避免污染或丢失用户已入库的向量）。
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


from checkers import rag_store as rs          # noqa: E402
from checkers.rag import engine as eng        # noqa: E402
from checkers.rag import text as rt           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_DIR = os.path.join(ROOT, ".tmp_ragtest_kb")

# 路径隔离：所有落盘操作重定向到临时目录
_orig_paths = (eng.KB_DIR, eng.KB_PATH, eng.FILES_DIR)
eng.KB_DIR = KB_DIR
eng.KB_PATH = os.path.join(KB_DIR, "knowledge.json")
eng.FILES_DIR = os.path.join(KB_DIR, "files")

# 离线：屏蔽 Ollama 嵌入调用，保证测试不依赖外部服务
_embed_calls = {"n": 0}


def _fake_embed(texts, ai_cfg=None, batch=24, timeout=30):
    _embed_calls["n"] += 1
    return None


_orig_embed = eng.embed_batch
eng.embed_batch = _fake_embed

MD = """# 企业资产评估规范（测试稿）

## 第三章 应收账款评估

### 3.1 账龄分析法

应收账款应当按账龄分析法计提坏账准备。

账龄 1 年以内的计提比例为 5%，1 至 2 年为 10%，2 至 3 年为 30%，3 年以上为 100%。

往来款项应当逐笔函证，回函率不得低于 80%。

## 第五章 在建工程评估

在建工程应当核实工程形象进度与付款进度是否匹配。

已完工未结算的工程量应当取得监理确认单。
"""

TXT = """第一章 总则
第一条 为规范资产评估执业行为，制定本准则。

第二条 资产评估师应当保持独立性和客观性。

第二章 评估程序
第三条 评估程序包括明确业务基本事项、签订业务约定书、编制评估计划。

第四条 现场调查应当形成工作底稿，底稿保存期限不少于十年。
"""


def main():
    global OK, FAIL
    shutil.rmtree(KB_DIR, ignore_errors=True)
    eng.invalidate()
    try:
        os.makedirs(eng.FILES_DIR, exist_ok=True)

        # ---------- 1. 入库与切片 ----------
        print("\n[1] 入库与按标题层级切片")
        added, docs, errors = eng.index_docs([("规范.md", MD.encode("utf-8")),
                                              ("准则.txt", TXT.encode("utf-8"))])
        chk("两个文档均入库成功", added == 2 and len(docs) == 2, f"added={added}")
        chk("无入库错误", not errors, f"errors={errors}")
        chk("入库返回分块数", all((d.get("chunks") or 0) > 0 for d in docs))

        idx = eng.load_index()
        chk("扁平片段数 > 0", idx.size > 0, f"size={idx.size}")

        paths = [p for p in idx.paths if p]
        chk("生成章节路径", len(paths) > 0, f"paths={paths[:3]}")
        chk("章节路径含层级分隔", any("/" in p for p in paths), f"paths={paths[:3]}")
        chk("识别到章级标题", any(("章" in p) for p in paths), f"paths={paths[:3]}")

        # ---------- 2. 检索（RRF 融合） ----------
        print("\n[2] 混合检索（离线时退化为纯 BM25）")
        hits = eng.retrieve("应收账款坏账准备计提比例是多少", None, 3)
        chk("返回命中结果", len(hits) > 0, f"hits={len(hits)}")
        if hits:
            h = hits[0]
            chk("命中片段来自规范文档", h["file"] == "规范.md", f"file={h['file']}")
            chk("命中文本含关键词", ("计提比例" in h["text"]) or ("坏账准备" in h["text"]),
                f"text={h['text'][:60]}")
            chk("输出含 RRF 名次", h.get("rank") == 1)
            chk("输出含分数", isinstance(h.get("score"), (int, float)))
            chk("输出含章节路径", bool(h.get("path")), f"path={h.get('path')}")
            chk("模式标记为 bm25", h.get("mode") == "bm25", f"mode={h.get('mode')}")
            chk("BM25 原始分 > 0", (h.get("bm25") or 0) > 0, f"bm25={h.get('bm25')}")

        # 中文查询不应被高频无区分度词元污染
        hits2 = eng.retrieve("底稿保存期限", None, 2)
        chk("专名查询命中准则文档",
            bool(hits2) and hits2[0]["file"] == "准则.txt",
            f"file={hits2[0]['file'] if hits2 else 'none'}")

        # 无关查询应低相关或空
        hits3 = eng.retrieve("量子计算编译器优化", None, 3)
        chk("无关查询不产生高质量误命中",
            (not hits3) or float(hits3[0].get("bm25") or 0) < 5.0,
            f"top_bm25={hits3[0].get('bm25') if hits3 else 'none'}")

        # ---------- 3. 增量索引 ----------
        print("\n[3] 增量索引（内容未变则复用）")
        _embed_calls["n"] = 0
        added2, docs2, err2 = eng.index_docs([("规范.md", MD.encode("utf-8"))])
        chk("重复入库仍计入成功数", added2 == 1)
        chk("重复入库标记为复用", bool(docs2 and docs2[0].get("reused")), f"docs2={docs2}")
        chk("重复入库未触发嵌入调用", _embed_calls["n"] == 0, f"calls={_embed_calls['n']}")

        _embed_calls["n"] = 0
        added3, docs3, _e3 = eng.index_docs([("规范.md", (MD + "\n补充条款：评估基准日应当明确。\n").encode("utf-8"))])
        chk("内容变更后重建分块", bool(docs3 and not docs3[0].get("reused")), f"docs3={docs3}")

        # ---------- 4. 切片配置 ----------
        print("\n[4] 切片参数读写")
        r = eng.save_kb_config({"chunk_size": 300, "chunk_overlap": 60})
        chk("写入切片参数", r.get("ok") and r["config"]["chunk_size"] == 300, f"cfg={r.get('config')}")
        chk("重叠不超过块长 1/3", r["config"]["chunk_overlap"] <= 100, f"cfg={r.get('config')}")
        r2 = eng.save_kb_config({"chunk_overlap": 9999})
        chk("越界重叠被夹紧", r2["config"]["chunk_overlap"] <= r2["config"]["chunk_size"] // 3,
            f"cfg={r2.get('config')}")
        eng.save_kb_config({"chunk_size": 500, "chunk_overlap": 100})

        # ---------- 5. 检索诊断 ----------
        print("\n[5] 检索诊断")
        d = eng.search_debug("坏账准备", None, 5)
        chk("诊断返回命中", d.get("ok") and len(d.get("hits") or []) > 0)
        chk("诊断含统计", (d.get("stats") or {}).get("chunks", 0) > 0, f"stats={d.get('stats')}")
        chk("诊断含检索词", len(d.get("query_terms") or []) > 0)

        # ---------- 6. 核验联动 ----------
        print("\n[6] 核验链路逐片段检索 + 知识依据回传")
        segs = [
            "应收账款账面余额为 23,381,372.12 元，账龄主要为 1 至 2 年。",
            "在建工程共 18 个单项工程，账面价值 18,000,000.00 元。",
        ]
        vhits = eng.retrieve_for_verify(segs, top_k=3, dense=False)
        chk("核验检索返回片段", len(vhits) > 0, f"vhits={len(vhits)}")
        pack, refs = eng.pack_hits(vhits, 6000)
        chk("打包上下文非空", bool(pack.strip()))
        chk("引用清单非空", len(refs) > 0)
        if refs:
            chk("引用含文件名", bool(refs[0].get("file")))
            chk("引用含片段摘要", bool(refs[0].get("snippet")))

        try:
            from checkers.ai_checker import _fmt_evidence
            ev = _fmt_evidence(refs)
            chk("知识依据格式化输出", "📚" not in ev and bool(ev.strip()), f"ev={ev[:80]}")
            chk("知识依据含出处", "规范.md" in ev, f"ev={ev[:80]}")
        except Exception as exc:  # noqa: BLE001
            chk("_fmt_evidence 可用", False, f"exc={exc}")

        # ---------- 7. 删除与清空 ----------
        print("\n[7] 删除与清空")
        st = eng.kb_status()
        chk("状态含文档列表", len(st["docs"]) == 2, f"docs={len(st['docs'])}")
        chk("状态回传切片参数", (st.get("config") or {}).get("chunk_size") == 500,
            f"config={st.get('config')}")

        ids = [st["docs"][0]["id"]]
        removed, names = eng.delete_docs(ids)
        chk("删除单个文档", removed == 1 and bool(names), f"removed={removed}")
        st2 = eng.kb_status()
        chk("删除后仅剩一个文档", len(st2["docs"]) == 1, f"docs={len(st2['docs'])}")

        eng.clear_kb()
        st3 = eng.kb_status()
        chk("清空后无文档", st3["total_chunks"] == 0 and not st3["docs"])
    finally:
        shutil.rmtree(KB_DIR, ignore_errors=True)
        eng.KB_DIR, eng.KB_PATH, eng.FILES_DIR = _orig_paths
        eng.invalidate()
        eng.embed_batch = _orig_embed

    print(f"\n结果：OK={OK} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
