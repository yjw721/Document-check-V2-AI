# -*- coding: utf-8 -*-
"""
AI 智能核验引擎（可选模块，默认关闭）
=================================================
两种模式（由后台设置 ai 组决定）：
    local    本地 AI（Ollama 原生 /api/chat 接口，默认 http://127.0.0.1:11434）
             零联网，模型与文档数据均不出本机
    online   联网 AI（OpenAI 兼容 /chat/completions 接口，如 DeepSeek、通义、
             Kimi、OpenAI 等），需配置 base_url 与 api_key

流程：
    1. 轻量提取文件文本（Word 段落+表格 / Excel 文本单元格 / PDF 逐页）
    2. 按 max_chars 分段（受 max_requests 上限保护）
    3. 逐段调用模型，要求返回严格 JSON 数组（问题清单）
    4. 解析结果生成 Issue（rule_key=ai_verify，source=ai）追加到文件结果；
       单段失败降级为弱提醒（不中断整体核验）

保密说明：默认关闭；local 模式全程零联网；online 模式仅将待核验文本
发送至用户自行配置的接口地址，本模块不做任何其它网络行为。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from checkers.base import Issue, clip
from checkers import ai_activity

# 参考资料目录（用户上传的标准/词汇/规范文件，AI 核验时自动携带）
REFS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "ai_refs")
REFS_META = os.path.join(REFS_DIR, "meta.json")
_REF_LOCK = threading.Lock()

# AI 核验问题条目统一规则键 / 标题
AI_RULE_KEY = "ai_verify"
AI_RULE_TITLE = "AI 智能核验"

# AI 问题类别写入 Issue.tag（category 字段保留给自定义规则，避免语义冲突）
AI_CATEGORY_MAP = {
    "用词不当": "用词不当", "句型语病": "句型语病", "错别字": "错别字",
    "数据错误": "数据错误", "表格计算": "表格计算", "逻辑矛盾": "逻辑矛盾",
    "内容缺失": "内容缺失", "规范不符": "规范不符", "其他": "其他",
}

# 本地 Ollama 流式调用超时下限（秒）
# CPU 机型 + qwen3 等 thinking 模型首 token 慢、整段推理可达 2 分钟以上；
# 若按用户配置的 60 秒硬超时，本地长文本几乎必然被误判为「连接失败 / 超时」。
# 故本地模式单次调用超时统一放宽到该下限（用户配置更小时自动提升）。
_LOCAL_TIMEOUT_FLOOR = 300.0

# 全局信号量：本地/在线模型并发上限（本地模型串行最稳，避免排队打爆）
_AI_SEM = threading.Semaphore(1)
_AI_SEM_SIZE = [1]


def _ai_sem(ai: Dict[str, Any]) -> threading.Semaphore:
    """按 rate_limit.concurrency 动态调整并发信号量（≥1）。"""
    global _AI_SEM, _AI_SEM_SIZE
    rl = ai.get("rate_limit") or {}
    c = max(1, int(rl.get("concurrency") or 1))
    if c != _AI_SEM_SIZE[0]:
        _AI_SEM = threading.Semaphore(c)
        _AI_SEM_SIZE[0] = c
    return _AI_SEM


# 限流：按 rate_limit.rpm 粗略节流（相邻调用最小间隔 = 60/rpm 秒）
_LAST_CALL = [0.0]
_RPM_LOCK = threading.Lock()


def _ai_throttle(ai: Dict[str, Any]) -> None:
    rl = ai.get("rate_limit") or {}
    if not rl.get("enabled"):
        return
    rpm = int(rl.get("rpm") or 0)
    if rpm <= 0:
        return
    gap = 60.0 / rpm
    with _RPM_LOCK:
        now = time.time()
        wait = gap - (now - _LAST_CALL[0])
        if wait > 0:
            time.sleep(min(wait, gap))
        _LAST_CALL[0] = time.time()


# 响应缓存（内存）：相同 mode+model+prompt 命中后直接返回，受 cache.ttl / max_entries 约束
_AI_CACHE: Dict[str, Any] = {}
_AI_CACHE_TS: Dict[str, float] = {}
_AI_CACHE_LOCK = threading.Lock()


def _cache_key(mode: str, model: str, prompt: str) -> str:
    return hashlib.sha256(f"{mode}|{model}|{prompt}".encode("utf-8")).hexdigest()


def _cache_get(key: str, ttl: int) -> Optional[str]:
    with _AI_CACHE_LOCK:
        if key in _AI_CACHE:
            if time.time() - _AI_CACHE_TS[key] <= ttl:
                return _AI_CACHE[key]
            _AI_CACHE.pop(key, None)
            _AI_CACHE_TS.pop(key, None)
    return None


def _cache_put(key: str, val: str, max_entries: int) -> None:
    with _AI_CACHE_LOCK:
        if len(_AI_CACHE) >= max_entries:
            try:
                oldest = min(_AI_CACHE_TS, key=_AI_CACHE_TS.get)
                _AI_CACHE.pop(oldest, None)
                _AI_CACHE_TS.pop(oldest, None)
            except (ValueError, TypeError):
                pass
        _AI_CACHE[key] = val
        _AI_CACHE_TS[key] = time.time()


# 响应风格 → 系统指令
_STYLE_INSTR: Dict[str, str] = {
    "precise": "回答务必严谨、准确，避免主观推测。",
    "concise": "回答尽量简洁，直击要点，减少冗余。",
    "balance": "在严谨与易读之间保持平衡。",
    "friendly": "语气友好、平易近人，便于理解。",
    "formal": "使用正式、专业的书面语。",
}


def compose_system(ai: Dict[str, Any], base: str) -> str:
    """将 AI 角色 / 响应风格 / 默认提示词预设组合到基础系统提示之前。"""
    parts: List[str] = []
    role = (ai.get("role") or "").strip()
    if role:
        parts.append(f"你的角色：{role}。")
    style = ai.get("response_style") or "balance"
    if style in _STYLE_INSTR:
        parts.append(_STYLE_INSTR[style])
    preset_id = ai.get("active_preset") or ""
    presets = ai.get("prompt_presets") or []
    if preset_id and isinstance(presets, list):
        p = next((x for x in presets if isinstance(x, dict) and x.get("id") == preset_id), None)
        if p and p.get("content"):
            parts.append(str(p["content"]))
    if parts:
        return "【通用设定】" + " ".join(parts) + "\n\n" + base
    return base


# 默认配置（与 settings 中 ai 组一致；缺省时使用）
DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "mode": "local",
    "base_url": "http://127.0.0.1:11434",
    "api_key": "",
    "model": "qwen2.5:7b",
    "timeout": 60,
    "max_chars": 3000,
    "max_requests": 10,
    "ref_enabled": True,
    "ref_max_chars": 2000,
    # 增强审核力度：deep_review 开启后每段追加一轮「补漏复核」请求（耗时约翻倍）
    "deep_review": True,
    # 严格取证：收紧引用命中阈值（最终判定阶段生效）
    "strict_grounding": True,
    # 广撒网：候选收集阶段放宽门槛（0.6 → 0.45），由规则复核把关准确率
    "min_confidence": 0.45,
    # 规则复核：AI 输出候选后，用确定性验算剔除误报、提升确证项（建议保持开启）
    "rule_recheck": True,
    # 专项技能（SKILL）：active_skills = 启用的技能 id 集合；custom_skills = 用户自定义技能库。
    # None 表示未显式配置，运行时自动启用所有内置 default_on 技能。
    "active_skills": None,
    "custom_skills": [],
}

# ---------------------------------------------------------------------------
# 本地模型同步：扫描 Ollama 已安装模型；配置的模型不在本机时自动切换
# ---------------------------------------------------------------------------
_model_cache: Dict[str, Any] = {"ts": 0.0, "models": [], "base": ""}
_MODEL_CACHE_TTL = 10.0


def list_local_models(base_url: str) -> List[str]:
    """扫描本地 Ollama 已安装模型（带 10 秒缓存），失败返回空列表。"""
    global _model_cache
    base = (base_url or DEFAULTS["base_url"]).rstrip("/")
    now = time.time()
    if _model_cache["base"] == base and now - _model_cache["ts"] < _MODEL_CACHE_TTL:
        return _model_cache["models"]
    try:
        req = urllib.request.Request(base + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = sorted(str(m.get("name") or "") for m in data.get("models", []) if m.get("name"))
    except Exception:  # noqa: BLE001 - Ollama 离线不影响调用方
        models = []
    _model_cache = {"ts": now, "models": models, "base": base}
    return models


def resolve_local_model(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """本地模式模型解析：配置的 model 若不在已安装列表，自动同步为首个可用模型。

    返回 (实际模型名, 提示说明)；Ollama 离线或列表为空时保持原配置、无提示。
    """
    model = str(cfg.get("model") or DEFAULTS["model"])
    base = str(cfg.get("base_url") or DEFAULTS["base_url"])
    models = list_local_models(base)
    if not models:
        return model, ""
    if model in models:
        return model, ""
    return models[0], f"本地模型已自动同步为「{models[0]}」（原配置「{model}」不在本机）"

# ---------------------------------------------------------------------------
# 主提示词：按模型能力自适应。
# 旧版反复强调「宁可漏报也不要误报」「无法确证一律不输出」，叠加解析层严格取证后，
# 小模型几乎只输出空数组。现改为「广撒网」：要求按把握度分级输出候选，
# 由后置规则复核（_rule_recheck）做确定性判定，剔除误报、提升确证项。
# ---------------------------------------------------------------------------
_SYS_HEAD = (
    "你是一名严谨的文档质量复核专家，承担文档归档前的终审职责。\n"
    "【工作方式】逐句、逐单元格通读片段，禁止跳读与概括式浏览；遇到表格必须逐行逐列"
    "重新计算，不得凭印象判断；读到数值、日期、金额、比例时必须与片段其它位置交叉核对。\n"
)

# 完整核查清单：联网大模型（online）使用，覆盖 9 类
_SYS_CHECKS = (
    "【核查维度】以下 9 类都要覆盖，发现问题就指出：\n"
    "1. 错别字：同音 / 形近字误用、多字漏字、重字、繁简混用；\n"
    "2. 用词不当：口语化表述、术语误用、词义重复啰嗦、褒贬与语体不符、称谓不统一；\n"
    "3. 句型语病：语法错误、成分残缺、搭配不当、句式杂糅、语序混乱、指代不明；\n"
    "4. 标点与格式：标点误用致歧义、中英文标点混用、编号层级错乱、空格与换行异常；\n"
    "5. 数据错误：前后数字矛盾、金额 / 数量 / 面积 / 日期 / 比例不合常理、单位缺失或混用、"
    "大写金额与小写金额不一致、数字位数或小数点错位；\n"
    "6. 表格计算：合计 / 小计 / 求和与分项不符、占比与分项不匹配、跨行跨列勾稽错误"
    "（逐项重算，写明『哪项 + 哪项 = 应得值，文中写为 X』）；\n"
    "7. 交叉一致性：同一主体（名称 / 编号 / 日期 / 金额 / 单位 / 口径）在片段内不同位置"
    "出现时必须一致；\n"
    "8. 逻辑与内容：前后矛盾、因果不成立、表意不清、内容缺失、事实性错误；\n"
    "9. 规范符合性：有参考资料 / 知识库资料时严格依据其条款核查，并在 detail 中注明"
    "所依据的标准名称或条款编号。\n"
)

# 精简核查清单：本地小模型（local）使用，聚焦最高频、最可判定的 5 类。
# 小模型上下文与指令跟随能力有限，清单过长会稀释注意力、反而检不出。
_SYS_CHECKS_LITE = (
    "【核查维度】重点排查以下 5 类问题：\n"
    "1. 错别字与用词：同音 / 形近误用、多字漏字、口语化、称谓不统一；\n"
    "2. 数据错误：前后数字矛盾、金额 / 数量 / 日期 / 比例不合常理、单位缺失或混用、"
    "大写金额与小写金额不一致、小数点错位；\n"
    "3. 表格计算：合计 / 小计与分项不符、占比与分项不匹配、勾稽错误"
    "（写明『哪项 + 哪项 = 应得值，文中写为 X』）；\n"
    "4. 交叉一致性：同一名称 / 编号 / 日期 / 金额 / 单位前后必须一致；\n"
    "5. 逻辑与内容：前后矛盾、内容缺失、事实性错误。\n"
)

_SYS_TAIL = (
    "【输出纪律】\n"
    "- 先全面排查，再按把握度输出：有依据的问题都要列出，不要因为把握不是百分之百就遗漏；\n"
    "- 把握很足的 confidence 给 0.8~1.0；仍应提示但存疑的给 0.4~0.7"
    "（存疑项会被单独标注，交人工判断，不会直接算作错误）；\n"
    "- quote 必须是片段中真实出现的原文，可截取关键片段，但严禁编造、拼凑或改写；\n"
    "- detail 必须写出证据：数据 / 表格类写明算式与正确值，矛盾类写明前后两处原文；\n"
    "- 禁止输出『建议优化』『表述可更佳』『建议斟酌』等主观偏好类意见；\n"
    "- 同一处问题只报告一次，选取最具代表性的 quote；\n"
    "- category 必须取以下之一：用词不当 / 句型语病 / 错别字 / 数据错误 / "
    "表格计算 / 逻辑矛盾 / 内容缺失 / 规范不符 / 其他；\n"
    "- severity 取 high / medium / low：影响结论或金额正确性取 high，表述性问题取 medium，"
    "把握不足取 low。\n"
    "【返回格式】严格 JSON 数组，不要返回任何其它文字或解释：\n"
    '[{"quote":"片段中的原文","category":"上述分类之一","issue":"问题简述",'
    '"detail":"举证说明（含算式/矛盾依据）","suggestion":"修改建议",'
    '"severity":"high或medium或low","confidence":0.9}]\n'
    "若片段没有发现问题，返回空数组 []。\n"
)


def _sys_prompt(mode: str = "local") -> str:
    """按模型能力自适应组装主提示词。

    local（本地小模型）用精简核查清单，避免长指令稀释注意力；
    online（联网大模型）用完整 9 类清单，追求覆盖率。
    """
    checks = _SYS_CHECKS if str(mode) != "local" else _SYS_CHECKS_LITE
    return _SYS_HEAD + checks + _SYS_TAIL

# 输出契约：作为最后一条系统消息下发，用于覆盖角色设定 / 预设提示词 / 参考资料中
# 与之冲突的自定义输出格式（否则模型可能返回非数组结构，导致核验结果被整体丢弃）
_OUTPUT_CONTRACT = (
    "【输出契约（最高优先级，覆盖上文任何与之冲突的输出格式要求）】\n"
    "无论上文的角色设定、预设提示词或参考资料如何描述输出形式，本轮最终只能输出"
    "一个 JSON 数组，且每个元素必须包含下列全部字段、不得嵌套、不得改名：\n"
    '[{"quote":"片段中逐字出现的原文","category":"用词不当/句型语病/错别字/'
    '数据错误/表格计算/逻辑矛盾/内容缺失/规范不符/其他","issue":"问题简述",'
    '"detail":"举证说明（含算式或矛盾依据）","suggestion":"修改建议",'
    '"severity":"high/medium/low","confidence":0.9}]\n'
    "不要输出任何解释性文字；没有确凿问题时输出 []。\n"
)

# 深度复核（第二轮）系统提示：{found} 运行时替换为上一轮已发现清单，避免重复
_REVIEW_PROMPT = (
    "以下是同一文档片段的【深度复核】轮次。\n"
    "上一轮已输出的问题清单（不要重复输出，也不要质疑推翻）：\n"
    "{found}\n"
    "请再次逐句、逐单元格通读片段，只输出上一轮【遗漏的问题】。\n"
    "补漏重点：表格勾稽与合计重算、跨段落数值 / 名称 / 日期一致性、金额大小写核对、"
    "单位与口径统一、标点与编号、术语与错别字、内容与逻辑缺口。\n"
    "仍然必须遵守：quote 取自片段真实原文（可截取，严禁编造）；"
    "detail 写明算式或矛盾依据；存疑项也要输出并标低 confidence，交人工判断；"
    "禁止主观偏好类意见。\n"
    "每条必须完整包含 quote / category / issue / detail / suggestion / severity / "
    "confidence 七个字段（缺一不可，尤其是 issue 简述与 category 分类）。\n"
    "返回同样格式的严格 JSON 数组；若确实没有遗漏，返回 []。\n"
)

# category 严格枚举（用于解析阶段归范化）
AI_CATEGORIES = ("用词不当", "句型语病", "错别字", "数据错误", "表格计算",
                 "逻辑矛盾", "内容缺失", "规范不符", "其他")


# ---------------------------------------------------------------------------
# 专项技能（SKILL）：内置可开关的专项核验能力
# 与「角色设定 / 提示词预设」并存生效——预设定义 AI 的"人设与风格"，
# 技能定义 AI 的"专项关注点"，二者同时注入系统提示、互不替代，共同收紧审核。
# ---------------------------------------------------------------------------
BUILTIN_SKILLS: List[Dict[str, Any]] = [
    {
        "id": "financial_reconciliation",
        "name": "财务数据勾稽",
        "description": "核查金额、比例、合计与跨表勾稽、大小写金额一致、单位/币种/口径统一。",
        "prompt": (
            "【专项技能·财务数据勾稽】额外重点核查：同一科目/项目在片段内不同位置的金额、数量、"
            "面积、比例是否一致；合计、小计、求和与分项是否相符；占比与分项是否匹配；"
            "大写金额与小写金额是否一致（例如「壹拾贰万元整」须对应 ¥120,000.00）；"
            "跨行、跨列及跨表勾稽关系（必须逐项重算，并写明『哪项 + 哪项 = 应得值，文中写为 X』）；"
            "单位、币种、口径前后是否统一。"
        ),
        "default_on": True,
    },
    {
        "id": "terminology_consistency",
        "name": "术语与口径一致性",
        "description": "全文专业术语、单位、称谓、时点、统计口径统一，避免同物异名。",
        "prompt": (
            "【专项技能·术语与口径一致性】额外重点核查：同一概念/主体在片段内是否使用统一术语与称谓"
            "（避免同物异名、前后称谓不一致）；计量单位、币种、时点、统计口径是否前后统一；"
            "专有名词、缩写、机构名称、文件编号是否前后一致；避免使用口语化或模糊代称指代关键主体。"
        ),
        "default_on": True,
    },
    {
        "id": "legal_compliance",
        "name": "法律与规范符合性",
        "description": "核查强制性规范、资质、条款引用、合规表述，避免违规或无效表述。",
        "prompt": (
            "【专项技能·法律与规范符合性】额外重点核查：是否出现与现行法规/行业强制性规范相悖的表述；"
            "引用法规、标准、条款时名称与编号是否准确、是否过时或废止；资质、许可、有效期等关键合规要素"
            "是否齐全且未超期；是否存在可能带来法律风险的绝对化、承诺性、误导性表述。"
        ),
        "default_on": False,
    },
    {
        "id": "sensitive_info",
        "name": "涉密与敏感信息",
        "description": "识别不应外发的个人信息、商业秘密、密级标识等敏感内容。",
        "prompt": (
            "【专项技能·涉密与敏感信息】额外重点核查：是否含有不应在此文档中出现的个人敏感信息"
            "（身份证号、手机号、住址、银行账户等）、商业秘密或未公开经营数据；是否误带密级标识"
            "（如『机密』『秘密』）或内部代号；对外发文是否可能泄露内部口径或暂未公开事项。"
        ),
        "default_on": False,
    },
    {
        "id": "citation_integrity",
        "name": "引用标注完整性",
        "description": "核查数据出处、引文来源、脚注与参考文献可追溯、不缺失。",
        "prompt": (
            "【专项技能·引用标注完整性】额外重点核查：文中引用的数据、结论、法规、外部观点是否标注可溯源的"
            "出处；脚注、尾注、参考文献是否完整且与正文引用一一对应；是否存在『据资料显示』等无出处表述；"
            "引用的来源是否真实可信、与所支撑的结论匹配。"
        ),
        "default_on": False,
    },
    {
        "id": "format_typography",
        "name": "格式与排版规范",
        "description": "核查编号层级、标题样式、字体字号统一、目录页码等排版规范。",
        "prompt": (
            "【专项技能·格式与排版规范】额外重点核查：章节编号层级是否连续、是否错层或重号；"
            "标题样式、字体、字号、缩进是否前后统一；目录、页码、图表编号是否完整且对应；"
            "列表、表格、断行是否规范；是否存在明显的排版错乱（如孤行、错位、空白异常）。"
        ),
        "default_on": False,
    },
]


# 单次核验注入的专项技能上限（过量技能会挤占上下文、稀释核查重点）
_SKILL_INJECT_MAX = 3


def _norm_ftype(ftype: str) -> str:
    """将文件类型归一为技能过滤用的短标签：doc / xls / pdf / txt / 空。"""
    f = (ftype or "").lower()
    if "word" in f or f.endswith(".docx") or f.endswith(".doc"):
        return "doc"
    if "excel" in f or f.endswith((".xlsx", ".xls", ".csv")):
        return "xls"
    if "pdf" in f:
        return "pdf"
    if "txt" in f or "text" in f:
        return "txt"
    return ""


def get_active_skill_list(ai: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回当前启用的专项技能（内置 + 自定义）统一字段列表：
    id, name, prompt, description, file_types, default_severity, builtin
    """
    active = ai.get("active_skills")
    if active is None:  # 未配置：自动启用所有内置 default_on 技能
        active = [s["id"] for s in BUILTIN_SKILLS if s.get("default_on")]
    active_set = set(active or [])
    out: List[Dict[str, Any]] = []
    for s in BUILTIN_SKILLS:
        if s["id"] in active_set:
            out.append({
                "id": s["id"], "name": s["name"], "prompt": s.get("prompt", ""),
                "description": s.get("description", ""), "file_types": "all",
                "default_severity": "", "builtin": True,
            })
    for c in (ai.get("custom_skills") or []):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if c["id"] in active_set:
            out.append({
                "id": c["id"], "name": c.get("name", "自定义技能"),
                "prompt": c.get("prompt", ""), "description": c.get("description", ""),
                "file_types": (c.get("file_types") or "all"),
                "default_severity": c.get("default_severity", ""), "builtin": False,
            })
    return out


