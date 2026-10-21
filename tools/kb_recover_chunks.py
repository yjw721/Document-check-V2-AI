# -*- coding: utf-8 -*-
"""知识库切片重建（一次性修复脚本）。

背景：config/knowledge/knowledge.json 曾丢失，已从 Git 基线恢复。
恢复出来的是 v2 旧切片（PDF 每页一个大块，章节路径退化为正文前 120 字），
本脚本利用其中**已抽取的全文**按当前切片器重新切片，
无需原始 PDF 即可还原「按标题层级 + 章节路径」的颗粒度。

用法：python tools/kb_recover_chunks.py [--dry]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkers.rag import engine as eng  # noqa: E402
from checkers.rag.text import chunk_blocks  # noqa: E402


def main(dry: bool = False) -> int:
    kb = eng.load_kb()
    cfg, size, overlap = eng._current_config(kb)
    sig = eng._doc_signature(cfg)
    docs = kb.get("docs") or []
    if not docs:
        print("知识库为空，无需重建")
        return 0

    total_before = total_after = 0
    for d in docs:
        name = str(d.get("name") or "")
        old = d.get("chunks") or []
        total_before += len(old)
        # 旧块的 text 即该页 / 段全文，作为块重新喂给切片器
        blocks = [(str(c.get("loc") or f"第 {i + 1} 块"), str(c.get("text") or ""))
                  for i, c in enumerate(old) if str(c.get("text") or "").strip()]
        new_chunks = chunk_blocks(blocks, size, overlap)
        if not new_chunks:
            print(f"  ! {name}：无可用文本，跳过")
            continue
        entries = eng._build_chunk_entries(str(d.get("id") or ""), name, new_chunks)
        d["chunks"] = entries
        d["sig"] = sig
        total_after += len(entries)
        print(f"  - {name}：{len(old)} → {len(entries)} 段")
        if entries:
            print(f"      示例路径：{entries[0].get('path') or '(无)'}")

    print(f"\n合计：{total_before} → {total_after} 段（切片 {size} 字 / 重叠 {overlap} 字）")
    if dry:
        print("[dry-run] 未写盘")
        return 0

    kb["updated_at"] = eng.time.strftime("%Y-%m-%d %H:%M:%S")
    # 向量需重建：原文已不在 files/ 下，但文本完整，可由 rebuild 流程重嵌
    kb["vectors"] = {}
    eng.save_kb(kb)
    eng.invalidate()
    print("已写盘。请执行「重建索引」重新生成向量。")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
