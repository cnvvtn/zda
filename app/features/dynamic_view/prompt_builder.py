# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：集中维护动态视图模板路径与 prompt 构建逻辑。"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.settings import settings
from app.features.chat.schemas import ConversationContextMessage, RoleProfileData
from app.features.dynamic_view.clue_context_support import build_clue_keywords
from app.features.dynamic_view.model_output_support import (
    extract_scene_primary_texts,
)
from app.features.dynamic_view.schemas import (
    DynamicViewClueItem,
    DynamicViewCreateRequest,
    normalize_dynamic_view_subtitle_languages,
    normalize_dynamic_view_template_type,
    resolve_dynamic_view_subtitle_language_name,
)
from app.features.dynamic_view.subject_type_support import (
    build_dynamic_view_subject_type_reference_text,
)
from app.services.chat_prompt_message_support import build_dialogue_history_xml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_DIR = _PROJECT_ROOT / "prompt"


# 执行resolve prompt spatial config相关逻辑。
def _resolve_prompt_spatial_config(request: DynamicViewCreateRequest | None) -> dict[str, str]:
    """根据模板比例返回 prompt 中需要注入的画布和数学坐标系描述。"""
    template_type = normalize_dynamic_view_template_type(
        request.template_type if request else None
    )
    if template_type == "portrait_9_16":
        return {
            "canvas_resolution": "1080x1920 resolution, Center (540, 960).",
            "math_x_range": "[-5, 5]",
            "math_y_range": "[-5, 13]",
            "origin_description": "The origin (0,0) is located at the lower part of the screen (specifically at pixel Y=1387).",
        }
    return {
        "canvas_resolution": "1920x1080 resolution, Center (960, 540).",
        "math_x_range": "[-8, 8]",
        "math_y_range": "[-2, 6]",
        "origin_description": "The origin (0,0) is slightly below the exact center of the screen.",
    }


# 执行build dynamic view customization config相关逻辑。
def build_dynamic_view_customization_config(
    request: DynamicViewCreateRequest | None,
) -> dict[str, object]:
    """统一收口动态视图自定义视图参数，供各 prompt 节点复用。"""
    if request is None:
        subtitle_languages = normalize_dynamic_view_subtitle_languages(None)
        return {
            "visual_style_override": "",
            "subtitle_languages": subtitle_languages,
        }
    subtitle_languages = normalize_dynamic_view_subtitle_languages(request.subtitle_languages)
    return {
        "visual_style_override": request.visual_style_override.strip(),
        "subtitle_languages": subtitle_languages,
    }


# 执行resolve subtitle language placeholders相关逻辑。
def _resolve_subtitle_language_placeholders(
    subtitle_languages: list[str],
) -> tuple[str, str]:
    """为旧版双语占位符生成稳定回填值。"""
    normalized_subtitle_languages = normalize_dynamic_view_subtitle_languages(
        subtitle_languages
    )
    primary_language_code = (
        normalized_subtitle_languages[0] if normalized_subtitle_languages else "zh"
    )
    secondary_language_code = (
        normalized_subtitle_languages[1]
        if len(normalized_subtitle_languages) >= 2
        else "en"
    )
    return (
        resolve_dynamic_view_subtitle_language_name(primary_language_code),
        resolve_dynamic_view_subtitle_language_name(secondary_language_code),
    )


# 执行build metadata customization block相关逻辑。
def _build_metadata_customization_block(request: DynamicViewCreateRequest | None) -> str:
    """为 metadata/detail 节点补充用户指定的口吻与语言要求。"""
    customization_config = build_dynamic_view_customization_config(request)
    subtitle_languages = customization_config["subtitle_languages"]
    if not isinstance(subtitle_languages, list):
        subtitle_languages = []
    primary_language_name, secondary_language_name = _resolve_subtitle_language_placeholders(
        subtitle_languages
    )
    visual_style_override = str(customization_config["visual_style_override"]).strip()
    extra_lines = [
        f"[Primary Subtitle Language]\n{primary_language_name}",
        f"[Secondary Subtitle Language]\n{secondary_language_name}",
    ]
    if visual_style_override:
        extra_lines.append(f"[Forced Visual Style]\n{visual_style_override}")
    return "\n\n".join(extra_lines)


