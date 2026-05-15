# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：集中构造动态视图返回载荷，避免流程层重复拼字段。"""

from __future__ import annotations

from app.features.dynamic_view.schemas import (
    DynamicViewAudioConfig,
    DynamicViewClueItem,
    DynamicViewPayload,
    DynamicViewSceneSubtitle,
    normalize_dynamic_view_template_type,
)


# 执行build scene subtitles related logic。
def _build_scene_subtitles(scene_subtitles: list[dict[str, str]] | None) -> list[DynamicViewSceneSubtitle]:
    """统一把字幕字典转换成结构化模型，并把非基础语言收口到 translations。"""
    normalized_scene_subtitles: list[DynamicViewSceneSubtitle] = []
    for scene_subtitle in scene_subtitles or []:
        vivid = str(scene_subtitle.get("vivid", "")).strip()
        ext = str(scene_subtitle.get("ext", "")).strip()
        duration_ms = int(str(scene_subtitle.get("durationMs", "0")).strip() or "0")
        translations = {
            str(language_code or "").strip().lower(): str(text or "").strip()
            for language_code, text in scene_subtitle.items()
            if str(language_code or "").strip().lower() not in {"vivid", "ext", "durationms"}
            and str(text or "").strip()
        }
        if not vivid and not ext and not translations and duration_ms <= 0:
            continue
        normalized_scene_subtitles.append(
            DynamicViewSceneSubtitle(
                vivid=vivid,
                ext=ext,
                durationMs=max(0, duration_ms),
                translations=translations,
            )
        )
    return normalized_scene_subtitles


# 执行build payload相关逻辑。
def build_payload(
    *,
    topic: str,
    title: str = "",
    status: str,
    preview_text: str,
    summary: str,
    view_type: str = "game",
    template_type: str = "landscape_16_9",
    game_view_id: int | None = None,
    knowledge_view_id: int | None = None,
    knowledge_generation_status: str = "idle",
    knowledge_ready: bool = False,
    subject_parent_type: str = "",
    subject_type: str = "",
    detail: str = "",
    final_question: str = "",
    clues: list[DynamicViewClueItem] | None = None,
    current_unlocked_clue_count: int = 0,
    total_clue_count: int | None = None,
    all_clues_unlocked: bool = False,
    view_count: int = 0,
    comment_count: int = 0,
    scene_subtitles: list[dict[str, str]] | None = None,
    total_duration_ms: int = 0,
    html: str = "",
    html_url: str = "",
    audio: DynamicViewAudioConfig | None = None,
    subtitle_audio: DynamicViewAudioConfig | None = None,
) -> DynamicViewPayload:
    """统一构造动态视图接口载荷。"""
    normalized_clues = clues or []
    normalized_scene_subtitles = _build_scene_subtitles(scene_subtitles)
    resolved_total_clue_count = total_clue_count if total_clue_count is not None else len(normalized_clues)
    return DynamicViewPayload(
        viewType=view_type,
        templateType=normalize_dynamic_view_template_type(template_type),
        renderMode="html",
        status=status,
        previewText=preview_text,
        gameViewId=game_view_id,
        knowledgeViewId=knowledge_view_id,
        knowledgeGenerationStatus=knowledge_generation_status,
        knowledgeReady=knowledge_ready,
        title=title.strip(),
        topic=topic,
        subjectParentType=subject_parent_type,
        subjectType=subject_type,
        summary=summary,
        detail=detail,
        finalQuestion=final_question,
        currentUnlockedClueCount=current_unlocked_clue_count,
        totalClueCount=resolved_total_clue_count,
        allCluesUnlocked=all_clues_unlocked,
        clues=normalized_clues,
        viewCount=view_count,
        commentCount=comment_count,
        sceneSubtitles=normalized_scene_subtitles,
        totalDurationMs=max(0, total_duration_ms),
        html=html,
        htmlUrl=html_url,
        audio=audio,
        subtitleAudio=subtitle_audio,
    )