def build_skill_section(ai: Dict[str, Any], ftype: str = "", mode: str = "") -> str:
    """构造专项技能的系统提示片段。

    优化点（长提示会稀释注意力，是本地小模型检不出问题的重要诱因）：
      1. 按文件类型过滤，且精确匹配该文件类型的技能优先保留；
      2. 限制注入数量：local 小模型 2 项、online 最多 _SKILL_INJECT_MAX 项；
      3. local 小模型注入精简要点（description），online 才用完整 prompt。
    """
    skills = get_active_skill_list(ai)
    if not skills:
        return ""
    if not mode:
        mode = _ai_mode(ai)
    token = _norm_ftype(ftype)
    max_n = 2 if mode == "local" else _SKILL_INJECT_MAX

    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for s in skills:
        ft = (s.get("file_types") or "all").lower()
        if ft != "all" and token:
            if token not in [t.strip() for t in ft.split(",")]:
                continue
        # 精确声明了本文件类型的技能视为更相关，优先保留
        rank = 0 if (ft != "all" and token) else 1
        candidates.append((rank, s))
    candidates.sort(key=lambda x: x[0])

    parts: List[str] = []
    for _rank, s in candidates[:max_n]:
        if mode == "local":
            body = (s.get("description") or s.get("prompt") or "").strip()
        else:
            body = (s.get("prompt") or s.get("description") or "").strip()
        if not body:
            continue
        name = s.get("name", "专项技能")
        sev = (s.get("default_severity") or "").strip()
        sev_hint = f"（建议标注 {sev}）" if sev else ""
        parts.append(f"■ {name}：{body}{sev_hint}")
    if not parts:
        return ""
    return (f"【专项技能核验】本次额外启用以下 {len(parts)} 项技能，请额外关注这些维度；"
            "发现问题仍按输出契约的字段与格式输出"
            "（category 取最匹配的标准分类，必要时用「其他」）：\n" + "\n".join(parts))