# 执行resolve versioned prompt path相关逻辑。
def _resolve_versioned_prompt_path(relative_path: str, *, prompt_version: int | None = None) -> Path:
    """按显式版本或当前配置解析模板路径，不做跨版本回退。"""
    resolved_prompt_version = (
        settings.llm.resolve_task_flow_version() if prompt_version is None else int(prompt_version)
    )
    if resolved_prompt_version not in {1, 2}:
        raise ValueError(f"未知的动态视图 prompt 版本：{resolved_prompt_version}")
    return _PROMPT_DIR / f"v{resolved_prompt_version}" / relative_path


# 定义DynamicViewScriptMode。
class DynamicViewScriptMode:
    """动态视图剧本模式常量，统一约束 node1 模板选择。"""

    GAME = "game"
    KNOWLEDGE = "knowledge"


# 执行build node1 prompt相关逻辑。
def build_node1_prompt(
    topic: str,
    scene_count_min: int,
    *,
    script_mode: str,
    request: DynamicViewCreateRequest | None = None,
    prompt_version: int | None = None,
) -> list[BaseMessage]:
    """把 node1 模板整理成单条 user 消息，并注入主题、自定义视图参数与分镜数量。"""
    # 执行resolve node1 prompt path相关逻辑。
    prompt_path = _resolve_node1_prompt_path(script_mode, prompt_version=prompt_version)
    # 执行read text file相关逻辑。
    if not prompt_path.exists():
        raise FileNotFoundError(f"未找到动态视图模板文件：{prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")
    # 执行inject subject scene prompt values相关逻辑。
    merged_prompt_text = _inject_subject_scene_prompt_values(
        prompt_text=prompt_text,
        topic=topic,
        scene_count_min=scene_count_min,
        request=request,
    )
    return [HumanMessage(content=merged_prompt_text)]


# 执行build dynamic view task prompt相关逻辑。
def build_dynamic_view_task_prompt(
    task_count: int,
    *,
    prompt_version: int | None = None,
) -> list[BaseMessage]:
    """把任务生成节点拆成 system 指令和 user 输入消息。"""
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "dynamic_view_prompt_topic.txt",
            prompt_version=prompt_version,
        )
    )
    user_prompt_text = f"请生成 {task_count} 个任务主题。"
    return _build_instruction_messages(
        prompt_text,
        user_prompt_text,
    )


# 执行build dynamic view topic analysis prompt相关逻辑。
def build_dynamic_view_topic_analysis_prompt(topic: str) -> list[BaseMessage]:
    """把用户主题整理成违规分析模型需要的系统指令和用户输入。"""
    instruction_text = (
        "你是知搭动态视图生成前的主题合规分析器。你的任务只有一件事：判断用户 topic 是否违法违规。\n\n"
        "判断规则：\n"
        "- 政治敏感、违法违规、色情、暴力违法、攻击仇恨、犯罪教学、规避监管等违法违规话题，isViolation=true。\n"
        "- 不检查完整性，不检查是否适合展示，不做道德约束；只要不违法违规，一律 isComplete=true、displayable=true、analysisStatus=passed。\n"
        "- 违法违规时 isViolation=true、displayable=false、analysisStatus=violation。\n"
        "- analysisStatus 只能是 passed、violation 二者之一。\n"
        "- 不要给推荐主题，不要拆分子话题，不要扩展用户意图。\n"
        "- decisionSummary 和 displayableReason 使用中文短句。"
    )
    user_text = f"请分析这个 topic：{topic.strip()}"
    return _build_instruction_messages(instruction_text, user_text)


