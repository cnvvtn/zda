# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\dynamic_view\gemini_client.py。"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Sequence
from typing import Any, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from app.clients.api_key_provider import ApiKeyProvider
from app.clients.langchain_chat_client import LangChainChatClient
from app.core.settings import (
    DynamicViewNode2RouterProfile,
    DynamicViewNode2StepProfile,
    ModelProfile,
)


logger = logging.getLogger(__name__)


HistoryItem = tuple[str, str]
StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


# 定义DynamicViewNode2Session。
class DynamicViewNode2Session:
    """动态视图 node2 会话抽象，统一承接多轮生成与历史快照。"""

    # 执行generate text相关逻辑。
    async def generate_text(self, prompt_text: str, stage_name: str) -> str:
        """在当前会话内执行一轮文本生成。"""
        raise NotImplementedError

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """释放当前会话持有的底层资源。"""
        raise NotImplementedError

    # 执行snapshot history相关逻辑。
    def snapshot_history(self) -> list[HistoryItem]:
        """返回当前会话已确认成功的对话历史，供 failover 时无损续接。"""
        raise NotImplementedError


# 定义DynamicViewNode2Client。
class DynamicViewNode2Client:
    """动态视图 node2 客户端抽象，统一暴露 create_session 与单次 generate_text。"""

    # 执行create session相关逻辑。
    def create_session(
        self,
        initial_history: list[HistoryItem] | None = None,
    ) -> DynamicViewNode2Session:
        """创建一条 node2 连续会话，可按给定历史继续生成。"""
        raise NotImplementedError

    # 执行generate text相关逻辑。
    async def generate_text(self, prompt_text: str, stage_name: str) -> str:
        """执行一次无状态 node2 文本生成。"""
        # 执行create session相关逻辑。
        session = self.create_session()
        try:
            return await session.generate_text(prompt_text, stage_name)
        finally:
            # 执行aclose相关逻辑。
            await session.aclose()


