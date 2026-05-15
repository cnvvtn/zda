# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：统一封装动态视图主题色解析与题材分类逻辑。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.features.dynamic_view.subject_type_support import infer_subject_type


# 定义DynamicViewThemeColors。
@dataclass(frozen=True)
class DynamicViewThemeColors:
    """承接 node1 输出的主题配色。"""

    background_color: str | None = None
    pattern_color: str | None = None
    primary_color: str | None = None
    highlight_color: str | None = None


# 默认主题色：当 metadata 未给出合法颜色时使用。
DEFAULT_BACKGROUND_COLOR = "rgb(13, 17, 36)"
DEFAULT_PATTERN_COLOR = "rgb(43, 54, 96)"
DEFAULT_PRIMARY_COLOR = "rgb(67, 94, 185)"
DEFAULT_HIGHLIGHT_COLOR = "rgb(93, 214, 255)"


# 执行extract theme colors from metadata相关逻辑。
def extract_theme_colors_from_metadata(metadata_output: Any) -> DynamicViewThemeColors:
    """从 metadata 结果中提取主题色，缺失时回退到默认主题色。"""
    background_color = extract_css_color_token(
        _read_metadata_color_value(metadata_output, "background_color")
    )
    pattern_color = extract_css_color_token(
        _read_metadata_color_value(metadata_output, "pattern_color")
    )
    primary_color = extract_css_color_token(
        _read_metadata_color_value(metadata_output, "primary_color")
    )
    highlight_color = extract_css_color_token(
        _read_metadata_color_value(metadata_output, "highlight_color")
    )
    return DynamicViewThemeColors(
        background_color=background_color or DEFAULT_BACKGROUND_COLOR,
        pattern_color=pattern_color or DEFAULT_PATTERN_COLOR,
        primary_color=primary_color or DEFAULT_PRIMARY_COLOR,
        highlight_color=highlight_color or DEFAULT_HIGHLIGHT_COLOR,
    )


# 执行read metadata color value相关逻辑。
def _read_metadata_color_value(metadata_output: Any, color_key: str) -> Any:
    """统一兼容 metadata 对象与 dict 两种读取方式。"""
    if isinstance(metadata_output, dict):
        return metadata_output.get(color_key)
    return getattr(metadata_output, color_key, None)


# 执行extract theme colors from node1 output相关逻辑。
def extract_theme_colors_from_node1_output(
    node1_raw_output: str,
    node1_structured_output: Any,
) -> DynamicViewThemeColors:
    """从 node1 响应中提取主题色，优先 structured_output，缺失时回退 raw_text。"""
    # 执行extract palette color from node1 output相关逻辑。
    background_color = extract_palette_color_from_node1_output(
        node1_raw_output,
        node1_structured_output,
        "bg",
    )
    # 执行extract palette color from node1 output相关逻辑。
    primary_color = extract_palette_color_from_node1_output(
        node1_raw_output,
        node1_structured_output,
        "primary",
    )
    # 执行extract palette color from node1 output相关逻辑。
    highlight_color = extract_palette_color_from_node1_output(
        node1_raw_output,
        node1_structured_output,
        "highlight",
    )
    return DynamicViewThemeColors(
        background_color=background_color or DEFAULT_BACKGROUND_COLOR,
        # 进度条轨道不能和 primary 同色，否则填充进度不可见。
        pattern_color=DEFAULT_PATTERN_COLOR,
        primary_color=primary_color or DEFAULT_PRIMARY_COLOR,
        highlight_color=highlight_color or DEFAULT_HIGHLIGHT_COLOR,
    )


# 执行extract palette color from node1 output相关逻辑。
def extract_palette_color_from_node1_output(
    node1_raw_output: str,
    node1_structured_output: Any,
    color_key: str,
) -> str | None:
    """从 node1 结构化结果或原始文本中提取同一套 palette 颜色。"""
    # 执行extract palette color相关逻辑。
    structured_color = extract_palette_color(node1_structured_output, color_key)
    if structured_color is not None:
        return structured_color
    # 执行extract palette color from raw text相关逻辑。
    return extract_palette_color_from_raw_text(node1_raw_output, color_key)