# 执行build metadata prompt相关逻辑。
def build_metadata_prompt(
    topic: str,
    scene_texts: list[dict[str, str]],
    *,
    request: DynamicViewCreateRequest | None = None,
    prompt_version: int | None = None,
) -> list[BaseMessage]:
    """把 metadata 节点拆成 system 指令和 user 输入消息。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "metadata/know.txt",
            prompt_version=prompt_version,
        )
    ).replace(
        "{{subject_type_reference_block}}",
        # 执行build dynamic view subject type reference text相关逻辑。
        build_dynamic_view_subject_type_reference_text(),
    )
    # 执行format scene primary text lines相关逻辑。
    joined_scene_text_lines = _format_scene_primary_text_lines(scene_texts)
    metadata_customization_block = _build_metadata_customization_block(request)
    return _build_instruction_messages(
        prompt_text,
        (
            f"【主题】\n{topic.strip()}\n\n"
            f"【分镜文本】\n{joined_scene_text_lines}\n\n"
            f"{metadata_customization_block}"
        ),
    )


# 执行build knowledge detail prompt相关逻辑。
def build_knowledge_detail_prompt(
    *,
    topic: str,
    scene_texts: list[dict[str, str]],
    request: DynamicViewCreateRequest | None = None,
    prompt_version: int | None = None,
) -> list[BaseMessage]:
    """把知识详情节点拆成 system 指令和 user 输入消息。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "metadata/detail.text",
            prompt_version=prompt_version,
        )
    )
    # 执行format scene primary text lines相关逻辑。
    joined_scene_text_lines = _format_scene_primary_text_lines(scene_texts)
    metadata_customization_block = _build_metadata_customization_block(request)
    return _build_instruction_messages(
        prompt_text,
        (
            f"【主题】\n{topic.strip()}\n\n"
            f"【分镜文本】\n{joined_scene_text_lines}\n\n"
            f"{metadata_customization_block}"
        ),
    )


# 执行build game metadata prompt相关逻辑。
def build_game_metadata_prompt(
    *,
    topic: str,
    final_question: str,
    scene_texts: list[dict[str, str]],
    request: DynamicViewCreateRequest | None = None,
    prompt_version: int | None = None,
) -> list[BaseMessage]:
    """把游戏元数据节点拆成 system 指令和 user 输入消息。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "metadata/game.txt",
            prompt_version=prompt_version,
        )
    )
    # 执行replace相关逻辑。
    joined_scene_text_lines = _format_scene_primary_text_lines(scene_texts)
    instruction_text, _ = _split_prompt_text(prompt_text, "【主题】")
    normalized_instruction_text = instruction_text.replace(
        "{{subject_type_reference_block}}",
        build_dynamic_view_subject_type_reference_text(),
    )
    metadata_customization_block = _build_metadata_customization_block(request)
    return _build_instruction_messages(
        normalized_instruction_text,
        (
            f"【主题】\n{topic.strip()}\n\n"
            f"【最终问题】\n{final_question.strip()}\n\n"
            f"【分镜文本】\n{joined_scene_text_lines}\n\n"
            f"{metadata_customization_block}"
        ),
    )


# 执行build scene character prompt相关逻辑。
def build_scene_character_prompt(
    *,
    topic: str,
    subject_type: str,
    scene_texts: list[dict[str, str]],
    prompt_version: int | None = None,
) -> list[BaseMessage]:
    """把角色抽取节点拆成 system 指令和 user 输入消息。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "character/scene_extract.txt",
            prompt_version=prompt_version,
        )
    )
    # 执行format scene text blocks相关逻辑。
    joined_scene_blocks = _format_scene_text_blocks(scene_texts)
    return _build_instruction_messages(
        prompt_text,
        (
            f"【topic】\n{topic.strip()}\n\n"
            f"【subject_type】\n{subject_type.strip()}\n\n"
            f"【scene_texts】\n{joined_scene_blocks}"
        ),
    )