# 定义DynamicViewGeminiClient。
class DynamicViewGeminiClient(DynamicViewNode2Client):
    """动态视图 node2 的 Gemini 原生客户端，直接走 google-genai 接口。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        profile: DynamicViewNode2StepProfile | ModelProfile,
        api_key_provider: ApiKeyProvider | None = None,
    ) -> None:
        """执行init相关逻辑。"""
        self.profile = _ensure_node2_step_profile(profile)
        self.api_key_provider = api_key_provider
        self._sdk_clients: dict[str, Any] = {}

    # 执行create session相关逻辑。
    def create_session(
        self,
        initial_history: list[HistoryItem] | None = None,
    ) -> "DynamicViewGeminiSession":
        """为 node2 创建一个连续会话，供多轮追加 types.Content 使用。"""
        return DynamicViewGeminiSession(self, self.profile, initial_history)

    # 执行iter clients相关逻辑。
    def iter_clients(self, profile: ModelProfile):
        """按 API Key 轮询顺序返回 Gemini client，供调用失败时切换下一个 Key。"""
        api_keys = (
            self.api_key_provider.get_ordered_keys(profile)
            if self.api_key_provider is not None
            else profile.api_keys
        )
        if not api_keys:
            raise RuntimeError(f"缺少 Gemini 数据库 API Key：{profile.profile_key}")
        for api_key in api_keys:
            yield self.ensure_client(profile, api_key)

    # 执行ensure client相关逻辑。
    def ensure_client(self, profile: ModelProfile, api_key: str):
        """按当前阶段 profile 懒加载 Gemini client，允许 step1/step2 使用不同模型参数。"""
        # 执行build node2 profile cache key相关逻辑。
        client_key = f"{_build_node2_profile_cache_key(profile)}:{api_key}"
        # 执行get相关逻辑。
        cached_client = self._sdk_clients.get(client_key)
        if cached_client is not None:
            return cached_client
        try:
            from google import genai
        except ImportError as error:
            raise RuntimeError("缺少 google-genai 依赖，请先安装 google-genai") from error
        if not api_key:
            raise RuntimeError(f"缺少 Gemini 数据库 API Key：{profile.profile_key}")
        # 执行build client kwargs相关逻辑。
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        resolved_base_url = str(profile.base_url or "").strip().rstrip("/")
        if resolved_base_url:
            client_kwargs["http_options"] = {
                "api_version": "v1beta",
                "base_url": resolved_base_url,
            }
        # 执行Client相关逻辑。
        client = genai.Client(**client_kwargs)
        self._sdk_clients[client_key] = client
        return client

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """清理 Gemini client 缓存。"""
        # 执行clear相关逻辑。
        self._sdk_clients.clear()


# 定义GeminiPromptClient。
class GeminiPromptClient:
    """复用 dynamic_view Gemini 原生客户端，供 PromptRunner 文本和结构化节点调用。"""

    # 执行init相关逻辑。
    def __init__(self, profile: ModelProfile, api_key_provider: ApiKeyProvider) -> None:
        """执行init相关逻辑。"""
        self.profile = profile
        self._client = DynamicViewGeminiClient(profile, api_key_provider)

    # 执行invoke text相关逻辑。
    async def invoke_text(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> str:
        """执行一次 Gemini 文本调用。"""
        if self.profile.stream:
            response_chunks: list[str] = []
            async for chunk_text in self.invoke_text_stream(
                messages,
                temperature=temperature,
                extra_body_override=extra_body_override,
            ):
                # 执行append相关逻辑。
                response_chunks.append(chunk_text)
            return "".join(response_chunks)
        # 执行invoke prompt once相关逻辑。
        return await self._invoke_prompt_once(messages, temperature)

    # 执行invoke text stream相关逻辑。
    async def invoke_text_stream(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None = None,
        extra_body_override: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """执行一次 Gemini 流式文本调用。"""
        if not self.profile.stream:
            yield await self.invoke_text(
                messages,
                temperature=temperature,
                extra_body_override=extra_body_override,
            )
            return
        from google.genai import types

        request_parts = _build_gemini_prompt_request_parts(types, messages)
        generate_content_config = _build_prompt_generate_content_config(
            types,
            self.profile,
            temperature,
        )
        last_error: Exception | None = None
        for client in self._client.iter_clients(self.profile):
            try:
                async for chunk_text in _stream_gemini_generate_content_chunks(
                    client,
                    self.profile.model,
                    request_parts["contents"],
                    generate_content_config,
                ):
                    yield chunk_text
                return
            except Exception as error:
                last_error = error
        raise last_error if last_error is not None else RuntimeError("Gemini stream request failed")

    # 执行invoke structured相关逻辑。
    async def invoke_structured(
        self,
        messages: Sequence[BaseMessage],
        schema: type[StructuredOutputT],
        temperature: float | None = None,
    ) -> StructuredOutputT:
        """执行一次 Gemini 结构化输出调用。"""
        from google.genai import types

        request_parts = _build_gemini_prompt_request_parts(types, messages)
        generate_content_config = _build_prompt_generate_content_config(
            types,
            self.profile,
            temperature,
            response_schema=schema,
        )
        response_text = await self._collect_prompt_text_with_key_failover(
            request_parts["contents"],
            generate_content_config,
        )
        try:
            return schema.model_validate_json(response_text)
        except Exception:
            return schema.model_validate(json.loads(response_text))

    # 执行allows streaming相关逻辑。
    def allows_streaming(self) -> bool:
        """返回当前 Gemini 配置是否允许流式输出。"""
        return self.profile.stream

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """关闭 Gemini 请求资源。"""
        # 执行aclose相关逻辑。
        await self._client.aclose()

    # 执行invoke prompt once相关逻辑。
    async def _invoke_prompt_once(
        self,
        messages: Sequence[BaseMessage],
        temperature: float | None,
    ) -> str:
        """执行一次非流式 Gemini 文本请求。"""
        from google.genai import types

        request_parts = _build_gemini_prompt_request_parts(types, messages)
        generate_content_config = _build_prompt_generate_content_config(
            types,
            self.profile,
            temperature,
        )
        return await self._collect_prompt_text_with_key_failover(
            request_parts["contents"],
            generate_content_config,
        )

    # 执行collect prompt text with key failover相关逻辑。
    async def _collect_prompt_text_with_key_failover(self, contents: object, config: object) -> str:
        """使用当前 Gemini profile 的全部 Key 依次请求，直到成功或全部失败。"""
        last_error: Exception | None = None
        for client in self._client.iter_clients(self.profile):
            try:
                return "".join(await _collect_gemini_generate_content_stream(
                    client,
                    self.profile.model,
                    contents,
                    config,
                )).strip()
            except Exception as error:
                last_error = error
        raise last_error if last_error is not None else RuntimeError("Gemini request failed")


# 定义DynamicViewGeminiSession。
class DynamicViewGeminiSession(DynamicViewNode2Session):
    """动态视图 node2 的连续会话包装，负责把多轮 prompt 追加进同一个 contents 历史。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        client: DynamicViewGeminiClient,
        profile: DynamicViewNode2StepProfile,
        initial_history: list[HistoryItem] | None = None,
    ) -> None:
        """执行init相关逻辑。"""
        self._client = client
        self._profile = profile
        self._history: list[HistoryItem] = list(initial_history or [])

    # 执行generate text相关逻辑。
    async def generate_text(self, prompt_text: str, stage_name: str) -> str:
        """在同一个 Gemini 会话内追加一轮 user content，并保存 model 回复。"""
        from google.genai import types

        # 执行resolve node2 stage profile相关逻辑。
        current_profile = _resolve_node2_stage_profile(self._profile, stage_name)
        contents = [
            # 执行Content相关逻辑。
            types.Content(
                role=message_role,
                parts=[types.Part.from_text(text=message_text)],
            )
            for message_role, message_text in [*self._history, ("user", prompt_text)]
        ]
        # 执行build generate content config相关逻辑。
        generate_content_config = self._build_generate_content_config(types, current_profile)
        last_error: Exception | None = None
        response_text = ""
        for client in self._client.iter_clients(current_profile):
            try:
                response_text = "".join(await _collect_gemini_generate_content_stream(
                    client,
                    current_profile.model,
                    contents,
                    generate_content_config,
                )).strip()
                break
            except Exception as error:
                last_error = error
        if not response_text and last_error is not None:
            raise last_error
        # 执行append相关逻辑。
        self._history.append(("user", prompt_text))
        # 执行append相关逻辑。
        self._history.append(("model", response_text))
        # 执行info相关逻辑。
        logger.info(
            "Gemini Response | stage=%s | model=%s | response=%s",
            stage_name,
            current_profile.model,
            # 执行normalize log text相关逻辑。
            _normalize_log_text(response_text),
        )
        return response_text

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """Gemini 同步流由线程内消费完毕，会话结束无需额外关闭。"""
        return None

    # 执行snapshot history相关逻辑。
    def snapshot_history(self) -> list[HistoryItem]:
        """返回当前 Gemini 会话历史，供 failover 时作为下一条路由的输入。"""
        return list(self._history)

    # 执行build generate content config相关逻辑。
    def _build_generate_content_config(self, types, profile: ModelProfile):
        """显式构造 Gemini 的 GenerateContentConfig，确保 step1/step2 各自使用自己的参数。"""
        config_kwargs: dict[str, Any] = {
            "thinking_config": types.ThinkingConfig(thinking_level="MEDIUM"),
            "media_resolution": "MEDIA_RESOLUTION_HIGH",
        }
        if profile.temperature is not None:
            config_kwargs["temperature"] = profile.temperature
        if profile.top_p is not None:
            config_kwargs["top_p"] = profile.top_p
        if profile.top_k is not None:
            config_kwargs["top_k"] = profile.top_k
        if profile.max_tokens is not None:
            config_kwargs["max_output_tokens"] = profile.max_tokens
        return types.GenerateContentConfig(**config_kwargs)