class AiError(Exception):
    """AI 调用失败（连接 / 超时 / 响应异常）。"""


def _is_timeout_like(exc: BaseException) -> bool:
    """判断底层异常是否属于超时（socket.timeout / TimeoutError / 含 timeout 描述）。"""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    s = str(reason).lower()
    return "timed out" in s or "timeout" in s


def _timeout_msg(seconds: float) -> str:
    """超时故障的可操作中文提示（区分本地/联网由调用方补充说明）。"""
    secs = int(seconds)
    return (f"AI 响应超时（已等待约 {secs} 秒）：模型处理过慢或服务无响应。"
            f"本机 CPU 推理建议将「单次超时」调大（本地模式已自动放宽至不小于 5 分钟），"
            f"或减小「每段字数上限」；若多次出现请确认 Ollama 已启动、模型已就绪")


def _net_reason(exc: BaseException, timeout: float = 0.0) -> str:
    """把底层网络异常转换为可操作的中文提示（区分超时 / 连接拒绝 / 其它）。"""
    if _is_timeout_like(exc):
        return _timeout_msg(timeout or 0) if timeout else "AI 响应超时：模型处理过慢或服务无响应"
    reason = getattr(exc, "reason", exc)
    s = str(reason)
    if "Connection refused" in s or isinstance(reason, ConnectionRefusedError):
        return ("无法连接 AI 服务（连接被拒绝）：请确认本地 Ollama 已启动（ollama serve），"
                "或检查联网 AI 的接口地址与 API Key 是否正确")
    return f"连接失败：{s}"


def _http_json(url: str, payload: Dict[str, Any], headers: Dict[str, str],
               timeout: float) -> Dict[str, Any]:
    """发送 JSON POST 请求并解析响应（仅标准库 urllib）。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", **headers,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise AiError(f"HTTP {exc.code}：{body or exc.reason}") from exc
    except (TimeoutError, socket.timeout):
        raise AiError(_timeout_msg(timeout)) from None
    except (urllib.error.URLError, OSError) as exc:
        raise AiError(_net_reason(exc, timeout)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AiError(f"响应不是合法 JSON：{raw[:120]}") from exc


def _call_ollama(cfg: Dict[str, Any], messages: List[Dict[str, str]],
                 think: Optional[bool] = None,
                 options: Optional[Dict[str, Any]] = None,
                 on_token: Optional[Callable[[str, str], None]] = None) -> str:
    """Ollama 原生 /api/chat（本地，零联网）。

    think=True/False：显式启用/禁用思考链（qwen3 等 thinking 模型生效，
    其它模型忽略该字段）。
    options：透传 Ollama options（如 num_predict 限制输出长度）。
    on_token(text, kind)：可选流式回调，kind ∈ {"content", "thinking"}，
    用于实时展示本地模型推理过程；传入时以 stream 模式逐 chunk 输出。
    """
    base = str(cfg.get("base_url") or DEFAULTS["base_url"]).rstrip("/")
    url = base + "/api/chat"
    payload: Dict[str, Any] = {
        "model": cfg.get("model") or DEFAULTS["model"],
        "messages": messages,
        "stream": bool(on_token),
    }
    if think is not None:
        payload["think"] = think
    # 透传上下文长度 / 温度等 Ollama options（与调用方传入的 options 合并）
    opts: Dict[str, Any] = dict(options or {})
    nc = cfg.get("num_ctx")
    if nc:
        try:
            opts["num_ctx"] = int(nc)
        except (TypeError, ValueError):
            pass
    tp = cfg.get("temperature")
    if tp not in (None, ""):
        try:
            opts["temperature"] = float(tp)
        except (TypeError, ValueError):
            pass
    if opts:
        payload["options"] = opts
    if on_token:
        return _call_ollama_stream(url, payload, on_token,
                                   _ollama_eff_timeout(cfg))
    resp = _http_json(url, payload, {}, _ollama_eff_timeout(cfg))
    msg = resp.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str):
        raise AiError(f"Ollama 响应缺少 message.content：{str(resp)[:120]}")
    # 思考型模型（qwen3 / qwen3.5 等）偶发把最终答案全部输出在 thinking 流、
    # content 为空：直接返回空会被上层静默记 0 条命中（报告零详情）。
    # 以 thinking 兜底，下游 _parse_issues 自带 JSON 提取 + 严格取证，不会引入误报。
    if not content.strip():
        thinking = msg.get("thinking") or ""
        if thinking.strip():
            return thinking
        raise AiError("模型返回为空（content 与 thinking 均无输出），建议重试或更换模型")
    return content


def _ollama_eff_timeout(cfg: Dict[str, Any]) -> float:
    """本地 Ollama 有效超时：用户配置过小时自动放宽（CPU 推理首 token 慢）。
    返回 (有效超时秒数)。"""
    t = float(cfg.get("timeout") or DEFAULTS["timeout"])
    return max(t, _LOCAL_TIMEOUT_FLOOR)


def _call_ollama_stream(url: str, payload: Dict[str, Any],
                        on_token: Callable[[str, str], None], timeout: float) -> str:
    """流式调用 Ollama /api/chat：逐行解析 NDJSON chunk 并回调增量。

    chunk 结构：{"message":{"content":"...","thinking":"..."}}。
    qwen3 等 thinking 模型启用 think=True 时 thinking 字段先行输出，
    非思考模型只有 content。返回拼接后的完整 content。

    timeout 为「总预算」：同时作用于 socket 读超时与墙钟截止线，
    超时按故障分类为可操作的超时提示，而非笼统的「连接失败」。
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    buf: List[str] = []          # content 增量（最终答案）
    think_buf: List[str] = []    # thinking 增量（兜底用，见函数尾注）
    deadline = time.monotonic() + float(timeout)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            _set_sock_read_timeout(resp, float(timeout))
            for raw in resp:
                if time.monotonic() > deadline:
                    raise AiError(_timeout_msg(float(timeout)))
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = chunk.get("message") or {}
                content = msg.get("content")
                if isinstance(content, str) and content:
                    buf.append(content)
                    on_token(content, "content")
                thinking = msg.get("thinking")
                if isinstance(thinking, str) and thinking:
                    think_buf.append(thinking)
                    on_token(thinking, "thinking")
                if chunk.get("done"):
                    break
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise AiError(f"HTTP {exc.code}：{body or exc.reason}") from exc
    except AiError:
        raise
    except (TimeoutError, socket.timeout):
        raise AiError(_timeout_msg(float(timeout))) from None
    except (urllib.error.URLError, OSError) as exc:
        raise AiError(_net_reason(exc, float(timeout))) from exc
    # 与非流式路径同一策略：content 为空时以 thinking 兜底，两者皆空按失败处理
    # （否则思考模型偶发的「答案全在 thinking」会被静默记 0 条命中）
    content_out = "".join(buf).strip()
    if not content_out:
        think_out = "".join(think_buf).strip()
        if think_out:
            return think_out
        raise AiError("模型返回为空（content 与 thinking 均无输出），建议重试或更换模型")
    return content_out


