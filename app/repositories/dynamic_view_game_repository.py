# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_game_repository.py。"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.url_catalog import PythonUrl
from app.db.models import DynamicViewGameArchive
from app.features.dynamic_view.schemas import DynamicViewListItem, normalize_dynamic_view_template_type
from app.repositories.dynamic_view_archive_repository_base import DynamicViewArchiveRepositoryBase

_DYNAMIC_VIEW_TYPE_GAME = "game"


# 定义DynamicViewGameRepository。
class DynamicViewGameRepository(DynamicViewArchiveRepositoryBase):
    """动态视图游戏主表仓储，负责保存解谜视图与知识视图关联状态。"""

    # 执行get archive model相关逻辑。
    def _get_archive_model(self):
        """返回游戏动态视图使用的存档模型。"""
        return DynamicViewGameArchive

    # 执行get view type相关逻辑。
    def _get_view_type(self) -> str:
        """返回当前仓储负责的动态视图类型。"""
        return _DYNAMIC_VIEW_TYPE_GAME

    # 执行build list item相关逻辑。
    def _build_list_item(self, archive: DynamicViewGameArchive) -> DynamicViewListItem:
        """把游戏动态视图存档转换成首页列表项。"""
        return DynamicViewListItem(
            viewType="game",
            finalQuestion=archive.final_question,
            clueCount=archive.clue_count,
            knowledgeReady=bool(
                archive.knowledge_archive_id and archive.knowledge_generation_status == "ready"
            ),
            htmlUrl=PythonUrl.DYNAMIC_VIEW_HTML_TEMPLATE.format_path(archive_id=int(archive.id)),
            **self._build_common_list_item_kwargs(archive),
        )

    # 执行build not found message相关逻辑。
    def _build_not_found_message(self, archive_id: int) -> str:
        """返回游戏动态视图缺失时的错误文案。"""
        return f"未找到动态视图存档：archive_id={archive_id}"

    # 执行build not ready message相关逻辑。
    def _build_not_ready_message(self, archive_id: int) -> str:
        """返回游戏动态视图尚不可播放时的错误文案。"""
        return f"动态视图尚不可播放：archive_id={archive_id}"

    # 执行create archive相关逻辑。
    def create_archive(
        self,
        db: Session,
        *,
        topic: str,
        author_id: str,
        template_type: str,
        scene_count_min: int,
        scene_subtitles: list[dict[str, object]],
        status: str,
        audio_name: str,
        audio_start_time: int,
        audio_end_time: int,
        audio_volume: int,
        source_type: str = "",
        source_model: str = "",
        type_code: str = "",
    ) -> int:
        """创建一条游戏动态视图主存档，并返回数据库主键。"""
        return self._create_archive_entity(
            db,
            type=_DYNAMIC_VIEW_TYPE_GAME,
            author_id=author_id.strip(),
            game_archive_id=None,
            topic=topic,
            source_topic=topic,
            template_type=normalize_dynamic_view_template_type(template_type),
            subject_type="",
            subtitle="",
            detail="",
            summary="",
            html_content="",
            audio_name=audio_name.strip(),
            audio_start_time=max(0, int(audio_start_time)),
            audio_end_time=max(0, int(audio_end_time)),
            audio_volume=max(0, min(100, int(audio_volume))),
            scene_subtitles_json=json.dumps(scene_subtitles, ensure_ascii=False),
            total_duration_ms=0,
            scene_count_min=scene_count_min,
            final_question="",
            clue_count=0,
            knowledge_archive_id=None,
            knowledge_generation_status="idle",
            view_count=0,
            comment_count=0,
            status=status,
            source_type=source_type.strip(),
            source_model=source_model.strip(),
            type_code=type_code.strip(),
        )

    # 执行update archive metadata相关逻辑。
    def update_archive_metadata(
        self,
        db: Session,
        *,
        archive_id: int,
        topic: str,
        subtitle: str,
        detail: str,
        summary: str,
        subject_type: str,
    ) -> None:
        """更新游戏动态视图的元数据字段。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            topic=topic,
            subtitle=subtitle,
            detail=detail,
            summary=summary,
            subject_type=subject_type,
        )

    # 执行update archive timeline data相关逻辑。
    def update_archive_timeline_data(
        self,
        db: Session,
        *,
        archive_id: int,
        scene_subtitles: list[dict[str, object]],
        total_duration_ms: int,
        final_question: str,
        clue_count: int,
    ) -> None:
        """更新游戏动态视图分镜字幕、最终问题、线索数与总时长。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            scene_subtitles_json=json.dumps(scene_subtitles, ensure_ascii=False),
            total_duration_ms=max(0, total_duration_ms),
            final_question=final_question.strip(),
            clue_count=max(0, clue_count),
        )

    # 执行update archive knowledge status相关逻辑。
    def update_archive_knowledge_status(
        self,
        db: Session,
        *,
        archive_id: int,
        knowledge_generation_status: str,
    ) -> None:
        """更新知识视图生成状态。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            knowledge_generation_status=knowledge_generation_status,
        )

    # 执行bind knowledge archive相关逻辑。
    def bind_knowledge_archive(
        self,
        db: Session,
        *,
        archive_id: int,
        knowledge_archive_id: int | None,
        knowledge_generation_status: str,
    ) -> None:
        """绑定或清空知识动态视图 ID，并同步写入知识生成状态。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            knowledge_archive_id=knowledge_archive_id,
            knowledge_generation_status=knowledge_generation_status,
        )

    # 执行update archive clue count相关逻辑。
    def update_archive_clue_count(
        self,
        db: Session,
        *,
        archive_id: int,
        clue_count: int,
    ) -> None:
        """单独更新游戏动态视图线索数量，避免线索在后处理阶段生成时把旧数量写死。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            clue_count=max(0, clue_count),
        )
