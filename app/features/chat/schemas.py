# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\chat\schemas.py。"""

from __future__ import annotations

from datetime import datetime
from typing import NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field


# 定义ConversationContextMessage。
class ConversationContextMessage(TypedDict):
    """供分析、决策、生成复用的会话上下文消息结构。"""

    role: str
    content: str
    speaker: str
    mention: NotRequired[str]

# 定义RoleProfileData。
class RoleProfileData(BaseModel):
    """当前会话绑定的角色资料，供生成阶段改写系统角色设定。"""

    role_key: str = Field(alias="roleKey")
    avatar: str
    identity: str
    name: str
    persona: str
    role_description: str | None = Field(default=None, alias="roleDescription")
    role_code: str | None = Field(default=None, alias="roleCode")
    category_name: str | None = Field(default=None, alias="categoryName")
    icon: str | None = None
    personality: str | None = None
    scene: str | None = None
    nsfw_setting: str | None = Field(default=None, alias="nsfwSetting")
    author: str | None = None
    avatar_prompt: str | None = Field(default=None, alias="avatarPrompt")
    supplement: str | None = None
    dynamic_view_chinese_subtitles: str | None = Field(
        default=None,
        alias="dynamicViewChineseSubtitles",
    )
    dynamic_view_game_archive_id: int | None = Field(
        default=None,
        alias="dynamicViewGameArchiveId",
    )
    dynamic_view_knowledge_ready: bool | None = Field(
        default=None,
        alias="dynamicViewKnowledgeReady",
    )


# 定义ChatBootstrapRequest。
class ChatBootstrapRequest(BaseModel):
    """Flutter 聊天启动握手请求，只负责声明当前用户。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(min_length=1, alias="userId")


# 定义ChatBootstrapResponse。
class ChatBootstrapResponse(BaseModel):
    """Flutter 连接 MQTT 前需要的最小启动参数。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    host: str
    port: int
    topic: str
    client_id: str = Field(alias="clientId")
    username: str | None = None
    password: str | None = None


# 定义ChatConversationCreateRequest。
class ChatConversationCreateRequest(BaseModel):
    """Flutter 创建动态视图聊天会话壳时使用的请求参数。"""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(min_length=1, alias="conversationId")
    user_id: str = Field(min_length=1, alias="userId")
    title: str = Field(min_length=1)
    snippet: str
    view_name: str | None = Field(default=None, alias="viewName")


# 定义ChatMessageResponse。
class ChatMessageResponse(BaseModel):
    """返回给 Flutter 的聊天消息存档。"""

    model_config = ConfigDict(populate_by_name=True)

    message_id: str = Field(alias="messageId")
    role: str
    content: str
    raw_content: str = Field(alias="rawContent")
    quoted_content: str | None = Field(default=None, alias="quotedContent")
    reply_type: str = Field(alias="replyType")
    created_at: datetime = Field(alias="createdAt")


# 定义ChatConversationResponse。
class ChatConversationResponse(BaseModel):
    """返回给 Flutter 的聊天会话存档。"""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    name: str
    snippet: str
    view_name: str = Field(alias="viewName")
    unread: int
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    messages: list[ChatMessageResponse]


# 定义ChatRequest。
class ChatRequest(BaseModel):
    """定义ChatRequest。"""
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(min_length=1, alias="conversationId")
    conversation_title: str = Field(min_length=1, alias="conversationTitle")
    view_name: str | None = Field(default=None, alias="viewName")
    user_id: str = Field(min_length=1, alias="userId")
    message_id: str = Field(min_length=1, alias="messageId")
    turn_group_id: str = Field(min_length=1, pattern=r"^\d+$", alias="turnGroupId")
    revision: int = 1
    content: str
    quoted_content: str | None = Field(default=None, alias="quotedContent")
    role_profiles: list[RoleProfileData] = Field(min_length=1, alias="roleProfiles")
    mention_all_roles: bool = Field(default=False, alias="mentionAllRoles")
    target_role_keys: list[str] = Field(default_factory=list, alias="targetRoleKeys")
    recent_messages: list[ConversationContextMessage] = Field(
        default_factory=list,
        alias="recentMessages",
    )


# 定义ChatProcessRequest。
class ChatProcessRequest(ChatRequest):
    """聊天消息被 Python 接单后，在后台生成阶段继续流转的内部请求。"""

    topic: str = Field(min_length=1)


# 定义ChatSendResponse。
class ChatSendResponse(BaseModel):
    """聊天发送接口的接单响应。"""

    model_config = ConfigDict(populate_by_name=True)

    accepted: bool
    user_id: str = Field(alias="userId")
    conversation_id: str = Field(alias="conversationId")
    topic: str


# 定义GeneratedContentPayload。
class GeneratedContentPayload(BaseModel):
    """生成节点输出的原始字符串载荷，最终消息切分统一由代码层完成。"""

    content: str