def _set_sock_read_timeout(resp, seconds: float) -> None:
    """尽力将 http.client 底层 socket 读超时设为剩余预算（版本差异容错）。

    目标：把「单次读最多等 timeout 秒」变成「每次读到数据即可续命」，
    配合 _call_ollama_stream 的墙钟截止线实现整体预算式超时。
    """
    try:
        fp = getattr(resp, "fp", None) or getattr(resp, "raw", None)
        sock = getattr(getattr(fp, "raw", None), "_sock", None)
        if sock is None:
            sock = getattr(fp, "_sock", None) or fp
        sock.settimeout(seconds)
    except Exception:  # noqa: BLE001 - socket 内部结构差异不影响主流程
        pass


def _call_openai(cfg: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    """OpenAI 兼容 /chat/completions（联网）。

    不同厂商版本前缀不同（OpenAI/DeepSeek 为 /v1，智谱 GLM 为 /v4 等），
    这里按 base_url 自动拼接，避免硬套 /v1 导致 404：
      - 已以 /chat/completions 结尾：原样使用
      - 以 /vN（如 /v1、/v4）结尾：追加 /chat/completions
      - 其它：默认追加 /v1/chat/completions（OpenAI 兼容习惯）
    """
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise AiError("未配置联网 AI 接口地址 base_url")
    if base.endswith("/chat/completions"):
        url = base
    elif re.search(r"/v\d+$", base):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    headers = {}
    key = str(cfg.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    resp = _http_json(url, {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "stream": False,
        "temperature": float(cfg.get("temperature") if cfg.get("temperature") not in (None, "") else 0.2),
    }, headers, float(cfg.get("timeout") or DEFAULTS["timeout"]))
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiError(f"OpenAI 兼容响应结构异常：{str(resp)[:120]}") from exc


def _norm_text(s: str) -> str:
    """去掉空白与标点，仅保留字母 / 数字 / 汉字，用于一致性比对。"""
    return re.sub(r"[^\w]", "", str(s or ""))


# 知识库（RAG）联动：单个核验片段注入的规范资料字符上限
_KB_CTX_BUDGET = 6000


def _fmt_evidence(refs: Sequence[Dict[str, Any]], limit: int = 3) -> str:
    """把命中片段格式化为可展示的「知识依据」文本。"""
    parts: List[str] = []
    for r in list(refs)[:limit]:
        path = (r.get("path") or r.get("section") or "").strip()
        head = str(r.get("file") or "") + (f" / {path}" if path else "")
        snippet = str(r.get("snippet") or "").strip().replace("\n", " ")
        score = r.get("score")
        tail = f"（相关度 {score}）" if score else ""
        loc = str(r.get("loc") or "").strip()
        if loc:
            tail += f"｜位置：{loc}"
        parts.append(f"{head}：{snippet}{tail}")
    return "\n".join(parts)


# 引用命中的最小连续字符数（严格取证模式下为 10，宽松模式回退为 8）
_QUOTE_MIN_HIT = 10
# 广撒网阶段（候选收集）的宽松阈值：小模型引用常带换行/空格差异，
# 用严格阈值会在解析阶段就把候选整体丢弃，导致检不出问题。
_QUOTE_MIN_HIT_LOOSE = 4


def set_strict_grounding(on: bool) -> None:
    """切换严格取证阈值（供后台设置 / 测试调用）。"""
    global _QUOTE_MIN_HIT
    _QUOTE_MIN_HIT = 10 if on else 8


def _quote_hit(quote: str, source: str, need: int) -> bool:
    """引用命中判定：归一化全文命中，或存在长度不小于 need 的连续片段命中。"""
    if not quote or not source:
        return True
    q = _norm_text(quote)
    s = _norm_text(source)
    if not q:
        return False
    if q in s:
        return True
    need = min(need, len(q))
    if len(q) < need:
        return False
    for i in range(0, len(q) - need + 1):
        if q[i:i + need] in s:
            return True
    return False


def _quote_grounded(quote: str, source: str) -> bool:
    """严格取证：短引用必须整段命中，避免偶然重合让编造引用通过。"""
    return _quote_hit(quote, source, _QUOTE_MIN_HIT)


def _quote_grounded_loose(quote: str, source: str) -> bool:
    """宽松取证：用于广撒网阶段保留候选，交由后续规则复核做最终判定。"""
    return _quote_hit(quote, source, _QUOTE_MIN_HIT_LOOSE)


# 字段别名：兼容模型或用户预设提示词驱动出的多样化字段名
_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "quote": ("quote", "原文", "引用", "原文片段", "问题原文", "片段", "出处"),
    "issue": ("issue", "问题简述", "错误描述", "问题说明", "问题", "错误", "标题", "title"),
    "detail": ("detail", "举证", "举证说明", "依据", "说明", "描述", "详细",
               "evidence", "desc", "detail_desc"),
    "suggestion": ("suggestion", "修改建议", "整改建议", "处理建议", "建议"),
    "category": ("category", "分类", "类别", "错误类型", "问题类型", "类型"),
    "severity": ("severity", "严重级别", "严重程度", "级别", "等级"),
    "confidence": ("confidence", "置信度", "把握度", "把握"),
}

# 严重级别别名
_SEV_ALIASES: Tuple[Tuple[str, str], ...] = (
    ("严重", "high"), ("高", "high"), ("high", "high"),
    ("一般", "medium"), ("中", "medium"), ("medium", "medium"),
    ("轻微", "low"), ("低", "low"), ("low", "low"),
)


def _pick(node: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    """按别名顺序取第一个非空字符串值（跳过 bool / 容器）。"""
    for k in keys:
        if k not in node:
            continue
        v = node.get(k)
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (str, int, float)):
            s = str(v).strip()
            if s:
                return s
    return ""


def _to_float(s: str) -> Optional[float]:
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def _norm_severity(s: str) -> str:
    v = str(s or "").strip().lower()
    for key, val in _SEV_ALIASES:
        if v == key:
            return val
    for key, val in _SEV_ALIASES:
        if key in v:
            return val
    return "medium"


def _brief(s: str, n: int = 40) -> str:
    """把长描述压成一句简述（按句读截断）。"""
    s = str(s or "").strip()
    for sep in ("。", "；", "\n", "，"):
        p = s.find(sep)
        if 0 < p < n:
            return s[:p]
    return s[:n]


def _norm_map(s: str) -> Tuple[str, List[int]]:
    """归一化文本并返回「归一化下标 -> 原文下标」映射，用于回译定位原文片段。"""
    chars: List[str] = []
    idxs: List[int] = []
    for i, ch in enumerate(str(s or "")):
        if re.match(r"\w", ch):
            chars.append(ch)
            idxs.append(i)
    return "".join(chars), idxs


def _locate_quote(text: str, source: str) -> str:
    """当模型未给出 quote 时，从其举证文本中回译定位原文里真实存在的片段。

    取满足最小命中长度的最长连续串并映射回原文；找不到返回 ""，该条目随后被丢弃，
    以此保证「必须在原文中可举证」的底线不被绕过。
    """
    t, _ti = _norm_map(text)
    s, si = _norm_map(source)
    if not t or not s:
        return ""
    for n in range(len(t), _QUOTE_MIN_HIT - 1, -1):
        for i in range(0, len(t) - n + 1):
            pos = s.find(t[i:i + n])
            if pos >= 0:
                return str(source)[si[pos]: si[pos + n - 1] + 1]
    return ""


def _flatten_nodes(obj: Any, depth: int = 0) -> List[Dict[str, Any]]:
    """递归收集 JSON 中所有字典节点（含嵌套），兼容非标准输出结构。"""
    nodes: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        nodes.append(obj)
        if depth < 4:
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    nodes.extend(_flatten_nodes(v, depth + 1))
    elif isinstance(obj, list) and depth < 4:
        for v in obj:
            if isinstance(v, (dict, list)):
                nodes.extend(_flatten_nodes(v, depth + 1))
    return nodes


def _node_to_item(node: Dict[str, Any], source: str,
                  min_confidence: float,
                  collect: bool = False) -> Optional[Dict[str, Any]]:
    """把单个（可能来自嵌套结构的）字典节点归一化为标准问题条目；无效返回 None。

    collect=True 为「广撒网」模式：不在此阶段用严格取证 / 置信度丢弃候选，
    而是记录 grounded 标志，交由 _rule_recheck 做确定性判定。
    """
    quote = _pick(node, _FIELD_ALIASES["quote"])
    issue = _pick(node, _FIELD_ALIASES["issue"])
    detail = _pick(node, _FIELD_ALIASES["detail"])
    if not detail:
        detail = issue
    if not issue:
        issue = _brief(detail)
    if not issue and not detail:
        return None
    if len(issue) > 50:          # 模型把整段描述塞进 issue 时压成简述
        issue = _brief(issue)
    if len(_norm_text(issue)) < 3:
        return None
    # 引用取证：优先用模型给出的 quote，缺失时从举证文本回译定位，仍无则丢弃
    if not quote:
        quote = _locate_quote(detail if len(detail) >= len(issue) else issue, source)
    if not quote:
        return None
    # collect 模式用宽松阈值保留候选；否则沿用严格取证 + 置信度门槛
    grounded = _quote_grounded_loose(quote, source) if source else True
    if not collect:
        if not _quote_grounded(quote, source):
            return None
    conf = _to_float(_pick(node, _FIELD_ALIASES["confidence"]))
    if conf is not None and conf < min_confidence and not collect:
        return None
    cat = _pick(node, _FIELD_ALIASES["category"])
    if not cat and ("条款" in node or "标准" in node or "准则" in node):
        cat = "规范不符"
    if cat not in AI_CATEGORIES:
        cat = next((c for c in AI_CATEGORIES if c and c in cat), "其他")
    sev = _norm_severity(_pick(node, _FIELD_ALIASES["severity"]))
    if sev == "high" and not detail:
        sev = "medium"
    return {
        "quote": clip(quote, 120),
        "category": cat,
        "issue": issue,
        "detail": detail,
        "suggestion": _pick(node, _FIELD_ALIASES["suggestion"]),
        "severity": sev,
        "confidence": conf,
        "grounded": grounded,
    }


def _extract_json(text: str) -> Any:
    """从模型输出中提取【最外层】JSON 结构。

    依次尝试：整体解析 → 去掉 ``` 代码围栏后解析 → 从首个括号起做平衡匹配解析。
    不能再用「首个 [ 到最后一个 ]」的朴素截取：模型返回顶层对象且内部含数组时
    会误抓内层数组，导致真正的问题条目被整体丢弃。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        t = t.rstrip()
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = -1
    for i, ch in enumerate(t):
        if ch in "{[":
            start = i
            break
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _parse_issues(content: str, source: str = "", min_confidence: float = 0.6,
                  collect: bool = False) -> List[Dict[str, Any]]:
    """从模型输出中提取问题条目（容忍 ```json 包裹、顶层对象与嵌套结构）。

    source 为当前片段原文，用于校验引用是否真实存在（避免模型编造）；
    分类不在枚举内时归范到最接近项；重复条目（同 quote+issue）去重。
    collect=True 时不在此阶段丢弃候选，改由 _rule_recheck 统一判定。
    """
    payload = _extract_json(content)
    if payload is None:
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for node in _flatten_nodes(payload):
        item = _node_to_item(node, source, min_confidence, collect=collect)
        if not item:
            continue
        key = (_norm_text(item["quote"]), _norm_text(item["issue"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 规则复核（确定性验算）：AI 广撒网输出候选后，由规则做精确判定
# ---------------------------------------------------------------------------
# 小模型算术与引用能力有限：让它先尽量报候选，再由下面这些确定性规则
# 剔除自相矛盾/实际一致的误报、并对验算通过项提升严重度，兼顾召回与准确率。
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2, "三": 3, "叁": 3,
              "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7,
              "八": 8, "捌": 8, "九": 9, "玖": 9}
_CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_SECTIONS = {"万": 10000, "亿": 100000000}
_UNIT_MULT = {"亿元": 1e8, "亿": 1e8, "万元": 1e4, "万": 1e4, "元": 1.0, "圆": 1.0, "": 1.0}

# 非贪婪 +?：让数字部分在首个单位处停下，避免把「万」并入数字段被重复折算
# （贪婪写法会把「壹拾贰万元」切成 数字段「贰万」+ 单位「元」，得到 20000 而非 120000）
_CN_AMT_RE = re.compile(
    r"([零〇一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+?)(亿元|万元|亿|万|元|圆)")
_NUM_AMT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(亿元|万元|亿|万|元|圆)")
# 形如「80+120+50=250」的显式算式
_ARITH_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?(?:\s*\+\s*[0-9]+(?:\.[0-9]+)?)+)\s*=\s*([0-9]+(?:\.[0-9]+)?)")


def _cn_to_int(cn: str) -> Optional[int]:
    """中文大写数字串（壹拾贰万 / 三千五百）转整数；含无关字符或为空返回 None。"""
    s = (cn or "").strip()
    if not s:
        return None
    total, section, number = 0, 0, None
    for c in s:
        if c in _CN_DIGITS:
            number = _CN_DIGITS[c]
        elif c in _CN_UNITS:
            section += (number if number is not None else 1) * _CN_UNITS[c]
            number = None
        elif c in _CN_SECTIONS:
            if number is not None:
                section += number
            total += section * _CN_SECTIONS[c]
            section, number = 0, None
        else:
            return None
    if number is not None:
        section += number
    val = total + section
    return val if val > 0 else None


def _extract_cn_amount(text: str) -> Optional[float]:
    """抽取中文大写金额（如「壹拾贰万元整」），返回折算后的数值；无则返回 None。"""
    m = _CN_AMT_RE.search(text or "")
    if not m:
        return None
    n = _cn_to_int(m.group(1))
    if n is None:
        return None
    return float(n) * _UNIT_MULT.get(m.group(2), 1.0)


def _extract_num_amount(text: str) -> Optional[float]:
    """抽取阿拉伯数字金额（如「¥120,000.00」「300万元」），返回折算后的数值。"""
    m = _NUM_AMT_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1)) * _UNIT_MULT.get(m.group(2), 1.0)
    except (TypeError, ValueError):
        return None


def _verify_arithmetic(detail: str) -> Optional[bool]:
    """验证举证文本中的显式算式是否自洽。

    True=算式成立（模型算术正确，该条可信）；False=自相矛盾（必为误报）；None=无算式。
    """
    m = _ARITH_RE.search(detail or "")
    if not m:
        return None
    try:
        left = [float(x) for x in re.split(r"\s*\+\s*", m.group(1))]
        right = float(m.group(2))
    except (TypeError, ValueError):
        return None
    return abs(sum(left) - right) < 0.01


def _amount_case_conflict(item: Dict[str, Any]) -> Optional[bool]:
    """判定「大写金额与小写金额不一致」类问题是否成立。

    True=确证冲突；False=实际一致（误报）；None=不涉及此类问题或无法判定。
    仅在模型明确表述为「大写…不一致/不符/矛盾」时才验算，避免误伤举例性表述。
    """
    text = " ".join(str(item.get(k) or "") for k in ("issue", "detail"))
    if "大写" not in text:
        return None
    if not any(k in text for k in ("不一致", "不符", "矛盾", "错误")):
        return None
    cn = _extract_cn_amount(text)
    num = _extract_num_amount(text)
    if cn is None or num is None:
        return None
    return abs(cn - num) > 0.01


def _rule_recheck(items: List[Dict[str, Any]], source: str = "",
                  min_confidence: float = 0.45) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """对 AI 广撒网候选做确定性复核，返回 (保留项, 统计)。

    判定链：引用取证（防幻觉）→ 算式自洽 → 大小写金额 → 最终置信度门槛。
    """
    kept: List[Dict[str, Any]] = []
    stat = {"in": len(items), "dropped_ungrounded": 0, "dropped_arith": 0,
            "dropped_amount": 0, "dropped_lowconf": 0, "verified": 0}
    for it in items:
        # 1) 引用取证：原文中完全找不到依据的一律剔除
        if source and not _quote_grounded_loose(it.get("quote", ""), source):
            loc = _locate_quote(it.get("detail") or it.get("issue") or "", source)
            if not loc:
                stat["dropped_ungrounded"] += 1
                continue
            it["quote"] = clip(loc, 120)
        # 2) 算式自洽：模型自己算错的必然是误报
        arith = _verify_arithmetic(it.get("detail", ""))
        if arith is False:
            stat["dropped_arith"] += 1
            continue
        # 3) 大小写金额：模型称不一致但实际一致 → 误报
        amt = _amount_case_conflict(it)
        if amt is False:
            stat["dropped_amount"] += 1
            continue
        # 4) 确定性验算通过 → 标记并提升为 high
        if arith is True or amt is True:
            it["verified"] = True
            it["severity"] = "high"
            prev = it.get("confidence")
            it["confidence"] = 0.95 if prev is None else max(float(prev), 0.9)
            stat["verified"] += 1
        else:
            it["verified"] = False
        # 5) 最终置信度门槛
        conf = it.get("confidence")
        if conf is not None and float(conf) < min_confidence:
            stat["dropped_lowconf"] += 1
            continue
        kept.append(it)
    stat["out"] = len(kept)
    return kept, stat


# ---------------------------------------------------------------------------
# 文本提取（轻量版：Word / Excel / PDF）
# ---------------------------------------------------------------------------
def extract_texts(path: str, ftype: str) -> List[Tuple[str, str]]:
    """提取 [(位置, 文本), ...]，任何解析失败返回空列表。"""
    try:
        if ftype == "Word":
            return _extract_word(path)
        if ftype == "Excel":
            return _extract_excel(path)
        if ftype == "PDF":
            return _extract_pdf(path)
    except Exception:  # noqa: BLE001
        pass
    return []


def _extract_word(path: str) -> List[Tuple[str, str]]:
    from docx import Document
    doc = Document(path)
    blocks: List[Tuple[str, str]] = []
    for i, p in enumerate(doc.paragraphs, start=1):
        t = (p.text or "").strip()
        if len(t) >= 4:
            blocks.append((f"第 {i} 段", t))
    # 表格按整行传递（单元格用 | 连接），便于核查合计 / 小计与分项是否一致
    for ti, table in enumerate(doc.tables, start=1):
        for ri, row in enumerate(table.rows, start=1):
            cells = [c.text.strip() for c in row.cells]
            t = " | ".join(c for c in cells if c)
            if len(t) >= 2:
                blocks.append((f"表 {ti} 第 {ri} 行", t))
    return blocks


def _extract_excel(path: str) -> List[Tuple[str, str]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    blocks: List[Tuple[str, str]] = []
    try:
        for ws in wb.worksheets:
            for ri, row in enumerate(ws.iter_rows(values_only=True), start=1):
                cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
                t = " | ".join(cells)
                if len(t) >= 2:
                    blocks.append((f"「{ws.title}」第 {ri} 行", t))
    finally:
        wb.close()
    return blocks


def _extract_pdf(path: str) -> List[Tuple[str, str]]:
    from pypdf import PdfReader
    reader = PdfReader(path)
    blocks: List[Tuple[str, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            t = ""
        if len(t) >= 4:
            blocks.append((f"第 {i} 页", t))
    return blocks


# ---------------------------------------------------------------------------
# 参考资料（标准/词汇/规范）：上传 → 提取文本 → 核验时注入
# ---------------------------------------------------------------------------
def _refs_meta() -> List[Dict[str, Any]]:
    """读取参考资料元信息列表。"""
    try:
        if os.path.exists(REFS_META):
            with open(REFS_META, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:  # noqa: BLE001
        pass
    return []


def _save_refs_meta(items: List[Dict[str, Any]]) -> None:
    os.makedirs(REFS_DIR, exist_ok=True)
    with open(REFS_META, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def list_refs() -> List[Dict[str, Any]]:
    """参考资料列表（仅保留文件仍存在的条目）。"""
    items = _refs_meta()
    out = []
    for it in items:
        name = str(it.get("name") or "")
        if not name or not os.path.exists(os.path.join(REFS_DIR, name)):
            continue
        out.append({
            "name": name,
            "chars": int(it.get("chars") or 0),
            "enabled": bool(it.get("enabled", True)),
            "updated": str(it.get("updated") or ""),
        })
    return out


def _extract_ref_text(filename: str, raw: bytes) -> str:
    """按扩展名从上传文件提取纯文本（txt/md/docx/pdf）。"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md", ".csv"):
        return raw.decode("utf-8", errors="replace")
    if ext == ".docx":
        import io as _io
        from docx import Document
        doc = Document(_io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            for row in t.rows:
                parts.extend(c.text for c in row.cells)
        return "\n".join(parts)
    if ext == ".pdf":
        from pypdf import PdfReader
        import io as _io
        reader = PdfReader(_io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    raise ValueError(f"不支持的参考资料格式：{ext or '无扩展名'}（支持 .txt / .md / .csv / .docx / .pdf）")


def save_ref(filename: str, raw: bytes) -> Dict[str, Any]:
    """保存参考资料（提取文本为 .txt 落盘），返回条目信息。"""
    name = os.path.basename(filename or "").strip()
    if not name:
        raise ValueError("文件名不能为空")
    text = _extract_ref_text(name, raw).strip()
    if len(text) < 10:
        raise ValueError("参考资料内容过少（提取不足 10 字符），请确认文件含有效文本")
    with _REF_LOCK:
        os.makedirs(REFS_DIR, exist_ok=True)
        stem = os.path.splitext(name)[0]
        out_name = stem + ".txt"
        out_path = os.path.join(REFS_DIR, out_name)
        # 同名覆盖
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        items = [it for it in _refs_meta() if it.get("name") != out_name]
        items.append({
            "name": out_name,
            "chars": len(text),
            "enabled": True,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save_refs_meta(items)
    return {"name": out_name, "chars": len(text), "enabled": True}


def delete_ref(name: str) -> bool:
    """删除参考资料。"""
    name = os.path.basename(name or "").strip()
    with _REF_LOCK:
        items = _refs_meta()
        path = os.path.join(REFS_DIR, name)
        existed = os.path.exists(path)
        if existed:
            try:
                os.remove(path)
            except OSError:
                pass
        rest = [it for it in items if it.get("name") != name]
        _save_refs_meta(rest)
    return existed


def set_ref_enabled(name: str, enabled: bool) -> bool:
    """启用 / 停用参考资料。"""
    name = os.path.basename(name or "").strip()
    with _REF_LOCK:
        items = _refs_meta()
        found = False
        for it in items:
            if it.get("name") == name:
                it["enabled"] = bool(enabled)
                found = True
        if found:
            _save_refs_meta(items)
    return found


def _load_ref_text(max_chars: int) -> str:
    """合并所有启用的参考资料文本（按总长度截断）。"""
    parts: List[str] = []
    total = 0
    for it in _refs_meta():
        if not it.get("enabled", True):
            continue
        name = str(it.get("name") or "")
        path = os.path.join(REFS_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        text = text.strip()
        if not text:
            continue
        head = text[:max_chars - total] if max_chars > total else ""
        if head:
            parts.append(f"【{name}】\n{head}")
            total += len(head)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 分段与核验
# ---------------------------------------------------------------------------
def _chunk_blocks(blocks: List[Tuple[str, str]], max_chars: int,
                  max_requests: int) -> List[Tuple[str, str]]:
    """把文本块按 max_chars 合并分段，最多 max_requests 段。

    容量不足（全文长度 > max_chars × max_requests）时不再只取最前面的 N 段，
    而是对全篇做「首段 + 末段 + 等距抽样」的均匀覆盖，避免长文档尾部整段漏检。
    """
    chunks: List[Tuple[str, str]] = []
    cur_loc, cur_text = "", []
    cur_len = 0
    for loc, text in blocks:
        if not text:
            continue
        if cur_len + len(text) > max_chars and cur_text:
            chunks.append((cur_loc, "\n".join(cur_text)))
            cur_loc, cur_text, cur_len = "", [], 0
        if not cur_text:
            cur_loc = loc
        cur_text.append(text)
        cur_len += len(text)
    if cur_text:
        chunks.append((cur_loc, "\n".join(cur_text)))
    if not chunks or max_requests <= 1:
        return chunks[:max(1, max_requests)]
    if len(chunks) <= max_requests:
        return chunks[:max_requests]
    # 均匀抽样：首段与末段必检，其余按等距取样填满剩余配额
    idxs = {0, len(chunks) - 1}
    step = (len(chunks) - 1) / (max_requests - 1)
    for i in range(max_requests):
        idxs.add(int(round(i * step)))
    return [chunks[i] for i in sorted(idxs)[:max_requests]]


def ai_check_file(path: str, ftype: str, issues_out: List[Issue],
                  cfg: Optional[Dict[str, Any]] = None,
                  limit: int = 800, max_files_issues: int = 0,
                  cancel: Optional[Callable[[], bool]] = None,
                  on_token: Optional[Callable[[str, str], None]] = None,
                  on_chunk: Optional[Callable[[int, int, str], None]] = None) -> Tuple[int, str]:
    """
    对单个文件执行 AI 智能核验，把命中问题追加到 issues_out。

    返回 (追加条数, 状态说明)。任何失败都不抛异常，降级为说明文字。
    cancel 可选：返回 True 表示任务已取消，请求间隙立即中止。
    on_token(text, kind)：可选流式回调（kind ∈ content / thinking），
    local 模式逐 token 触发，用于实时展示本地模型推理过程。
    on_chunk(k, total, preview)：每段推理开始前回调（段序号 / 总段数 /
    当前段文本摘要，供界面展示模型正在分析的内容）。
    """
    ai = {**DEFAULTS, **(cfg or {})}
    if not ai.get("enabled"):
        return 0, "AI 核验未启用"
    if cancel and cancel():
        return 0, "任务已取消"

    mode = ai.get("mode") or "local"
    sync_note = ""
    if mode == "local":
        model, sync_note = resolve_local_model(ai)
        if sync_note:
            ai["model"] = model
        # 本地 CPU 推理可能超过用户配置的短超时：自动放宽，避免误报「连接失败/超时」
        t = float(ai.get("timeout") or DEFAULTS["timeout"])
        if t < _LOCAL_TIMEOUT_FLOOR:
            ai["timeout"] = float(_LOCAL_TIMEOUT_FLOOR)
            extra = (f"单次调用超时已自动放宽至 {int(_LOCAL_TIMEOUT_FLOOR)} 秒"
                     f"（原配置 {int(t)} 秒，本地 CPU 推理需要更长时间）")
            sync_note = (sync_note + "；" if sync_note else "") + extra
    retry_n = max(0, int(ai.get("retry") or 0))

    blocks = extract_texts(path, ftype)
    if not blocks:
        return 0, "未能提取文本，跳过 AI 核验"

    chunks = _chunk_blocks(blocks, int(ai.get("max_chars") or 3000),
                           int(ai.get("max_requests") or 10))
    if not chunks:
        return 0, "文本为空，跳过 AI 核验"

    mode = ai.get("mode") or "local"
    ref_text = ""
    if ai.get("ref_enabled", True):
        ref_text = _load_ref_text(int(ai.get("ref_max_chars") or 2000))
    # ---- 知识库（RAG）联动 ----
    # 旧实现用「文件开头 2000 字」作唯一查询，与逐段核验内容脱节，长文档后半段
    # 几乎取不到依据。改为：以每个待核验片段各起一路检索（BM25 走缓存索引，
    # 毫秒级；向量路把所有片段**一次性**批量嵌入，只发一批请求），
    # 再用 RRF 融合为文件级依据，命中原文随问题回传（Issue.evidence）。
    kb_on = bool(ai.get("kb_enabled"))
    kb_top_k = max(1, min(8, int(ai.get("kb_top_k") or 3)))
    kb_dense = bool(ai.get("kb_dense_in_verify", False))
    rag_text, rag_refs = "", []
    if kb_on:
        try:
            from checkers import rag_store
            _hs = rag_store.retrieve_for_verify(
                [t for _l, t in chunks], top_k=kb_top_k,
                dense=kb_dense, ai_cfg=ai)
            rag_text, rag_refs = rag_store.pack_hits(_hs, _KB_CTX_BUDGET)
        except Exception:  # noqa: BLE001 - 知识库异常不影响核验
            rag_text, rag_refs = "", []

    # 增强审核力度：严格取证阈值 + 置信度门槛 + 深度复核轮次
    strict = bool(ai.get("strict_grounding", True))
    set_strict_grounding(strict)
    try:
        min_conf = float(ai.get("min_confidence") or 0.45)
    except (TypeError, ValueError):
        min_conf = 0.45
    deep = bool(ai.get("deep_review", True))
    # 规则复核：AI 广撒网输出候选后，由确定性规则验算剔除误报（可关闭，默认开启）
    recheck = bool(ai.get("rule_recheck", True))
    recheck_stat = {"in": 0, "out": 0, "verified": 0}

    added = 0
    fail_reasons: List[str] = []
    seen_global: set = set()  # 跨段去重，避免同一问题在重叠分段中重复出现
    for k, (loc, text) in enumerate(chunks, 1):
        if cancel and cancel():
            return added, f"任务已取消（已完成 {added} 条）"
        if max_files_issues > 0 and len(issues_out) >= max_files_issues:
            break
        if limit > 0 and len(issues_out) >= limit:
            break
        if on_chunk:
            try:
                on_chunk(k, len(chunks), clip(text, 120))
            except Exception:  # noqa: BLE001 - 回调异常不影响核验
                pass

        # ---- 第一轮：主核查（广撒网收集候选） ----
        items, err = _request_issues(ai, mode, _messages(ai, text, ref_text, rag_text, ftype),
                                     retry_n, min_conf, text, on_token=on_token,
                                     cancel=cancel, collect=recheck)
        if err:
            fail_reasons.append(f"{loc}：{err}")
            if err == "任务已取消":
                return added, f"任务已取消（已完成 {added} 条）"
            continue

        # ---- 第二轮：深度复核补漏（可关闭，默认开启） ----
        if deep and (limit <= 0 or len(issues_out) + len(items) < limit):
            if cancel and cancel():
                return added, f"任务已取消（已完成 {added} 条）"
            if on_chunk:
                try:
                    on_chunk(k, len(chunks), f"[深度复核] {clip(text, 100)}")
                except Exception:  # noqa: BLE001
                    pass
            found = [f"{it['issue']}（{it['quote']}）" for it in items]
            items2, err2 = _request_issues(
                ai, mode, _messages_review(ai, text, found, ref_text, rag_text, ftype),
                retry_n, min_conf, text, on_token=on_token, cancel=cancel,
                collect=recheck)
            if err2:
                # 复核轮失败不影响首轮结论，仅记录
                fail_reasons.append(f"{loc}：深度复核未完成（{err2}）")
            else:
                items = items + items2

        # ---- 规则复核：对候选做确定性验算，剔除误报、提升确证项 ----
        if recheck and items:
            items, _st = _rule_recheck(items, text, min_conf)
            recheck_stat["in"] += _st["in"]
            recheck_stat["out"] += _st["out"]
            recheck_stat["verified"] += _st["verified"]

        for it in items:
            gkey = (_norm_text(it["quote"]), _norm_text(it["issue"]))
            if gkey in seen_global:
                continue
            if limit > 0 and len(issues_out) >= limit:
                break
            seen_global.add(gkey)
            cat = f"[{it['category']}] " if it.get("category") else ""
            issues_out.append(Issue(
                rule_key=AI_RULE_KEY,
                rule_title=AI_RULE_TITLE,
                severity=it["severity"],
                location=loc,
                detail=f"{cat}{it['issue']}：{it['detail']}",
                snippet=it["quote"],
                suggestion=it["suggestion"],
                source="ai",
                tag=AI_CATEGORY_MAP.get(it.get("category", ""), it.get("category", "") or "其他"),
                evidence=_fmt_evidence(rag_refs),
            ))
            added += 1

    # 知识库命中与规则复核情况并入文件级说明，界面徽标 / 任务日志可直接看到
    kb_note = ""
    if kb_on:
        kb_note = f"；知识库命中 {len(rag_refs)} 段"
    if recheck and recheck_stat["in"]:
        kb_note += (f"；规则复核 {recheck_stat['in']}→{recheck_stat['out']} 条"
                    f"（验算确证 {recheck_stat['verified']} 条）")

    if fail_reasons and added == 0:
        ai_activity.log_event("verify", model=str(ai.get("model", "")),
                             detail="执行失败：" + "；".join(fail_reasons[:2]), ok=False)
        return 0, "AI 核验执行失败：" + "；".join(fail_reasons[:2]) + kb_note
    if fail_reasons:
        ai_activity.log_event("verify", model=str(ai.get("model", "")),
                             detail=f"部分分段失败，已命中 {added} 条", ok=True)
        return added, "部分分段失败：" + "；".join(fail_reasons[:2]) + kb_note
    ai_activity.log_event("verify", model=str(ai.get("model", "")),
                         detail=f"核验通过，命中 {added} 条问题", ok=True)
    return added, ("完成" + (f"（{sync_note}）" if sync_note else "") + kb_note)


def _ai_mode(ai: Dict[str, Any]) -> str:
    """取当前推理模式：local（本地小模型）/ online（联网大模型）。"""
    return "local" if str(ai.get("mode") or "local") == "local" else "online"


def _messages(ai: Dict[str, Any], text: str, ref_text: str = "", rag_text: str = "",
              ftype: str = "") -> List[Dict[str, str]]:
    return _base_messages(ai, compose_system(ai, _sys_prompt(_ai_mode(ai))),
                          text, ref_text, rag_text, ftype)


def _messages_review(ai: Dict[str, Any], text: str, found: List[str],
                     ref_text: str = "", rag_text: str = "",
                     ftype: str = "") -> List[Dict[str, str]]:
    """深度复核（第二轮）消息：携带上一轮已发现问题清单，要求只输出遗漏项。"""
    found_txt = "\n".join(f"- {f}" for f in found[:40]) or "（上一轮未发现问题）"
    sys_txt = _REVIEW_PROMPT.replace("{found}", found_txt)
    return _base_messages(ai, compose_system(ai, sys_txt), text, ref_text, rag_text, ftype)


def _base_messages(ai: Dict[str, Any], sys_txt: str, text: str,
                   ref_text: str = "", rag_text: str = "",
                   ftype: str = "") -> List[Dict[str, str]]:
    msgs = [{"role": "system", "content": sys_txt}]
    if ref_text:
        msgs.append({
            "role": "system",
            "content": "以下是用户上传的核验参考资料（行业标准 / 术语定义 / 书写规范），"
                       "核验时请严格依据这些标准检查文档，不得违背；"
                       "凡判定为『规范不符』的问题，必须在 detail 中注明所依据的标准名称或条款：\n"
                       + ref_text,
        })
    if rag_text:
        msgs.append({
            "role": "system",
            "content": "以下是本地知识库（RAG）检索到的相关规范资料，仅作参考："
                       "仅当与待核验片段内容一致时依据其核查，不得仅凭资料臆测片段中不存在的内容；"
                       "若判定『规范不符』，请在 detail 中注明所依据的资料原文：\n"
                       + rag_text,
        })
    # 专项技能：在参考资料之后、输出契约之前注入，使技能提示聚焦且不被格式契约覆盖
    skill_sec = build_skill_section(ai, ftype, _ai_mode(ai))
    if skill_sec:
        msgs.append({"role": "system", "content": skill_sec})
    # 输出契约放最后：紧邻 user 消息，用于压过 preset / 资料中对输出格式的描述
    msgs.append({"role": "system", "content": _OUTPUT_CONTRACT})
    msgs.append({"role": "user",
                 "content": f"文档片段：\n{text}\n\n（请严格按输出契约返回 JSON 数组。）"})
    return msgs


# 判定「字典是否像一条问题条目」的字段白名单：仅当命中这些与输出契约相关的
# 字段时才视为问题项，避免把思考型模型推理文本中散落的 JSON 碎片（如
# {"name": "x", "val": 1} / {"Role": "analyst"}）误判为合规结果。
_ISSUE_LIKE_KEYS = {
    # 问题主体
    "issue", "问题", "问题描述", "风险点", "异常", "缺陷", "problem",
    # 引用取证
    "quote", "引用", "引文", "原文", "evidence", "证据",
    # 严重性与处置
    "severity", "级别", "严重程度", "风险等级", "level",
    "suggestion", "建议", "整改建议", "修改建议", "处理意见", "recommendation",
    # 分类与说明
    "category", "类型", "分类", "类别", "问题类型", "风险类别",
    "detail", "说明", "详情", "描述", "分析", "解读", "description",
}


def _looks_like_issue(d: Any) -> bool:
    """字典是否像一条问题条目（含输出契约相关字段）。"""
    if not isinstance(d, dict):
        return False
    keys = set(k.lower() if isinstance(k, str) else k for k in d.keys())
    return bool(keys & _ISSUE_LIKE_KEYS)


def _has_json_structure(text: str) -> bool:
    """返回文本是否包含符合输出契约的 JSON（对象数组）。

    用于区分「合法的空结果（[]）」与「根本没按输出契约输出 JSON」：
    前者是真实的"未发现问题"，后者是格式违规（应重试/记失败，而不是静默记 0）。

    收紧判定：必须是「对象数组」或「对象包裹的对象数组」形状，且数组中的对象
    必须看起来像问题条目（命中 issue/quote/severity/suggestion 等契约字段）。
    思考型模型常把 token 耗在思考链上，输出大段英文推理文本并在其中散落零星
    JSON 碎片（{"name": "x"} / [1, 2] / {"Role": …}），这些碎片若被放行会解出
    0 条且无报错，造成「看似成功、实际静默记 0」的假阴性。因此碎片一律判为
    不合规，触发重试/记失败。
    """
    data = _extract_json(text)
    if isinstance(data, list):
        # 空数组 [] 是合法的"未发现问题"结果，放行
        if not data:
            return True
        return all(isinstance(x, dict) and _looks_like_issue(x) for x in data)
    if isinstance(data, dict):
        return any(
            isinstance(v, list) and v
            and all(isinstance(x, dict) and _looks_like_issue(x) for x in v)
            for v in data.values()
        )
    return False


def _request_issues(ai: Dict[str, Any], mode: str, msgs: List[Dict[str, str]],
                    retry_n: int, min_confidence: float,
                    source_text: str,
                    on_token: Optional[Callable[[str, str], None]] = None,
                    cancel: Optional[Callable[[], bool]] = None,
                    collect: bool = False) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """执行一次模型请求并解析问题列表，返回 (问题条目, 错误说明)。

    命中响应缓存时直接复用；失败按 retry_n 重试；解析阶段按 source_text 校验引用真实性。
    collect=True 时不做严格过滤，仅收集候选供 _rule_recheck 判定。
    """
    cache_cfg = ai.get("cache") or {}
    ckey = None
    if cache_cfg.get("enabled") and on_token is None:
        ckey = _cache_key(mode, str(ai.get("model", "")),
                          json.dumps(msgs, ensure_ascii=False))
        cached = _cache_get(ckey, int(cache_cfg.get("ttl") or 3600))
        if cached is not None:
            return _parse_issues(cached, source_text, min_confidence, collect=collect), None
    last_err: Optional[str] = None
    send_msgs = msgs
    for attempt in range(retry_n + 1):
        if cancel and cancel():
            return [], "任务已取消"
        if attempt > 0:   # 重试前稍作退避，避免夯住本地服务
            time.sleep(0.5 * attempt)
        try:
            with _ai_sem(ai):
                _ai_throttle(ai)
                # think 参数可选透传（仅显式配置时下发）：qwen3 等思考型模型配置
                # think=false 可跳过思考链直接输出答案，规避「答案全在 thinking /
                # content 混推理文本」的格式问题；未配置时不发送该字段（兼容
                # 不支持 think 的旧版 Ollama 与非思考模型）
                _think = ai.get("think") if "think" in ai else None
                content = _call_ollama(ai, send_msgs, think=_think,
                                       on_token=on_token) if mode == "local" \
                    else _call_openai(ai, send_msgs)
            if not _has_json_structure(content):
                # 模型只输出了推理/说明文本、未按输出契约给出 JSON（常见于思考型
                # 模型把 token 耗在思考上）：视为格式违规走重试；重试耗尽后该段
                # 记失败，避免「看起来成功、实际静默记 0 条命中」
                last_err = "模型未按输出契约返回 JSON 数组（仅输出推理或说明文本）"
                # 重试附带纠偏指令（明确指出上一轮问题），显著提高第二次命中率
                send_msgs = list(msgs) + [{
                    "role": "user",
                    "content": "你上一轮没有按输出契约返回 JSON 数组，只输出了推理或说明文字。"
                               "请重新作答：只输出 JSON 数组本身，不要任何推理过程、解释"
                               "或 Markdown 代码块；没有问题时输出 []。",
                }]
                continue
            if ckey:
                _cache_put(ckey, content, int(cache_cfg.get("max_entries") or 200))
            return _parse_issues(content, source_text, min_confidence, collect=collect), None
        except AiError as exc:
            last_err = str(exc)
            continue
    return [], last_err


def build_prompt_record(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构建本次 AI 核验的「配置 + 提示词」记录，供报告归档与溯源。

    仅记录本机配置与提示词文本，不含任何待核验文档内容。
    """
    ai = {**DEFAULTS, **(cfg or {})}
    mode = str(ai.get("mode") or "local")
    model = str(ai.get("model") or "")
    base_url = str(ai.get("base_url") or DEFAULTS["base_url"]).rstrip("/")
    if mode == "local":
        model, _sync = resolve_local_model(ai)
    review_prompt = _REVIEW_PROMPT.replace(
        "{found}", "（运行时注入：上一轮已发现的问题清单，逐条输出不重复）")
    kb_stat: Dict[str, Any] = {}
    if bool(ai.get("kb_enabled", False)):
        try:
            from checkers import rag_store
            kb_stat = rag_store.kb_status()
        except Exception:  # noqa: BLE001 - 统计失败不影响归档
            kb_stat = {}
    # 统一数据层：AI 生成产出草稿箱统计（供报告溯源「产出→入库→检测」闭环）
    ds_stat: Dict[str, Any] = {}
    try:
        from checkers.datastore import store_stats
        ds_stat = store_stats()
    except Exception:  # noqa: BLE001 - 统计失败不影响归档
        ds_stat = {}
    return {
        "enabled": bool(ai.get("enabled")),
        "mode": "本地 AI（Ollama，零联网）" if mode == "local" else "联网 AI（OpenAI 兼容接口）",
        "mode_key": mode,
        "model": model,
        "base_url": base_url,
        "deep_review": bool(ai.get("deep_review", True)),
        "strict_grounding": bool(ai.get("strict_grounding", True)),
        "min_confidence": ai.get("min_confidence", 0.45),
        "rule_recheck": bool(ai.get("rule_recheck", True)),
        "max_chars": int(ai.get("max_chars") or 3000),
        "max_requests": int(ai.get("max_requests") or 10),
        "retry": int(ai.get("retry") or 0),
        "verify_scope": "全部可解析文件" if str(ai.get("verify_scope", "all")) == "all"
                        else "仅基础检测出问题的文件",
        "ref_enabled": bool(ai.get("ref_enabled", True)),
        "kb_enabled": bool(ai.get("kb_enabled", False)),
        "kb_top_k": int(ai.get("kb_top_k") or 3),
        "kb_docs": len(kb_stat.get("docs") or []),
        "kb_chunks": int(kb_stat.get("total_chunks") or 0),
        "kb_embed": bool(kb_stat.get("embed_available")),
        "coverage": "全篇均匀覆盖（首段 + 末段 + 等距抽样）",
        # 技能详情（名称 / 提示词正文）不再进入报告记录：仅保留数量，避免报告暴露细节
        "skill_count": len(get_active_skill_list(ai)),
        # 统一数据层：生成产出草稿箱与入库情况（全链路溯源）
        "datastore": ds_stat,
        "system_prompt": compose_system(ai, _sys_prompt(_ai_mode(ai))),
        "review_prompt": review_prompt,
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def test_connection(ai: Dict[str, Any]) -> Tuple[bool, str]:
    """测试 AI 连接：发送最小请求，返回 (是否成功, 说明)。

    连通性判定：服务可达且模型返回了非空响应即视为成功
    （不同模型对“请回复 ok”的遵循度不同，不据此判失败）。
    """
    cfg = {**DEFAULTS, **(ai or {})}
    mode = cfg.get("mode") or "local"
    sync_note = ""
    if mode == "local":
        model, sync_note = resolve_local_model(cfg)
        if sync_note:
            cfg["model"] = model
    try:
        if mode == "local":
            content = _call_ollama(cfg, [{"role": "user", "content": "你好，请回复：ok"}])
        else:
            content = _call_openai(cfg, [{"role": "user", "content": "你好，请回复：ok"}])
        ok = bool((content or "").strip())
        msg = f"连接成功（模型响应：{clip(content, 60)}）" if ok \
            else "连接成功但模型未返回内容"
        if sync_note:
            msg += f"；{sync_note}"
        ai_activity.log_event("test", model=str(cfg.get("model", "")), detail=msg, ok=ok)
        return ok, msg
    except AiError as exc:
        ai_activity.log_event("test", model=str(cfg.get("model", "")), detail=str(exc), ok=False)
        return False, str(exc)