# 执行extract palette color from raw text相关逻辑。
def extract_palette_color_from_raw_text(raw_text: str, color_key: str) -> str | None:
    """从 node1 原始响应文本中提取 palette 指定颜色，兼容 YAML/JS。"""
    if not isinstance(raw_text, str):
        return None
    normalized_text = raw_text.strip()
    if not normalized_text:
        return None
    # 先尝试 JS: const palette = { ... }。
    js_palette_block_match = re.search(
        r"(?:const|let|var)\s+palette\s*=\s*\{(?P<body>[\s\S]*?)\}",
        normalized_text,
        flags=re.IGNORECASE,
    )
    if js_palette_block_match is not None:
        parsed_color = _extract_palette_color_from_block(js_palette_block_match.group("body"), color_key)
        if parsed_color is not None:
            return parsed_color
    # 再尝试 YAML: palette:\n  bg: ...。
    yaml_palette_block_match = re.search(
        r"palette\s*:\s*(?P<body>(?:\r?\n[ \t]+[^\r\n]+)+)",
        normalized_text,
        flags=re.IGNORECASE,
    )
    if yaml_palette_block_match is not None:
        parsed_color = _extract_palette_color_from_block(yaml_palette_block_match.group("body"), color_key)
        if parsed_color is not None:
            return parsed_color
    return None


# 执行extract palette color from block相关逻辑。
def _extract_palette_color_from_block(block_text: str, color_key: str) -> str | None:
    """从 palette 代码块中提取指定 key 的颜色值。"""
    color_match = re.search(
        rf"\b{re.escape(color_key)}\b\s*:\s*(?P<value>[^\r\n,}}]+)",
        block_text,
        flags=re.IGNORECASE,
    )
    if color_match is None:
        return None
    return extract_css_color_token(color_match.group("value"))
# 执行extract palette color相关逻辑。
def extract_palette_color(structured_output: Any, color_key: str) -> str | None:
    """从结构化输出里的 palette 中提取指定颜色。"""
    if not isinstance(structured_output, dict):
        return None
    # 执行get相关逻辑。
    palette = structured_output.get("palette")
    if isinstance(palette, dict):
        return extract_css_color_token(palette.get(color_key))
    # 执行get相关逻辑。
    global_settings = structured_output.get("global_settings")
    if not isinstance(global_settings, dict):
        return None
    # 执行get相关逻辑。
    palette = global_settings.get("palette")
    if not isinstance(palette, dict):
        return None
    return extract_css_color_token(palette.get(color_key))


# 执行extract css color token相关逻辑。
def extract_css_color_token(value: Any) -> str | None:
    """从模型返回文本中提取首个合法 CSS 颜色。"""
    if not isinstance(value, str):
        return None
    # 执行strip相关逻辑。
    normalized_text = value.strip()
    if not normalized_text:
        return None
    # 执行search相关逻辑。
    hex_match = re.search(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", normalized_text)
    if hex_match is not None:
        red, green, blue = parse_css_color_channels(hex_match.group(0))
        return f"rgb({red}, {green}, {blue})"
    # 执行search相关逻辑。
    rgb_match = re.search(
        r"rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)",
        normalized_text,
        flags=re.IGNORECASE,
    )
    if rgb_match is not None:
        red, green, blue = parse_css_color_channels(rgb_match.group(0))
        return f"rgb({red}, {green}, {blue})"
    return None


# 执行parse css color channels相关逻辑。
def parse_css_color_channels(color_text: str) -> tuple[int, int, int]:
    """把 hex/rgb/rgba 转成 RGB 通道三元组。"""
    # 执行strip相关逻辑。
    normalized_color_text = color_text.strip()
    if normalized_color_text.startswith("#"):
        hex_value = normalized_color_text[1:]
        if len(hex_value) == 3:
            hex_value = "".join(char * 2 for char in hex_value)
        if len(hex_value) != 6:
            raise ValueError(f"不支持的 HEX 颜色格式：{color_text}")
        return (
            # 执行int相关逻辑。
            int(hex_value[0:2], 16),
            # 执行int相关逻辑。
            int(hex_value[2:4], 16),
            # 执行int相关逻辑。
            int(hex_value[4:6], 16),
        )
    # 执行findall相关逻辑。
    channel_matches = re.findall(r"\d{1,3}", normalized_color_text)
    if len(channel_matches) < 3:
        raise ValueError(f"不支持的 RGB 颜色格式：{color_text}")
    red, green, blue = (max(0, min(255, int(channel))) for channel in channel_matches[:3])
    return red, green, blue