# 执行build clue match prompt相关逻辑。
def build_clue_match_prompt(
    *,
    final_question: str,
    chat_messages: list[ConversationContextMessage],
    unresolved_clues: list[DynamicViewClueItem],
    prompt_version: int | None = None,
) -> list[BaseMessage]:
    """把线索判定节点拆成 system 指令和 user 输入消息。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "clue/clue_match.txt",
            prompt_version=prompt_version,
        )
    )
    instruction_text, _ = _split_prompt_text(prompt_text, "## 【最终问题】")
    chat_history_xml = build_dialogue_history_xml(chat_messages)
    unresolved_clues_block = _format_clues_block(
        unresolved_clues,
        include_match_key=True,
    )
    return _build_instruction_messages(
        instruction_text,
        (
            f"【最终问题】\n{final_question.strip()}\n\n"
            f"【未解锁线索】\n{unresolved_clues_block}\n\n"
            f"【聊天记录】\n{chat_history_xml}"
        ),
    )


# 执行build knowledge revealed role chat prompt相关逻辑。
def build_knowledge_revealed_role_chat_prompt(
    *,
    role_profile: RoleProfileData,
    prompt_version: int | None = None,
) -> str:
    """读取知识视图已揭晓后的角色问答模板，并注入当前角色名。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "clue/role_chat_revealed.txt",
            prompt_version=prompt_version,
        )
    )
    return prompt_text.replace("{{role_name}}", role_profile.name.strip() or "当前角色")


# 执行build generation role chat prompt相关逻辑。
def build_generation_role_chat_prompt(
    *,
    role_profile: RoleProfileData,
    prompt_version: int | None = None,
) -> str:
    """读取未揭晓阶段 generation 模板，并注入数据库角色字段拼接的人设块。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "generation_prompt.txt",
            prompt_version=prompt_version,
        )
    )
    role_name = role_profile.name.strip() or "当前角色"
    role_description = (
        role_profile.role_description.strip()
        if role_profile.role_description
        else ""
    )
    persona = role_profile.persona.strip() if role_profile.persona else ""
    personality = role_profile.personality.strip() if role_profile.personality else ""
    scene = role_profile.scene.strip() if role_profile.scene else ""
    nsfw_setting = role_profile.nsfw_setting.strip() if role_profile.nsfw_setting else ""
    category_name = role_profile.category_name.strip() if role_profile.category_name else ""
    author = role_profile.author.strip() if role_profile.author else ""
    dynamic_view_vivid_subtitles = (
        role_profile.dynamic_view_chinese_subtitles.strip()
        if role_profile.dynamic_view_chinese_subtitles
        else ""
    )
    dynamic_view_vivid_subtitles_block = (
        f"【故事剧情】\n{dynamic_view_vivid_subtitles}"
        if dynamic_view_vivid_subtitles
        else ""
    )
    character_profile_lines = [
        f"【个人角色】：你是 {role_name}。绝对不能扮演或代替其他角色发言。",
        "【角色人设】：",
        f"【角色名称】：{role_name}",
    ]
    if category_name:
        character_profile_lines.append(f"【角色分类】：{category_name}")
    if role_description:
        character_profile_lines.append(f"【角色简介】：{role_description}")
    if persona:
        character_profile_lines.append(f"【核心身份】：{persona}")
    if personality:
        character_profile_lines.append(f"【性格特质】：{personality}")
    if scene:
        character_profile_lines.append(f"【首次场景】：{scene}")
    if nsfw_setting:
        character_profile_lines.append(f"【关系设定】：{nsfw_setting}")
    if author:
        character_profile_lines.append(f"【作者】：{author}")
    character_profile_text = "\n".join(character_profile_lines).strip()
    if not prompt_text.strip():
        return character_profile_text
    rendered_prompt_text = prompt_text
    for placeholder, replacement in (
        ("{{role_name}}", role_name),
        ("{{category_name}}", category_name),
        ("{{role_description}}", role_description),
        ("{{persona}}", persona),
        ("{{personality}}", personality),
        ("{{scene}}", scene),
        ("{{nsfw_setting}}", nsfw_setting),
        ("{{author}}", author),
        ("{{supplement}}", ""),
        ("{{character_profile}}", character_profile_text),
        ("{{dynamic_view_vivid_subtitles_block}}", dynamic_view_vivid_subtitles_block),
    ):
        rendered_prompt_text = rendered_prompt_text.replace(placeholder, replacement)
    if "{{character_profile}}" not in prompt_text:
        rendered_prompt_text = f"{rendered_prompt_text.strip()}\n\n{character_profile_text}"
    return rendered_prompt_text.strip()


# 执行build node2 step1 prompt相关逻辑。
def build_node2_step1_prompt(
    node1_clean_text: str,
    *,
    topic: str,
    scene_count_min: int,
    script_mode: str,
    prompt_version: int | None = None,
) -> str:
    """拼接 node2 step1 所需输入，并统一注入主题与分镜数量上下文。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "dynamic_view_prompt2.txt",
            prompt_version=prompt_version,
        )
    )
    # 执行read text file相关逻辑。
    timeline_data_reference = read_text_file(
        _resolve_timeline_data_path(
            script_mode,
            prompt_version=prompt_version,
        )
    )
    # 执行build subject scene context block相关逻辑。
    subject_scene_context = _build_subject_scene_context_block(
        topic=topic,
        scene_count_min=scene_count_min,
    )
    return (
        f"{subject_scene_context}\n\n"
        f"{prompt_text}\n\n"
        f"{timeline_data_reference}\n\n"
        f"{node1_clean_text.strip()}\n"
    )


