# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\website\schemas.py。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# 定义WebsiteGenerationSessionItem。
class WebsiteGenerationSessionItem(BaseModel):
    """官网生成页会话项，承接网站生成窗口左侧历史列表。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    session_id: int = Field(alias="sessionId")
    user_id: str = Field(alias="userId", min_length=1, max_length=64)
    topic: str = Field(min_length=1, max_length=300)
    task_id: str = Field(default="", alias="taskId", max_length=64)
    source: str = Field(default="website", max_length=32)
    stage: str = Field(default="queued", max_length=64)
    message: str = Field(default="", max_length=1000)
    node_status: str = Field(default="", alias="nodeStatus", max_length=64)
    payload_status: str = Field(default="", alias="payloadStatus", max_length=64)
    is_terminal: int = Field(default=0, alias="isTerminal")
    html_url: str = Field(default="", alias="htmlUrl", max_length=1000)
    snapshot: dict[str, object] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    tasks: list[dict[str, object]] = Field(default_factory=list)


# 定义WebsiteGenerationSessionTaskItem。
class WebsiteGenerationSessionTaskItem(BaseModel):
    """官网生成页会话任务项，承接一个会话下的多次生成记录。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    session_id: int = Field(alias="sessionId")
    user_id: str = Field(alias="userId", min_length=1, max_length=64)
    topic: str = Field(min_length=1, max_length=300)
    task_id: str = Field(alias="taskId", max_length=64)
    source: str = Field(default="website", max_length=32)
    stage: str = Field(default="queued", max_length=64)
    message: str = Field(default="", max_length=1000)
    node_status: str = Field(default="", alias="nodeStatus", max_length=64)
    payload_status: str = Field(default="", alias="payloadStatus", max_length=64)
    is_terminal: int = Field(default=0, alias="isTerminal")
    html_url: str = Field(default="", alias="htmlUrl", max_length=1000)
    snapshot: dict[str, object] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


# 定义WebsiteGenerationSessionCreateRequest。
class WebsiteGenerationSessionCreateRequest(BaseModel):
    """官网生成页会话创建请求。"""

    model_config = ConfigDict(populate_by_name=True)

    session_id: int | None = Field(default=None, alias="sessionId")
    user_id: str = Field(alias="userId", min_length=1, max_length=64)
    topic: str = Field(min_length=1, max_length=300)
    task_id: str = Field(default="", alias="taskId", max_length=64)
    source: str = Field(default="website", max_length=32)

    # 执行validate user id相关逻辑。
    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        """统一清洗官网会话用户 ID。"""
        normalized_user_id = str(value or "").strip()
        if not normalized_user_id:
            raise ValueError("userId 不能为空")
        return normalized_user_id

    # 执行validate topic相关逻辑。
    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str) -> str:
        """统一清洗官网会话主题。"""
        normalized_topic = str(value or "").strip()
        if not normalized_topic:
            raise ValueError("topic 不能为空")
        return normalized_topic

    # 执行validate task id相关逻辑。
    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        """统一清洗官网会话任务 ID。"""
        return str(value or "").strip()

    # 执行validate source相关逻辑。
    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        """统一清洗官网会话来源。"""
        return str(value or "").strip() or "website"


# 定义WebsiteGenerationSessionStatusRequest。
class WebsiteGenerationSessionStatusRequest(BaseModel):
    """官网生成页会话状态更新请求。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId", min_length=1, max_length=64)
    task_id: str = Field(alias="taskId", min_length=1, max_length=64)
    stage: str = Field(default="", max_length=64)
    message: str = Field(default="", max_length=1000)
    node_status: str = Field(default="", alias="nodeStatus", max_length=64)
    payload_status: str = Field(default="", alias="payloadStatus", max_length=64)
    is_terminal: int = Field(default=0, alias="isTerminal")
    html_url: str = Field(default="", alias="htmlUrl", max_length=1000)
    snapshot: dict[str, object] = Field(default_factory=dict)


# 定义WebsiteTopicGroupBatch。
class WebsiteTopicGroupBatch(BaseModel):
    """官网单个主题分组的一批 chip 文案。"""

    model_config = ConfigDict(populate_by_name=True)

    theme: str = Field(min_length=1, max_length=32)
    topics: list[str] = Field(min_length=3, max_length=3)

    # 执行validate theme相关逻辑。
    @field_validator("theme")
    @classmethod
    def validate_theme(cls, value: str) -> str:
        """清洗主题分组标识。"""
        normalized_theme = str(value or "").strip()
        if not normalized_theme:
            raise ValueError("theme 不能为空")
        return normalized_theme

    # 执行validate topics相关逻辑。
    @field_validator("topics")
    @classmethod
    def validate_topics(cls, value: list[str]) -> list[str]:
        """清洗官网主题 chip，只拒绝空值。"""
        normalized_topics = [str(item or "").strip() for item in value]
        if len(normalized_topics) != 3 or any(not item for item in normalized_topics):
            raise ValueError("topics 必须包含 3 个非空主题")
        return normalized_topics


# 定义WebsiteTopicFlatBatchItem。
class WebsiteTopicFlatBatchItem(BaseModel):
    """官网话题生成专用扁平结构，避免模型把嵌套 groups 输出成破损字符串。"""

    model_config = ConfigDict(populate_by_name=True)

    general: list[str] = Field(min_length=3, max_length=3)
    science: list[str] = Field(min_length=3, max_length=3)
    history: list[str] = Field(min_length=3, max_length=3)
    mind: list[str] = Field(min_length=3, max_length=3)
    tech: list[str] = Field(min_length=3, max_length=3)

    # 执行normalize topic list相关逻辑。
    @field_validator("general", "science", "history", "mind", "tech")
    @classmethod
    def normalize_topic_list(cls, value: list[str]) -> list[str]:
        """清洗官网扁平主题列表，只拒绝空值。"""
        normalized_topics = [str(item or "").strip() for item in value]
        if len(normalized_topics) != 3 or any(not item for item in normalized_topics):
            raise ValueError("主题列表必须包含 3 个非空主题")
        return normalized_topics


# 定义WebsiteTopicBatchItem。
class WebsiteTopicBatchItem(BaseModel):
    """官网一整批主题 chip，覆盖首页所有主题分组。"""

    model_config = ConfigDict(populate_by_name=True)

    groups: list[WebsiteTopicGroupBatch] = Field(min_length=5, max_length=5)
