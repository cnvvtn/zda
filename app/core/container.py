# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\core\container.py。"""

from __future__ import annotations

from app.clients.api_key_provider import ApiKeyProvider
from app.core.settings import (
    Settings,
    settings,
)
from app.repositories.dynamic_view_game_repository import DynamicViewGameRepository
from app.repositories.dynamic_view_character_repository import DynamicViewCharacterRepository
from app.repositories.dynamic_view_clue_repository import DynamicViewClueRepository
from app.repositories.dynamic_view_comment_repository import DynamicViewCommentRepository
from app.repositories.dynamic_view_knowledge_repository import DynamicViewKnowledgeRepository
from app.repositories.dynamic_view_model_profile_repository import DynamicViewModelProfileRepository
from app.repositories.dynamic_view_progress_repository import DynamicViewProgressRepository
from app.repositories.dynamic_view_task_repository import DynamicViewTaskRepository
from app.repositories.dynamic_view_source_task_repository import DynamicViewSourceTaskRepository
from app.repositories.chat_repository import ChatRepository
from app.features.dynamic_view.task_service import DynamicViewTaskService
from app.services.chat_service import ChatService
from app.features.dynamic_view.service import DynamicViewService
from app.services.generation_service import GenerationService
from app.services.mqtt_publish_service import MqttPublishService
from app.services.website_topic_batch_service import WebsiteTopicBatchService
from app.services.website_universe_service import WebsiteUniverseService
from app.db.session import SessionLocal


# 定义AppContainer。
class AppContainer:
    """应用启动时统一构建依赖，避免每个请求重建整套模型与图对象。"""

    # 执行init相关逻辑。
    def __init__(self, app_settings: Settings) -> None:
        """执行init相关逻辑。"""
        self.settings = app_settings
        self.api_key_provider = ApiKeyProvider()

        self.chat_repository = ChatRepository()
        self.dynamic_view_game_repository = DynamicViewGameRepository()
        self.dynamic_view_knowledge_repository = DynamicViewKnowledgeRepository()
        self.dynamic_view_model_profile_repository = DynamicViewModelProfileRepository()
        self.dynamic_view_character_repository = DynamicViewCharacterRepository()
        self.dynamic_view_clue_repository = DynamicViewClueRepository()
        self.dynamic_view_comment_repository = DynamicViewCommentRepository()
        self.dynamic_view_progress_repository = DynamicViewProgressRepository()
        self.dynamic_view_task_repository = DynamicViewTaskRepository()
        self.dynamic_view_source_task_repository = DynamicViewSourceTaskRepository()

        self.dynamic_view_service = DynamicViewService(
            dynamic_view_game_repository=self.dynamic_view_game_repository,
            dynamic_view_knowledge_repository=self.dynamic_view_knowledge_repository,
            dynamic_view_model_profile_repository=self.dynamic_view_model_profile_repository,
            dynamic_view_character_repository=self.dynamic_view_character_repository,
            dynamic_view_clue_repository=self.dynamic_view_clue_repository,
            dynamic_view_comment_repository=self.dynamic_view_comment_repository,
            dynamic_view_progress_repository=self.dynamic_view_progress_repository,
            dynamic_view_task_repository=self.dynamic_view_task_repository,
            dynamic_view_source_task_repository=self.dynamic_view_source_task_repository,
            api_key_provider=self.api_key_provider,
            session_factory=SessionLocal,
        )
        self.dynamic_view_task_service = DynamicViewTaskService(
            dynamic_view_service=self.dynamic_view_service,
            dynamic_view_source_task_repository=self.dynamic_view_source_task_repository,
            session_factory=SessionLocal,
        )
        self.website_topic_batch_service = WebsiteTopicBatchService(
            dynamic_view_service=self.dynamic_view_service,
            session_factory=SessionLocal,
        )
        self.website_universe_service = WebsiteUniverseService(
            session_factory=SessionLocal,
        )
        self.generation_service = GenerationService(
            dynamic_view_model_profile_repository=self.dynamic_view_model_profile_repository,
            api_key_provider=self.api_key_provider,
            session_factory=SessionLocal,
        )
        self.mqtt_publish_service = MqttPublishService()
        self.chat_service = ChatService(
            chat_repository=self.chat_repository,
            generation_service=self.generation_service,
            mqtt_publish_service=self.mqtt_publish_service,
            dynamic_view_service=self.dynamic_view_service,
        )

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """为未来需要显式释放的资源预留统一出口。"""
        # 执行aclose相关逻辑。
        await self.mqtt_publish_service.aclose()


# 执行build app container相关逻辑。
def build_app_container(app_settings: Settings = settings) -> AppContainer:
    """构建应用级依赖容器。"""
    return AppContainer(app_settings)
