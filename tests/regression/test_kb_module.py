# -*- coding: utf-8 -*-
"""集成验证：知识库模块完善项（向量库 / OCR / 树形目录 / 权限 / 低耦合接口）。

覆盖：
    1. 向量库抽象层：后端枚举、按配置注入、不可用时自动降级
    2. 多格式解析统一入口与 OCR 状态自检
    3. 树形目录：建目录 → 入库归位 → 移动 → 重命名 → 删除（含环路检测）
    4. 权限：三角色能力矩阵、服务端强制拦截、历史角色值归一
    5. 低耦合门面：retrieve_for_verification / context_for_verification
    6. 单文档重新向量化
    7. 防幻觉：来源标注解析与审计

隔离策略：把引擎路径常量临时指向项目内 ``.tmp_kbtest/``，
全程不触碰真实的 config/knowledge/。
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


from checkers.rag import engine as eng            # noqa: E402
from checkers.rag import parsers                  # noqa: E402
from checkers.rag import permissions as perms     # noqa: E402
from checkers.rag import service as svc           # noqa: E402
from checkers.rag import stores                   # noqa: E402
from checkers.rag import tree                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_DIR = os.path.join(ROOT, ".tmp_kbtest")

_orig_paths = (eng.KB_DIR, eng.KB_PATH, eng.FILES_DIR)
eng.KB_DIR = KB_DIR
eng.KB_PATH = os.path.join(KB_DIR, "knowledge.json")
eng.FILES_DIR = os.path.join(KB_DIR, "files")

# 离线：屏蔽 Ollama 嵌入，保证测试不依赖外部服务
_orig_embed = eng.embed_batch
_embed_calls = {"n": 0}


def _fake_embed(texts, ai_cfg=None, batch=24, timeout=30):
    _embed_calls["n"] += 1
    return None


eng.embed_batch = _fake_embed

MD = """# 资产评估执业规范（测试稿）

## 第三章 应收账款评估

### 3.1 账龄分析法

应收账款应当按账龄分析法计提坏账准备。

账龄 1 年以内的计提比例为 5%，1 至 2 年为 10%。

## 第五章 在建工程评估

在建工程应当核实工程形象进度与付款进度是否匹配。
"""

TXT = """第一章 总则
第一条 为规范资产评估执业行为，制定本准则。

