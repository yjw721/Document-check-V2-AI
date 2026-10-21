# -*- coding: utf-8 -*-
"""内置 RAG 提示词与防幻觉校验。

防幻觉做成**三层**，任何一层单独失效都不至于把编造内容放出去：

1. **检索层闸门**（``engine._gate``）：余弦低于 ``MIN_COS`` 的片段不进上下文，
   宁可少给资料也不给噪声；
2. **提示词层约束**：系统提示词强制「只能依据资料 + 逐句标注来源 + 无依据要明说」，
   并给出明确的拒答模板；
3. **输出层校验**（``audit_answer``）：回扫回答里出现的来源标记，
   若引用了资料中不存在的来源，标记为 ``grounded=False`` 并附告警，
   界面上可据此提示复核。

所有提示词集中在本文件，便于按机构口径统一调整。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 一、问答提示词（RAG 主链路）
# ---------------------------------------------------------------------------
RAG_SYSTEM = """你是一个严谨的本地知识库问答助手，服务于资产评估报告核验工作。

【最高准则】
你只能依据下方【知识库资料】作答，不得使用资料之外的任何信息，不得推测、不得编造、不得调用你的记忆中的法规条文或数据。

【引用规范】
1. 每一个事实性结论句，必须在句末用方括号标注来源，格式为：[来源：文件名 / 章节路径]。
   文件名与章节路径必须与【知识库资料】中的标注完全一致，不得改写、不得自造。
2. 若同一句引用多个来源，依次列出：[来源：A / a1][来源：B / b2]。
3. 资料中出现的数字、比例、期限、金额，必须原样引用，不得四舍五入或换算。

【无依据时的处理】
如果【知识库资料】中确实没有回答该问题所需的信息，你必须原样输出下面这句话，然后补充说明缺什么资料：
知识库资料中未找到相关内容。

【禁止事项】
- 禁止写出资料中不存在的法规名称、文号、条款号；
- 禁止给出资料中没有的评估方法、参数、比例；
- 禁止用“一般来说”“通常情况下”“根据相关规定”等模糊表述绕过引用要求；
- 禁止把多个来源的信息拼接成资料中并未给出的新结论。

【输出格式】
先给结论（1~2 句），再分段列出依据，每段后面带来源标注。语言简洁、专业、不客套。"""

RAG_USER_TEMPLATE = """【知识库资料】
{context}

【用户问题】
{question}

请严格依据上述资料回答，并逐句标注来源。"""

#: 回答里出现这句话即判定为「资料不足」，前端据此弱化展示
REFUSE_MARK = "知识库资料中未找到相关内容"

# ---------------------------------------------------------------------------
# 二、核验辅助提示词（核验流程调用知识库时使用）
# -------------------------------------------------------------------
VERIFY_SYSTEM = """你是一名资产评估报告核验专家，正在核验一份报告片段。

下方提供了两部分内容：
- 【知识库依据】：与本次核验相关的内部规范 / 准则 / 历史资料节选；
- 【待核验片段】：本次需要核验的报告正文。

【核验要求】
1. 只针对【待核验片段】中实际存在的内容提出核验意见，不要臆测未出现的内容；
2. 判断依据必须来自【知识库依据】，引用时在句末标注 [来源：文件名 / 章节路径]；
3. 若【知识库依据】与待核验片段无关或不足以支撑判断，则只做常规文字与逻辑核验，
   并说明「本次未命中可引用的知识库依据」；
4. 不得因为知识库里有某条规定，就断言报告违反了它 —— 必须先确认片段中确有对应表述；
5. 不得编造法规名称、文号、条款号。

【输出要求】
按核验系统既有格式输出问题条目，每条包含：位置、类型、严重程度、问题描述、整改建议。"""

VERIFY_USER_TEMPLATE = """【知识库依据】
{context}

【待核验片段】
{segment}

请输出核验意见。"""

# ---------------------------------------------------------------------------
# 三、来源标注解析
# ---------------------------------------------------------------------------
#: 匹配 [来源：xxx / yyy]，容忍中文全角冒号与空格
_CITE_RE = re.compile(r"\[\s*来源\s*[：:]\s*([^\]\[]+?)\s*\]")


def extract_citations(answer: str) -> List[str]:
    """从回答中抽出全部来源标注文本（去重、保序）。"""
    out: List[str] = []
    for m in _CITE_RE.finditer(str(answer or "")):
        t = m.group(1).strip()
        if t and t not in out:
            out.append(t)
    return out


def _ref_signatures(refs: Sequence[Dict[str, Any]]) -> List[str]:
    """引用清单的等价签名，用于比对回答中出现的标注。"""
    sigs: List[str] = []
    for r in refs or []:
        file_ = str(r.get("file") or "").strip()
        sec = str(r.get("section") or "").strip()
        path = str(r.get("path") or "").strip()
        if file_:
            sigs.append(file_)
        if sec:
            sigs.append(f"{file_} / {sec}".strip(" /"))
        if path:
            sigs.append(f"{file_} / {path}".strip(" /"))
    return [s for s in sigs if s]


def audit_answer(answer: str, refs: Sequence[Dict[str, Any]],
                 max_cos: float = 0.0) -> Dict[str, Any]:
    """回答审计：判断是否有依据、是否存在疑似凭空引用。

    返回::

        {grounded, refused, citations, unknown, confidence, warnings}
    """
    text = str(answer or "")
    refused = REFUSE_MARK in text
    cites = extract_citations(text)
    known = _ref_signatures(refs)
    unknown: List[str] = []

    for c in cites:
        c_norm = re.sub(r"\s+", " ", c).strip()
        if not any(c_norm == re.sub(r"\s+", " ", k).strip() for k in known):
            unknown.append(c)

    if refused:
        grounded, confidence = True, 0.2
    elif not refs:
        grounded, confidence = False, 0.0
    else:
        grounded = not unknown
        base = 0.35 + 0.5 * max(0.0, min(1.0, float(max_cos or 0.0)))
        base += 0.1 * min(len(refs), 3)
        confidence = round(min(0.95, base), 3)

    warnings: List[str] = []
    if unknown:
        warnings.append("回答引用了知识库资料中不存在的来源：" + "、".join(unknown[:5]))
    if not refused and refs and not cites:
        warnings.append("回答未标注任何来源，建议核对是否超出资料范围")
    return {
        "grounded": bool(grounded),
        "refused": bool(refused),
        "citations": cites,
        "unknown": unknown,
        "confidence": confidence,
        "warnings": warnings,
    }


def build_rag_messages(question: str, context: str) -> List[Dict[str, str]]:
    """组装问答消息（system + user）。"""
    return [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user",
         "content": RAG_USER_TEMPLATE.format(context=context or "（无命中资料）",
                                             question=question or "")},
    ]


def build_verify_messages(segment: str, context: str) -> List[Dict[str, str]]:
    """组装核验辅助消息（system + user）。"""
    return [
        {"role": "system", "content": VERIFY_SYSTEM},
        {"role": "user",
         "content": VERIFY_USER_TEMPLATE.format(
            context=context or "（本次未命中知识库依据）", segment=segment or "")},
    ]
