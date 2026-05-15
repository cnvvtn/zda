# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：集中维护动态视图 HTML 模板组装与配色回填逻辑。"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlsplit, urlunsplit

from app.features.dynamic_view.prompt_builder import get_html_template_path, read_text_file
from app.features.dynamic_view.theme_support import DynamicViewThemeColors


_HTML_TEMPLATE_START_MARKER = "        /* [MODULE_TIMELINE_START] */"
_HTML_TEMPLATE_END_MARKER = "        /* [MODULE_TIMELINE_END] */"
_MOBILE_INPUT_FOCUS_PATCH_ID = "zda-mobile-input-focus-patch"
_MOBILE_INPUT_FOCUS_PATCH = f"""
<style id="{_MOBILE_INPUT_FOCUS_PATCH_ID}-style">
@media (max-width: 480px) {{
    #hud-layer {{ transform: none !important; }}
}}
#chat-input {{ -webkit-user-select: text; user-select: text; }}
</style>
<script id="{_MOBILE_INPUT_FOCUS_PATCH_ID}">
(function initZdaMobileInputFocusPatch() {{
    // 执行focus zda chat input相关逻辑。
    function focusZdaChatInput(event) {{
        var chatInput = document.getElementById('chat-input');
        if (!chatInput) return;
        event.stopPropagation();
        chatInput.focus({{ preventScroll: true }});
    }}

    // 执行bind zda chat input touch focus相关逻辑。
    function bindZdaChatInputTouchFocus() {{
        var chatInput = document.getElementById('chat-input');
        var chatInputWrapper = document.getElementById('chat-input-wrapper');
        if (!chatInput || chatInput.dataset.mobileFocusBound === '1') return;
        chatInput.dataset.mobileFocusBound = '1';
        chatInput.addEventListener('touchstart', focusZdaChatInput, {{ passive: true }});
        if (chatInputWrapper) {{
            chatInputWrapper.addEventListener('touchstart', focusZdaChatInput, {{ passive: true }});
        }}
    }}

    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', bindZdaChatInputTouchFocus, {{ once: true }});
    }} else {{
        bindZdaChatInputTouchFocus();
    }}
}})();
</script>
"""


# 执行assemble dynamic view html相关逻辑。
def assemble_dynamic_view_html(
    timeline_code: str,
    dynamic_css_code: str,
    theme_colors: DynamicViewThemeColors,
    template_type: str = "landscape_16_9",
    *,
    prompt_version: int | None = None,
) -> str:
    """把模型产出的时间轴和样式注入 HTML 骨架。"""
    # 执行read text file相关逻辑。
    html_template = read_text_file(
        get_html_template_path(
            template_type,
            prompt_version=prompt_version,
        )
    )
    insert_block = (
        f"{_HTML_TEMPLATE_START_MARKER}\n"
        f"{timeline_code}\n"
        f"{dynamic_css_code}\n"
        f"        {_HTML_TEMPLATE_END_MARKER.strip()}"
    )
    # 执行find相关逻辑。
    start_index = html_template.find(_HTML_TEMPLATE_START_MARKER)
    # 执行find相关逻辑。
    end_index = html_template.find(_HTML_TEMPLATE_END_MARKER)
    if start_index < 0 or end_index < 0 or end_index < start_index:
        raise ValueError("动态视图 HTML 骨架缺少合法的模块标记区")
    end_index += len(_HTML_TEMPLATE_END_MARKER)
    assembled_html = html_template[:start_index] + insert_block + html_template[end_index:]
    # 执行apply theme colors to html template相关逻辑。
    return apply_theme_colors_to_html_template(assembled_html, theme_colors)


# 执行inject dynamic view audio config相关逻辑。
def inject_dynamic_view_audio_config(
    html_template: str,
    *,
    audio: object | None,
    subtitle_audio: object | None,
    base_url: str = "",
) -> str:
    """向模板固定 audio 标签注入运行时配置接口，避免 HTML 持有固定音频地址和音量。"""
    html_template = _remove_legacy_audio_runtime(html_template)
    html_template = inject_mobile_input_focus_patch(html_template)
    background_audio = _normalize_audio_config(audio)
    subtitle_audio_config = _normalize_audio_config(subtitle_audio)
    config_url = _build_audio_config_url(background_audio, subtitle_audio_config, base_url)
    html_template = _upsert_audio_tag(
        html_template,
        element_id="zda-background-audio",
        config_url=config_url,
        loop=True,
    )
    return _upsert_audio_tag(
        html_template,
        element_id="zda-subtitle-audio",
        config_url=config_url,
        loop=False,
    )