第二章 评估程序
第三条 现场调查应当形成工作底稿，底稿保存期限不少于十年。
"""


def main():
    global OK, FAIL
    shutil.rmtree(KB_DIR, ignore_errors=True)
    eng.invalidate()

    try:
        os.makedirs(eng.FILES_DIR, exist_ok=True)

        # ---------- 1. 向量库抽象层 ----------
        print("\n[1] 向量库抽象层")
        backs = stores.vector_backends()
        ids = [b["id"] for b in backs]
        chk("列出全部后端", set(ids) >= {"chroma", "memory"}, f"ids={ids}")
        chk("后端带可用性标记", all("available" in b for b in backs))

        st = stores.get_store("memory")
        chk("可获取 memory 后端", getattr(st, "backend", "") == "memory")
        st.upsert([("c_1", [1.0, 0.0], {"doc_id": "d_1"})])
        chk("memory 写入生效", st.count() >= 1, f"count={st.count()}")
        hits = st.search([1.0, 0.0], 3)
        chk("memory 检索命中", bool(hits) and hits[0][0] == "c_1", f"hits={hits}")
        chk("余弦归一", bool(hits) and abs(hits[0][1] - 1.0) < 1e-6, f"score={hits[0][1] if hits else None}")
        st.delete(["c_1"])
        chk("memory 删除生效", st.count() == 0, f"count={st.count()}")

        # ---------- 2. 多格式解析 ----------
        print("\n[2] 多格式解析与 OCR 自检")
        b1, d1 = parsers.extract_blocks("a.md", MD.encode("utf-8"))
        chk("Markdown 解析出块", len(b1) > 0, f"blocks={len(b1)}")
        b2, d2 = parsers.extract_blocks("b.txt", TXT.encode("utf-8"))
        chk("纯文本解析出块", len(b2) > 0, f"blocks={len(b2)}")
        b3, d3 = parsers.extract_blocks("c.csv",
                                        "项目,金额\n货币资金,330991.97".encode("utf-8"))
        chk("CSV 保留列结构", bool(b3) and " | " in b3[1][1], f"row={b3[1] if len(b3)>1 else None}")
        chk("诊断信息含抽字数", (d1.get("chars") or 0) > 0, f"diag={d1}")
        chk("支持扩展名覆盖 PDF/Word/图片",
            {".pdf", ".docx", ".xlsx", ".png"} <= set(parsers.SUPPORTED_EXTS))
        pst = parsers.parser_status()
        chk("解析能力自检含 OCR", "ocr" in pst and "available" in pst["ocr"])
        try:
            parsers.extract_blocks("x.xyz", b"abc")
            chk("未知格式抛错", False)
        except ValueError:
            chk("未知格式抛错", True)

        # ---------- 3. 树形目录 ----------
        print("\n[3] 树形目录")
        r = tree.create_folder("评估准则")
        chk("新建目录成功", r.get("ok"), f"r={r}")
        f1 = (r.get("folder") or {}).get("id") or ""
        chk("目录带路径", bool((r.get("folder") or {}).get("path")))

        r2 = tree.create_folder("2024", f1)
        chk("新建子目录成功", r2.get("ok"), f"r2={r2}")
        f2 = (r2.get("folder") or {}).get("id") or ""

        added, docs, errs = eng.index_docs([("规范.md", MD.encode("utf-8"))], f2)
        chk("入库并归入子目录", added == 1 and not errs, f"added={added} errs={errs}")
        did = (docs[0].get("id") if docs else "") or ""
        chk("返回归属目录", bool(docs) and docs[0].get("folder") == f2, f"doc={docs}")

        t = tree.build_tree()
        chk("树含子目录", any(f.get("id") == f1 for f in t["folders"]),
            f"folders={[f.get('name') for f in t['folders']]}")
        chk("树递归含文档", (t.get("doc_count") or 0) >= 1, f"doc_count={t.get('doc_count')}")

        fl = tree.flat_folders()
        chk("扁平目录含路径", any(x.get("id") == f2 and "/" in (x.get("path") or "")
                                for x in fl), f"f2={f2}")

        # 环路检测：把父目录移动到自己的子目录下
        bad = tree.move_folder(f1, f2)
        chk("环路移动被拒绝", not bad.get("ok"), f"bad={bad}")
        bad2 = tree.move_folder(f1, f1)
        chk("自环移动被拒绝", not bad2.get("ok"), f"bad2={bad2}")

        rr = tree.rename_folder(f1, "准则库")
        chk("重命名成功", rr.get("ok") and (rr.get("folder") or {}).get("name") == "准则库",
            f"rr={rr}")

        mv = tree.move_docs([did], "")
        chk("文档移动到根目录", mv.get("ok") and mv.get("moved") == 1, f"mv={mv}")

        # ---------- 4. 权限 ----------
        print("\n[4] 权限模型")
        chk("历史 user 归一为 operator", perms.normalize_role("user") == "operator")
        chk("未知角色回落 operator", perms.normalize_role("nope") == "operator")
        chk("admin 归一", perms.normalize_role("ADMIN") == "admin")
        chk("管理员具备删除权", perms.can("admin", "kb.delete"))
        chk("操作员无删除权", not perms.can("operator", "kb.delete"))
        chk("操作员可上传", perms.can("operator", "kb.upload"))
        chk("操作员可重向量化", perms.can("operator", "kb.reindex"))
        chk("访客不可上传", not perms.can("viewer", "kb.upload"))
        chk("访客可检索", perms.can("viewer", "kb.search"))
        chk("未知能力一律拒绝", not perms.can("admin", "kb.nope"))

        p = perms.permissions_payload()
        chk("载荷含角色与能力", bool(p.get("role")) and isinstance(p.get("caps"), dict))
        chk("载荷含角色定义", len(p.get("roles") or []) == 3)

        # ---------- 5. 低耦合门面 ----------
        print("\n[5] 低耦合服务门面")
        refs = svc.retrieve_for_verification(["应收账款坏账准备计提比例是多少"])
        chk("门面返回引用", len(refs) > 0, f"refs={len(refs)}")
        if refs:
            r0 = refs[0]
            chk("引用为 KbRef 类型", isinstance(r0, svc.KbRef))
            chk("引用含文件名", bool(r0.file), f"file={r0.file}")
            chk("引用含出处文本", bool(r0.cite), f"cite={r0.cite}")
        ctx, crefs = svc.context_for_verification(["账龄分析法如何计提"])
        chk("门面打包上下文非空", bool(ctx.strip()))
        chk("门面打包引用非空", len(crefs) > 0)
        chk("空输入返回空结果", svc.retrieve_for_verification("") == [])

        # ---------- 6. 单文档重新向量化 ----------
        print("\n[6] 单文档重新向量化")
        res = eng.reindex_doc(did)
        chk("重建成功", res.get("ok"), f"res={res}")
        chk("重建后仍有分块", (res.get("chunks") or 0) > 0, f"chunks={res.get('chunks')}")
        bad3 = eng.reindex_doc("d_notexist")
        chk("不存在的文档返回失败", not bad3.get("ok"))

        # ---------- 7. 防幻觉审计 ----------
        print("\n[7] 防幻觉审计")
        from checkers.rag import prompts
        cites = prompts.extract_citations("计提比例为 5%。[来源：规范.md / 第三章 应收账款评估]")
        chk("解析来源标注", cites == ["规范.md / 第三章 应收账款评估"], f"cites={cites}")
        a1 = prompts.audit_answer("无依据。[来源：幽灵文件 / x]",
                                  [{"file": "规范.md", "path": "第三章"}])
        chk("凭空引用被标记", not a1["grounded"] and bool(a1["unknown"]), f"a1={a1}")
        a2 = prompts.audit_answer(prompts.REFUSE_MARK, [])
        chk("拒答判定", a2["refused"] and a2["grounded"])
        a3 = prompts.audit_answer("比例为 5%。[来源：规范.md / 第三章]",
                                  [{"file": "规范.md", "path": "第三章"}], 0.8)
        chk("有据回答 grounded", a3["grounded"] and a3["confidence"] > 0.6, f"a3={a3}")
        msgs = prompts.build_rag_messages("问题", "资料")
        chk("消息含系统与用户", len(msgs) == 2 and msgs[0]["role"] == "system")

        # ---------- 8. 清理 ----------
        print("\n[8] 清空")
        eng.clear_kb()
        chk("清空后无文档", eng.kb_status()["total_chunks"] == 0)
    finally:
        shutil.rmtree(KB_DIR, ignore_errors=True)
        eng.KB_DIR, eng.KB_PATH, eng.FILES_DIR = _orig_paths
        eng.invalidate()
        eng.embed_batch = _orig_embed

    print(f"\n总计：OK={OK} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