# 执行build node2 step2 prompt相关逻辑。
def build_node2_step2_prompt(
    node2_step1_output: str,
    *,
    topic: str,
    scene_count_min: int,
    script_mode: str,
    prompt_version: int | None = None,
) -> str:
    """拼接 node2 step2 所需输入，并统一注入主题与分镜数量上下文。"""
    # 执行read text file相关逻辑。
    prompt_text = read_text_file(
        _resolve_versioned_prompt_path(
            "dynamic_view_prompt3.txt",
            prompt_version=prompt_version,
        )
    )
    # script_mode 预留给后续分模式模板切换，当前阶段只做参数收口。
    _ = script_mode
    # 执行build subject scene context block相关逻辑。
    subject_scene_context = _build_subject_scene_context_block(
        topic=topic,
        scene_count_min=scene_count_min,
    )
    return f"{subject_scene_context}\n\n{prompt_text}\n\n{node2_step1_output.strip()}\n"


# 执行build instruction messages相关逻辑。
def _build_instruction_messages(
    instruction_text: str,
    user_text: str,
) -> list[BaseMessage]:
    """把固定指令和运行时输入拆成 system 与 user 两条消息。"""
    return [
        SystemMessage(content=instruction_text.strip()),
        HumanMessage(content=user_text.strip()),
    ]


# 执行split prompt text相关逻辑。
def _split_prompt_text(prompt_text: str, marker: str) -> tuple[str, str]:
    """按给定标记切开模板正文，前半部分作为 system 指令，后半部分交给调用方重建 user 输入。"""
    marker_index = prompt_text.find(marker)
    if marker_index < 0:
        return prompt_text.strip(), ""
    return prompt_text[:marker_index].strip(), prompt_text[marker_index:].strip()