# 定义DynamicViewOpenAICompatibleNode2Client。
class DynamicViewOpenAICompatibleNode2Client(DynamicViewNode2Client):
    """动态视图 node2 的 OpenAI 兼容客户端，适配带 base_url 的通用网关。"""

    # 执行init相关逻辑。
    def __init__(self, profile: DynamicViewNode2StepProfile | ModelProfile, api_key_provider: ApiKeyProvider) -> None:
        """执行init相关逻辑。"""
        self.profile = _ensure_node2_step_profile(profile)
        self._step1_chat_client = LangChainChatClient(self.profile.step1, api_key_provider)
        self._step2_chat_client = LangChainChatClient(self.profile.step2, api_key_provider)

    # 执行create session相关逻辑。
    def create_session(
        self,
        initial_history: list[HistoryItem] | None = None,
    ) -> "DynamicViewOpenAICompatibleNode2Session":
        """创建一条基于 OpenAI 兼容接口的 node2 会话。"""
        return DynamicViewOpenAICompatibleNode2Session(
            self._step1_chat_client,
            self._step2_chat_client,
            self.profile,
            initial_history,
        )

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """关闭 node2 两个阶段各自持有的底层 HTTP 连接池。"""
        # 执行aclose相关逻辑。
        await self._step1_chat_client.aclose()
        if self._step2_chat_client is self._step1_chat_client:
            return
        # 执行aclose相关逻辑。
        await self._step2_chat_client.aclose()


