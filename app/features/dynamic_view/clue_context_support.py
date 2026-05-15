# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：统一封装动态视图线索上下文清洗、关键词提取与相关性选择逻辑。"""

from __future__ import annotations

import re

from app.features.chat.schemas import ConversationContextMessage
from app.features.dynamic_view.schemas import DynamicViewClueItem


_CLUE_SEGMENT_SPLIT_PATTERN = re.compile(r"[，。！？；：、\s/|（）()“”\"'《》【】\[\],]+")


# 执行normalize recent clue context messages相关逻辑。
def normalize_recent_clue_context_messages(
    recent_messages: list[ConversationContextMessage],
    *,
    limit: int,
) -> list[ConversationContextMessage]:
    """统一清洗线索相关接口上传的最近对话，并按调用场景截取最近若干条。"""
    normalized_messages: list[ConversationContextMessage] = []
    for message in recent_messages:
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        speaker = str(message.get("speaker", "")).strip()
        mention = str(message.get("mention", "")).strip()
        if not content:
            continue
        normalized_message: ConversationContextMessage = {
            "role": "assistant" if role == "assistant" else "user",
            "content": content,
            "speaker": speaker or ("助手" if role == "assistant" else "用户"),
        }
        if mention:
            normalized_message["mention"] = mention
        normalized_messages.append(normalized_message)
    return normalized_messages[-limit:]


# 执行build clue keywords相关逻辑。
def build_clue_keywords(clue: DynamicViewClueItem, *, max_keywords: int = 4) -> list[str]:
    """按 clue_key、标题和内容提取少量稳定关键词，优先保留命中关键词。"""
    normalized_keywords: list[str] = []
    candidate_texts = [
        *split_clue_key_segments(clue.clue_key),
        clue.clue_title.strip(),
        *split_clue_text_segments(clue.clue_content),
    ]
    for candidate_text in candidate_texts:
        normalized_candidate = candidate_text.strip()
        if len(normalized_candidate) < 2:
            continue
        if len(normalized_candidate) > 16:
            continue
        if normalized_candidate in normalized_keywords:
            continue
        normalized_keywords.append(normalized_candidate)
        if len(normalized_keywords) >= max_keywords:
            break
    if normalized_keywords:
        return normalized_keywords
    fallback_title = clue.clue_title.strip() or "关键线索"
    return [fallback_title]


# 执行split clue key segments相关逻辑。
def split_clue_key_segments(clue_key: str) -> list[str]:
    """把冒号分隔的 clue_key 拆成稳定关键词片段。"""
    return [segment.strip() for segment in clue_key.replace("：", ":").split(":") if segment.strip()]


# 执行split clue text segments相关逻辑。
def split_clue_text_segments(clue_content: str) -> list[str]:
    """把线索内容按常见停顿切成候选关键词片段，供提示词和相关性判断复用。"""
    raw_segments = _CLUE_SEGMENT_SPLIT_PATTERN.split(clue_content)
    return [segment.strip() for segment in raw_segments if segment.strip()]


# 执行select most relevant clue相关逻辑。
def select_most_relevant_clue(
    unresolved_clues: list[DynamicViewClueItem],
    recent_messages: list[ConversationContextMessage],
) -> DynamicViewClueItem | None:
    """按最近对话和线索关键词相关性选择目标线索，不再依赖固定线索顺序。"""
    if not unresolved_clues:
        return None
    recent_context_text = " ".join(
        message["content"].strip()
        for message in recent_messages
        if message["content"].strip()
    )
    if not recent_context_text:
        return min(unresolved_clues, key=lambda clue: clue.clue_key)
    scored_clues = [
        (
            _compute_clue_context_relevance_score(
                clue=clue,
                recent_context_text=recent_context_text,
            ),
            clue.clue_key,
            clue,
        )
        for clue in unresolved_clues
    ]
    scored_clues.sort(key=lambda item: (-item[0], item[1]))
    return scored_clues[0][2]


# 执行compute clue context relevance score相关逻辑。
def _compute_clue_context_relevance_score(
    *,
    clue: DynamicViewClueItem,
    recent_context_text: str,
) -> int:
    """按 clue_key、标题和内容片段粗略计算线索与最近对话的重合度。"""
    keyword_segments = split_clue_key_segments(clue.clue_key)
    candidate_segments = [
        segment.strip()
        for segment in (
            *keyword_segments,
            clue.clue_title,
            *split_clue_text_segments(clue.clue_content),
        )
        if segment.strip()
    ]
    score = 0
    for candidate_segment in candidate_segments:
        if len(candidate_segment) < 2:
            continue
        if candidate_segment in recent_context_text:
            weight_bonus = 6 if candidate_segment in keyword_segments else 0
            score += max(1, min(len(candidate_segment), 8)) + weight_bonus
    return score
