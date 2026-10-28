# -*- coding: utf-8 -*-
"""规则配置：内置规则读写与恢复。"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from config.config_manager import load_rules, save_rules, RuleConfig


from server.state import ROOT

router = APIRouter()

# 规则 / 自定义规则 / 词库
# ---------------------------------------------------------------------------
@router.get("/api/rules")
def api_rules():
    return load_rules().data


@router.post("/api/rules")
def api_save_rules(payload: Dict[str, Any]):
    ok = save_rules(RuleConfig(payload))
    if not ok:
        raise HTTPException(500, "规则保存失败")
    return {"ok": True}


@router.post("/api/rules/restore")
def api_restore_rules():
    # 复用 settings_manager 的相同原子写回逻辑：直接删除 rules.json 让其重建默认
    rp = os.path.join(ROOT, "config", "rules.json")
    try:
        if os.path.exists(rp):
            os.remove(rp)
    except OSError:
        pass
    return {"ok": True, "data": load_rules().data}


# ---------------------------------------------------------------------------