# 定义DynamicViewOpenAICompatibleNode2Session。
class DynamicViewOpenAICompatibleNode2Session(DynamicViewNode2Session):
    """动态视图 node2 的 OpenAI 兼容会话，按完整历史重放方式维持连续上下文。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        step1_chat_client: LangChainChatClient,
        step2_chat_client: LangChainChatClient,
        profile: DynamicViewNode2StepProfile,
        initial_history: list[HistoryItem] | None = None,
    ) -> None:
        """执行init相关逻辑。"""
        self._step1_chat_client = step1_chat_client
        self._step2_chat_client = step2_chat_client
        self._profile = profile
        self._history: list[HistoryItem] = list(initial_history or [])

    # 执行generate text相关逻辑。
    async def generate_text(self, prompt_text: str, stage_name: str) -> str:
        """按当前历史重放 user/model 消息，再向 OpenAI 兼容接口追加一轮生成。"""
        # 执行resolve node2 stage profile相关逻辑。
        current_profile = _resolve_node2_stage_profile(self._profile, stage_name)
        current_chat_client = (
            self._step2_chat_client
            if current_profile is self._profile.step2
            else self._step1_chat_client
        )
        # 执行build langchain history messages相关逻辑。
        rendered_messages = _build_langchain_history_messages(self._history, prompt_text)
        # 执行invoke text相关逻辑。
        response_text = await current_chat_client.invoke_text(rendered_messages)
        # 执行strip相关逻辑。
        normalized_response_text = response_text.strip()
        # 执行append相关逻辑。
        self._history.append(("user", prompt_text))
        # 执行append相关逻辑。
        self._history.append(("model", normalized_response_text))
        # 执行info相关逻辑。
        logger.info(
            "Node2 OpenAI Response | stage=%s | model=%s | response=%s",
            stage_name,
            current_profile.model,
            # 执行normalize log text相关逻辑。
            _normalize_log_text(normalized_response_text),
        )
        return normalized_response_text

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """OpenAI 兼容会话不持有额外连接资源，结束时无需额外处理。"""
        return None

    # 执行snapshot history相关逻辑。
    def snapshot_history(self) -> list[HistoryItem]:
        """返回当前 OpenAI 兼容会话历史，供 failover 时续接。"""
        return list(self._history)


# 定义DynamicViewNode2FailoverClient。
class DynamicViewNode2FailoverClient(DynamicViewNode2Client):
    """动态视图 node2 的多路由客户端，按配置声明顺序依次失败切换。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        profile: DynamicViewNode2RouterProfile,
        api_key_provider: ApiKeyProvider,
    ) -> None:
        """执行init相关逻辑。"""
        route_profiles = profile.list_profiles()
        self.profile = _ensure_node2_step_profile(route_profiles[0])
        self._route_clients = [
            # 执行build node2 route client相关逻辑。
            build_dynamic_view_node2_client(route_profile, api_key_provider)
            for route_profile in route_profiles
        ]

    # 执行create session相关逻辑。
    def create_session(
        self,
        initial_history: list[HistoryItem] | None = None,
    ) -> "DynamicViewNode2FailoverSession":
        """创建一条支持多路由自动切换的 node2 会话。"""
        return DynamicViewNode2FailoverSession(self._route_clients, initial_history)

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """关闭 failover 客户端下所有可关闭的底层连接。"""
        for route_client in self._route_clients:
            if hasattr(route_client, "aclose"):
                # 执行aclose相关逻辑。
                await route_client.aclose()


