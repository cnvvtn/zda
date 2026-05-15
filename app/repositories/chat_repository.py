# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\chat_repository.py。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, ChatSession


# 定义ChatRepository。
class ChatRepository:
    """Python 侧聊天存档仓储；聊天上下文不再从 MySQL 读取。"""

    # 执行get or create session相关逻辑。
    def get_or_create_session(
        self,
        db: Session,
        conversation_id: str,
        user_id: str,
        title: str,
        view_name: str,
    ) -> ChatSession:
        """在写消息前确保会话行存在。"""
        # 执行scalar相关逻辑。
        session = db.scalar(select(ChatSession).where(ChatSession.conversation_id == conversation_id))
        if session is not None:
            if session.title != title:
                # 会话角色名以当前请求里的角色资料为准，避免残留历史默认标题。
                session.title = title
            if session.view_name != view_name:
                # 会话视图名称统一以当前请求里的角色档案为准，避免列表标签残留旧值。
                session.view_name = view_name
            return session
        # 同一个会话被并发创建时，直接走 MySQL UPSERT，避免 INSERT IGNORE 后短时间查不到记录。
        upsert_statement = insert(ChatSession).values(
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            snippet="",
            view_name=view_name,
            unread=0,
        )
        # 执行execute相关逻辑。
        db.execute(
            # 执行on duplicate key update相关逻辑。
            upsert_statement.on_duplicate_key_update(
                user_id=upsert_statement.inserted.user_id,
                title=upsert_statement.inserted.title,
                view_name=upsert_statement.inserted.view_name,
            )
        )
        # 执行flush相关逻辑。
        db.flush()
        # 执行scalar相关逻辑。
        session = db.scalar(select(ChatSession).where(ChatSession.conversation_id == conversation_id))
        if session is None:
            raise RuntimeError(f"Failed to create or load chat session: {conversation_id}")
        return session

    # 执行list sessions相关逻辑。
    def list_sessions(self, db: Session, *, user_id: str) -> list[ChatSession]:
        """按用户读取会话列表，Flutter 不再从 ObjectBox 读取会话概要。"""
        return list(
            db.scalars(
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc())
            )
        )

    # 执行get session相关逻辑。
    def get_session(
        self,
        db: Session,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ChatSession | None:
        """按用户和会话 ID 读取单个会话。"""
        return db.scalar(
            select(ChatSession).where(
                ChatSession.conversation_id == conversation_id,
                ChatSession.user_id == user_id,
            )
        )

    # 执行list messages相关逻辑。
    def list_messages(
        self,
        db: Session,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ChatMessage]:
        """按会话读取已落库消息，作为 Flutter 会话详情的后端真源。"""
        return list(
            db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.user_id == user_id,
                )
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
        )

    # 执行create conversation shell相关逻辑。
    def create_conversation_shell(
        self,
        db: Session,
        *,
        conversation_id: str,
        user_id: str,
        title: str,
        snippet: str,
        view_name: str,
    ) -> ChatSession:
        """创建或更新无消息会话壳，供动态视图角色入口立即可查。"""
        session = self.get_or_create_session(
            db,
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            view_name=view_name,
        )
        session.snippet = snippet
        # 执行commit相关逻辑。
        db.commit()
        # 执行refresh相关逻辑。
        db.refresh(session)
        return session

    # 执行save assistant message相关逻辑。
    def save_assistant_message(
        self,
        db: Session,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: str,
        reply_type: str,
        raw_content: str | None = None,
    ) -> None:
        """在 MQTT 推送过程中落库单条 assistant 消息。"""
        # 执行save message相关逻辑。
        self._save_message(
            db,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content=content,
            reply_type=reply_type,
            raw_content=raw_content,
        )

    # 执行save user message相关逻辑。
    def save_user_message(
        self,
        db: Session,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: str,
        reply_type: str,
        quoted_content: str | None = None,
        raw_content: str | None = None,
    ) -> None:
        """在用户消息被服务端接单时落库一条 user 消息。"""
        # 执行save message相关逻辑。
        self._save_message(
            db,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role="user",
            content=content,
            reply_type=reply_type,
            quoted_content=quoted_content,
            raw_content=raw_content,
        )

    # 执行save message相关逻辑。
    def _save_message(
        self,
        db: Session,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        reply_type: str,
        quoted_content: str | None = None,
        raw_content: str | None = None,
    ) -> None:
        """统一封装消息写入，用数据库唯一键保证 message_id 幂等。"""
        resolved_raw_content = raw_content if raw_content is not None else content
        # 同一条业务消息被前端重放或后台重试时，直接忽略重复写入，不再抛唯一键异常。
        db.execute(
            # 执行insert相关逻辑。
            insert(ChatMessage)
            .values(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                role=role,
                content=content,
                raw_content=resolved_raw_content,
                quoted_text=quoted_content,
                reply_type=reply_type,
            )
            .prefix_with("IGNORE")
        )