# 执行inject mobile input focus patch相关逻辑。
def inject_mobile_input_focus_patch(html_template: str) -> str:
    """给已落盘的动态视图 HTML 注入移动端 iframe 输入框聚焦补丁。"""
    html_template = _remove_mobile_input_focus_patch(html_template)
    if "</body>" in html_template:
        return html_template.replace("</body>", f"{_MOBILE_INPUT_FOCUS_PATCH}\n</body>", 1)
    return f"{html_template}\n{_MOBILE_INPUT_FOCUS_PATCH}"


# 执行remove mobile input focus patch相关逻辑。
def _remove_mobile_input_focus_patch(html_template: str) -> str:
    """移除旧的移动端输入框聚焦补丁。"""
    cleaned_html = re.sub(
        rf'\s*<style id="{_MOBILE_INPUT_FOCUS_PATCH_ID}-style">[\s\S]*?</style>',
        "",
        html_template,
        count=1,
    )
    return re.sub(
        rf'\s*<script id="{_MOBILE_INPUT_FOCUS_PATCH_ID}">[\s\S]*?</script>',
        "",
        cleaned_html,
        count=1,
    )


# 执行remove legacy audio runtime相关逻辑。
def _remove_legacy_audio_runtime(html_template: str) -> str:
    """移除旧音频运行时配置脚本。"""
    cleaned_html = re.sub(
        r'\s*<script id="zda-audio-config" type="application/json">[\s\S]*?</script>',
        "",
        html_template,
        count=1,
    )
    cleaned_html = re.sub(
        r'\s*<script id="zda-audio-runtime">[\s\S]*?</script>',
        "",
        cleaned_html,
        count=1,
    )
    return cleaned_html


# 执行upsert audio tag相关逻辑。
def _upsert_audio_tag(
    html_template: str,
    *,
    element_id: str,
    config_url: str,
    loop: bool,
) -> str:
    """替换模板里的固定 audio 标签，旧 HTML 没有标签时补到 body 里。"""
    audio_tag = _build_audio_tag(
        element_id=element_id,
        config_url=config_url,
        loop=loop,
    )
    tag_pattern = rf'<audio\b[^>]*id="{re.escape(element_id)}"[^>]*>[\s\S]*?</audio>'
    if re.search(tag_pattern, html_template, flags=re.IGNORECASE):
        return re.sub(tag_pattern, audio_tag, html_template, count=1, flags=re.IGNORECASE)
    body_match = re.search(r"<body[^>]*>", html_template, flags=re.IGNORECASE)
    if body_match is None:
        return f"{audio_tag}\n{html_template}"
    insert_index = body_match.end()
    return f"{html_template[:insert_index]}\n{audio_tag}{html_template[insert_index:]}"


# 执行build audio tag相关逻辑。
def _build_audio_tag(
    *,
    element_id: str,
    config_url: str,
    loop: bool,
) -> str:
    """生成隐藏 audio 标签，只保存运行时配置接口。"""
    escaped_config_url = escape(config_url, quote=True)
    loop_attribute = " loop" if loop else ""
    return (
        f'<audio id="{element_id}" '
        f'preload="auto"{loop_attribute} style="display:none" '
        f'data-config-url="{escaped_config_url}"></audio>'
    )


# 执行build audio config url相关逻辑。
def _build_audio_config_url(
    audio: dict[str, object] | None,
    subtitle_audio: dict[str, object] | None,
    base_url: str,
) -> str:
    """从音频接口路径推导运行时配置接口地址。"""
    source_path = ""
    if audio is not None:
        source_path = str(audio["path"])
    elif subtitle_audio is not None:
        source_path = str(subtitle_audio["path"])
    normalized_path = source_path.strip()
    if not normalized_path:
        return ""
    config_path = _replace_audio_path_tail(normalized_path)
    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url or config_path.startswith(("http://", "https://")):
        return config_path
    return f"{normalized_base_url}/{config_path.lstrip('/')}"


# 执行replace audio path tail相关逻辑。
def _replace_audio_path_tail(path: str) -> str:
    """把背景音乐或字幕音频接口路径替换成同存档的音频配置接口。"""
    parsed_url = urlsplit(path)
    normalized_path = parsed_url.path
    if normalized_path.endswith("/subtitle-audio"):
        normalized_path = normalized_path[: -len("/subtitle-audio")] + "/audio-config"
    elif normalized_path.endswith("/audio"):
        normalized_path = normalized_path[: -len("/audio")] + "/audio-config"
    replaced_url = parsed_url._replace(path=normalized_path, query="", fragment="")
    return urlunsplit(replaced_url)


