# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\prompt_runner.py。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
import logging
from typing import TypeVar

from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from app.clients.langchain_chat_client import LangChainChatClient


StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)

logger = logging.getLogger(__name__)


# 定义PromptRunner。
class PromptRunner:
    """统一执行提示词格式化与 LangChain 调用，减少各服务重复代码。"""

    # 执行init相关逻辑。
    def __init__(self, client: LangChainChatClient) -> None:
        """执行init相关逻辑。"""
        self.client = client

    # 执行run text相关逻辑。
    async def run_text(
        self,
        prompt: ChatPromptTemplate,
        temperature: float | None = None,
        stage_name: str | None = None,
        extra_body_override: dict[str, Any] | None = None,
        **prompt_kwargs: object,
    ) -> str:
        """把提示词渲染为消息后执行普通文本调用。"""
        # 执行format messages相关逻辑。
        rendered_messages = prompt.format_messages(**prompt_kwargs)
        # 执行invoke text相关逻辑。
        response_text = await self.client.invoke_text(
            rendered_messages,
            temperature=temperature,
            extra_body_override=extra_body_override,
        )
        # 执行log stage response相关逻辑。
        self._log_stage_response(stage_name, response_text)
        return response_text

    # 执行run text messages相关逻辑。
    async def run_text_messages(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        stage_name: str | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> str:
        """直接使用外部组装好的消息列表执行普通文本调用。"""
        # 执行list相关逻辑。
        rendered_messages = list(messages)
        # 执行invoke text相关逻辑。
        response_text = await self.client.invoke_text(
            rendered_messages,
            temperature=temperature,
            extra_body_override=extra_body_override,
        )
        # 执行log stage response相关逻辑。
        self._log_stage_response(stage_name, response_text)
        return response_text

    # 执行run text messages stream相关逻辑。
    async def run_text_messages_stream(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        stage_name: str | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """直接使用外部组装好的消息列表执行流式文本调用。"""
        # 执行list相关逻辑。
        rendered_messages = list(messages)
        response_chunks: list[str] = []
        async for chunk_text in self.client.invoke_text_stream(
            rendered_messages,
            temperature=temperature,
            extra_body_override=extra_body_override,
        ):
            # 执行append相关逻辑。
            response_chunks.append(chunk_text)
            yield chunk_text
        # 执行log stage response相关逻辑。
        self._log_stage_response(stage_name, "".join(response_chunks))

    # 执行run structured相关逻辑。
    async def run_structured(
        self,
        prompt: ChatPromptTemplate,
        schema: type[StructuredOutputT],
        temperature: float | None = None,
        stage_name: str | None = None,
        **prompt_kwargs: object,
    ) -> StructuredOutputT:
        """把提示词渲染为消息后执行结构化输出调用。"""
        # 执行format messages相关逻辑。
        rendered_messages = prompt.format_messages(**prompt_kwargs)
        # 执行invoke structured相关逻辑。
        response_data = await self.client.invoke_structured(
            rendered_messages,
            schema=schema,
            temperature=temperature,
        )
        # 执行log stage response相关逻辑。
        self._log_stage_response(stage_name, response_data.model_dump(mode="json"))
        return response_data

    # 执行run structured messages相关逻辑。
    async def run_structured_messages(
        self,
        messages: Sequence[BaseMessage],
        schema: type[StructuredOutputT],
        temperature: float | None = None,
        stage_name: str | None = None,
    ) -> StructuredOutputT:
        """直接使用外部组装好的消息列表执行结构化输出调用。"""
        # 执行list相关逻辑。
        rendered_messages = list(messages)
        # 执行invoke structured相关逻辑。
        response_data = await self.client.invoke_structured(
            rendered_messages,
            schema=schema,
            temperature=temperature,
        )
        # 执行log stage response相关逻辑。
        self._log_stage_response(stage_name, response_data.model_dump(mode="json"))
        return response_data

    # 执行log stage response相关逻辑。
    def _log_stage_response(self, stage_name: str | None, response: object) -> None:
        """统一记录模型阶段响应，便于和请求日志配对排查。"""
        if not stage_name:
            return
        # 执行info相关逻辑。
        logger.info(
            "LLM Response | stage=%s | model=%s | response=%s",
            stage_name,
            self.client.profile.model,
            # 执行normalize log text相关逻辑。
            _normalize_log_text(response),
        )


# 执行normalize log text相关逻辑。
def _normalize_log_text(content: object, max_length: int = 240) -> str:
    """把日志内容压成单行并做长度截断，避免控制台被大段提示词刷屏。"""
    # 执行str相关逻辑。
    normalized_content = str(content).replace("\r", " ").replace("\n", " ")
    normalized_content = " ".join(normalized_content.split())
    if len(normalized_content) <= max_length:
        return normalized_content
    return normalized_content[:max_length] + "...[truncated]"
