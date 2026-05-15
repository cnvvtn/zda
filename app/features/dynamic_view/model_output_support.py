# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：统一封装动态视图模型输出清洗、解析与流式能力判断。"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from app.features.dynamic_view.schemas import DynamicViewClueItem, DynamicViewCreateRequest
from app.services.prompt_runner import PromptRunner


# 执行normalize create request相关逻辑。
def normalize_create_request(request: DynamicViewCreateRequest) -> DynamicViewCreateRequest:
    """统一清洗创建请求里的主题文本。"""
    # 执行strip相关逻辑。
    normalized_topic = request.topic.strip() or "当前主题"
    return request.model_copy(update={"topic": normalized_topic})


# 执行runner allows streaming相关逻辑。
def runner_allows_streaming(runner: PromptRunner) -> bool:
    """读取 PromptRunner 底层客户端是否支持流式输出。"""
    client = runner.client
    if hasattr(client, "allows_streaming"):
        return bool(client.allows_streaming())
    # 执行getattr相关逻辑。
    profile = getattr(client, "profile", None)
    return bool(getattr(profile, "stream", False))


# 执行try parse structured document相关逻辑。
def try_parse_structured_document(raw_text: str) -> Any | None:
    """尽力把模型输出解析成 JSON 或 YAML 结构。"""
    # 执行extract code block text相关逻辑。
    normalized_text = extract_code_block_text(raw_text)
    if not normalized_text:
        return None
    try:
        return json.loads(normalized_text)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(normalized_text)
    except yaml.YAMLError:
        return None