# 定义DynamicViewNode2FailoverSession。
class DynamicViewNode2FailoverSession(DynamicViewNode2Session):
    """动态视图 node2 的 failover 会话，保证两段 prompt 在切路由后仍能续接历史。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        route_clients: list[DynamicViewNode2Client],
        initial_history: list[HistoryItem] | None = None,
    ) -> None:
        """执行init相关逻辑。"""
        self._route_clients = route_clients
        self._active_route_index: int | None = None
        self._active_session: DynamicViewNode2Session | None = None
        self._seed_history: list[HistoryItem] = list(initial_history or [])

    # 执行generate text相关逻辑。
    async def generate_text(self, prompt_text: str, stage_name: str) -> str:
        """优先沿用当前路由；当前路由失败时，再把已确认历史续接到下一条路由。"""
        if self._active_session is None:
            return await self._activate_and_generate(
                prompt_text=prompt_text,
                stage_name=stage_name,
                start_index=0,
                history=self._seed_history,
            )
        try:
            return await self._active_session.generate_text(prompt_text, stage_name)
        except Exception as error:
            if self._active_route_index is None:
                raise
            current_route_index = self._active_route_index
            # 执行snapshot history相关逻辑。
            current_history = self._active_session.snapshot_history()
            # 执行aclose相关逻辑。
            await self._active_session.aclose()
            self._active_session = None
            # 执行log node2 failover warning相关逻辑。
            _log_node2_failover_warning(
                route_index=current_route_index + 1,
                profile=self._route_clients[current_route_index].profile,
                error=error,
            )
            return await self._activate_and_generate(
                prompt_text=prompt_text,
                stage_name=stage_name,
                start_index=current_route_index + 1,
                history=current_history,
            )

    # 执行activate and generate相关逻辑。
    async def _activate_and_generate(
        self,
        *,
        prompt_text: str,
        stage_name: str,
        start_index: int,
        history: list[HistoryItem],
    ) -> str:
        """从指定路由索引开始尝试激活可用会话，直到成功或所有路由全部失败。"""
        last_error: Exception | None = None
        for route_index in range(start_index, len(self._route_clients)):
            route_client = self._route_clients[route_index]
            # 执行create session相关逻辑。
            current_session = route_client.create_session(history)
            try:
                # 执行generate text相关逻辑。
                response_text = await current_session.generate_text(prompt_text, stage_name)
                self._active_route_index = route_index
                self._active_session = current_session
                return response_text
            except Exception as error:
                last_error = error
                # 执行aclose相关逻辑。
                await current_session.aclose()
                # 执行log node2 failover warning相关逻辑。
                _log_node2_failover_warning(
                    route_index=route_index + 1,
                    profile=route_client.profile,
                    error=error,
                )
        raise last_error if last_error is not None else RuntimeError("Node2 request failed")

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """关闭当前激活的 node2 会话。"""
        if self._active_session is None:
            return
        # 执行aclose相关逻辑。
        await self._active_session.aclose()
        self._active_session = None

    # 执行snapshot history相关逻辑。
    def snapshot_history(self) -> list[HistoryItem]:
        """返回当前有效历史；若尚未激活任何路由，则返回初始历史。"""
        if self._active_session is not None:
            return self._active_session.snapshot_history()
        return list(self._seed_history)


# 定义GeminiStreamController。
class GeminiStreamController:
    """保存 Gemini 同步 stream 对象，供 asyncio 取消时主动关闭底层流。"""

    # 执行init相关逻辑。
    def __init__(self) -> None:
        """执行init相关逻辑。"""
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._stream: object | None = None

    # 执行bind stream相关逻辑。
    def bind_stream(self, stream: object) -> None:
        """绑定当前正在消费的 Gemini stream 对象。"""
        with self._lock:
            # 执行set stream相关逻辑。
            self._stream = stream
        if self.stop_event.is_set():
            # 执行close gemini stream related logic。
            _close_gemini_stream(stream)

    # 执行request stop相关逻辑。
    def request_stop(self) -> None:
        """请求停止线程消费，并尽量关闭 Gemini stream。"""
        # 执行set相关逻辑。
        self.stop_event.set()
        with self._lock:
            # 执行get stream相关逻辑。
            stream = self._stream
        if stream is not None:
            # 执行close gemini stream related logic。
            _close_gemini_stream(stream)


# 执行stream gemini generate content chunks相关逻辑。
async def _stream_gemini_generate_content_chunks(
    client: object,
    model: str,
    contents: object,
    config: object,
) -> AsyncIterator[str]:
    """把 google-genai 同步流桥接为可取消的异步流。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    controller = GeminiStreamController()
    thread = threading.Thread(
        target=_consume_gemini_generate_content_stream,
        args=(client, model, contents, config, loop, queue, controller),
        daemon=True,
    )
    # 执行start相关逻辑。
    thread.start()
    try:
        while True:
            # 执行get相关逻辑。
            event_type, event_value = await queue.get()
            if event_type == "chunk":
                yield str(event_value)
                continue
            if event_type == "error":
                raise event_value
            return
    except asyncio.CancelledError:
        # 执行request stop相关逻辑。
        controller.request_stop()
        raise


