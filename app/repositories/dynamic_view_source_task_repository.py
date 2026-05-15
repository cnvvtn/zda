# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_source_task_repository.py。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.models import DynamicViewArchive
from app.features.dynamic_view.schemas import DynamicViewSourceTaskItem

_DYNAMIC_VIEW_TYPE_TASK = "task"


# 定义DynamicViewSourceTaskRepository。
class DynamicViewSourceTaskRepository:
    """动态视图任务来源仓储，负责任务入库、批量领取与结果回写。"""

    # 执行create tasks相关逻辑。
    def create_tasks(
        self,
        db: Session,
        *,
        tasks: list[DynamicViewSourceTaskItem],
        source_type: str,
        source_model: str,
    ) -> int:
        """批量写入新任务，并跳过数据库里已存在的相同 topic 与 typeCode 组合。"""
        normalized_tasks = [task_item for task_item in tasks if task_item.topic.strip()]
        if not normalized_tasks:
            return 0
        existing_task_keys = {
            f"{record.topic.strip()}|{str(record.type_code or '').strip()}"
            for record in db.scalars(
                select(DynamicViewArchive).where(DynamicViewArchive.type == _DYNAMIC_VIEW_TYPE_TASK)
            ).all()
        }
        inserted_count = 0
        for task_item in normalized_tasks:
            topic = task_item.topic.strip()
            type_code = task_item.type_code.strip()
            task_key = f"{topic}|{type_code}"
            if task_key in existing_task_keys:
                continue
            db.add(
                DynamicViewArchive(
                    type=_DYNAMIC_VIEW_TYPE_TASK,
                    author_id=task_item.author_id,
                    game_archive_id=None,
                    topic=topic,
                    source_topic="",
                    template_type="landscape_16_9",
                    subject_type="",
                    subtitle="",
                    detail="",
                    summary="",
                    html_content="",
                    audio_name="",
                    audio_start_time=0,
                    audio_end_time=0,
                    audio_volume=100,
                    subtitle_audio_name="",
                    subtitle_audio_volume=100,
                    scene_subtitles_json="[]",
                    total_duration_ms=0,
                    scene_count_min=8,
                    final_question="",
                    clue_count=0,
                    knowledge_archive_id=None,
                    knowledge_generation_status="idle",
                    view_count=0,
                    comment_count=0,
                    status="pending",
                    source_type=source_type,
                    source_model=source_model,
                    type_code=type_code,
                    generation_task_id=None,
                    error_message=None,
                    processing_started_at=None,
                    completed_at=None,
                )
            )
            existing_task_keys.add(task_key)
            inserted_count += 1
        if inserted_count > 0:
            db.commit()
        return inserted_count

    # 执行claim pending tasks相关逻辑。
    def claim_pending_tasks(self, db: Session, *, limit: int) -> list[DynamicViewArchive]:
        """按创建顺序领取待处理任务，并立即标记为处理中。"""
        claimed_tasks = (
            db.scalars(
                select(DynamicViewArchive)
                .where(
                    and_(
                        DynamicViewArchive.type == _DYNAMIC_VIEW_TYPE_TASK,
                        DynamicViewArchive.status == "pending",
                    )
                )
                .order_by(DynamicViewArchive.id.asc())
                .limit(limit)
            ).all()
        )
        if not claimed_tasks:
            return []
        current_time = datetime.now()
        for task_record in claimed_tasks:
            task_record.status = "processing"
            task_record.processing_started_at = current_time
            task_record.error_message = None
        db.commit()
        return claimed_tasks

    # 执行bind generation task相关逻辑。
    def bind_generation_task(
        self,
        db: Session,
        *,
        task_record_id: int,
        generation_task_id: str,
    ) -> None:
        """把任务来源记录与动态视图后台任务绑定起来，便于后续追踪。"""
        task_record = db.get(DynamicViewArchive, task_record_id)
        if task_record is None or task_record.type != _DYNAMIC_VIEW_TYPE_TASK:
            raise ValueError(f"未找到动态视图任务来源记录：task_record_id={task_record_id}")
        task_record.generation_task_id = generation_task_id
        task_record.error_message = None
        db.commit()

    # 执行mark task completed相关逻辑。
    def mark_task_completed(
        self,
        db: Session,
        *,
        task_record_id: int,
        game_archive_id: int | None,
        knowledge_archive_id: int | None,
    ) -> None:
        """把任务来源记录更新为已完成，并按流程绑定最终生成的视图档案。"""
        task_record = db.get(DynamicViewArchive, task_record_id)
        if task_record is None or task_record.type != _DYNAMIC_VIEW_TYPE_TASK:
            raise ValueError(f"未找到动态视图任务来源记录：task_record_id={task_record_id}")
        task_record.status = "completed"
        task_record.game_archive_id = game_archive_id
        task_record.knowledge_archive_id = knowledge_archive_id
        task_record.completed_at = datetime.now()
        task_record.error_message = None
        db.commit()

    # 执行mark task failed相关逻辑。
    def mark_task_failed(
        self,
        db: Session,
        *,
        task_record_id: int,
        error_message: str,
        game_archive_id: int | None = None,
        knowledge_archive_id: int | None = None,
    ) -> None:
        """把任务来源记录更新为失败，并记录本轮失败摘要。"""
        task_record = db.get(DynamicViewArchive, task_record_id)
        if task_record is None or task_record.type != _DYNAMIC_VIEW_TYPE_TASK:
            raise ValueError(f"未找到动态视图任务来源记录：task_record_id={task_record_id}")
        task_record.status = "failed"
        task_record.game_archive_id = game_archive_id
        task_record.knowledge_archive_id = knowledge_archive_id
        task_record.completed_at = datetime.now()
        normalized_error_message = error_message[:1000].strip()
        task_record.error_message = normalized_error_message or None
        db.commit()