# 执行extract final question相关逻辑。
def extract_final_question(structured_output: Any | None, raw_text: str) -> str:
    """从游戏剧本输出里提取最终提问。"""
    if isinstance(structured_output, dict):
        for field_name in ("final_question", "finalQuestion"):
            # 执行get相关逻辑。
            field_value = str(structured_output.get(field_name, "")).strip()
            if field_value:
                # 执行normalize extracted final question相关逻辑。
                return normalize_extracted_final_question(field_value)
    for pattern in (
        r"^\s*final_question\s*:\s*(?P<value>[^\r\n]+)",
        r"^\s*finalQuestion\s*:\s*(?P<value>[^\r\n]+)",
        r"^\s*(?:\*\*)?最终问题(?:\*\*)?\s*[：:]\s*(?P<value>[^\r\n]+)",
    ):
        match = re.search(
            pattern,
            raw_text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if match is None:
            continue
        # 执行normalize extracted final question相关逻辑。
        return normalize_extracted_final_question(match.group("value"))
    return ""


# 执行normalize extracted final question相关逻辑。
def normalize_extracted_final_question(raw_value: str) -> str:
    """把最终问题收敛成单行短句，避免把后续 YAML 或 Markdown 一起吞进去。"""
    normalized_value = str(raw_value).strip()
    if not normalized_value:
        return ""
    normalized_value = normalized_value.splitlines()[0].strip()
    for marker in ("```", "---", "###", "## ", "# "):
        if marker in normalized_value:
            # 执行split相关逻辑。
            normalized_value = normalized_value.split(marker, 1)[0].strip()
    normalized_value = normalized_value.strip().strip('"').strip("'").strip()
    # 执行sub相关逻辑。
    normalized_value = re.sub(r"\s+", " ", normalized_value)
    return normalized_value[:1000].strip()


# 执行normalize scene text field name相关逻辑。
def normalize_scene_text_field_name(raw_field_name: str) -> str:
    """统一清洗字幕字段名，保留 vivid/ext 和动态语言键。"""
    normalized_field_name = str(raw_field_name or "").strip().lower()
    if not normalized_field_name:
        return ""
    return normalized_field_name.replace("_", "-")


# 执行extract scene text mapping相关逻辑。
def extract_scene_text_mapping(raw_text_mapping: Any) -> dict[str, str]:
    """从模型输出的 text/texts 对象里提取可落库字幕字段。"""
    if not isinstance(raw_text_mapping, dict):
        return {}
    normalized_scene_text: dict[str, str] = {}
    for raw_field_name, raw_field_value in raw_text_mapping.items():
        normalized_field_name = normalize_scene_text_field_name(raw_field_name)
        normalized_text = str(raw_field_value or "").strip()
        if not normalized_field_name or not normalized_text:
            continue
        normalized_scene_text[normalized_field_name] = normalized_text
    return normalized_scene_text


# 执行extract scene texts from node1 output相关逻辑。
def extract_scene_texts_from_node1_output(structured_output: Any | None) -> list[dict[str, str]]:
    """从 node1 结构化输出中提取 scene_subtitles 可落库文本。"""
    if not isinstance(structured_output, dict):
        return []
    scenes = structured_output.get("scenes")
    if not isinstance(scenes, list):
        return []
    scene_texts: list[dict[str, str]] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        text_mapping = scene.get("text")
        if not isinstance(text_mapping, dict):
            text_mapping = scene.get("texts")
        normalized_scene_text = extract_scene_text_mapping(text_mapping)
        if normalized_scene_text:
            scene_texts.append(normalized_scene_text)
    return scene_texts


# 执行extract game clues相关逻辑。
def extract_game_clues(structured_output: Any | None) -> list[DynamicViewClueItem]:
    """从游戏剧本结构里提取线索列表。"""
    if not isinstance(structured_output, dict):
        return []
    normalized_clues: list[DynamicViewClueItem] = []
    seen_clue_keys: set[str] = set()
    # 执行get相关逻辑。
    global_settings = structured_output.get("global_settings")
    if isinstance(global_settings, dict):
        # 执行get相关逻辑。
        entities = global_settings.get("entities")
        if isinstance(entities, list):
            for index, entity in enumerate(entities, start=1):
                if not isinstance(entity, dict):
                    continue
                # 执行normalize clue key相关逻辑。
                clue_key = normalize_clue_key(str(entity.get("id", "")).strip() or f"clue_{index}")
                if not clue_key or clue_key in seen_clue_keys:
                    continue
                # 执行get相关逻辑。
                clue_content = str(entity.get("clue_info", "")).strip()
                if not clue_content:
                    continue
                # 执行add相关逻辑。
                seen_clue_keys.add(clue_key)
                # 执行append相关逻辑。
                normalized_clues.append(
                    DynamicViewClueItem(
                        clueKey=clue_key,
                        clueTitle=_resolve_clue_title(
                            str(entity.get("id", "")).strip(),
                            index=index,
                        ),
                        clueContent=clue_content,
                        unlocked=False,
                    )
                )
    if normalized_clues:
        return normalized_clues
    # 执行get相关逻辑。
    scenes = structured_output.get("scenes")
    if not isinstance(scenes, list):
        return []
    fallback_index = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        # 执行get相关逻辑。
        interactive_hints = scene.get("interactive_hints")
        if not isinstance(interactive_hints, list):
            continue
        for hint_text in interactive_hints:
            normalized_hint_text = str(hint_text).strip()
            if not normalized_hint_text:
                continue
            fallback_index += 1
            clue_key = normalize_clue_key(f"线索{fallback_index}:{normalized_hint_text}")
            if clue_key in seen_clue_keys:
                continue
            # 执行add相关逻辑。
            seen_clue_keys.add(clue_key)
            # 执行append相关逻辑。
            normalized_clues.append(
                DynamicViewClueItem(
                    clueKey=clue_key,
                    clueTitle=f"线索{fallback_index}",
                    clueContent=normalized_hint_text,
                    unlocked=False,
                )
            )
    return normalized_clues


# 执行extract code block text相关逻辑。
def extract_code_block_text(raw_text: str) -> str:
    """移除 Markdown 围栏与模型残留标记，只保留正文。"""
    # 执行strip相关逻辑。
    normalized_text = raw_text.strip()
    # 执行sub相关逻辑。
    normalized_text = re.sub(r"^`{3,}[a-zA-Z0-9_-]*\s*", "", normalized_text)
    # 执行sub相关逻辑。
    normalized_text = re.sub(r"\s*`{3,}$", "", normalized_text)
    # 执行sub相关逻辑。
    normalized_text = re.sub(r"^[`]+", "", normalized_text)
    # 执行sub相关逻辑。
    normalized_text = re.sub(r"[`]+\s*$", "", normalized_text)
    # 执行strip model artifact markers相关逻辑。
    normalized_text = strip_model_artifact_markers(normalized_text)
    return normalized_text.strip()


# 执行strip model artifact markers相关逻辑。
def strip_model_artifact_markers(raw_text: str) -> str:
    """清理 [Image #1] 一类模型附带噪声。"""
    normalized_text = raw_text
    # 执行sub相关逻辑。
    normalized_text = re.sub(
        r"<image\s+name=\[Image\s*#\d+\]>\s*</image>",
        "",
        normalized_text,
        flags=re.IGNORECASE,
    )
    # 执行sub相关逻辑。
    normalized_text = re.sub(
        r"<image\s+name=\[Image\s*#\d+\]>",
        "",
        normalized_text,
        flags=re.IGNORECASE,
    )
    # 执行sub相关逻辑。
    normalized_text = re.sub(
        r"\[Image\s*#\d+\]",
        "",
        normalized_text,
        flags=re.IGNORECASE,
    )
    # 执行sub相关逻辑。
    normalized_text = re.sub(r"\n{3,}", "\n\n", normalized_text)
    return normalized_text


# 执行clean model output text相关逻辑。
def clean_model_output_text(raw_text: str) -> str:
    """输出供后续 prompt 继续拼接的干净文本。"""
    return extract_code_block_text(raw_text)


# 执行extract timeline assets from node1 output相关逻辑。
def extract_timeline_assets_from_node1_output(raw_text: str) -> tuple[str, str]:
    """从 node1 原始输出里拆出 TimelineData 与 dynamicCSS 两段脚本。"""
    # 执行extract code block text相关逻辑。
    normalized_text = extract_code_block_text(raw_text)
    if not normalized_text:
        return "", ""
    timeline_match = re.search(
        r"\b(?:var|const|let)\s+TimelineData\s*=",
        normalized_text,
        flags=re.IGNORECASE,
    )
    dynamic_css_match = re.search(
        r"\b(?:var|const|let)\s+dynamicCSS\s*=",
        normalized_text,
        flags=re.IGNORECASE,
    )
    if timeline_match is None and dynamic_css_match is None:
        return "", ""
    if timeline_match is None:
        return "", normalized_text[dynamic_css_match.start() :].strip()
    timeline_start = timeline_match.start()
    if dynamic_css_match is None or dynamic_css_match.start() <= timeline_start:
        return sanitize_timeline_text_literals(normalized_text[timeline_start:].strip()), ""
    timeline_code = normalized_text[timeline_start : dynamic_css_match.start()].strip()
    dynamic_css_code = normalized_text[dynamic_css_match.start() :].strip()
    return sanitize_timeline_text_literals(timeline_code), dynamic_css_code




# 执行sanitize Timeline Text Literals相关逻辑。
def sanitize_timeline_text_literals(timeline_code: str) -> str:
    """统一清理 TimelineData 里字幕文本中的单双引号，避免符号歧义。"""
    normalized_code = timeline_code.strip()
    if not normalized_code:
        return ""

    def replace_timeline_text_literal(match: re.Match[str]) -> str:
        field_prefix = match.group("prefix")
        decoded_value = decode_timeline_text_value(match.group("value"))
        sanitized_value = sanitize_timeline_text_content(decoded_value)
        encoded_value = encode_timeline_text_value(sanitized_value)
        return f'{field_prefix}"{encoded_value}"'

    return re.sub(
        r"(?P<prefix>\b(?:vivid|ext|sub)\s*:\s*)(?P<quote>['\"`])(?P<value>(?:\\.|[\s\S])*?)(?P=quote)",
        replace_timeline_text_literal,
        normalized_code,
        flags=re.IGNORECASE,
    )



# 执行sanitize timeline text field names相关逻辑。
def sanitize_timeline_text_field_names(block_text: str) -> str:
    """统一把 texts 里的字幕字段名规范化，兼容大小写和下划线键名。"""
    return re.sub(
        r"(?P<prefix>[{,]\s*)(?P<field_name>[A-Za-z][A-Za-z0-9_-]*)\s*:",
        lambda match: f"{match.group('prefix')}{normalize_scene_text_field_name(match.group('field_name'))}:",
        block_text,
    )


# 执行extract timeline text mapping相关逻辑。
def extract_timeline_text_mapping(block_text: str) -> dict[str, str]:
    """从单个 texts 代码块里提取所有字符串字幕字段。"""
    normalized_block_text = sanitize_timeline_text_field_names(block_text)
    scene_text_mapping: dict[str, str] = {}
    for match in re.finditer(
        r"(?P<field_name>[A-Za-z][A-Za-z0-9_-]*)\s*:\s*(?P<quote>['\"`])(?P<value>(?:\\.|[\s\S])*?)(?P=quote)",
        normalized_block_text,
        flags=re.IGNORECASE,
    ):
        normalized_field_name = normalize_scene_text_field_name(match.group("field_name"))
        if not normalized_field_name:
            continue
        decoded_value = decode_timeline_text_value(match.group("value"))
        if not decoded_value:
            continue
        scene_text_mapping[normalized_field_name] = decoded_value
    return scene_text_mapping


# 执行extract scene texts from timeline code相关逻辑。
def extract_scene_texts_from_timeline_code(timeline_code: str) -> list[dict[str, str]]:
    """用正则从 node2 生成的 TimelineData 代码里提取每一幕 texts 字段。"""
    normalized_code = timeline_code.strip()
    if not normalized_code:
        return []
    scene_texts: list[dict[str, str]] = []
    text_blocks = re.findall(
        r"texts\s*:\s*\{(?P<body>[\s\S]*?)\}\s*(?:,|\n|\r)",
        normalized_code,
        flags=re.IGNORECASE,
    )
    for block_text in text_blocks:
        normalized_scene_text = extract_timeline_text_mapping(block_text)
        if normalized_scene_text:
            scene_texts.append(normalized_scene_text)
    return scene_texts


# 执行extract scene primary texts相关逻辑。
def extract_scene_primary_texts(scene_texts: list[dict[str, str]]) -> list[str]:
    """按 ext -> vivid -> sub -> 其他字段 的优先级提取每幕主文本。"""
    primary_texts: list[str] = []
    for scene_text in scene_texts:
        ordered_field_names = ["ext", "vivid", "sub"]
        ordered_field_names.extend(
            field_name
            for field_name in scene_text.keys()
            if field_name not in {"ext", "vivid", "sub"}
        )
        for field_name in ordered_field_names:
            current_text = scene_text.get(field_name, "").strip()
            if current_text:
                primary_texts.append(current_text)
                break
    return primary_texts


# 执行sanitize dynamic view subtitle text相关逻辑。
def sanitize_dynamic_view_subtitle_text(text: str | None) -> str:
    """按字幕音频口播规则清理文本后参与时长计算和 TTS。"""
    sanitized_text = re.sub(
        r"[\U00010000-\U0010ffff\uD800-\uDBFF\uDC00-\uDFFF]",
        "",
        str(text or ""),
    )
    return sanitized_text.replace("<", "").replace(">", "").strip()


# 执行count dynamic view duration units相关逻辑。
def count_dynamic_view_duration_units(text: str | None) -> int:
    """计算动态视图时长单位，英文单词按一个单位，其他字符按字符计。"""
    normalized_text = sanitize_dynamic_view_subtitle_text(text)
    english_word_pattern = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
    unit_count = 0
    cursor_index = 0
    for word_match in english_word_pattern.finditer(normalized_text):
        # 执行sub相关逻辑。
        non_english_text = re.sub(r"\s+", "", normalized_text[cursor_index:word_match.start()])
        unit_count += len(non_english_text)
        unit_count += 1
        cursor_index = word_match.end()
    # 执行sub相关逻辑。
    tail_text = re.sub(r"\s+", "", normalized_text[cursor_index:])
    return unit_count + len(tail_text)


# 执行calculate dynamic durations相关逻辑。
def calculate_dynamic_durations(scene_texts: list[dict[str, str]]) -> list[int]:
    """按文本预清理后的固定口播规则计算每幕动态视图时长，单位毫秒。"""
    base_duration_ms = 1500
    ms_per_vivid_char = 150
    ms_per_ext_char = 50
    dynamic_durations: list[int] = []
    for scene_text in scene_texts:
        # 执行count dynamic view duration units相关逻辑。
        vivid_unit_count = count_dynamic_view_duration_units(scene_text.get("vivid", ""))
        # 执行count dynamic view duration units相关逻辑。
        ext_unit_count = count_dynamic_view_duration_units(scene_text.get("ext", ""))
        # 执行append相关逻辑。
        dynamic_durations.append(
            base_duration_ms
            + (vivid_unit_count * ms_per_vivid_char)
            + (ext_unit_count * ms_per_ext_char)
        )
    return dynamic_durations


# 执行inject dynamic durations into timeline code相关逻辑。
def inject_dynamic_durations_into_timeline_code(
    timeline_code: str,
    dynamic_durations: list[int],
) -> str:
    """把后端计算好的每幕时长注入 TimelineData，浏览器只读取固定 duration。"""
    normalized_timeline_code = timeline_code.rstrip()
    # 执行dumps相关逻辑。
    duration_json = json.dumps(dynamic_durations, ensure_ascii=False)
    return (
        f"{normalized_timeline_code}\n"
        f"const ZdaDynamicDurations = {duration_json};\n"
        "TimelineData.forEach((item, index) => {\n"
        "    item.duration = ZdaDynamicDurations[index];\n"
        "});"
    )


# 执行resolve dynamic durations from scene texts相关逻辑。
def resolve_dynamic_durations_from_scene_texts(scene_texts: list[dict[str, str]]) -> list[int]:
    """从分镜数据库字段读取每幕真实时长。"""
    dynamic_durations: list[int] = []
    for scene_text in scene_texts:
        duration_ms = int(str(scene_text.get("durationMs", "0")).strip() or "0")
        dynamic_durations.append(max(0, duration_ms))
    return dynamic_durations


# 执行decode timeline text value相关逻辑。
def decode_timeline_text_value(raw_value: str) -> str:
    """对正则抓到的 JS 字符串做最小反转义，保留实际字幕文本。"""
    normalized_value = raw_value
    escape_mapping = {
        r"\\n": "\n",
        r"\\r": "\r",
        r"\\t": "\t",
        r'\\"': '"',
        r"\\'": "'",
        r"\\`": "`",
        r"\\\\": "\\",
    }
    for source_text, target_text in escape_mapping.items():
        # 执行replace相关逻辑。
        normalized_value = normalized_value.replace(source_text, target_text)
    # 执行strip相关逻辑。
    return normalized_value.strip()


# 执行sanitize Timeline Text Content相关逻辑。
def sanitize_timeline_text_content(raw_value: str) -> str:
    """把字幕正文里的单双引号统一替换成反引号，避免后续拼接时出现符号歧义。"""
    # 执行replace相关逻辑。
    normalized_value = raw_value.replace('"', "`")
    # 执行replace相关逻辑。
    normalized_value = normalized_value.replace("'", "`")
    # 执行strip相关逻辑。
    return normalized_value.strip()


# 执行encode Timeline Text Value相关逻辑。
def encode_timeline_text_value(raw_value: str) -> str:
    """把清洗后的字幕正文重新编码成稳定的 JS 双引号字符串内容。"""
    normalized_value = raw_value
    escape_mapping = {
        "\\": r"\\",
        "\n": r"\n",
        "\r": r"\r",
        "\t": r"\t",
        '"': r"\"",
    }
    for source_text, target_text in escape_mapping.items():
        # 执行replace相关逻辑。
        normalized_value = normalized_value.replace(source_text, target_text)
    return normalized_value


# 执行normalize clue key相关逻辑。
def normalize_clue_key(raw_clue_key: str) -> str:
    """统一清洗线索键，收敛为中文关键词冒号分隔格式。"""
    normalized_clue_key = raw_clue_key.strip()
    if not normalized_clue_key:
        return ""
    # 执行replace相关逻辑。
    normalized_clue_key = normalized_clue_key.replace("：", ":")
    # 执行sub相关逻辑。
    normalized_clue_key = re.sub(r"[\s、，,；;/|]+", ":", normalized_clue_key)
    # 执行sub相关逻辑。
    normalized_clue_key = re.sub(r":+", ":", normalized_clue_key).strip(":")
    normalized_segments: list[str] = []
    for segment in normalized_clue_key.split(":"):
        # 执行sub相关逻辑。
        cleaned_segment = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", segment).strip()
        if len(cleaned_segment) < 2:
            continue
        if cleaned_segment in normalized_segments:
            continue
        normalized_segments.append(cleaned_segment)
    return ":".join(normalized_segments)


# 执行resolve clue title相关逻辑。
def _resolve_clue_title(raw_title: str, *, index: int) -> str:
    """为线索生成稳定标题，优先复用实体 ID。"""
    normalized_title = raw_title.strip()
    if normalized_title:
        return normalized_title
    return f"线索{index}"