# 执行collect gemini generate content stream相关逻辑。
async def _collect_gemini_generate_content_stream(
    client: object,
    model: str,
    contents: object,
    config: object,
) -> list[str]:
    """收集 Gemini 流式结果；取消时同样关闭底层 stream。"""
    response_chunks: list[str] = []
    async for chunk_text in _stream_gemini_generate_content_chunks(
        client,
        model,
        contents,
        config,
    ):
        # 执行append相关逻辑。
        response_chunks.append(chunk_text)
    return response_chunks


# 执行consume gemini generate content stream相关逻辑。
def _consume_gemini_generate_content_stream(
    client: object,
    model: str,
    contents: object,
    config: object,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[tuple[str, object]],
    controller: GeminiStreamController,
) -> None:
    """在线程里逐块消费 Gemini 同步流，并允许外部关闭。"""
    stream: object | None = None
    try:
        # 执行generate content stream相关逻辑。
        stream = client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        )
        # 执行bind stream相关逻辑。
        controller.bind_stream(stream)
        for chunk in stream:
            if controller.stop_event.is_set():
                break
            chunk_text = getattr(chunk, "text", "") or ""
            if chunk_text:
                # 执行emit gemini stream event相关逻辑。
                _emit_gemini_stream_event(loop, queue, "chunk", chunk_text)
    except BaseException as error:
        if not controller.stop_event.is_set():
            # 执行emit gemini stream event相关逻辑。
            _emit_gemini_stream_event(loop, queue, "error", error)
    finally:
        if stream is not None:
            # 执行close gemini stream related logic。
            _close_gemini_stream(stream)
        # 执行emit gemini stream event相关逻辑。
        _emit_gemini_stream_event(loop, queue, "done", None)


# 执行emit gemini stream event相关逻辑。
def _emit_gemini_stream_event(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[tuple[str, object]],
    event_type: str,
    event_value: object,
) -> None:
    """把线程内事件安全投递到 asyncio 队列。"""
    try:
        # 执行call soon threadsafe相关逻辑。
        loop.call_soon_threadsafe(queue.put_nowait, (event_type, event_value))
    except RuntimeError:
        return


# 执行close gemini stream相关逻辑。
def _close_gemini_stream(stream: object) -> None:
    """尽量关闭 google-genai 返回的同步流对象。"""
    close_method = getattr(stream, "close", None)
    if callable(close_method):
        try:
            # 执行close相关逻辑。
            close_method()
        except Exception:
            return


# 执行normalize log text相关逻辑。
def _normalize_log_text(content: object, max_length: int = 240) -> str:
    """把 Gemini 请求与响应日志压成单行，避免控制台被大段文本刷屏。"""
    # 执行str相关逻辑。
    normalized_content = str(content).replace("\r", " ").replace("\n", " ")
    normalized_content = " ".join(normalized_content.split())
    if len(normalized_content) <= max_length:
        return normalized_content
    return normalized_content[:max_length] + "...[truncated]"


# 执行build langchain history messages相关逻辑。
def _build_langchain_history_messages(
    history: list[HistoryItem],
    prompt_text: str,
) -> list[HumanMessage | AIMessage]:
    """把 node2 历史转成 LangChain 消息列表，并在末尾追加当前 user prompt。"""
    rendered_messages: list[HumanMessage | AIMessage] = []
    for message_role, message_text in history:
        if message_role == "model":
            # 执行append相关逻辑。
            rendered_messages.append(AIMessage(content=message_text))
            continue
        # 执行append相关逻辑。
        rendered_messages.append(HumanMessage(content=message_text))
    # 执行append相关逻辑。
    rendered_messages.append(HumanMessage(content=prompt_text))
    return rendered_messages


# 执行build gemini prompt request parts相关逻辑。
def _build_gemini_prompt_request_parts(types, messages: Sequence[BaseMessage]) -> dict[str, object]:
    """把 PromptRunner 的 LangChain 消息转换成 Gemini contents。"""
    contents: list[object] = []
    for message in messages:
        message_text = str(message.content)
        role = "model" if message.type == "ai" else "user"
        # 执行append相关逻辑。
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=message_text)]))
    return {
        "contents": contents,
    }


