# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_archive_repository_base.py。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, desc
from sqlalchemy.orm import Session

from app.features.dynamic_view.html_storage import (
    delete_dynamic_view_html_file,
    delete_dynamic_view_recycled_html_file,
    delete_dynamic_view_recycled_music_file,
    move_dynamic_view_music_file_to_recycle_bin,
    move_dynamic_view_html_file_to_recycle_bin,
    resolve_dynamic_view_html_path_from_relative_path,
    restore_dynamic_view_html_file_from_recycle_bin,
    restore_dynamic_view_music_file_from_recycle_bin,
)
from app.features.dynamic_view.schemas import DynamicViewListItem
from app.features.dynamic_view.subject_type_support import resolve_dynamic_view_subject_parent_type


# 定义DynamicViewArchiveRepositoryBase。
class DynamicViewArchiveRepositoryBase:
    """动态视图主存档仓储公共基类，统一收口 game/knowledge 共用的读写逻辑。"""

    # 执行get archive model相关逻辑。
    def _get_archive_model(self):
        """返回当前仓储绑定的存档模型。"""
        raise NotImplementedError

    # 执行get view type相关逻辑。
    def _get_view_type(self) -> str:
        """返回当前仓储负责的动态视图类型。"""
        raise NotImplementedError

    # 执行build list item相关逻辑。
    def _build_list_item(self, archive: Any) -> DynamicViewListItem:
        """把单条存档实体转换成首页列表项。"""
        raise NotImplementedError

    # 执行build not found message相关逻辑。
    def _build_not_found_message(self, archive_id: int) -> str:
        """返回当前仓储缺失存档时的错误文案。"""
        raise NotImplementedError

    # 执行build not ready message相关逻辑。
    def _build_not_ready_message(self, archive_id: int) -> str:
        """返回当前仓储存档尚不可播放时的错误文案。"""
        raise NotImplementedError

    # 执行build common list item kwargs相关逻辑。
    def _build_common_list_item_kwargs(self, archive: Any) -> dict[str, Any]:
        """收口 game/knowledge 列表项公共字段，避免子类重复拼装。"""
        return {
            "id": int(archive.id),
            "title": archive.subtitle,
            "topic": archive.topic,
            "subjectParentType": resolve_dynamic_view_subject_parent_type(archive.subject_type),
            "subjectType": archive.subject_type,
            "summary": archive.summary,
            "detail": archive.detail,
            "viewCount": archive.view_count,
            "commentCount": archive.comment_count,
            "totalDurationMs": archive.total_duration_ms,
        }

    # 执行create archive entity相关逻辑。
    def _create_archive_entity(self, db: Session, **archive_fields: Any) -> int:
        """统一创建当前类型动态视图存档，避免子类重复 add、commit 与 refresh。"""
        archive_model = self._get_archive_model()
        archive = archive_model(**archive_fields)
        db.add(archive)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(archive)
        return int(archive.id)

    # 执行update archive fields相关逻辑。
    def _update_archive_fields(
        self,
        db: Session,
        *,
        archive_id: int,
        **field_values: Any,
    ) -> Any:
        """统一更新当前类型动态视图字段并提交事务，避免子类重复赋值和提交。"""
        archive = self.get_archive_or_raise(db, archive_id)
        for field_name, field_value in field_values.items():
            setattr(archive, field_name, field_value)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        return archive

    # 执行list ready archives相关逻辑。
    def list_ready_archives(
        self,
        db: Session,
        *,
        cursor_id: int | None = None,
        limit: int = 20,
    ) -> list[DynamicViewListItem]:
        """读取当前类型下可直接展示的 ready 存档列表。"""
        archive_model = self._get_archive_model()
        query = (
            db.query(archive_model)
            .filter(
                archive_model.type == self._get_view_type(),
                archive_model.status == "ready",
                archive_model.is_deleted == 0,
                archive_model.html_content != "",
            )
        )
        if cursor_id is not None:
            query = query.filter(archive_model.id < int(cursor_id))
        archives = (
            query.order_by(
                desc(archive_model.created_at),
                desc(archive_model.id),
            )
            .limit(limit)
            .all()
        )
        return [self._build_list_item(archive) for archive in archives]

    # 执行get archive or raise相关逻辑。
    def get_archive_or_raise(self, db: Session, archive_id: int):
        """按主键读取当前类型的动态视图存档，缺失时直接抛错。"""
        archive_model = self._get_archive_model()
        archive = (
            db.query(archive_model)
            .filter(
                archive_model.id == archive_id,
                archive_model.type == self._get_view_type(),
                archive_model.is_deleted == 0,
            )
            .first()
        )
        if archive is None:
            raise ValueError(self._build_not_found_message(archive_id))
        return archive

    # 执行get ready archive detail相关逻辑。
    def get_ready_archive_detail(
        self,
        db: Session,
        *,
        archive_id: int,
        increase_view_count: bool = False,
    ):
        """读取当前类型的详情存档，必要时同步累加观看次数。"""
        archive = self.get_archive_or_raise(db, archive_id)
        html_relative_path = archive.html_content.strip()
        if archive.status != "ready" or not html_relative_path:
            raise ValueError(self._build_not_ready_message(archive_id))
        resolved_html_path = resolve_dynamic_view_html_path_from_relative_path(html_relative_path)
        if not resolved_html_path.is_file():
            raise ValueError(self._build_not_ready_message(archive_id))
        if increase_view_count:
            archive.view_count += 1
            db.commit()
            db.refresh(archive)
        return archive

    # 执行update archive render result相关逻辑。
    def update_archive_render_result(
        self,
        db: Session,
        *,
        archive_id: int,
        html_relative_path: str,
        status: str,
    ) -> None:
        """更新当前类型动态视图的最终 HTML 文件路径与状态。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            html_content=html_relative_path.strip(),
            status=status,
        )

    # 执行update archive status相关逻辑。
    def update_archive_status(self, db: Session, *, archive_id: int, status: str) -> None:
        """更新当前类型动态视图状态，不改动其他业务字段。"""
        self._update_archive_fields(db, archive_id=archive_id, status=status)

    # 执行mark unfinished archives failed相关逻辑。
    def mark_unfinished_archives_failed(self, db: Session, *, error_message: str) -> int:
        """服务启动时把孤立的 processing 存档改成失败，避免无任务记录的半成品继续占位。"""
        archive_model = self._get_archive_model()
        archives = (
            db.query(archive_model)
            .filter(
                archive_model.type == self._get_view_type(),
                archive_model.status == "processing",
                archive_model.is_deleted == 0,
                archive_model.html_content == "",
                archive_model.generation_task_id.is_(None),
            )
            .all()
        )
        for archive in archives:
            archive.status = "failed"
            archive.error_message = error_message[:1000].strip() or None
            archive.completed_at = datetime.now()
        if archives:
            db.commit()
        return len(archives)

    # 执行update archive audio相关逻辑。
    def update_archive_audio(
        self,
        db: Session,
        *,
        archive_id: int,
        audio_name: str,
        audio_start_time: int,
        audio_end_time: int,
        audio_volume: int,
    ) -> None:
        """为当前类型动态视图补写固定背景音乐配置。"""
        self._update_archive_fields(
            db,
            archive_id=archive_id,
            audio_name=audio_name.strip(),
            audio_start_time=max(0, int(audio_start_time)),
            audio_end_time=max(0, int(audio_end_time)),
            audio_volume=max(0, min(100, int(audio_volume))),
        )

    # 执行update archive comment count相关逻辑。
    def update_archive_comment_count(
        self,
        db: Session,
        *,
        archive_id: int,
        comment_count: int,
    ) -> None:
        """同步当前类型动态视图评论数量，交给外层统一决定何时提交事务。"""
        archive = self.get_archive_or_raise(db, archive_id)
        archive.comment_count = max(0, comment_count)

    # 执行delete archive相关逻辑。
    def delete_archive(self, db: Session, *, archive_id: int) -> None:
        """按主键删除当前类型的动态视图主存档。"""
        archive = self.get_archive_or_raise(db, archive_id)
        html_relative_path = archive.html_content.strip()
        archive_model = self._get_archive_model()
        db.execute(
            delete(archive_model).where(
                archive_model.id == archive_id,
                archive_model.type == self._get_view_type(),
            )
        )
        db.commit()
        if html_relative_path:
            delete_dynamic_view_html_file(html_relative_path)

    # 执行move archive to recycle bin相关逻辑。
    def move_archive_to_recycle_bin(self, db: Session, *, archive_id: int) -> Any:
        """把当前类型动态视图移入回收站，并把 HTML 文件同步移入回收站目录。"""
        archive = self.get_archive_or_raise(db, archive_id)
        html_relative_path = archive.html_content.strip()
        current_time = datetime.now()
        archive.is_deleted = 1
        archive.deleted_at = current_time
        archive.physical_delete_after = current_time + timedelta(hours=24)
        archive.status = "deleted"
        db.commit()
        if html_relative_path:
            move_dynamic_view_html_file_to_recycle_bin(html_relative_path)
        subtitle_audio_name = str(getattr(archive, "subtitle_audio_name", "") or "").strip()
        if subtitle_audio_name:
            move_dynamic_view_music_file_to_recycle_bin(subtitle_audio_name)
        return archive

    # 执行hard delete recycled archive相关逻辑。
    def hard_delete_recycled_archive(self, db: Session, *, archive_id: int) -> None:
        """物理删除已进入回收站的动态视图主存档和回收站 HTML 文件。"""
        archive_model = self._get_archive_model()
        archive = (
            db.query(archive_model)
            .filter(
                archive_model.id == archive_id,
                archive_model.type == self._get_view_type(),
                archive_model.is_deleted == 1,
            )
            .first()
        )
        if archive is None:
            return
        html_relative_path = archive.html_content.strip()
        subtitle_audio_name = str(getattr(archive, "subtitle_audio_name", "") or "").strip()
        db.execute(
            delete(archive_model).where(
                archive_model.id == archive_id,
                archive_model.type == self._get_view_type(),
                archive_model.is_deleted == 1,
            )
        )
        db.commit()
        if html_relative_path:
            delete_dynamic_view_recycled_html_file(html_relative_path)
        if subtitle_audio_name:
            delete_dynamic_view_recycled_music_file(subtitle_audio_name)

    # 执行restore archive from recycle bin相关逻辑。
    def restore_archive_from_recycle_bin(self, db: Session, *, archive_id: int) -> Any:
        """把当前类型动态视图从回收站恢复为可播放记录，并恢复文件位置。"""
        archive_model = self._get_archive_model()
        archive = (
            db.query(archive_model)
            .filter(
                archive_model.id == archive_id,
                archive_model.type == self._get_view_type(),
                archive_model.is_deleted == 1,
            )
            .first()
        )
        if archive is None:
            raise ValueError(self._build_not_found_message(archive_id))
        html_relative_path = archive.html_content.strip()
        subtitle_audio_name = str(getattr(archive, "subtitle_audio_name", "") or "").strip()
        archive.is_deleted = 0
        archive.deleted_at = None
        archive.physical_delete_after = None
        archive.status = "ready"
        db.commit()
        if html_relative_path:
            restore_dynamic_view_html_file_from_recycle_bin(html_relative_path)
        if subtitle_audio_name:
            restore_dynamic_view_music_file_from_recycle_bin(subtitle_audio_name)
        return archive
