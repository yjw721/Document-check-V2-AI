# -*- coding: utf-8 -*-
"""知识库树形目录。

数据模型（存在 knowledge.json 内，不额外引入文件）：
    folders: [{ id, name, parent, created_at }]   parent="" 表示根目录
    docs[].folder: 所属目录 id（"" = 根目录）

能力：
- ``tree()``          构建嵌套树（目录 + 文档），供前端递归渲染
- ``create_folder``   新建目录（同级重名自动加后缀）
- ``rename_folder``   重命名
- ``delete_folder``   删除目录（子文档上移到父级 / 一并删除，可选）
- ``move_folder``     移动目录（带**环路检测**，禁止把父目录拖进自己的子孙）
- ``move_docs``       批量移动文档到指定目录
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT_ID = ""


def _new_id(prefix: str, seed: str) -> str:
    return prefix + hashlib.md5(
        f"{seed}:{time.time()}".encode("utf-8")).hexdigest()[:10]


def _load():
    from checkers.rag.engine import load_kb
    return load_kb()


def _save(kb: Dict[str, Any]) -> None:
    from checkers.rag.engine import invalidate, save_kb
    kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_kb(kb)
    invalidate()


def _folders(kb: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = kb.get("folders")
    if not isinstance(out, list):
        out = []
        kb["folders"] = out
    return [f for f in out if isinstance(f, dict)]


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def path_of(kb: Dict[str, Any], folder_id: str) -> str:
    """目录全路径，如「根目录 / 评估准则 / 2024」。"""
    fmap = {str(f.get("id") or ""): f for f in _folders(kb)}
    parts: List[str] = []
    cur = str(folder_id or ROOT_ID)
    guard = 0
    while cur and cur in fmap and guard < 64:
        parts.append(str(fmap[cur].get("name") or "").strip() or "(未命名)")
        cur = str(fmap[cur].get("parent") or ROOT_ID)
        guard += 1
    parts.append("根目录")
    parts.reverse()
    return " / ".join(parts)


def build_tree(kb: Optional[Dict[str, Any]] = None,
               doc_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """构建目录树。

    返回::

        { "id": "", "name": "根目录", "path": "根目录", "folders": [...], "docs": [...] }

    ``doc_ids`` 非空时只保留这些文档（用于搜索结果高亮定位）。
    """
    kb = kb if kb is not None else _load()
    fl = _folders(kb)
    wanted = {str(d) for d in doc_ids} if doc_ids else None

    nodes: Dict[str, Dict[str, Any]] = {
        ROOT_ID: {"id": ROOT_ID, "name": "根目录", "path": "根目录",
                  "folders": [], "docs": [], "folder_count": 0, "doc_count": 0}
    }
    for f in fl:
        fid = str(f.get("id") or "")
        if not fid or fid == ROOT_ID:
            continue
        nodes[fid] = {
            "id": fid,
            "name": str(f.get("name") or "").strip() or "(未命名)",
            "path": path_of(kb, fid),
            "folders": [], "docs": [],
            "folder_count": 0, "doc_count": 0,
        }

    for f in fl:
        fid = str(f.get("id") or "")
        parent = str(f.get("parent") or ROOT_ID)
        if not fid or fid == ROOT_ID:
            continue
        # 父节点缺失（数据被手改过）时挂到根目录，避免整棵树丢失
        nodes.setdefault(parent, nodes[ROOT_ID])
        if parent == fid:                       # 自环保护
            parent = ROOT_ID
        nodes[parent]["folders"].append(nodes[fid])

    for d in kb.get("docs") or []:
        if not isinstance(d, dict):
            continue
        if wanted is not None and str(d.get("id") or "") not in wanted:
            continue
        fid = str(d.get("folder") or ROOT_ID)
        target = nodes.get(fid) or nodes[ROOT_ID]
        chunks = d.get("chunks") or []
        target["docs"].append({
            "id": d.get("id", ""), "name": d.get("name", ""),
            "size": int(d.get("size") or 0),
            "chunks": len(chunks),
            "chars": sum(len(c.get("text") or "") for c in chunks),
            "added_at": d.get("added_at", ""),
            "folder": target["id"],
            "path": target["path"],
        })

    def _sort(n: Dict[str, Any]) -> None:
        n["folders"].sort(key=lambda x: (str(x.get("name") or "")))
        n["docs"].sort(key=lambda x: (str(x.get("name") or "")))
        for c in n["folders"]:
            _sort(c)
        n["folder_count"] = len(n["folders"])
        n["doc_count"] = len(n["docs"]) + sum(c["doc_count"] for c in n["folders"])
        n["total_docs"] = n["doc_count"]

    _sort(nodes[ROOT_ID])
    return nodes[ROOT_ID]


def flat_folders(kb: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """扁平目录列表（带 path / 文档数），用于下拉选择。"""
    kb = kb if kb is not None else _load()
    fl = _folders(kb)
    counts: Dict[str, int] = {}
    for d in kb.get("docs") or []:
        fid = str((d or {}).get("folder") or ROOT_ID)
        counts[fid] = counts.get(fid, 0) + 1
    out = [{"id": ROOT_ID, "name": "根目录", "parent": ROOT_ID,
            "path": "根目录", "docs": counts.get(ROOT_ID, 0)}]
    for f in fl:
        fid = str(f.get("id") or "")
        if not fid:
            continue
        out.append({"id": fid,
                    "name": str(f.get("name") or "").strip() or "(未命名)",
                    "parent": str(f.get("parent") or ROOT_ID),
                    "path": path_of(kb, fid),
                    "docs": counts.get(fid, 0)})
    return out


# ---------------------------------------------------------------------------
# 变更
# ---------------------------------------------------------------------------
def _unique_name(kb: Dict[str, Any], parent: str, name: str) -> str:
    siblings = {str(f.get("name") or "") for f in _folders(kb)
                if str(f.get("parent") or ROOT_ID) == parent}
    if name not in siblings:
        return name
    i = 2
    while f"{name} ({i})" in siblings:
        i += 1
    return f"{name} ({i})"


def create_folder(name: str, parent: str = ROOT_ID) -> Dict[str, Any]:
    kb = _load()
    nm = str(name or "").strip()[:60] or "新建目录"
    fl = _folders(kb)
    parent = str(parent or ROOT_ID)
    if parent != ROOT_ID and not any(str(f.get("id")) == parent for f in fl):
        return {"ok": False, "message": "父目录不存在"}
    nm = _unique_name(kb, parent, nm)
    folder = {"id": _new_id("f_", nm), "name": nm, "parent": parent,
              "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    fl.append(folder)
    kb["folders"] = fl
    _save(kb)
    return {"ok": True, "folder": dict(folder, path=path_of(kb, folder["id"]))}


def rename_folder(folder_id: str, name: str) -> Dict[str, Any]:
    kb = _load()
    nm = str(name or "").strip()[:60]
    if not nm:
        return {"ok": False, "message": "目录名不能为空"}
    fl = _folders(kb)
    for f in fl:
        if str(f.get("id")) == str(folder_id):
            f["name"] = _unique_name(kb, str(f.get("parent") or ROOT_ID), nm)
            _save(kb)
            return {"ok": True, "folder": dict(f, path=path_of(kb, str(f["id"])))}
    return {"ok": False, "message": "目录不存在"}


def delete_folder(folder_id: str, cascade: bool = False) -> Dict[str, Any]:
    """删除目录。

    cascade=False（默认）：子目录与文档上移到父级，安全不丢数据；
    cascade=True        ：连同子目录与文档一并删除。
    """
    kb = _load()
    fl = _folders(kb)
    target = next((f for f in fl if str(f.get("id")) == str(folder_id)), None)
    if target is None:
        return {"ok": False, "message": "目录不存在"}
    parent = str(target.get("parent") or ROOT_ID)

    kids = [f for f in fl if str(f.get("parent")) == str(folder_id)]
    docs = [d for d in (kb.get("docs") or []) if str((d or {}).get("folder")) == str(folder_id)]

    if cascade:
        # 收集整棵子树后一次性移除
        stack = [str(folder_id)]
        subtree = {str(folder_id)}
        while stack:
            cur = stack.pop()
            for f in fl:
                if str(f.get("parent")) == cur and str(f.get("id")) not in subtree:
                    subtree.add(str(f.get("id")))
                    stack.append(str(f.get("id")))
        from checkers.rag.engine import delete_docs
        ids = [str(d.get("id")) for d in (kb.get("docs") or [])
               if str((d or {}).get("folder")) in subtree]
        if ids:
            delete_docs(ids)
        kb = _load()
        kb["folders"] = [f for f in _folders(kb) if str(f.get("id")) not in subtree]
        _save(kb)
        return {"ok": True, "removed_folders": len(subtree), "removed_docs": len(ids)}

    for f in kids:
        f["parent"] = parent
    for d in docs:
        d["folder"] = parent
    kb["folders"] = [f for f in fl if str(f.get("id")) != str(folder_id)]
    _save(kb)
    return {"ok": True, "removed_folders": 1, "moved_docs": len(docs),
            "moved_folders": len(kids)}


def move_folder(folder_id: str, parent: str = ROOT_ID) -> Dict[str, Any]:
    """移动目录；禁止移动到自身或其子孙（否则会形成环路）。"""
    kb = _load()
    fl = _folders(kb)
    fid, want = str(folder_id or ""), str(parent or ROOT_ID)
    if not fid:
        return {"ok": False, "message": "目录 id 不能为空"}
    if fid == want:
        return {"ok": False, "message": "不能将目录移动到自身"}
    if want != ROOT_ID and not any(str(f.get("id")) == want for f in fl):
        return {"ok": False, "message": "目标父目录不存在"}

    # 沿 parent 链上溯：若遇到 fid 说明 want 是 fid 的子孙
    cur, guard = want, 0
    while cur and guard < 64:
        if cur == fid:
            return {"ok": False, "message": "不能移动到自己的子目录中（会形成环路）"}
        nxt = next((f for f in fl if str(f.get("id")) == cur), None)
        if nxt is None:
            break
        cur = str(nxt.get("parent") or ROOT_ID)
        guard += 1

    for f in fl:
        if str(f.get("id")) == fid:
            f["parent"] = want
            _save(kb)
            return {"ok": True, "folder": dict(f, path=path_of(kb, fid))}
    return {"ok": False, "message": "目录不存在"}


def move_docs(doc_ids: Sequence[str], folder_id: str = ROOT_ID) -> Dict[str, Any]:
    """把文档批量移动到目标目录。"""
    kb = _load()
    want = str(folder_id or ROOT_ID)
    ids = {str(i) for i in (doc_ids or []) if str(i)}
    if not ids:
        return {"ok": False, "message": "请选择要移动的文档"}
    if want != ROOT_ID:
        fl = _folders(kb)
        if not any(str(f.get("id")) == want for f in fl):
            return {"ok": False, "message": "目标目录不存在"}
    moved = 0
    for d in kb.get("docs") or []:
        if str((d or {}).get("id")) in ids:
            d["folder"] = want
            moved += 1
    if moved:
        _save(kb)
    return {"ok": True, "moved": moved}


def docs_in_subtree(kb: Dict[str, Any], folder_id: str) -> List[str]:
    """返回该目录及其所有子目录下的文档 id（用于按目录检索过滤）。"""
    fl = _folders(kb)
    want = str(folder_id or ROOT_ID)
    if want == ROOT_ID:
        return [str(d.get("id")) for d in (kb.get("docs") or []) if d]
    keep = {want}
    changed = True
    while changed:                       # 迭代展开子节点，避免递归深度问题
        changed = False
        for f in fl:
            fid, par = str(f.get("id") or ""), str(f.get("parent") or ROOT_ID)
            if par in keep and fid not in keep:
                keep.add(fid)
                changed = True
    return [str(d.get("id")) for d in (kb.get("docs") or [])
            if d and str(d.get("folder") or ROOT_ID) in keep]
