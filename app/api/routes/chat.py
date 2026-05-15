# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\routes\chat.py。"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.exceptions import HTTPException

from app.api.deps import get_chat_service, get_db
from app.api.response import ajax_success
from app.api.response import table_data
from app.core.url_catalog import PythonUrl
from app.db.session import SessionLocal
from app.features.chat.schemas import (
    ChatBootstrapRequest,
    ChatConversationCreateRequest,
    ChatProcessRequest,
    ChatRequest,
)
from app.services.chat_service import ChatService
from app.services.credit_service import CreditService
from sqlalchemy.orm import Session

router = APIRouter(prefix=PythonUrl.CHAT_API_PREFIX.value, tags=["chat"])
logger = logging.getLogger(__name__)
credit_service = CreditService()


# 执行process in background相关逻辑。
async def _process_in_background(
    request: ChatProcessRequest,
    chat_service: ChatService,
) -> None:
    """用独立数据库会话执行后台生成任务，避免响应返回后复用已关闭的请求级 Session。"""
    db = SessionLocal()
    try:
        # 执行process and publish相关逻辑。
        await chat_service.process_and_publish(db, request)
    except Exception as error:
        # 后台任务失败时只记录日志，不能再反向影响已经确认接单的 HTTP 响应。
        db.rollback()
        # 执行exception相关逻辑。
        logger.exception("Chat background processing failed: conversationId=%s", request.conversation_id)
        try:
            # 执行publish turn failed相关逻辑。
            await chat_service.publish_turn_failed(request, error)
        except Exception:
            # 执行exception相关逻辑。
            logger.exception("Chat turn failed event publish failed: conversationId=%s", request.conversation_id)
    finally:
        # 执行close相关逻辑。
        db.close()


# 执行bootstrap相关逻辑。
@router.post("/bootstrap")
async def bootstrap(
    request: ChatBootstrapRequest,
    chat_service: ChatService = Depends(get_chat_service),
) -> dict[str, object]:
    """Flutter 启动聊天页时，直接由 Python 下发 MQTT 连接参数。"""
    return ajax_success(chat_service.build_bootstrap_response(request.user_id))


# 执行list conversations相关逻辑。
@router.get("/conversations")
async def list_conversations(
    user_id: str,
    db: Session = Depends(get_db),
    chat_service: ChatService = Depends(get_chat_service),
) -> dict[str, object]:
    """返回当前用户聊天会话列表，供 Flutter 直接查询后端数据。"""
    try:
        return table_data(chat_service.list_conversations(db, user_id=user_id))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# 执行create conversation相关逻辑。
@router.post("/conversations")
async def create_conversation(
    request: ChatConversationCreateRequest,
    db: Session = Depends(get_db),
    chat_service: ChatService = Depends(get_chat_service),
) -> dict[str, object]:
    """创建或更新聊天会话壳，消息仍由 send 接口写入。"""
    try:
        return ajax_success(chat_service.create_conversation_shell(db, request))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# 执行get conversation相关逻辑。
@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    chat_service: ChatService = Depends(get_chat_service),
) -> dict[str, object]:
    """返回单个聊天会话及消息，供 Flutter 详情页直接回源。"""
    try:
        return ajax_success(
            chat_service.get_conversation(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# 执行send相关逻辑。
@router.post("/send")
async def send(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    chat_service: ChatService = Depends(get_chat_service),
) -> dict[str, object]:
    """Flutter 聊天发送入口，Python 负责接单、存档和后台分发。"""
    try:
        request_id = uuid4().hex
        credit_service.consume_credits(
            db,
            user_id=request.user_id,
            amount=credit_service.resolve_model_cost("basic"),
            model_level="basic",
            usage_type="chat",
            request_id=request_id,
        )
        db.commit()
        # 执行accept send相关逻辑。
        process_request, response = chat_service.accept_send(db, request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # 执行info相关逻辑。
    logger.info(
        "Chat send accepted: conversationId=%s turnGroupId=%s revision=%s replyTopic=%s content=%s",
        process_request.conversation_id,
        process_request.turn_group_id,
        process_request.revision,
        process_request.topic,
        # 执行strip相关逻辑。
        process_request.content.strip(),
    )
    # 执行add task相关逻辑。
    background_tasks.add_task(_process_in_background, process_request, chat_service)
    return ajax_success(response)
