# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\clients\langchain_chat_client.py。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Sequence, TypeVar

import httpx
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from openai import BadRequestError, NotFoundError
from pydantic import BaseModel

from app.clients.api_key_provider import ApiKeyProvider
from app.clients.http_request_policy import (
    HTTP_CLOSE_HEADERS,
    HTTP_MAX_RETRIES,
    build_httpx_async_client_kwargs,
)
from app.core.settings import ModelProfile, RouterFailoverProfile, resolve_development_proxy_url


StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)
logger = logging.getLogger(__name__)

# 统一约束所有聊天模型请求参数，确保各业务节点走同一套网关策略。
DEFAULT_EXTRA_BODY: dict[str, Any] = {
    "enable_search": False,
    "max_price": {
        "completion": "3",
        "prompt": "1"
    },
}


# 执行merge extra body dict相关逻辑。
def _merge_extra_body_dict(base_value: dict[str, Any], override_value: dict[str, Any]) -> dict[str, Any]:
    """递归合并额外请求体，确保 provider.only 不会把默认 provider 配置整体覆盖掉。"""
    # 执行dict相关逻辑。
    merged_value = dict(base_value)
    for current_key, current_value in override_value.items():
        # 执行get相关逻辑。
        base_item = merged_value.get(current_key)
        if isinstance(base_item, dict) and isinstance(current_value, dict):
            merged_value[current_key] = _merge_extra_body_dict(base_item, current_value)
            continue
        merged_value[current_key] = current_value
    return merged_value


