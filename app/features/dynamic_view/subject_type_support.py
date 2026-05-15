# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：统一维护动态视图父子分类、提示词参考文本与本地兜底分类规则。"""

from __future__ import annotations

DYNAMIC_VIEW_SUBJECT_TYPE_REFERENCE: tuple[
    tuple[str, tuple[tuple[str, str, tuple[str, ...]], ...]],
    ...,
] = (
    (
        "心理学",
        (
            (
                "认知心理",
                "注意力、记忆、思维偏差、决策机制、认知加工。",
                (
                    "认知",
                    "注意力",
                    "记忆",
                    "思维",
                    "决策",
                    "偏差",
                    "心智",
                    "大脑",
                ),
            ),
            (
                "行为心理",
                "习惯形成、行为动机、条件反射、行为改变。",
                (
                    "行为",
                    "习惯",
                    "动机",
                    "奖励",
                    "拖延",
                    "自律",
                    "条件反射",
                ),
            ),
            (
                "发展心理",
                "成长阶段、依恋模式、人格形成、原生家庭影响。",
                (
                    "发展心理",
                    "成长阶段",
                    "依恋",
                    "人格",
                    "原生家庭",
                    "童年",
                    "青少年",
                ),
            ),
            (
                "社会心理",
                "群体互动、人际认知、从众效应、社会角色。",
                (
                    "社会心理",
                    "从众",
                    "群体",
                    "人际",
                    "社交",
                    "关系边界",
                    "沟通模式",
                ),
            ),
        ),
    ),
    (
        "恋爱关系",
        (
            (
                "亲密关系经营",
                "长期关系中的安全感、陪伴感、信任与稳定投入。",
                (
                    "亲密关系",
                    "长期关系",
                    "婚姻",
                    "伴侣",
                    "安全感",
                    "信任",
                    "相处",
                ),
            ),
            (
                "恋爱沟通",
                "表达需求、处理误会、倾听回应、有效沟通。",
                (
                    "恋爱沟通",
                    "表白",
                    "沟通",
                    "误会",
                    "冷战",
                    "争吵",
                    "倾听",
                    "表达需求",
                ),
            ),
            (
                "吸引与追求",
                "建立吸引、推进关系、边界判断、追求策略。",
                (
                    "吸引",
                    "追求",
                    "暧昧",
                    "好感",
                    "脱单",
                    "约会",
                    "表白",
                ),
            ),
            (
                "分手与修复",
                "关系破裂、情感拉扯、修复尝试、分离重建。",
                (
                    "分手",
                    "复合",
                    "挽回",
                    "失恋",
                    "修复关系",
                    "情感拉扯",
                ),
            ),
        ),
    ),
    (
        "学习方法",
        (
            (
                "记忆方法",
                "编码、提取、联想、间隔重复与长期记忆策略。",
                (
                    "记忆",
                    "背诵",
                    "联想",
                    "间隔重复",
                    "遗忘曲线",
                    "记忆宫殿",
                ),
            ),
            (
                "理解策略",
                "费曼学习法、类比迁移、知识结构化、深度理解。",
                (
                    "理解",
                    "费曼学习法",
                    "类比",
                    "迁移",
                    "知识体系",
                    "结构化",
                ),
            ),
            (
                "复习方法",
                "错题整理、回顾节奏、知识巩固、复盘方法。",
                (
                    "复习",
                    "错题",
                    "复盘",
                    "巩固",
                    "回顾",
                    "查漏补缺",
                ),
            ),
            (
                "学习习惯",
                "专注、计划、执行、拖延治理、学习节律。",
                (
                    "学习习惯",
                    "专注",
                    "计划",
                    "执行",
                    "拖延",
                    "时间管理",
                    "番茄钟",
                ),
            ),
        ),
    ),
    (
        "科学原理",
        (
            (
                "自然科学原理",
                "物理、化学、生物、天文、自然现象的底层机制。",
                (
                    "物理",
                    "化学",
                    "生物",
                    "自然科学",
                    "天文",
                    "宇宙",
                    "地理",
                    "地质",
                    "海洋",
                    "气候",
                    "气象",
                    "量子",
                    "力学",
                    "双缝干涉",
                    "电流",
                    "黑洞",
                ),
            ),
            (
                "技术系统原理",
                "计算机、AI、工程系统、互联网与技术机制。",
                (
                    "技术",
                    "工程",
                    "计算机",
                    "互联网",
                    "ai",
                    "人工智能",
                    "编程",
                    "代码",
                    "数据库",
                    "网络安全",
                    "python",
                    "java",
                    "flutter",
                    "rag",
                    "大数据",
                    "神经网络",
                    "机器学习",
                    "深度学习",
                    "操作系统",
                    "半导体",
                    "芯片",
                    "5g",
                    "6g",
                    "卫星通信",
                    "光纤",
                    "机械",
                    "3d打印",
                ),
            ),
            (
                "生命健康原理",
                "人体、药物、免疫、营养、睡眠与健康机制。",
                (
                    "人体",
                    "医学",
                    "健康",
                    "器官",
                    "免疫",
                    "神经",
                    "营养",
                    "睡眠",
                    "运动",
                    "急救",
                    "药物",
                    "疫苗",
                    "病毒",
                    "细菌",
                    "多巴胺",
                ),
            ),
            (
                "数学逻辑原理",
                "数学模型、概率统计、推理结构与逻辑规则。",
                (
                    "数学",
                    "几何",
                    "代数",
                    "概率",
                    "统计",
                    "方程",
                    "微积分",
                    "逻辑",
                    "推理",
                    "博弈论",
                    "算法",
                    "悖论",
                    "拉格朗日",
                    "a*",
                ),
            ),
            (
                "社会人文原理",
                "历史、经济、法律、社会运行与文化现象背后的机制。",
                (
                    "历史",
                    "文明",
                    "考古",
                    "法律",
                    "经济",
                    "货币",
                    "理财",
                    "市场",
                    "社会学",
                    "人类学",
                    "人口",
                    "城市",
                    "文化差异",
                    "语言",
                    "文字",
                    "民俗",
                    "非遗",
                ),
            ),
        ),
    ),
    (
        "情绪成长",
        (
            (
                "情绪认知",
                "识别情绪来源、理解情绪信号、建立觉察能力。",
                (
                    "情绪",
                    "情绪认知",
                    "情绪来源",
                    "觉察",
                    "感受",
                    "情绪信号",
                ),
            ),
            (
                "情绪调节",
                "稳定情绪、缓解内耗、建立调节工具与节奏。",
                (
                    "情绪调节",
                    "内耗",
                    "崩溃",
                    "稳定情绪",
                    "放松",
                    "舒缓",
                    "调节",
                ),
            ),
            (
                "压力管理",
                "焦虑、压力、倦怠与恢复机制。",
                (
                    "压力",
                    "焦虑",
                    "紧张",
                    "倦怠",
                    " burnout",
                    "恢复",
                    "压抑",
                ),
            ),
            (
                "自我成长",
                "自我接纳、边界建立、价值感、自我修复。",
                (
                    "自我成长",
                    "自我接纳",
                    "边界",
                    "价值感",
                    "自卑",
                    "疗愈",
                    "成长",
                    "复原",
                ),
            ),
        ),
    ),
)

