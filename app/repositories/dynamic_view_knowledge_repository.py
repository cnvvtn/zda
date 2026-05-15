# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_knowledge_repository.py。"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.url_catalog import PythonUrl
from app.db.models import DynamicViewKnowledgeArchive
from app.features.dynamic_view.schemas import DynamicViewListItem, normalize_dynamic_view_template_type
from app.repositories.dynamic_view_archive_repository_base import DynamicViewArchiveRepositoryBase

_DYNAMIC_VIEW_TYPE_KNOWLEDGE = "knowledge"


# 定义DynamicViewKnowledgeRepository。
class DynamicViewKnowledgeRepository(DynamicViewArchiveRepositoryBase):
    """动态视图知识主表仓储，负责保存知识讲解视图。"""

    # 执行get archive model相关逻辑。
    def _get_archive_model(self):
        """返回知识动态视图使用的存档模型。"""
        return DynamicViewKnowledgeArchive

    # 执行get view type相关逻辑。
    def _get_view_type(self) -> str:
        """返回当前仓储负责的动态视图类型。"""
        return _DYNAMIC_VIEW_TYPE_KNOWLEDGE

    # 执行build list item相关逻辑。
    def _build_list_item(self, archive: DynamicViewKnowledgeArchive) -> DynamicViewListItem:
        """把知识动态视图存档转换成首页列表项。"""
        return DynamicViewListItem(
            viewType="knowledge",
            finalQuestion="",
            clueCount=0,
            knowledgeReady=True,
            htmlUrl=PythonUrl.DYNAMIC_VIEW_KNOWLEDGE_HTML_TEMPLATE.format_path(archive_id=int(archive.id)),
            **self._build_common_list_item_kwargs(archive),
        )

    # 执行build not found message相关逻辑。
    def _build_not_found_message(self, archive_id: int) -> str:
        """返回知识动态视图缺失时的错误文案。"""
        return f"未找到知识动态视图：archive_id={archive_id}"

    # 执行build not ready message相关逻辑。
    def _build_not_ready_message(self, archive_id: int) -> str:
        """返回知识动态视图尚不可播放时的错误文案。"""
        return f"知识动态视图尚不可播放：archive_id={archive_id}"

    # 执行create archive相关逻辑。
    def create_archive(
        self,
        db: Session,
        *,
        game_archive_id: int | None,
        author_id: str,
        template_type: str,
        topic: str,
        status: str,
        audio_name: str,
        audio_start_time: int,
        audio_end_time: int,
        audio_volume: int,
        source_type: str = "",
        source_model: str = "",
        type_code: str = "",
    ) -> int:
        """创建一条知识动态视图存档，并返回数据库主键。"""
        return self._create_archive_entity(
            db,
            type=_DYNAMIC_VIEW_TYPE_KNOWLEDGE,
            author_id=author_id.strip(),
            game_archive_id=game_archive_id,
            source_topic=topic,
            template_type=normalize_dynamic_view_template_type(template_type),
            topic=topic,
            subject_type="",
            subtitle="",
            detail="",
            summary="",
            html_content="",
            audio_name=audio_name.strip(),
            audio_start_time=max(0, int(audio_start_time)),
            audio_end_time=max(0, int(audio_end_time)),
            audio_volume=max(0, min(100, int(audio_volume))),
            subtitle_audio_name="",
            subtitle_audio_volume=100,
            scene_subtitles_json="[]",
            total_duration_ms=0,
            status=status,
            source_type=source_type.strip(),
            source_model=source_model.strip(),
            type_code=type_code.strip(),
        )

    # 执行get by game archive id相关逻辑。
    def get_by_game_archive_id(
        self,
        db: Session,
        *,
        game_archive_id: int,
    ) -> DynamicViewKnowledgeArchive | None:
        """按游戏动态视图 ID 读取知识动态视图。"""
        return (
            db.query(DynamicViewKnowledgeArchive)
            .filter(
                DynamicViewKnowledgeArchive.type == _DYNAMIC_VIEW_TYPE_KNOWLEDGE,
                DynamicViewKnowledgeArchive.game_archive_id == game_archive_id,
            )
            .first()
        )

    # 执行update archive metadata相关逻辑。
    def update_archive_metadata(
        self,
        db: Session,
        *,
        archive_id: int,
        subtitle: str,
        detail: str,
        summary: str,
        subject_type: str,
    ) -> None:
        """更新知识动态视图的元数据字段。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
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
    ) -> None:
        """更新知识动态视图分镜字幕与总时长。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            scene_subtitles_json=json.dumps(scene_subtitles, ensure_ascii=False),
            total_duration_ms=max(0, total_duration_ms),
        )

    # 执行update archive subtitle audio相关逻辑。
    def update_archive_subtitle_audio(
        self,
        db: Session,
        *,
        archive_id: int,
        subtitle_audio_name: str,
        subtitle_audio_volume: int,
    ) -> None:
        """为知识动态视图补写字幕音频文件路径和独立音量。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            subtitle_audio_name=subtitle_audio_name.strip(),
            subtitle_audio_volume=max(0, min(100, int(subtitle_audio_volume))),
        )
