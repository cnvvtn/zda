# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\deps.py。"""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.container import AppContainer
from app.db.session import SessionLocal
from app.services.chat_service import ChatService
from app.services.generation_service import GenerationService
from app.features.dynamic_view.service import DynamicViewService
from app.features.dynamic_view.task_service import DynamicViewTaskService
from app.services.website_topic_batch_service import WebsiteTopicBatchService


# 执行get db相关逻辑。
def get_db() -> Generator[Session, None, None]:
    """为每个 HTTP 请求提供一个独立的 SQLAlchemy Session。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 执行get app container相关逻辑。
def get_app_container(request: Request) -> AppContainer:
    """从 FastAPI 应用状态里读取启动期构建好的依赖容器。"""
    return request.app.state.container


# 执行get chat service相关逻辑。
def get_chat_service(container: AppContainer = Depends(get_app_container)) -> ChatService:
    """从应用级容器中返回聊天门面，避免每个请求重新装配。"""
    return container.chat_service


# 执行get generation service相关逻辑。
def get_generation_service(container: AppContainer = Depends(get_app_container)) -> GenerationService:
    """从应用级容器中返回生成服务。"""
    return container.generation_service


# 执行get dynamic view service相关逻辑。
def get_dynamic_view_service(container: AppContainer = Depends(get_app_container)) -> DynamicViewService:
    """从应用级容器中返回动态视图服务，避免每个请求重复创建模型客户端。"""
    return container.dynamic_view_service


# 执行get dynamic view task service相关逻辑。
def get_dynamic_view_task_service(
    container: AppContainer = Depends(get_app_container),
) -> DynamicViewTaskService:
    """从应用级容器中返回动态视图任务服务。"""
    return container.dynamic_view_task_service


# 执行get website topic batch service相关逻辑。
def get_website_topic_batch_service(
    container: AppContainer = Depends(get_app_container),
) -> WebsiteTopicBatchService:
    """从应用级容器中返回官网主题批次服务。"""
    return container.website_topic_batch_service