# 执行normalize audio config相关逻辑。
def _normalize_audio_config(audio: object | None) -> dict[str, object] | None:
    """把 Pydantic 音频配置或字典统一转成可写入 HTML 的 JSON。"""
    if audio is None:
        return None
    if hasattr(audio, "model_dump"):
        raw_config = audio.model_dump(by_alias=True)
    elif isinstance(audio, dict):
        raw_config = audio
    else:
        return None
    path = str(raw_config.get("path") or "").strip()
    if not path:
        return None
    return {
        "path": path,
    }


# 执行apply theme colors to html template相关逻辑。
def apply_theme_colors_to_html_template(
    html_template: str,
    theme_colors: DynamicViewThemeColors,
) -> str:
    """把 palette 主题色回填到完整 HTML。"""
    themed_html = html_template
    if theme_colors.background_color is not None:
        # 执行replace css property value相关逻辑。
        themed_html = replace_css_property_value(
            themed_html,
            selector="#app-container",
            property_name="background",
            property_value=theme_colors.background_color,
        )
        # 执行replace css property value相关逻辑。
        themed_html = replace_css_property_value(
            themed_html,
            selector="#subtitle-area",
            property_name="background",
            property_value=theme_colors.background_color,
        )
    if theme_colors.pattern_color is not None:
        # 执行replace css property value相关逻辑。
        themed_html = replace_css_property_value(
            themed_html,
            selector="#progress-bar-container",
            property_name="background",
            property_value=theme_colors.pattern_color,
        )
    if theme_colors.highlight_color is not None:
        # 执行replace css property value相关逻辑。
        themed_html = replace_css_property_value(
            themed_html,
            selector="#sub-cn-vivid",
            property_name="color",
            property_value=theme_colors.highlight_color,
        )
        # 执行replace css property value相关逻辑。
        themed_html = replace_css_property_value(
            themed_html,
            selector="#progress-bar",
            property_name="background",
            property_value=theme_colors.highlight_color,
        )
        themed_html, marker_replace_count = re.subn(
            r'(<marker id="arrow-dynamic"[\s\S]*?<path[^>]*\bfill=")([^"]+)(")',
            rf'\1{theme_colors.highlight_color}\3',
            themed_html,
            count=1,
        )
        if marker_replace_count != 1:
            raise ValueError("动态视图 HTML 模板缺少 arrow-dynamic 填充色位置")
    # 执行append palette lock style相关逻辑。
    themed_html = append_palette_lock_style(themed_html, theme_colors)
    return themed_html


# 执行append palette lock style相关逻辑。
def append_palette_lock_style(
    html_template: str,
    theme_colors: DynamicViewThemeColors,
) -> str:
    """在最终 HTML 里追加 palette 锁定样式，避免模型动态 CSS 覆盖字幕背景。"""
    style_rules: list[str] = []
    if theme_colors.background_color is not None:
        style_rules.append(f"#app-container {{ background: {theme_colors.background_color} !important; }}")
        style_rules.append(f"#subtitle-area {{ background: {theme_colors.background_color} !important; }}")
    if theme_colors.pattern_color is not None:
        style_rules.append(f"#progress-bar-container {{ background: {theme_colors.pattern_color} !important; }}")
    if theme_colors.highlight_color is not None:
        style_rules.append(f"#progress-bar {{ background: {theme_colors.highlight_color} !important; }}")
    if not style_rules:
        return html_template
    palette_lock_style = "\n".join(
        [
            '<style id="zda-palette-lock">',
            *style_rules,
            "</style>",
        ]
    )
    if "</body>" in html_template:
        return html_template.replace("</body>", f"{palette_lock_style}\n</body>", 1)
    return f"{html_template}\n{palette_lock_style}"


# 执行replace css property value相关逻辑。
def replace_css_property_value(
    html_template: str,
    *,
    selector: str,
    property_name: str,
    property_value: str,
) -> str:
    """按选择器与属性名精确替换样式值。"""
    replace_pattern = (
        rf"({re.escape(selector)}\s*\{{[^}}]*?\b{re.escape(property_name)}\s*:\s*)([^;]+)(;)"
    )
    replaced_html, replace_count = re.subn(
        replace_pattern,
        rf"\1{property_value}\3",
        html_template,
        count=0,
        flags=re.DOTALL,
    )
    if replace_count < 1:
        raise ValueError(f"动态视图 HTML 模板缺少样式位置：{selector} -> {property_name}")
    return replaced_html