# 执行build prompt generate content config相关逻辑。
def _build_prompt_generate_content_config(
    types,
    profile: ModelProfile,
    temperature: float | None,
    response_schema: type[BaseModel] | None = None,
):
    """按 PromptRunner 的 ModelProfile 构造 Gemini GenerateContentConfig。"""
    config_kwargs: dict[str, Any] = {
        "thinking_config": types.ThinkingConfig(thinking_level="MEDIUM"),
        "media_resolution": "MEDIA_RESOLUTION_HIGH",
    }
    resolved_temperature = profile.temperature if temperature is None else temperature
    if resolved_temperature is not None:
        config_kwargs["temperature"] = resolved_temperature
    if profile.top_p is not None:
        config_kwargs["top_p"] = profile.top_p
    if profile.top_k is not None:
        config_kwargs["top_k"] = profile.top_k
    if profile.max_tokens is not None:
        config_kwargs["max_output_tokens"] = profile.max_tokens
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema
    return types.GenerateContentConfig(**config_kwargs)


# 执行build dynamic view node2 client相关逻辑。
def build_dynamic_view_node2_client(
    profile: DynamicViewNode2StepProfile | ModelProfile,
    api_key_provider: ApiKeyProvider,
) -> DynamicViewNode2Client:
    """按 router_type 显式选择 node2 路由实现：openai 走兼容接口，gemini 走原生接口。"""
    # 执行ensure node2 step profile相关逻辑。
    normalized_profile = _ensure_node2_step_profile(profile)
    router_type = _resolve_node2_router_type(normalized_profile)
    if router_type == "gemini":
        return DynamicViewGeminiClient(profile, api_key_provider)
    if router_type == "openai":
        return DynamicViewOpenAICompatibleNode2Client(profile, api_key_provider)
    raise ValueError(f"Unsupported node2 router_type: {router_type}")


# 执行log node2 failover warning相关逻辑。
def _log_node2_failover_warning(
    *,
    route_index: int,
    profile: DynamicViewNode2StepProfile | ModelProfile,
    error: Exception,
) -> None:
    """记录 node2 路由失败与切换日志，方便排查当前切换到了哪条后备路由。"""
    # 执行ensure node2 step profile相关逻辑。
    normalized_profile = _ensure_node2_step_profile(profile)
    # 执行warning相关逻辑。
    logger.warning(
        "Node2 failover | route=%s | routerType=%s | step1Model=%s | step2Model=%s | step1BaseUrl=%s | step2BaseUrl=%s | error=%s",
        route_index,
        _resolve_node2_router_type(normalized_profile),
        normalized_profile.step1.model,
        normalized_profile.step2.model,
        normalized_profile.step1.base_url,
        normalized_profile.step2.base_url,
        # 执行str相关逻辑。
        str(error).strip() or error.__class__.__name__,
    )


# 执行resolve node2 router type相关逻辑。
def _resolve_node2_router_type(profile: DynamicViewNode2StepProfile) -> str:
    """读取 node2 路由类型，step1/step2 必须保持一致。"""
    step1_router_type = str(profile.step1.router_type or "openai").strip().lower()
    step2_router_type = str(profile.step2.router_type or step1_router_type).strip().lower()
    if step1_router_type != step2_router_type:
        raise ValueError(f"node2 step router_type mismatch: {step1_router_type}/{step2_router_type}")
    return step1_router_type


# 执行ensure node2 step profile相关逻辑。
def _ensure_node2_step_profile(
    profile: DynamicViewNode2StepProfile | ModelProfile,
) -> DynamicViewNode2StepProfile:
    """把 node2 单阶段配置统一归一成 step1/step2 双阶段视图，方便会话层始终按同一套接口取参。"""
    if isinstance(profile, DynamicViewNode2StepProfile):
        return profile
    return DynamicViewNode2StepProfile(step1=profile, step2=profile)


# 执行resolve node2 stage profile相关逻辑。
def _resolve_node2_stage_profile(
    profile: DynamicViewNode2StepProfile,
    stage_name: str,
) -> ModelProfile:
    """按当前 node2 阶段选择真正生效的模型参数，约定时间轴脚本走 step1，动态样式走 step2。"""
    if stage_name == "dynamic_view_node2_dynamic_css":
        return profile.step2
    return profile.step1


# 执行build node2 profile cache key相关逻辑。
def _build_node2_profile_cache_key(profile: ModelProfile) -> str:
    """为 Gemini 多阶段客户端构造稳定缓存键，避免 step1/step2 共用错底层连接。"""
    return f"{profile.profile_key}:{profile.base_url}:{profile.model}"
