# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\generation_service.py。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.clients.api_key_provider import ApiKeyProvider
from app.clients.langchain_chat_client import LangChainChatClient
from app.features.chat.schemas import (
    ConversationContextMessage,
    RoleProfileData,
)
from app.features.dynamic_view.prompt_builder import (
    build_generation_role_chat_prompt,
    build_knowledge_revealed_role_chat_prompt,
)
from app.features.dynamic_view.gemini_client import GeminiPromptClient
from app.repositories.dynamic_view_model_profile_repository import DynamicViewModelProfileRepository
from app.services.prompt_runner import PromptRunner


# 定义GenerationService。
class GenerationService:
    """由单一生成模型直接产出最终 assistant 文本流。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        *,
        dynamic_view_model_profile_repository: DynamicViewModelProfileRepository,
        api_key_provider: ApiKeyProvider,
        session_factory: Callable[[], Session],
    ) -> None:
        """执行init相关逻辑。"""
        self.dynamic_view_model_profile_repository = dynamic_view_model_profile_repository
        self.api_key_provider = api_key_provider
        self.session_factory = session_factory

    # 执行build latest generator runner相关逻辑。
    def _build_latest_generator_runner(self) -> PromptRunner:
        """每次聊天生成前从 dynamic_view_model_profile 读取最新 generator 配置。"""
        with self.session_factory() as db:
            profile = self.dynamic_view_model_profile_repository.resolve_active_profile(
                db,
                "system",
                "generator",
            )
        if profile is None:
            raise RuntimeError("当前 system/generator 模型节点暂不可用")
        if str(profile.router_type or "").strip().lower() == "gemini":
            return PromptRunner(GeminiPromptClient(profile, self.api_key_provider))
        return PromptRunner(LangChainChatClient(profile, self.api_key_provider))

    # 执行runner allows streaming相关逻辑。
    def _runner_allows_streaming(self, runner: PromptRunner) -> bool:
        """统一读取当前 generator runner 是否允许真正流式输出。"""
        if hasattr(runner.client, "allows_streaming"):
            return bool(runner.client.allows_streaming())
        return False

    # 执行close runner相关逻辑。
    async def _close_runner(self, runner: PromptRunner) -> None:
        """关闭本次聊天生成临时创建的模型客户端。"""
        client = runner.client
        if hasattr(client, "aclose"):
            await client.aclose()

    # 执行stream content text相关逻辑。
    async def stream_content_text(
        self,
        content: str,
        recent_messages: list[ConversationContextMessage],
        user_mention: str,
        role_profile: RoleProfileData,
    ) -> AsyncIterator[str]:
        """按配置返回 assistant 原始文本流；未开启流式时退化为一次性返回整段文本。"""
        # 执行build generation messages相关逻辑。
        messages = self._build_generation_messages(
            content=content,
            recent_messages=recent_messages,
            user_mention=user_mention,
            role_profile=role_profile,
        )
        extra_body_override = (
            {"enable_search": True}
            if role_profile.dynamic_view_knowledge_ready is True
            else None
        )
        generator_runner = self._build_latest_generator_runner()
        try:
            if self._runner_allows_streaming(generator_runner):
                # 执行run text messages stream相关逻辑。
                async for chunk_text in generator_runner.run_text_messages_stream(
                    messages,
                    stage_name="generation_message",
                    extra_body_override=extra_body_override,
                ):
                    if chunk_text:
                        yield chunk_text
                return
            # 执行run text messages相关逻辑。
            response_text = await generator_runner.run_text_messages(
                messages,
                stage_name="generation_message",
                extra_body_override=extra_body_override,
            )
            if response_text:
                yield response_text
        finally:
            # 执行close runner相关逻辑。
            await self._close_runner(generator_runner)

    # 执行answer dynamic view knowledge question相关逻辑。
    async def answer_dynamic_view_knowledge_question(
        self,
        *,
        image_data_url: str,
        vivid: str,
        ext: str,
        en: str,
        user_input: str,
    ) -> str:
        """结合当前知识画面和字幕回答用户问题。"""
        question = user_input.strip()
        if not question:
            raise ValueError("用户输入不能为空")
        prompt_text = (
            "你是知搭的知识视图问答助手。请结合当前画面截图、当前幕字幕和用户输入进行解释。\n"
            "要求：\n"
            "1. 直接回答用户问题，不要复述规则。\n"
            "2. 优先解释当前画面正在表达的知识点。\n"
            "3. 简短描述画面，然后说明用户输入和画面的关系\n"
            "4. 使用纯中文，禁止使用markdown语法。控制在 100 字以内。\n\n"
            f"【当前中文字幕一】{vivid.strip()}\n"
            f"【当前中文字幕二】{ext.strip()}\n"
            f"【当前英文字幕】{en.strip()}\n"
            f"【用户输入】{question}"
        )
        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ]
            )
        ]
        generator_runner = self._build_latest_generator_runner()
        try:
            return await generator_runner.run_text_messages(
                messages,
                extra_body_override={
                    "enable_search": False,
                    "enable_thinking": False,
                    "reasoning": {"effort": "none"},
                },
            )
        finally:
            # 执行close runner相关逻辑。
            await self._close_runner(generator_runner)

    # 执行build generation messages相关逻辑。
    def _build_generation_messages(
        self,
        *,
        content: str,
        recent_messages: list[ConversationContextMessage],
        user_mention: str,
        role_profile: RoleProfileData,
    ) -> list[SystemMessage | HumanMessage | AIMessage]:
        """把系统设定、历史消息和当前用户消息展开成真实 messages 序列。"""
        return [
            *self._build_generation_system_messages(role_profile=role_profile),
            *self._build_history_messages(recent_messages),
            self._build_current_user_message(
                content=content,
                user_mention=user_mention,
            ),
        ]

    # 执行build generation system messages相关逻辑。
    def _build_generation_system_messages(
        self,
        *,
        role_profile: RoleProfileData,
    ) -> list[SystemMessage]:
        """按线索揭晓状态切换 generation 阶段 system prompt 模板。"""
        if role_profile.dynamic_view_knowledge_ready is True:
            return [
                SystemMessage(
                    content=build_knowledge_revealed_role_chat_prompt(
                        role_profile=role_profile,
                        prompt_version=1,
                    )
                )
            ]
        return [
                SystemMessage(
                    content=build_generation_role_chat_prompt(
                        role_profile=role_profile,
                        prompt_version=1,
                    )
                )
            ]

    # 执行build history messages相关逻辑。
    def _build_history_messages(
        self,
        recent_messages: list[ConversationContextMessage],
    ) -> list[HumanMessage | AIMessage]:
        """把最近上下文按 user/assistant 消息列表展开，直接传原始文本 content。"""
        rendered_messages: list[HumanMessage | AIMessage] = []
        for message in recent_messages:
            role = message["role"].strip().lower()
            content = message["content"]
            if not role or not content.strip():
                continue
            if role == "assistant":
                rendered_messages.append(AIMessage(content=content))
                continue
            rendered_messages.append(HumanMessage(content=content))
        return rendered_messages

    # 执行build current user message相关逻辑。
    def _build_current_user_message(
        self,
        *,
        content: str,
        user_mention: str,
    ) -> HumanMessage:
        """把当前用户输入与回复输出协议一起追加为最后一条 user 消息。"""
        _ = user_mention
        protocol_text = (
            "【输出格式约束】\n"
            "你必须只输出 5 段文本，并使用半角竖线|分隔：\n"
            "回复正文|选项1|选项2|选项3|选项4\n"
            "要求：4 个选项里必须是 3 个迷惑项 + 1 个正确推进项；"
            "不要输出额外说明、序号、引号或多余段落。"
        )
        return HumanMessage(content=f"{content}\n\n{protocol_text}")