# 执行build chat extra body相关逻辑。
def build_chat_extra_body(
    profile: ModelProfile,
    extra_body_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一拼装聊天模型额外请求体，保证所有模型走同一套网关策略。"""
    # 执行dict相关逻辑。
    extra_body = dict(DEFAULT_EXTRA_BODY)
    # 执行merge extra body dict相关逻辑。
    extra_body = _merge_extra_body_dict(
        extra_body,
        {"reasoning": {"effort": profile.reasoning.effort}},
    )
    extra_body["enable_thinking"] = profile.enable_deepthinking
    if profile.provider.only:
        # 执行merge extra body dict相关逻辑。
        extra_body = _merge_extra_body_dict(
            extra_body,
            {"provider": {"only": profile.provider.only}},
        )
    if profile.top_k is not None:
        # 执行merge extra body dict相关逻辑。
        extra_body = _merge_extra_body_dict(
            extra_body,
            {"top_k": profile.top_k},
        )
    # 执行merge extra body dict相关逻辑。
    extra_body = _merge_extra_body_dict(extra_body, profile.extra_body)
    if extra_body_override:
        # 执行merge extra body dict相关逻辑。
        extra_body = _merge_extra_body_dict(extra_body, extra_body_override)
    return extra_body


# 定义LangChainChatClient。
class LangChainChatClient:
    """基于 LangChain 1.x 的统一聊天客户端，兼容 OpenAI 风格网关。"""

    # 执行init相关逻辑。
    def __init__(self, profile: ModelProfile, api_key_provider: ApiKeyProvider) -> None:
        """执行init相关逻辑。"""
        self.profile = profile
        self.api_key_provider = api_key_provider
        self._http_async_client: httpx.AsyncClient | None = None

    # 执行invoke text相关逻辑。
    async def invoke_text(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> str:
        """执行一次普通聊天调用，返回纯文本结果。"""
        last_error: Exception | None = None
        for api_key in self.api_key_provider.get_ordered_keys(self.profile):
            try:
                if self.profile.stream:
                    response_chunks: list[str] = []
                    async for chunk_text in self._stream_text_chunks(
                        api_key=api_key,
                        messages=messages,
                        temperature=temperature,
                        extra_body_override=extra_body_override,
                    ):
                        # 执行append相关逻辑。
                        response_chunks.append(chunk_text)
                    return "".join(response_chunks)
                # 执行build model相关逻辑。
                model = self._build_model(api_key, temperature, extra_body_override)
                # 执行run with total timeout相关逻辑。
                result = await self._run_with_total_timeout(model.ainvoke(list(messages)))
                return self._extract_text(result)
            except Exception as error:
                last_error = error
        raise last_error if last_error is not None else RuntimeError("Chat model request failed")

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """关闭当前客户端持有的 HTTP 连接池。"""
        if self._http_async_client is None:
            return
        # 执行aclose相关逻辑。
        await self._http_async_client.aclose()
        self._http_async_client = None

    # 执行invoke text stream相关逻辑。
    async def invoke_text_stream(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """执行一次文本调用；仅当配置允许流式时逐段返回，否则一次性返回完整文本。"""
        if not self.profile.stream:
            yield await self.invoke_text(
                messages,
                temperature=temperature,
                extra_body_override=extra_body_override,
            )
            return
        last_error: Exception | None = None
        for api_key in self.api_key_provider.get_ordered_keys(self.profile):
            try:
                async for chunk_text in self._stream_text_chunks(
                    api_key=api_key,
                    messages=messages,
                    temperature=temperature,
                    extra_body_override=extra_body_override,
                ):
                    yield chunk_text
                return
            except Exception as error:
                last_error = error
        raise last_error if last_error is not None else RuntimeError("Chat model stream request failed")

    # 执行invoke structured相关逻辑。
    async def invoke_structured(
        self,
        messages: Sequence[BaseMessage],
        schema: type[StructuredOutputT],
        temperature: float | None = None,
    ) -> StructuredOutputT:
        """执行一次结构化输出调用，直接返回 Pydantic 结果。"""
        last_error: Exception | None = None
        for api_key in self.api_key_provider.get_ordered_keys(self.profile):
            try:
                return await self._invoke_structured_once(
                    api_key,
                    messages,
                    schema,
                    temperature,
                )
            except Exception as error:
                # 兼容 Qwen 混合推理模型默认开启 thinking 时，禁止强制 tool_choice 的场景。
                if self._should_retry_with_thinking_disabled(error):
                    try:
                        return await self._invoke_structured_once(
                            api_key,
                            messages,
                            schema,
                            temperature,
                            extra_body_override={"enable_thinking": False},
                        )
                    except Exception as retry_error:
                        last_error = retry_error
                        continue
                # 兼容部分 OpenAI 兼容网关不支持 function calling 的 tool_choice 路由。
                if self._should_retry_with_json_mode(error):
                    try:
                        return await self._invoke_structured_once(
                            api_key,
                            messages,
                            schema,
                            temperature,
                            structured_method="json_mode",
                        )
                    except Exception as retry_error:
                        last_error = retry_error
                        continue
                last_error = error
        raise last_error if last_error is not None else RuntimeError("Structured chat model request failed")

    # 执行invoke structured once相关逻辑。
    async def _invoke_structured_once(
        self,
        api_key: str,
        messages: Sequence[BaseMessage],
        schema: type[StructuredOutputT],
        temperature: float | None,
        extra_body_override: dict[str, Any] | None = None,
        structured_method: str = "function_calling",
    ) -> StructuredOutputT:
        """执行一次结构化输出调用，并统一还原成目标 Pydantic 结构。"""
        # 执行build model相关逻辑。
        model = self._build_model(api_key, temperature, extra_body_override).with_structured_output(
            schema,
            method=structured_method,
        )
        # 执行run with total timeout相关逻辑。
        result = await self._run_with_total_timeout(
            # 执行ainvoke相关逻辑。
            model.ainvoke(list(messages))
        )
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)

    # 执行run with total timeout相关逻辑。
    async def _run_with_total_timeout(self, awaitable: Any) -> Any:
        """用模型配置的总超时限制完整请求耗时。"""
        async with _build_total_timeout_context(self.profile.total_timeout):
            return await awaitable

    # 执行stream text chunks相关逻辑。
    async def _stream_text_chunks(
        self,
        *,
        api_key: str,
        messages: Sequence[BaseMessage],
        temperature: float | None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """按流式方式读取模型输出，并用空闲超时判断首包/分片是否卡住。"""
        # 执行build model相关逻辑。
        model = self._build_model(
            api_key,
            temperature,
            extra_body_override,
            streaming_override=True,
        )
        async with _build_total_timeout_context(self.profile.total_timeout):
            async for chunk in _iterate_with_first_token_timeout(
                # 执行astream相关逻辑。
                model.astream(list(messages)),
                self.profile.timeout,
                self._extract_text,
            ):
                # 执行extract text相关逻辑。
                chunk_text = self._extract_text(chunk)
                if chunk_text:
                    yield chunk_text

    # 执行build model相关逻辑。
    def _build_model(
        self,
        api_key: str,
        temperature: float | None,
        extra_body_override: dict[str, Any] | None = None,
        streaming_override: bool | None = None,
    ) -> ChatOpenAI:
        """按当前配置构造一个 LangChain ChatOpenAI 实例。"""
        # 先挂全局默认策略，再允许模型级和本次调用的参数做覆盖。
        extra_body = build_chat_extra_body(self.profile, extra_body_override)
        # 调用方没显式传 temperature 时，直接使用模型配置中的值；再没有才回退到统一默认值。
        resolved_temperature = (
            self.profile.resolved_temperature if temperature is None else temperature
        )
        # base_url 为空时立即报错，避免静默回退导致路由到错误网关。
        resolved_base_url = (self.profile.base_url or "").strip()
        if not resolved_base_url:
            raise ValueError(f"Model profile base_url is required: {self.profile.model}")
        model_kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "api_key": api_key,
            "base_url": resolved_base_url,
            "temperature": resolved_temperature,
            "max_retries": 0,
            "streaming": self.profile.stream if streaming_override is None else streaming_override,
            "extra_body": extra_body or None,
            "default_headers": HTTP_CLOSE_HEADERS,
            "http_async_client": self._get_or_create_http_async_client(),
        }
        # timeout 统一交给外层 asyncio.timeout 管理，避免 SDK 层和业务层双重超时语义互相打架。
        if self.profile.max_tokens is not None:
            model_kwargs["max_tokens"] = self.profile.max_tokens
        if self.profile.top_p is not None:
            model_kwargs["top_p"] = self.profile.top_p
        return ChatOpenAI(
            **model_kwargs,
        )

    # 执行get or create http async client相关逻辑。
    def _get_or_create_http_async_client(self) -> httpx.AsyncClient:
        """按当前模型的 proxy 配置缓存并返回禁用 keep-alive 的 HTTP 异步客户端。"""
        if self._http_async_client is not None:
            return self._http_async_client
        # timeout 统一交给外层 asyncio.timeout 管理，代理只在开发环境显式启用。
        # 执行resolve development proxy url相关逻辑。
        proxy_url = resolve_development_proxy_url()
        # 执行build httpx async client kwargs相关逻辑。
        client_kwargs = build_httpx_async_client_kwargs(
            timeout=None,
            proxy_url=proxy_url,
        )
        self._http_async_client = httpx.AsyncClient(**client_kwargs)
        # 执行info相关逻辑。
        logger.info(
            "LLM HTTP client created | model=%s | proxy=%s | baseUrl=%s | maxRetries=%s | keepAlive=false",
            self.profile.model,
            proxy_url,
            self.profile.base_url,
            HTTP_MAX_RETRIES,
        )
        return self._http_async_client

    # 执行should retry with thinking disabled相关逻辑。
    def _should_retry_with_thinking_disabled(self, error: Exception) -> bool:
        """判断当前错误是否需要自动关闭 thinking 后重试一次。"""
        if not isinstance(error, BadRequestError):
            return False
        if not self.profile.enable_deepthinking:
            return False
        message = self._extract_error_message(error)
        return "tool_choice" in message and "thinking mode" in message

    # 执行should retry with json mode相关逻辑。
    def _should_retry_with_json_mode(self, error: Exception) -> bool:
        """判断当前错误是否需要从 function calling 自动降级到 json_mode。"""
        if not isinstance(error, (BadRequestError, NotFoundError)):
            return False
        message = self._extract_error_message(error)
        return "tool_choice" in message

    # 执行extract error message相关逻辑。
    def _extract_error_message(self, error: Exception) -> str:
        """统一提取 OpenAI 风格异常文案，避免不同异常类型各写一套取值逻辑。"""
        return str(getattr(error, "message", str(error))).lower()

    # 执行extract text相关逻辑。
    def _extract_text(self, message: Any) -> str:
        """把 LangChain 消息内容还原成稳定的纯文本。"""
        # 执行getattr相关逻辑。
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    # 执行append相关逻辑。
                    parts.append(item)
                    continue
                if isinstance(item, dict) and item.get("type") == "text":
                    # 执行append相关逻辑。
                    parts.append(str(item.get("text", "")))
                    continue
                # 执行append相关逻辑。
                parts.append(str(item))
            return "".join(parts)
        return str(content)

    # 执行allows streaming相关逻辑。
    def allows_streaming(self) -> bool:
        """返回当前客户端是否允许对上游发起真正的流式请求。"""
        return self.profile.stream


# 定义RouterFailoverLangChainChatClient。
class RouterFailoverLangChainChatClient:
    """为指定业务节点提供按优先级自动切换的多路由聊天客户端。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        profile: RouterFailoverProfile,
        api_key_provider: ApiKeyProvider,
    ) -> None:
        """执行init相关逻辑。"""
        self.profile = profile.router1
        self._route_clients = [
            LangChainChatClient(current_profile, api_key_provider)
            for current_profile in profile.list_profiles()
        ]

    # 执行invoke text相关逻辑。
    async def invoke_text(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> str:
        """按路由优先级执行普通文本调用，上一条路由失败时自动切到下一条。"""
        last_error: Exception | None = None
        for route_index, route_client in enumerate(self._route_clients, start=1):
            try:
                self.profile = route_client.profile
                return await route_client.invoke_text(
                    messages,
                    temperature=temperature,
                    extra_body_override=extra_body_override,
                )
            except Exception as error:
                last_error = error
                # 执行log router failover warning相关逻辑。
                _log_router_failover_warning(route_index, route_client.profile, error)
        raise last_error if last_error is not None else RuntimeError("Chat model request failed")

    # 执行invoke text stream相关逻辑。
    async def invoke_text_stream(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """按路由优先级执行文本调用；仅当当前路由允许流式时才逐段返回。"""
        last_error: Exception | None = None
        for route_index, route_client in enumerate(self._route_clients, start=1):
            has_yielded_chunk = False
            try:
                self.profile = route_client.profile
                async for chunk_text in route_client.invoke_text_stream(
                    messages,
                    temperature=temperature,
                    extra_body_override=extra_body_override,
                ):
                    has_yielded_chunk = True
                    yield chunk_text
                return
            except Exception as error:
                last_error = error
                # 执行log router failover warning相关逻辑。
                _log_router_failover_warning(route_index, route_client.profile, error)
                if has_yielded_chunk:
                    raise
        raise last_error if last_error is not None else RuntimeError("Chat model stream request failed")

    # 执行invoke structured相关逻辑。
    async def invoke_structured(
        self,
        messages: Sequence[BaseMessage],
        schema: type[StructuredOutputT],
        temperature: float | None = None,
    ) -> StructuredOutputT:
        """按路由优先级执行结构化调用，上一条路由失败时自动切到下一条。"""
        last_error: Exception | None = None
        for route_index, route_client in enumerate(self._route_clients, start=1):
            try:
                self.profile = route_client.profile
                return await route_client.invoke_structured(
                    messages,
                    schema=schema,
                    temperature=temperature,
                )
            except Exception as error:
                last_error = error
                # 执行log router failover warning相关逻辑。
                _log_router_failover_warning(route_index, route_client.profile, error)
        raise last_error if last_error is not None else RuntimeError("Structured chat model request failed")

    # 执行allows streaming相关逻辑。
    def allows_streaming(self) -> bool:
        """严格返回当前生效路由的 stream 配置，不额外叠加业务层限制或放宽。"""
        return bool(self.profile.stream)

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """关闭所有路由客户端持有的底层 HTTP 连接池。"""
        for route_client in self._route_clients:
            # 执行aclose相关逻辑。
            await route_client.aclose()


# 执行build total timeout context相关逻辑。
def _build_total_timeout_context(timeout_seconds: int | None):
    """统一构造总时长超时上下文；未配置 timeout 时返回空上下文。"""
    if timeout_seconds is None or timeout_seconds <= 0:
        return asyncio.timeout(None)
    return asyncio.timeout(timeout_seconds)


# 执行iterate with first token timeout相关逻辑。
async def _iterate_with_first_token_timeout(async_iterable: Any, timeout_seconds: int | None, extract_text):
    """对异步流只限制首个有效文本分片的等待时间。"""
    # 执行aiter相关逻辑。
    iterator = async_iterable.__aiter__()
    first_token_received = False
    first_token_deadline = None
    if timeout_seconds is not None and timeout_seconds > 0:
        # 执行get running loop相关逻辑。
        first_token_deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            if first_token_received or first_token_deadline is None:
                # 执行anext相关逻辑。
                next_item = await iterator.__anext__()
            else:
                # 执行calculate remaining timeout相关逻辑。
                remaining_timeout = first_token_deadline - asyncio.get_running_loop().time()
                if remaining_timeout <= 0:
                    raise TimeoutError("Chat model first token timeout")
                # 执行wait for相关逻辑。
                next_item = await asyncio.wait_for(iterator.__anext__(), remaining_timeout)
        except StopAsyncIteration:
            return
        if extract_text(next_item):
            first_token_received = True
        yield next_item


# 执行log router failover warning相关逻辑。
def _log_router_failover_warning(
    route_index: int,
    profile: ModelProfile,
    error: Exception,
) -> None:
    """记录路由失败与后续切换日志，便于排查具体是哪个 router 崩掉。"""
    import logging

    # 执行get Logger相关逻辑。
    logger = logging.getLogger(__name__)
    # 执行warning相关逻辑。
    logger.warning(
        "Router failover | route=%s | model=%s | baseUrl=%s | error=%s",
        route_index,
        profile.model,
        profile.base_url,
        # 执行str相关逻辑。
        str(error).strip() or error.__class__.__name__,
    )
