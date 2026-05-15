# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\chat_prompt_message_support.py。"""

from __future__ import annotations

from html import escape

from app.features.chat.schemas import ConversationContextMessage


# 执行build dialogue message xml相关逻辑。
def build_dialogue_message_xml(message: ConversationContextMessage) -> str:
    """把单条会话消息编码成 XML 节点，统一补齐 speaker 和 mention 属性。"""
    content = message["content"].strip()
    speaker = message["speaker"].strip()
    mention = str(message.get("mention", "")).strip()
    mention_attributes = (
        ""
        if not mention
        else f' mention="{escape(mention, quote=True)}"'
    )
    return (
        f'  <message speaker="{escape(speaker, quote=True)}"{mention_attributes}>'
        f"{escape(content, quote=True)}</message>"
    )


# 执行build dialogue history xml相关逻辑。
def build_dialogue_history_xml(
    recent_messages: list[ConversationContextMessage],
) -> str:
    """把 Flutter 上传的上下文整理成 XML 历史块，供多人角色生成统一消费。"""
    xml_lines: list[str] = ["<chat_history>"]
    for message in recent_messages:
        role = message["role"].strip().lower()
        content = message["content"].strip()
        speaker = message["speaker"].strip()
        if not role or not content or not speaker:
            continue
        xml_lines.append(build_dialogue_message_xml(message))
    xml_lines.append("</chat_history>")
    return "\n".join(xml_lines)
