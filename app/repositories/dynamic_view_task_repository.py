# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_task_repository.py。"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.models import DynamicViewTaskArchive
from app.features.dynamic_view.payload_builder import build_payload
from app.features.dynamic_view.schemas import DynamicViewTaskSnapshot


# 定义DynamicViewTaskRepository。
class DynamicViewTaskRepository:
    """动态视图后台任务仓储，负责持久化任务状态和视图关联。"""

    # 执行save task snapshot相关逻辑。
    def save_task_snapshot(
        self,
        db: Session,
        *,
        snapshot: DynamicViewTaskSnapshot,
    ) -> None:
        """按 taskId 持久化最新任务状态，供轮询恢复和取消接口跨进程兜底读取。"""
        # 执行find task archive相关逻辑。
        archive = self._find_task_archive(db, snapshot.task_id)
        if archive is None:
            archive = DynamicViewTaskArchive(task_id=snapshot.task_id)
            # 执行add相关逻辑。
            db.add(archive)
        self._apply_snapshot_to_archive(archive, snapshot)
        # 执行commit相关逻辑。
        db.commit()

    # 执行get task snapshot相关逻辑。
    def get_task_snapshot(
        self,
        db: Session,
        *,
        task_id: str,
    ) -> DynamicViewTaskSnapshot | None:
        """按 taskId 读取后台任务快照，缺失时返回空。"""
        # 执行find task archive相关逻辑。
        archive = self._find_task_archive(db, task_id)
        if archive is None:
            return None
        return self._map_archive_to_snapshot(archive)

    # 执行get latest task snapshot相关逻辑。
    def get_latest_task_snapshot(
        self,
        db: Session,
        *,
        author_id: str,
    ) -> DynamicViewTaskSnapshot | None:
        """按用户读取最近一条未归档任务快照，任务记录本身不做物理删除。"""
        archive = (
            # 执行query相关逻辑。
            db.query(DynamicViewTaskArchive)
            .filter(DynamicViewTaskArchive.author_id == author_id)
            .filter(DynamicViewTaskArchive.stage != "archived")
            .order_by(desc(DynamicViewTaskArchive.updated_at))
            .first()
        )
        if archive is None:
            return None
        return self._map_archive_to_snapshot(archive)

    # 执行mark unfinished tasks failed相关逻辑。
    def mark_unfinished_tasks_failed(
        self,
        db: Session,
        *,
        message: str,
    ) -> int:
        """服务启动时把所有未完成任务统一改成失败终态，避免重启后继续停留在 processing。"""
        updated_count = 0
        # 执行all相关逻辑。
        archives = (
            db.query(DynamicViewTaskArchive)
            .filter(DynamicViewTaskArchive.is_terminal == 0)
            .all()
        )
        for archive in archives:
            archive.stage = "failed"
            archive.message = message
            archive.node_status = "failed"
            archive.progress = None
            archive.is_final = 1
            archive.is_terminal = 1
            archive.payload_status = "failed"
            updated_count += 1
        if updated_count > 0:
            # 执行commit相关逻辑。
            db.commit()
        return updated_count

    # 执行apply snapshot to archive相关逻辑。
    def _apply_snapshot_to_archive(
        self,
        archive: DynamicViewTaskArchive,
        snapshot: DynamicViewTaskSnapshot,
    ) -> None:
        """统一把任务快照写回 ORM 实体，避免新增字段时多处赋值不同步。"""
        archive.request_id = snapshot.request_id
        archive.topic = snapshot.topic
        archive.author_id = snapshot.author_id
        archive.scene_count_min = snapshot.scene_count_min
        archive.view_type = snapshot.payload.view_type
        archive.template_type = snapshot.payload.template_type
        archive.model_level = snapshot.model_level
        archive.payload_status = snapshot.payload.status
        archive.generation_status = snapshot.generation_status
        archive.game_archive_id = snapshot.payload.game_view_id
        archive.knowledge_archive_id = snapshot.payload.knowledge_view_id
        archive.stage = snapshot.stage
        archive.message = snapshot.message
        archive.node_title = snapshot.node_title
        archive.node_status = snapshot.node_status
        archive.stream_char_count = snapshot.stream_char_count
        archive.progress = None if snapshot.progress is None else str(snapshot.progress)
        archive.is_final = 1 if snapshot.is_final else 0
        archive.is_terminal = 1 if snapshot.is_terminal else 0
        archive.created_at = snapshot.created_at
        archive.updated_at = snapshot.updated_at

    # 执行map archive to snapshot相关逻辑。
    def _map_archive_to_snapshot(
        self,
        archive: DynamicViewTaskArchive,
    ) -> DynamicViewTaskSnapshot:
        """数据库任务实体统一映射回快照模型，payload 按状态和视图关联即时构造。"""
        payload = build_payload(
            topic=archive.topic,
            template_type=archive.template_type,
            status=archive.payload_status,
            preview_text=archive.message,
            summary=archive.message,
            view_type=archive.view_type,
            game_view_id=archive.game_archive_id,
            knowledge_view_id=archive.knowledge_archive_id,
        )
        return DynamicViewTaskSnapshot.model_validate(
            {
                "taskId": archive.task_id,
                "requestId": archive.request_id,
                "authorId": archive.author_id,
                "topic": archive.topic,
                "sceneCountMin": archive.scene_count_min,
                "stage": archive.stage,
                "message": archive.message,
                "nodeTitle": archive.node_title,
                "nodeStatus": archive.node_status,
                "streamCharCount": archive.stream_char_count,
                "progress": None if archive.progress is None else float(archive.progress),
                "modelLevel": archive.model_level,
                "generationStatus": archive.generation_status,
                "isFinal": archive.is_final == 1,
                "isTerminal": archive.is_terminal == 1,
                "createdAt": archive.created_at,
                "updatedAt": archive.updated_at,
                "payload": payload,
            }
        )

    # 执行find task archive相关逻辑。
    def _find_task_archive(
        self,
        db: Session,
        task_id: str,
    ) -> DynamicViewTaskArchive | None:
        """按 taskId 查询任务快照实体，避免仓储方法重复写同一段查询。"""
        return (
            # 执行query相关逻辑。
            db.query(DynamicViewTaskArchive)
            .filter(DynamicViewTaskArchive.task_id == task_id)
            .one_or_none()
        )