_DEFAULT_PARENT_TYPE = "科学原理"
_DEFAULT_SUBJECT_TYPE = "自然科学原理"


# 执行build subject parent type map相关逻辑。
def _build_subject_parent_type_map() -> dict[str, str]:
    """构建子分类到父分类的映射表。"""
    return {
        subject_type: parent_type
        for parent_type, children in DYNAMIC_VIEW_SUBJECT_TYPE_REFERENCE
        for subject_type, _, _ in children
    }


_SUBJECT_TYPE_TO_PARENT_TYPE = _build_subject_parent_type_map()
_SUBJECT_PARENT_TYPES = tuple(parent_type for parent_type, _ in DYNAMIC_VIEW_SUBJECT_TYPE_REFERENCE)
_SUBJECT_TYPES = tuple(_SUBJECT_TYPE_TO_PARENT_TYPE.keys())
_PARENT_DEFAULT_SUBJECT_TYPE_MAP = {
    parent_type: children[0][0]
    for parent_type, children in DYNAMIC_VIEW_SUBJECT_TYPE_REFERENCE
}
_LEGACY_SUBJECT_TYPE_TO_NEW_SUBJECT_TYPE = {
    "自然科学类": "自然科学原理",
    "工程与技术类": "技术系统原理",
    "医学与健康类": "生命健康原理",
    "社会与人文类": "社会人文原理",
    "数学与逻辑类": "数学逻辑原理",
    "生活与实用科普": "自然科学原理",
    "环境与地球类": "自然科学原理",
    "艺术与文化科普": "社会人文原理",
    "前沿与未来类": "技术系统原理",
    "心理学": "认知心理",
    "恋爱关系": "亲密关系经营",
    "学习方法": "理解策略",
    "科学原理": "自然科学原理",
    "情绪成长": "情绪认知",
    "其他": _DEFAULT_SUBJECT_TYPE,
}


# 执行normalize dynamic view subject type相关逻辑。
def normalize_dynamic_view_subject_type(subject_type: str) -> str:
    """统一清洗并兼容动态视图子分类文本。"""
    normalized_subject_type = subject_type.strip()
    if not normalized_subject_type:
        return _DEFAULT_SUBJECT_TYPE
    if normalized_subject_type in _LEGACY_SUBJECT_TYPE_TO_NEW_SUBJECT_TYPE:
        return _LEGACY_SUBJECT_TYPE_TO_NEW_SUBJECT_TYPE[normalized_subject_type]
    return normalized_subject_type