# 执行get html template path相关逻辑。
def get_html_template_path(
    template_type: str = "landscape_16_9",
    *,
    prompt_version: int | None = None,
) -> Path:
    """按模板类型返回动态视图 HTML 骨架路径。"""
    normalized_template_type = normalize_dynamic_view_template_type(template_type)
    if normalized_template_type == "portrait_9_16":
        return _resolve_versioned_prompt_path(
            "dynamic_view_template/dynamic_view_template_portrait.html",
            prompt_version=prompt_version,
        )
    return _resolve_versioned_prompt_path(
        "dynamic_view_template/dynamic_view_template_landscape.html",
        prompt_version=prompt_version,
    )


# 执行read text file相关逻辑。
def read_text_file(file_path: Path) -> str:
    """读取模板文件并在缺失时直接抛错。"""
    if not file_path.exists():
        raise FileNotFoundError(f"未找到动态视图模板文件：{file_path}")
    return file_path.read_text(encoding="utf-8")


# 执行resolve node1 prompt path相关逻辑。
def _resolve_node1_prompt_path(
    script_mode: str,
    *,
    prompt_version: int | None = None,
) -> Path:
    """按剧本模式选择 node1 模板路径。"""
    if script_mode == DynamicViewScriptMode.GAME:
        return _resolve_versioned_prompt_path(
            "dynamic_view_prompt1/game.txt",
            prompt_version=prompt_version,
        )
    if script_mode == DynamicViewScriptMode.KNOWLEDGE:
        return _resolve_versioned_prompt_path(
            "dynamic_view_prompt1/know.txt",
            prompt_version=prompt_version,
        )
    raise ValueError(f"未知的动态视图剧本模式：{script_mode}")


# 执行resolve timeline data path相关逻辑。
def _resolve_timeline_data_path(
    script_mode: str,
    *,
    prompt_version: int | None = None,
) -> Path:
    """按视图类型选择 node2 step2 使用的 TimelineData 参考模板路径。"""
    if script_mode == DynamicViewScriptMode.GAME:
        return _resolve_versioned_prompt_path(
            "timelinedata/game.txt",
            prompt_version=prompt_version,
        )
    if script_mode == DynamicViewScriptMode.KNOWLEDGE:
        return _resolve_versioned_prompt_path(
            "timelinedata/know.txt",
            prompt_version=prompt_version,
        )
    raise ValueError(f"未知的动态视图剧本模式：{script_mode}")


# 执行format scene primary text lines相关逻辑。
def _format_scene_primary_text_lines(scene_texts: list[dict[str, str]]) -> str:
    """把每幕主文本整理成 metadata prompt 需要的编号列表。"""
    normalized_lines: list[str] = []
    # 执行extract scene primary texts相关逻辑。
    primary_scene_texts = extract_scene_primary_texts(scene_texts)
    for index, primary_text in enumerate(primary_scene_texts, start=1):
        if primary_text:
            # 执行append相关逻辑。
            normalized_lines.append(f"{index}. {primary_text}")
    if not normalized_lines:
        return "1. 未提取到可用的分镜文本，请仅根据主题做最小整理。"
    return "\n".join(normalized_lines)


# 执行format scene text blocks相关逻辑。
def _format_scene_text_blocks(scene_texts: list[dict[str, str]]) -> str:
    """把每幕 texts 结构整理成角色抽取 prompt 需要的场景块。"""
    formatted_scene_blocks: list[str] = []
    for index, scene_text in enumerate(scene_texts, start=1):
        scene_lines = [f"场景{index}："]
        # 角色抽取只使用 vivid 主文本，不传 sub/ext。
        field_value = scene_text.get("vivid", "").strip()
        if field_value:
            # 执行append相关逻辑。
            scene_lines.append(f"- vivid: {field_value}")
        if len(scene_lines) > 1:
            # 执行append相关逻辑。
            formatted_scene_blocks.append("\n".join(scene_lines))
    if not formatted_scene_blocks:
        return "场景1：\n- vivid: 未提取到可用的分镜文本，请仅根据主题谨慎抽取。"
    return "\n\n".join(formatted_scene_blocks)


