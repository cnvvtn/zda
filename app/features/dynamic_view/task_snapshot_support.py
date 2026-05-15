# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

"""文件说明：集中构造动态视图任务快照与阶段块，避免主服务反复拼装同构字段。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.features.dynamic_view.payload_builder import build_payload
from app.features.dynamic_view.schemas import DynamicViewPayload, DynamicViewTaskSnapshot
from app.features.dynamic_view.subject_type_support import (
    infer_subject_type,
    resolve_dynamic_view_subject_parent_type,
)


@dataclass(frozen=True)
class DynamicViewStreamChunk:
    """动态视图流式阶段结果，统一承接节点状态与摘要信息。"""

    payload: DynamicViewPayload
    is_final: bool
    stage: str
    node_key: str | None = None
    node_title: str | None = None
    node_status: str | None = None
    stream_char_count: int | None = None


@dataclass(frozen=True)
class DynamicViewStageDescriptor:
    """统一描述单个生成节点的阶段文案，避免节点状态文案散落在主流程中。"""

    stage: str
    node_key: str
    node_title: str
    ready_preview_text: str
    processing_preview_text: str
    completed_preview_text: str


def build_processing_payload(
    *,
    template_type: str,
    preview_text: str,
    topic: str,
    archive_id: int | None = None,
    view_type: str = "game",
) -> DynamicViewPayload:
    """统一构造动态视图处理中载荷。"""
    resolved_subject_type = infer_subject_type(topic)
    return build_payload(
        view_type=view_type,
        topic=topic,
        template_type=template_type,
        status="processing",
        preview_text=preview_text,
        game_view_id=archive_id if view_type == "game" else None,
        knowledge_view_id=archive_id if view_type == "knowledge" else None,
        summary=preview_text,
        subject_parent_type=resolve_dynamic_view_subject_parent_type(resolved_subject_type),
        subject_type=resolved_subject_type,
    )


def build_stream_chunk(
    *,
    template_type: str,
    stage: str,
    preview_text: str,
    is_final: bool,
    topic: str,
    archive_id: int | None = None,
    view_type: str = "game",
    node_key: str | None = None,
    node_title: str | None = None,
    node_status: str | None = None,
    stream_char_count: int | None = None,
    payload_status: str | None = None,
) -> DynamicViewStreamChunk:
    """统一构造流式阶段块。"""
    resolved_status = payload_status or ("ready" if is_final else "processing")
    resolved_subject_type = infer_subject_type(topic)
    payload = build_payload(
        view_type=view_type,
        topic=topic,
        template_type=template_type,
        status=resolved_status,
        preview_text=preview_text,
        game_view_id=archive_id if view_type == "game" else None,
        knowledge_view_id=archive_id if view_type == "knowledge" else None,
        summary=preview_text,
        subject_parent_type=resolve_dynamic_view_subject_parent_type(resolved_subject_type),
        subject_type=resolved_subject_type,
    )
    return DynamicViewStreamChunk(
        payload=payload,
        is_final=is_final,
        stage=stage,
        node_key=node_key,
        node_title=node_title,
        node_status=node_status,
        stream_char_count=stream_char_count,
    )


def build_stage_chunk(
    *,
    template_type: str,
    descriptor: DynamicViewStageDescriptor,
    node_status: str,
    topic: str,
    archive_id: int | None = None,
    view_type: str = "game",
    stream_char_count: int | None = None,
) -> DynamicViewStreamChunk:
    """按统一阶段描述生成节点块。"""
    preview_text_map = {
        "ready": descriptor.ready_preview_text,
        "processing": descriptor.processing_preview_text,
        "completed": descriptor.completed_preview_text,
    }
    return build_stream_chunk(
        template_type=template_type,
        stage=descriptor.stage,
        preview_text=preview_text_map[node_status],
        is_final=False,
        topic=topic,
        archive_id=archive_id,
        view_type=view_type,
        node_key=descriptor.node_key,
        node_title=descriptor.node_title,
        node_status=node_status,
        stream_char_count=stream_char_count,
    )


def build_task_snapshot(
    *,
    task_id: str,
    request_id: str = "",
    author_id: str,
    scene_count_min: int,
    stream_chunk: DynamicViewStreamChunk,
    created_at: datetime,
    updated_at: datetime,
    model_level: str = "basic",
    generation_status: str | None = None,
) -> DynamicViewTaskSnapshot:
    """把流式阶段块收口成后台任务快照。"""
    return DynamicViewTaskSnapshot(
        taskId=task_id,
        requestId=request_id,
        authorId=author_id,
        topic=stream_chunk.payload.topic,
        sceneCountMin=scene_count_min,
        stage=stream_chunk.stage,
        message=stream_chunk.payload.preview_text.strip(),
        nodeTitle=stream_chunk.node_title,
        nodeStatus=stream_chunk.node_status,
        streamCharCount=stream_chunk.stream_char_count,
        progress=1.0 if stream_chunk.is_final and stream_chunk.payload.status == "ready" else None,
        modelLevel=str(model_level or "basic"),
        generationStatus=str(generation_status or stream_chunk.stage or "queued"),
        isFinal=stream_chunk.is_final,
        isTerminal=stream_chunk.is_final,
        createdAt=created_at,
        updatedAt=updated_at,
        payload=stream_chunk.payload,
    )


def build_terminal_task_snapshot(
    *,
    task_id: str,
    scene_count_min: int,
    previous_snapshot: DynamicViewTaskSnapshot,
    stage: str,
    message: str,
    node_status: str,
    payload_status: str,
    updated_at: datetime,
) -> DynamicViewTaskSnapshot:
    """统一构造取消和失败的终态任务快照。"""
    previous_payload = previous_snapshot.payload
    return DynamicViewTaskSnapshot(
        taskId=task_id,
        requestId=previous_snapshot.request_id,
        authorId=previous_snapshot.author_id,
        topic=previous_payload.topic,
        sceneCountMin=scene_count_min,
        stage=stage,
        message=message,
        nodeTitle=previous_snapshot.node_title,
        nodeStatus=node_status,
        streamCharCount=previous_snapshot.stream_char_count,
        progress=None,
        modelLevel=previous_snapshot.model_level,
        generationStatus=stage,
        isFinal=True,
        isTerminal=True,
        createdAt=previous_snapshot.created_at,
        updatedAt=updated_at,
        payload=build_payload(
            topic=previous_payload.topic,
            template_type=previous_payload.template_type,
            status=payload_status,
            preview_text=message,
            game_view_id=previous_payload.game_view_id,
            knowledge_view_id=previous_payload.knowledge_view_id,
            knowledge_generation_status=previous_payload.knowledge_generation_status,
            knowledge_ready=previous_payload.knowledge_ready,
            summary=previous_payload.summary,
            subject_parent_type=previous_payload.subject_parent_type,
            subject_type=previous_payload.subject_type,
            detail=previous_payload.detail,
            final_question=previous_payload.final_question,
            clues=previous_payload.clues,
            current_unlocked_clue_count=previous_payload.current_unlocked_clue_count,
            total_clue_count=previous_payload.total_clue_count,
            all_clues_unlocked=previous_payload.all_clues_unlocked,
            view_count=previous_payload.view_count,
            comment_count=previous_payload.comment_count,
            html=previous_payload.html,
            audio=previous_payload.audio,
        ),
    )