# 执行normalize dynamic view subject parent type相关逻辑。
def normalize_dynamic_view_subject_parent_type(subject_parent_type: str) -> str:
    """统一清洗动态视图父分类文本。"""
    normalized_subject_parent_type = subject_parent_type.strip()
    if not normalized_subject_parent_type:
        return ""
    if normalized_subject_parent_type in _SUBJECT_PARENT_TYPES:
        return normalized_subject_parent_type
    if normalized_subject_parent_type in _LEGACY_SUBJECT_TYPE_TO_NEW_SUBJECT_TYPE:
        return resolve_dynamic_view_subject_parent_type(normalized_subject_parent_type)
    if normalized_subject_parent_type in _SUBJECT_TYPE_TO_PARENT_TYPE:
        return _SUBJECT_TYPE_TO_PARENT_TYPE[normalized_subject_parent_type]
    return normalized_subject_parent_type


# 执行resolve dynamic view subject parent type相关逻辑。
def resolve_dynamic_view_subject_parent_type(subject_type: str) -> str:
    """根据子分类或旧分类值解析所属父分类。"""
    normalized_subject_type = normalize_dynamic_view_subject_type(subject_type)
    return _SUBJECT_TYPE_TO_PARENT_TYPE.get(normalized_subject_type, _DEFAULT_PARENT_TYPE)


# 执行normalize dynamic view subject taxonomy相关逻辑。
def normalize_dynamic_view_subject_taxonomy(
    subject_type: str,
    subject_parent_type: str = "",
) -> tuple[str, str]:
    """统一收口动态视图父子分类，兼容旧值与空值。"""
    normalized_subject_parent_type = normalize_dynamic_view_subject_parent_type(
        subject_parent_type
    )
    normalized_subject_type = normalize_dynamic_view_subject_type(subject_type)
    if normalized_subject_type in _SUBJECT_TYPE_TO_PARENT_TYPE:
        return normalized_subject_type, _SUBJECT_TYPE_TO_PARENT_TYPE[normalized_subject_type]
    if normalized_subject_type in _SUBJECT_PARENT_TYPES:
        resolved_subject_type = _PARENT_DEFAULT_SUBJECT_TYPE_MAP[normalized_subject_type]
        return resolved_subject_type, normalized_subject_type
    if normalized_subject_parent_type in _SUBJECT_PARENT_TYPES:
        return normalized_subject_type, normalized_subject_parent_type
    return normalized_subject_type, _DEFAULT_PARENT_TYPE


# 执行validate dynamic view subject type相关逻辑。
def validate_dynamic_view_subject_type(subject_type: str) -> str:
    """统一规范动态视图子分类文本。"""
    normalized_subject_type, _ = normalize_dynamic_view_subject_taxonomy(subject_type)
    return normalized_subject_type


# 执行validate dynamic view subject parent type相关逻辑。
def validate_dynamic_view_subject_parent_type(subject_parent_type: str) -> str:
    """统一规范动态视图父分类文本。"""
    normalized_subject_parent_type = normalize_dynamic_view_subject_parent_type(
        subject_parent_type
    )
    return normalized_subject_parent_type or _DEFAULT_PARENT_TYPE


# 执行build dynamic view subject type reference text相关逻辑。
def build_dynamic_view_subject_type_reference_text() -> str:
    """把父分类与子分类参考整理成可直接注入提示词的文本块。"""
    lines: list[str] = []
    for parent_type, children in DYNAMIC_VIEW_SUBJECT_TYPE_REFERENCE:
        # 执行append相关逻辑。
        lines.append(f"- 父分类：{parent_type}")
        for subject_type, description, _ in children:
            # 执行append相关逻辑。
            lines.append(f"  - 子分类：{subject_type}；说明：{description}")
    return "\n".join(lines)


# 执行infer subject taxonomy相关逻辑。
def infer_subject_taxonomy(topic: str) -> tuple[str, str]:
    """当模型没有返回可用分类时，按主题关键词推断父子分类。"""
    normalized_topic = topic.strip().lower()
    for parent_type, children in DYNAMIC_VIEW_SUBJECT_TYPE_REFERENCE:
        for subject_type, _, keywords in children:
            if contains_any_keyword(normalized_topic, keywords):
                return parent_type, subject_type
    return _DEFAULT_PARENT_TYPE, _DEFAULT_SUBJECT_TYPE


# 执行infer subject type相关逻辑。
def infer_subject_type(topic: str) -> str:
    """兼容旧调用方，继续只返回推断出的子分类。"""
    _, subject_type = infer_subject_taxonomy(topic)
    return subject_type


# 执行contains any keyword相关逻辑。
def contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """封装关键词命中判断，避免各分类分支重复写 any 逻辑。"""
    return any(keyword.lower() in text for keyword in keywords)