# 执行format clues block相关逻辑。
def _format_clues_block(
    clues: list[DynamicViewClueItem],
    *,
    include_match_key: bool = False,
) -> str:
    """把线索列表整理成 prompt 可读列表，避免把程序字段名直接暴露给模型。"""
    if not clues:
        return "当前没有可用线索。"
    return "\n".join(
        _format_single_clue_block(
            clue,
            index=index,
            include_match_key=include_match_key,
        )
        for index, clue in enumerate(clues, start=1)
    )


# 执行format single clue block相关逻辑。
def _format_single_clue_block(
    clue: DynamicViewClueItem,
    *,
    index: int | None = None,
    include_match_key: bool = False,
) -> str:
    """把单条线索整理成更自然的文本块，供不同 prompt 直接复用。"""
    clue_label = clue.clue_title.strip() or f"线索{index or 1}"
    clue_lines = [f"标题：{clue_label}", f"线索内容：{clue.clue_content.strip()}"]
    if not include_match_key:
        clue_keywords = "、".join(build_clue_keywords(clue))
        clue_lines.insert(1, f"关键词：{clue_keywords}")
    if include_match_key:
        clue_lines.insert(0, f"匹配键：{clue.clue_key}")
    if index is None:
        return "\n".join(clue_lines)
    return f"{index}.\n" + "\n".join(clue_lines)


# 执行inject subject scene prompt values相关逻辑。
def _inject_subject_scene_prompt_values(
    *,
    prompt_text: str,
    topic: str,
    scene_count_min: int,
    request: DynamicViewCreateRequest | None = None,
) -> str:
    """统一替换模板里的主题、分镜和自定义视图占位符，兼容 v1/v2 prompt 版本。"""
    normalized_topic = topic.strip()
    normalized_scene_count = int(scene_count_min)
    customization_config = build_dynamic_view_customization_config(request)
    subtitle_languages = customization_config["subtitle_languages"]
    if not isinstance(subtitle_languages, list):
        subtitle_languages = []
    language1, language2 = _resolve_subtitle_language_placeholders(subtitle_languages)
    visual_style_override = str(customization_config["visual_style_override"]).strip()
    if visual_style_override:
        visual_style_block = (
            "Ignore the automatic style deduction for this run and enforce this explicit visual style: "
            f"{visual_style_override}"
        )
    else:
        visual_style_block = (
            "Automatically infer the most suitable visual style from the topic domain and render it with a top-tier documentary or keynote-level texture."
        )
    normalized_prompt_text = (
        prompt_text.replace("XXX", normalized_topic)
        .replace(": N", f": {normalized_scene_count}")
        .replace("：N", f"：{normalized_scene_count}")
    )
    spatial_config = _resolve_prompt_spatial_config(request)
    customization_replacements = (
        ("{{CANVAS_RESOLUTION}}", spatial_config["canvas_resolution"]),
        ("{{MATH_X_RANGE}}", spatial_config["math_x_range"]),
        ("{{MATH_Y_RANGE}}", spatial_config["math_y_range"]),
        ("{{ORIGIN_DESCRIPTION}}", spatial_config["origin_description"]),
        ("{{VISUAL_STYLE_OVERRIDE}}", visual_style_block),
        ("{{LANGUAGE1}}", language1),
        ("{{LANGUAGE2}}", language2),
    )
    for placeholder_text, replacement_text in customization_replacements:
        normalized_prompt_text = normalized_prompt_text.replace(
            placeholder_text,
            replacement_text,
        )
    return f"{normalized_prompt_text.strip()}"


# 执行build subject scene context block相关逻辑。
def _build_subject_scene_context_block(*, topic: str, scene_count_min: int) -> str:
    """统一构造节点 prompt 的主题与分镜数量上下文块。"""
    return (
        f"[Subject & Theme]: {topic.strip()}\n"
        f"[Expected Number of Scenes]: {int(scene_count_min)}"
    )
