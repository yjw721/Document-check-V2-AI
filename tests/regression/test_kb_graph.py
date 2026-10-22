# -*- coding: utf-8 -*-
"""知识库思维图谱构建器回归测试。

隔离策略同 test_kb_module：引擎路径指向项目内 ``.tmp_kbgraph/``，
并屏蔽 Ollama 嵌入以验证「无向量时退化为关键词共现关联」的降级路径。
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
from checkers.rag import graph                    # noqa: E402
from checkers.rag import tree                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_DIR = os.path.join(ROOT, ".tmp_kbgraph")
KB_PATH = os.path.join(KB_DIR, "knowledge.json")

eng.KB_DIR = KB_DIR
eng.KB_PATH = KB_PATH
eng.FILES_DIR = os.path.join(KB_DIR, "files")

# 无向量：模拟离线（embed_batch 返回 None → 向量库为空，related 走关键词共现）
_orig_embed = eng.embed_batch


def _none_embed(texts, ai_cfg=None, batch=24, timeout=30):
    return None


eng.embed_batch = _none_embed


DOC_A = """# 资产评估方法（测试稿）

## 1. 成本法

### 1.1 重置成本法

重置成本法是资产评估中的基本方法之一，通过估算重置成本并扣除各项贬值确定评估值。

### 1.2 重置成本法的适用前提

重置成本法适用于具有可复制性的资产，例如机器设备与建筑物。

## 2. 收益法

收益法通过预测未来收益并折算现值确定资产价值。
"""

DOC_B = """# 评估方法对比（测试稿）

## 3. 方法比较

重置成本法侧重资产再取得成本，收益法侧重未来获利能力。

两种方法均依赖评估师的专业判断。
"""


def _rmtree(p):
    try:
        if os.path.isdir(p):
            shutil.rmtree(p)
    except OSError:
        pass


def build():
    _rmtree(KB_DIR)
    eng.invalidate()
    eng.index_docs([("成本法.md", DOC_A.encode("utf-8"))], folder="")
    eng.index_docs([("方法对比.md", DOC_B.encode("utf-8"))], folder="")
    return graph.build_graph()


def main():
    global OK, FAIL
    g = build()

    chk("图谱构建成功", g.get("ok") is True)
    chk("存在文档节点", any(n["type"] == "doc" for n in g["nodes"]), "nodes=%d" % len(g["nodes"]))
    chk("存在章节节点", any(n["type"] == "sec" for n in g["nodes"]))
    chk("结构边(hierarchy)存在", any(e["kind"] == "hierarchy" for e in g["edges"]))
    # 无向量时应退化为关键词共现关联，且两文档共享「重置成本法」等术语 → 有 related 边
    rel = [e for e in g["edges"] if e["kind"] == "related"]
    chk("无向量时生成关键词共现关联边", len(rel) > 0, "related=%d" % len(rel))
    chk("关联边带权重", all(0 < e["weight"] <= 1 for e in rel))
    chk("节点含层级与段数", all(("level" in n and "chunks" in n) for n in g["nodes"]))

    # 节点 id 唯一
    ids = [n["id"] for n in g["nodes"]]
    chk("节点 id 唯一", len(ids) == len(set(ids)))

    # 边端点都在节点集合内
    idset = set(ids)
    chk("边端点合法", all(e["source"] in idset and e["target"] in idset for e in g["edges"]))

    # 目录过滤：仅当前目录
    f = tree.create_folder("专题", "")
    fid = f["folder"]["id"]
    eng.invalidate()
    eng.index_docs([("专题文档.md", DOC_A.encode("utf-8"))], folder=fid)
    g2 = graph.build_graph(folder=fid, include_sub=True)
    docs2 = [n for n in g2["nodes"] if n["type"] == "doc"]
    chk("目录过滤仅含该目录文档", len(docs2) == 1, "docs=%d" % len(docs2))

    # 空库
    eng.invalidate()
    _rmtree(KB_DIR)
    g3 = graph.build_graph()
    chk("空库返回空图谱", g3["ok"] and g3["nodes"] == [] and g3["edges"] == [])

    print(f"\n图谱测试：通过 {OK} / 失败 {FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
