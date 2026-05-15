# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\website_generation_session_repository.py。"""

from __future__ import annotations

import json

from datetime import datetime

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.models import WebsiteGenerationSession, WebsiteGenerationSessionTask
from app.features.website.schemas import (
    WebsiteGenerationSessionCreateRequest,
    WebsiteGenerationSessionItem,
    WebsiteGenerationSessionStatusRequest,
    WebsiteGenerationSessionTaskItem,
)


# 定义WebsiteGenerationSessionRepository。
class WebsiteGenerationSessionRepository:
    """官网生成页会话仓储，负责保存已登录用户的网站生成会话。"""

    # 执行list user sessions相关逻辑。
    def list_user_sessions(
        self,
        db: Session,
        *,
        user_id: str,
        limit: int,
    ) -> list[WebsiteGenerationSessionItem]:
        """按用户读取最近的网站生成会话。"""
        normalized_limit = max(1, min(50, int(limit)))
        archives = (
            db.query(WebsiteGenerationSession)
            .filter(WebsiteGenerationSession.user_id == user_id)
            .order_by(desc(WebsiteGenerationSession.updated_at), desc(WebsiteGenerationSession.id))
            .limit(normalized_limit)
            .all()
        )
        session_ids = [archive.id for archive in archives]
        tasks_by_session_id = self._list_tasks_by_session_ids(db, session_ids=session_ids)
        return [
            self._map_archive_to_item(
                archive,
                tasks=tasks_by_session_id.get(archive.id, []),
            )
            for archive in archives
        ]

    # 执行create session相关逻辑。
    def create_session(
        self,
        db: Session,
        *,
        request: WebsiteGenerationSessionCreateRequest,
    ) -> WebsiteGenerationSessionItem:
        """创建会话并把 task 追加到会话任务列表。"""
        archive = self._get_or_create_session_archive(db, request=request)
        task_archive = self._upsert_session_task(db, session=archive, request=request)
        db.commit()
        db.refresh(archive)
        if task_archive is not None:
            db.refresh(task_archive)
        tasks = self._list_session_tasks(db, session_id=archive.id)
        return self._map_archive_to_item(archive, tasks=tasks)

    # 执行update session status相关逻辑。
    def update_session_status(
        self,
        db: Session,
        *,
        request: WebsiteGenerationSessionStatusRequest,
    ) -> WebsiteGenerationSessionItem:
        """按 taskId 更新已登录用户的网站生成会话任务状态。"""
        task_archive = (
            db.query(WebsiteGenerationSessionTask)
            .filter(
                WebsiteGenerationSessionTask.user_id == request.user_id,
                WebsiteGenerationSessionTask.task_id == request.task_id,
            )
            .first()
        )
        if task_archive is None:
            raise ValueError("生成会话任务不存在")
        task_archive.stage = request.stage
        task_archive.message = request.message
        task_archive.node_status = request.node_status
        task_archive.payload_status = request.payload_status
        task_archive.is_terminal = int(request.is_terminal)
        task_archive.html_url = request.html_url
        task_archive.snapshot_json = json.dumps(request.snapshot, ensure_ascii=False)
        session = self.get_user_session(
            db,
            session_id=task_archive.session_id,
            user_id=request.user_id,
        )
        if session is None:
            raise ValueError("生成会话不存在")
        db.add(session)
        db.commit()
        db.refresh(session)
        tasks = self._list_session_tasks(db, session_id=session.id)
        return self._map_archive_to_item(session, tasks=tasks)

    # 执行get user session相关逻辑。
    def get_user_session(
        self,
        db: Session,
        *,
        session_id: int,
        user_id: str,
    ) -> WebsiteGenerationSession | None:
        """按用户和会话 ID 读取官网生成会话。"""
        return (
            db.query(WebsiteGenerationSession)
            .filter(
                WebsiteGenerationSession.id == int(session_id),
                WebsiteGenerationSession.user_id == user_id,
            )
            .first()
        )

    # 执行list user session tasks相关逻辑。
    def list_user_session_tasks(
        self,
        db: Session,
        *,
        session_id: int,
        user_id: str,
    ) -> list[WebsiteGenerationSessionTaskItem]:
        """读取一个官网生成会话下的所有任务。"""
        session = self.get_user_session(db, session_id=session_id, user_id=user_id)
        if session is None:
            return []
        return self._list_session_tasks(db, session_id=session.id)

    # 执行delete user session相关逻辑。
    def delete_user_session(
        self,
        db: Session,
        *,
        archive: WebsiteGenerationSession,
    ) -> bool:
        """删除官网生成页会话记录，让近期对话列表立即移除该项。"""
        db.query(WebsiteGenerationSessionTask).filter(
            WebsiteGenerationSessionTask.session_id == archive.id
        ).delete(synchronize_session=False)
        db.delete(archive)
        db.commit()
        return True

    # 执行get or create session archive相关逻辑。
    def _get_or_create_session_archive(
        self,
        db: Session,
        *,
        request: WebsiteGenerationSessionCreateRequest,
    ) -> WebsiteGenerationSession:
        """按 sessionId 读取会话，缺失时创建新会话。"""
        archive = None
        if request.session_id:
            archive = (
                db.query(WebsiteGenerationSession)
                .filter(
                    WebsiteGenerationSession.id == request.session_id,
                    WebsiteGenerationSession.user_id == request.user_id,
                )
                .with_for_update()
                .first()
            )
        if archive is not None:
            archive.topic = request.topic
            archive.source = request.source
            archive.updated_at = datetime.now()
            return archive
        archive = WebsiteGenerationSession(
            user_id=request.user_id,
            topic=request.topic,
            source=request.source,
        )
        db.add(archive)
        db.flush()
        return archive

    # 执行upsert session task相关逻辑。
    def _upsert_session_task(
        self,
        db: Session,
        *,
        session: WebsiteGenerationSession,
        request: WebsiteGenerationSessionCreateRequest,
    ) -> WebsiteGenerationSessionTask | None:
        """把生成 task 写入当前会话任务表。"""
        if not request.task_id:
            return None
        task_archive = (
            db.query(WebsiteGenerationSessionTask)
            .filter(
                WebsiteGenerationSessionTask.user_id == request.user_id,
                WebsiteGenerationSessionTask.task_id == request.task_id,
            )
            .with_for_update()
            .first()
        )
        if task_archive is None:
            task_archive = WebsiteGenerationSessionTask(
                session_id=session.id,
                user_id=request.user_id,
                topic=request.topic,
                task_id=request.task_id,
                source=request.source,
            )
            db.add(task_archive)
            return task_archive
        task_archive.session_id = session.id
        task_archive.topic = request.topic
        task_archive.source = request.source
        return task_archive

    # 执行list tasks by session ids相关逻辑。
    def _list_tasks_by_session_ids(
        self,
        db: Session,
        *,
        session_ids: list[int],
    ) -> dict[int, list[WebsiteGenerationSessionTaskItem]]:
        """批量读取会话任务并按 sessionId 分组。"""
        if not session_ids:
            return {}
        task_archives = (
            db.query(WebsiteGenerationSessionTask)
            .filter(WebsiteGenerationSessionTask.session_id.in_(session_ids))
            .order_by(desc(WebsiteGenerationSessionTask.updated_at), desc(WebsiteGenerationSessionTask.id))
            .all()
        )
        tasks_by_session_id: dict[int, list[WebsiteGenerationSessionTaskItem]] = {}
        for task_archive in task_archives:
            tasks_by_session_id.setdefault(task_archive.session_id, []).append(
                self._map_task_archive_to_item(task_archive)
            )
        return tasks_by_session_id

    # 执行list session tasks相关逻辑。
    def _list_session_tasks(
        self,
        db: Session,
        *,
        session_id: int,
    ) -> list[WebsiteGenerationSessionTaskItem]:
        """读取单个会话下的任务列表。"""
        return self._list_tasks_by_session_ids(db, session_ids=[session_id]).get(session_id, [])

    # 执行map archive to item相关逻辑。
    def _map_archive_to_item(
        self,
        archive: WebsiteGenerationSession,
        *,
        tasks: list[WebsiteGenerationSessionTaskItem],
    ) -> WebsiteGenerationSessionItem:
        """把数据库官网会话实体映射为接口返回项。"""
        latest_task = tasks[0] if tasks else None
        return WebsiteGenerationSessionItem.model_validate(
            {
                "id": archive.id,
                "sessionId": archive.id,
                "userId": archive.user_id,
                "topic": latest_task.topic if latest_task else archive.topic,
                "taskId": latest_task.task_id if latest_task else "",
                "source": latest_task.source if latest_task else archive.source,
                "stage": latest_task.stage if latest_task else "queued",
                "message": latest_task.message if latest_task else "",
                "nodeStatus": latest_task.node_status if latest_task else "",
                "payloadStatus": latest_task.payload_status if latest_task else "",
                "isTerminal": latest_task.is_terminal if latest_task else 0,
                "htmlUrl": latest_task.html_url if latest_task else "",
                "snapshot": latest_task.snapshot if latest_task else None,
                "createdAt": archive.created_at,
                "updatedAt": latest_task.updated_at if latest_task else archive.updated_at,
                "tasks": [task.model_dump(by_alias=True) for task in tasks],
            }
        )

    # 执行map task archive to item相关逻辑。
    def _map_task_archive_to_item(self, archive: WebsiteGenerationSessionTask) -> WebsiteGenerationSessionTaskItem:
        """把数据库官网会话任务实体映射为接口返回项。"""
        snapshot = None
        if archive.snapshot_json:
            try:
                snapshot = json.loads(archive.snapshot_json)
            except json.JSONDecodeError:
                snapshot = None
        return WebsiteGenerationSessionTaskItem.model_validate(
            {
                "id": archive.id,
                "sessionId": archive.session_id,
                "userId": archive.user_id,
                "topic": archive.topic,
                "taskId": archive.task_id,
                "source": archive.source,
                "stage": archive.stage,
                "message": archive.message,
                "nodeStatus": archive.node_status,
                "payloadStatus": archive.payload_status,
                "isTerminal": archive.is_terminal,
                "htmlUrl": archive.html_url,
                "snapshot": snapshot,
                "createdAt": archive.created_at,
                "updatedAt": archive.updated_at,
            }
        )
