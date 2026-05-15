# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\chat_service.py。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import hashlib
from html import escape
import logging
import uuid
from dataclasses import dataclass
from threading import Lock

from sqlalchemy.orm import Session

from app.core.constants import ChatConstants
from app.core.settings import settings
from app.features.chat.schemas import (
    ChatBootstrapResponse,
    ChatConversationCreateRequest,
    ChatConversationResponse,
    ChatMessageResponse,
    ChatProcessRequest,
    ChatRequest,
    ChatSendResponse,
    RoleProfileData,
)
from app.features.dynamic_view.service import DynamicViewService
from app.repositories.chat_repository import ChatRepository
from app.services.generation_service import GenerationService
from app.services.mqtt_publish_service import MqttPublishService


logger = logging.getLogger(__name__)
_MAX_TURN_FAILURE_MESSAGE_LENGTH = 120
_DEFAULT_TURN_FAILURE_MESSAGE = "当前回复生成失败，请稍后重试。"


# 定义ChatService。
class ChatService:
    """Python 聊天总门面，负责生成、存档和 MQTT 推送。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        chat_repository: ChatRepository,
        generation_service: GenerationService,
        mqtt_publish_service: MqttPublishService,
        dynamic_view_service: DynamicViewService,
    ) -> None:
        """执行init相关逻辑。"""
        self.chat_repository = chat_repository
        self.generation_service = generation_service
        self.mqtt_publish_service = mqtt_publish_service
        self.dynamic_view_service = dynamic_view_service
        self._latest_turn_markers: dict[str, _TurnMarker] = {}
        self._turn_marker_lock = Lock()

    # 执行build bootstrap response相关逻辑。
    def build_bootstrap_response(self, user_id: str) -> ChatBootstrapResponse:
        """构造 Flutter 连接 MQTT 所需的握手参数。"""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("userId 不能为空")
        return ChatBootstrapResponse(
            user_id=normalized_user_id,
            host=settings.mqtt.host,
            port=settings.mqtt.port,
            topic=self._build_user_topic(normalized_user_id),
            client_id=self._build_client_id(normalized_user_id),
            username=settings.mqtt.username or None,
            password=settings.mqtt.password or None,
        )

    # 执行list conversations相关逻辑。
    def list_conversations(self, db: Session, *, user_id: str) -> list[ChatConversationResponse]:
        """从 MySQL 读取当前用户会话列表，替代 Flutter 本地会话表。"""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id 不能为空")
        sessions = self.chat_repository.list_sessions(db, user_id=normalized_user_id)
        return [
            self._map_session_to_conversation_response(
                db,
                session=session,
                include_messages=False,
            )
            for session in sessions
        ]

    # 执行get conversation相关逻辑。
    def get_conversation(
        self,
        db: Session,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ChatConversationResponse:
        """从 MySQL 读取单个会话及其消息。"""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id 不能为空")
        session = self.chat_repository.get_session(
            db,
            conversation_id=conversation_id,
            user_id=normalized_user_id,
        )
        if session is None:
            raise ValueError(f"未找到聊天会话：conversation_id={conversation_id}")
        return self._map_session_to_conversation_response(
            db,
            session=session,
            include_messages=True,
        )

    # 执行create conversation shell相关逻辑。
    def create_conversation_shell(
        self,
        db: Session,
        request: ChatConversationCreateRequest,
    ) -> ChatConversationResponse:
        """创建动态视图聊天会话壳，消息仍由后续发送链路写入。"""
        session = self.chat_repository.create_conversation_shell(
            db,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            title=request.title,
            snippet=request.snippet,
            view_name=request.view_name or "",
        )
        return self._map_session_to_conversation_response(
            db,
            session=session,
            include_messages=False,
        )

    # 执行accept send相关逻辑。
    def accept_send(self, db: Session, request: ChatRequest) -> tuple[ChatProcessRequest, ChatSendResponse]:
        """接管 Flutter 发送入口，先存档用户消息，再返回后台处理请求。"""
        if not request.role_profiles:
            raise ValueError("当前会话缺少可用角色")
        topic = self._build_user_topic(request.user_id)
        # 执行persist visible user message相关逻辑。
        self._persist_visible_user_message(db, request)
        process_request = ChatProcessRequest.model_validate(
            {
                **request.model_dump(by_alias=True),
                "topic": topic,
            }
        )
        # 执行mark latest turn相关逻辑。
        self.mark_latest_turn(process_request)
        return (
            process_request,
            ChatSendResponse(
                accepted=True,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                topic=topic,
            ),
        )

    # 执行mark latest turn相关逻辑。
    def mark_latest_turn(self, request: ChatRequest) -> None:
        """记录同一会话当前最新的一轮版本，供后台任务丢弃旧输出。"""
        conversation_id = request.conversation_id
        turn_group_id = request.turn_group_id
        if not conversation_id or not turn_group_id:
            return
        with self._turn_marker_lock:
            current_marker = self._latest_turn_markers.get(conversation_id)
            next_marker = _TurnMarker(
                turn_group_id=turn_group_id,
                revision=request.revision,
            )
            if current_marker is None or self._should_replace_turn_marker(
                current_marker,
                next_marker,
            ):
                self._latest_turn_markers[conversation_id] = next_marker

    # 执行process and publish相关逻辑。
    async def process_and_publish(self, db: Session, request: ChatProcessRequest) -> None:
        """执行完整一轮对话，并按配置把原始文本流直接推到 MQTT。"""
        conversation_id = request.conversation_id
        user_id = request.user_id
        target_role_profiles = self._prepare_target_role_profiles(
            db,
            request=request,
        )
        if not target_role_profiles:
            raise ValueError("当前会话缺少可回复角色")
        # 执行get or create session相关逻辑。
        session = self.chat_repository.get_or_create_session(
            db,
            conversation_id,
            user_id,
            request.conversation_title,
            request.view_name or "",
        )
        if not self._is_latest_turn(request):
            logger.warning(
                "Dynamic view clue unlock skipped at chat service: not_latest_turn_before_role_execution | conversationId=%s turnGroupId=%s revision=%s",
                conversation_id,
                request.turn_group_id,
                request.revision,
            )
            return
        role_execution_results = await asyncio.gather(
            *[
                self._execute_role_response_stream(
                    request=request,
                    role_profile=role_profile,
                )
                for role_profile in target_role_profiles
            ]
        )
        if not self._is_latest_turn(request):
            logger.warning(
                "Dynamic view clue unlock skipped at chat service: not_latest_turn_after_role_execution | conversationId=%s turnGroupId=%s revision=%s",
                conversation_id,
                request.turn_group_id,
                request.revision,
            )
            return
        first_completed_role_result = self._resolve_first_completed_role_result(
            role_execution_results
        )
        for role_execution_result in role_execution_results:
            if not role_execution_result.final_content:
                continue
            self._persist_assistant_response(
                db=db,
                session=session,
                conversation_id=conversation_id,
                user_id=user_id,
                content=role_execution_result.final_content,
                role_profile=role_execution_result.role_profile,
            )
        reply_options = (
            first_completed_role_result.reply_options
            if first_completed_role_result is not None
            else []
        )
        if self._is_latest_turn(request):
            # 执行publish turn completed相关逻辑。
            await self._publish_turn_completed(
                topic=request.topic,
                user_id=user_id,
                conversation_id=conversation_id,
                turn_group_id=request.turn_group_id,
                revision=request.revision,
                reply_options=reply_options,
            )
        if self._is_latest_turn(request):
            try:
                if first_completed_role_result is None:
                    logger.warning(
                        "Dynamic view clue unlock skipped at chat service: no_completed_role_result | conversationId=%s turnGroupId=%s revision=%s",
                        conversation_id,
                        request.turn_group_id,
                        request.revision,
                    )
                    return
                logger.warning(
                    "Dynamic view clue unlock entered at chat service | conversationId=%s turnGroupId=%s revision=%s",
                    conversation_id,
                    request.turn_group_id,
                    request.revision,
                )
                # 执行process dynamic view clue unlock after message相关逻辑。
                unlocked_clue_keys = await self._process_dynamic_view_clue_unlock_after_message(
                    request=request,
                    role_profile=first_completed_role_result.role_profile,
                )
                logger.warning(
                    "Dynamic view clue unlock finished at chat service | conversationId=%s turnGroupId=%s revision=%s unlockedCount=%s",
                    conversation_id,
                    request.turn_group_id,
                    request.revision,
                    len(unlocked_clue_keys),
                )
                if unlocked_clue_keys and self._is_latest_turn(request):
                    # 执行publish Dynamic View Clue Unlocked相关逻辑。
                    await self._publish_dynamic_view_clue_unlocked(
                        topic=request.topic,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        turn_group_id=request.turn_group_id,
                        revision=request.revision,
                        clue_keys=unlocked_clue_keys,
                    )
            except Exception:
                logger.exception(
                    "Dynamic view clue unlock side effect failed after chat turn completed: conversationId=%s turnGroupId=%s revision=%s",
                    conversation_id,
                    request.turn_group_id,
                    request.revision,
                )

    # 执行resolve first completed role result相关逻辑。
    def _resolve_first_completed_role_result(
        self,
        role_execution_results: list["_RoleExecutionResult"],
    ) -> "_RoleExecutionResult | None":
        """从并发角色执行结果里取首个有效回复，供后续选项生成和线索链路复用。"""
        return next(
            (
                role_execution_result
                for role_execution_result in role_execution_results
                if role_execution_result.final_content.strip()
            ),
            None,
        )

    # 执行prepare target role profiles相关逻辑。
    def _prepare_target_role_profiles(
        self,
        db: Session,
        *,
        request: ChatProcessRequest,
    ) -> list[RoleProfileData]:
        """按 @ 目标挑出本轮需要回复的角色，并补齐动态视图线索态。"""
        target_role_profiles = self._resolve_target_role_profiles(request)
        fallback_game_archive_id: int | None = None
        if request.conversation_id.startswith("dynamic-view-"):
            conversation_suffix = request.conversation_id.removeprefix("dynamic-view-").strip()
            fallback_game_archive_id = (
                int(conversation_suffix) if conversation_suffix.isdigit() else None
            )
        resolved_role_profiles: list[RoleProfileData] = []
        for role_profile in target_role_profiles:
            if (
                role_profile.dynamic_view_game_archive_id is None
                and fallback_game_archive_id is not None
            ):
                role_profile = role_profile.model_copy(
                    update={
                        "dynamic_view_game_archive_id": fallback_game_archive_id,
                    }
                )
            resolved_role_profiles.append(
                self.dynamic_view_service.resolve_role_profile_for_chat(
                    db,
                    user_id=request.user_id,
                    role_profile=role_profile,
                )
            )
        return resolved_role_profiles

    # 执行process dynamic view clue unlock after message相关逻辑。
    async def _process_dynamic_view_clue_unlock_after_message(
        self,
        *,
        request: ChatProcessRequest,
        role_profile: RoleProfileData,
    ) -> list[str]:
        """聊天主流程完成后再异步执行线索命中判定，不再和消息发送并发。"""
        return await self.dynamic_view_service.process_clue_unlock_after_message(
            user_id=request.user_id,
            message_id=request.message_id,
            user_input=request.content,
            user_mention=self._resolve_request_mention_label(request),
            role_profile=role_profile,
        )

    # 执行build assistant response stream相关逻辑。
    def _build_assistant_response_stream(
        self,
        *,
        request: ChatRequest,
        role_profile: RoleProfileData,
    ) -> AsyncIterator[str]:
        """统一构造 assistant 原始文本流，并严格按 generator 配置决定是否流式。"""
        return self.generation_service.stream_content_text(
            request.content,
            request.recent_messages,
            self._resolve_request_mention_label(request),
            role_profile,
        )

    # 执行execute role response stream相关逻辑。
    async def _execute_role_response_stream(
        self,
        *,
        request: ChatRequest,
        role_profile: RoleProfileData,
    ) -> _RoleExecutionResult:
        """单个角色的生成流与 MQTT 推送统一封装，并从同一次生成结果里解析候选选项。"""
        response_chunks: list[str] = []
        published_reply_length = 0
        assistant_response_stream = self._build_assistant_response_stream(
            request=request,
            role_profile=role_profile,
        )
        async for response_chunk in assistant_response_stream:
            if not self._is_latest_turn(request):
                return _RoleExecutionResult(
                    role_profile=role_profile,
                    final_content="",
                    reply_options=[],
                )
            normalized_chunk = response_chunk
            if not normalized_chunk:
                continue
            response_chunks.append(normalized_chunk)
            combined_output = "".join(response_chunks)
            streamable_reply_text = self._resolve_streamable_reply_text(combined_output)
            publish_increment = streamable_reply_text[published_reply_length:]
            if publish_increment:
                await self._publish_assistant_chunk(
                    topic=request.topic,
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    turn_group_id=request.turn_group_id,
                    revision=request.revision,
                    content=publish_increment,
                    role_profile=role_profile,
                )
                published_reply_length = len(streamable_reply_text)
        final_content, reply_options = self._split_generation_output("".join(response_chunks))
        return _RoleExecutionResult(
            role_profile=role_profile,
            final_content=final_content,
            reply_options=reply_options,
        )

    # 执行resolve streamable reply text相关逻辑。
    def _resolve_streamable_reply_text(self, combined_output: str) -> str:
        """从同次生成结果里提取可流式展示的回复正文（第一段）。"""
        normalized_output = combined_output.replace("｜", "|")
        delimiter_index = normalized_output.find("|")
        if delimiter_index < 0:
            return normalized_output
        return normalized_output[:delimiter_index]

    # 执行split generation output相关逻辑。
    def _split_generation_output(self, combined_output: str) -> tuple[str, list[str]]:
        """把 generation 输出拆成回复正文和 4 个候选项。"""
        normalized_output = combined_output.replace("｜", "|").strip()
        if not normalized_output:
            return "", []
        output_parts = [part.strip() for part in normalized_output.split("|")]
        reply_text = output_parts[0] if output_parts else ""
        raw_options = output_parts[1:]
        normalized_options = self._normalize_generation_reply_options(
            raw_options,
            reply_text=reply_text,
        )
        return reply_text.strip(), normalized_options

    # 执行normalize generation reply options相关逻辑。
    def _normalize_generation_reply_options(
        self,
        raw_options: list[str],
        *,
        reply_text: str,
    ) -> list[str]:
        """清洗同次生成携带的候选项，确保最终给 Flutter 始终返回 4 条。"""
        normalized_options: list[str] = []
        seen_options: set[str] = set()
        for raw_option in raw_options:
            normalized_option = " ".join(str(raw_option).replace("\r", " ").replace("\n", " ").split()).strip()
            if not normalized_option or normalized_option in seen_options:
                continue
            seen_options.add(normalized_option)
            normalized_options.append(normalized_option)
            if len(normalized_options) >= 4:
                return normalized_options[:4]
        fallback_options = self._build_fallback_reply_options(reply_text=reply_text)
        for fallback_option in fallback_options:
            if fallback_option in seen_options:
                continue
            normalized_options.append(fallback_option)
            if len(normalized_options) >= 4:
                break
        return normalized_options[:4]

    # 执行build fallback reply options相关逻辑。
    def _build_fallback_reply_options(self, *, reply_text: str) -> list[str]:
        """当模型输出选项不足 4 条时，用固定模板补齐剩余选项。"""
        normalized_reply_text = " ".join(reply_text.replace("\r", " ").replace("\n", " ").split()).strip()
        focus_fragment = normalized_reply_text[:18].strip("，。！？!?；;：:")
        if not focus_fragment:
            focus_fragment = "关键线索"
        return [
            f"你提到“{focus_fragment}”，它为什么关键？",
            "先不管主线，我们换个轻松话题吧。",
            "我直接猜最终答案可以吗？",
            "这件事是不是和线索无关？",
        ]

    # 执行persist assistant response相关逻辑。
    def _persist_assistant_response(
        self,
        *,
        db: Session,
        session,
        conversation_id: str,
        user_id: str,
        content: str,
        role_profile: RoleProfileData,
    ) -> None:
        """在整轮生成完成后一次性落库 assistant 原始文本，避免 MQTT 流式分片污染服务端存档。"""
        # 执行uuid4相关逻辑。
        message_id = uuid.uuid4().hex
        normalized_speaker_name = role_profile.name.strip() or "assistant"
        # 执行save assistant message相关逻辑。
        self.chat_repository.save_assistant_message(
            db,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
            reply_type=ChatConstants.DEFAULT_REPLY_TYPE,
            raw_content=self._build_message_xml_payload(
                speaker=normalized_speaker_name,
                content=content,
            ),
        )
        session.snippet = f"{normalized_speaker_name}：{content}"
        session.unread = 0
        # 执行add相关逻辑。
        db.add(session)
        # 执行commit相关逻辑。
        db.commit()

    # 执行publish assistant chunk相关逻辑。
    async def _publish_assistant_chunk(
        self,
        *,
        topic: str,
        conversation_id: str,
        user_id: str,
        turn_group_id: str | None,
        revision: int,
        content: str,
        role_profile: RoleProfileData,
    ) -> None:
        """把 assistant 原始文本分片直接推给 Flutter，由 Flutter 自己按协议分隔符切句。"""
        # 执行publish相关逻辑。
        await self.mqtt_publish_service.publish(
            topic,
            {
                "eventType": ChatConstants.ASSISTANT_CHUNK_EVENT_TYPE,
                "userId": user_id,
                "conversationId": conversation_id,
                "turnGroupId": turn_group_id,
                "revision": revision,
                "content": content,
                "speakerName": role_profile.name,
                "speakerRoleKey": role_profile.role_key,
            },
        )

    # 执行resolve target role profiles相关逻辑。
    def _resolve_target_role_profiles(self, request: ChatRequest) -> list[RoleProfileData]:
        """统一根据 @ 全部角色或单角色键筛出本轮需要回复的角色。"""
        if request.mention_all_roles:
            return request.role_profiles
        target_role_keys = {role_key.strip() for role_key in request.target_role_keys if role_key.strip()}
        if not target_role_keys:
            return request.role_profiles[:1]
        matched_role_profiles = [
            role_profile
            for role_profile in request.role_profiles
            if role_profile.role_key in target_role_keys
        ]
        if matched_role_profiles:
            return matched_role_profiles
        return request.role_profiles[:1]

    # 执行resolve request mention label相关逻辑。
    def _resolve_request_mention_label(self, request: ChatRequest) -> str:
        """按当前请求还原本轮 @ 目标，供动态视图线索判定拼装 XML 聊天记录。"""
        if request.mention_all_roles:
            return "@全部角色"
        target_role_keys = {role_key.strip() for role_key in request.target_role_keys if role_key.strip()}
        if not target_role_keys:
            return ""
        matched_role_profile = next(
            (
                role_profile
                for role_profile in request.role_profiles
                if role_profile.role_key in target_role_keys
            ),
            None,
        )
        if matched_role_profile is None:
            return ""
        normalized_name = matched_role_profile.name.strip()
        return f"@{normalized_name}" if normalized_name else ""

    # 执行is latest turn相关逻辑。
    def _is_latest_turn(self, request: ChatRequest) -> bool:
        """只允许当前会话最新版本继续输出，旧版本统一视为已被新消息中断。"""
        conversation_id = request.conversation_id
        turn_group_id = request.turn_group_id
        if not conversation_id or not turn_group_id:
            return True
        with self._turn_marker_lock:
            # 执行get相关逻辑。
            marker = self._latest_turn_markers.get(conversation_id)
        if marker is None:
            return True
        return marker.turn_group_id == turn_group_id and marker.revision == request.revision

    # 执行should replace turn marker相关逻辑。
    def _should_replace_turn_marker(
        self,
        current_marker: "_TurnMarker",
        next_marker: "_TurnMarker",
    ) -> bool:
        """连续发送时只允许更晚的 turnGroupId 或更高 revision 覆盖当前最新轮次。"""
        # 入参来自用户边界，极端情况下若 turnGroupId 不是纯数字，这里也不能让主链路直接 500。
        if current_marker.turn_group_id.isdigit() and next_marker.turn_group_id.isdigit():
            current_turn_group_id = int(current_marker.turn_group_id)
            next_turn_group_id = int(next_marker.turn_group_id)
            if next_turn_group_id != current_turn_group_id:
                return next_turn_group_id > current_turn_group_id
        elif next_marker.turn_group_id != current_marker.turn_group_id:
            return True
        return next_marker.revision > current_marker.revision

    # 执行publish turn completed相关逻辑。
    async def _publish_turn_completed(
        self,
        *,
        topic: str,
        user_id: str,
        conversation_id: str,
        turn_group_id: str | None,
        revision: int,
        reply_options: list[str] | None = None,
    ) -> None:
        """统一发送当前轮次完成事件。"""
        # 执行publish Turn Event相关逻辑。
        await self._publish_turn_event(
            topic=topic,
            event=self._build_turn_event(
                event_type=ChatConstants.ASSISTANT_TURN_COMPLETE_EVENT_TYPE,
                user_id=user_id,
                conversation_id=conversation_id,
                turn_group_id=turn_group_id,
                revision=revision,
                reply_options=reply_options or [],
            ),
        )

    # 执行publish Dynamic View Clue Unlocked相关逻辑。
    async def _publish_dynamic_view_clue_unlocked(
        self,
        *,
        topic: str,
        user_id: str,
        conversation_id: str,
        turn_group_id: str | None,
        revision: int,
        clue_keys: list[str],
    ) -> None:
        """线索点亮后主动推送 MQTT 事件，通知 Flutter 立即刷新动态视图状态。"""
        # 执行publish Turn Event相关逻辑。
        await self._publish_turn_event(
            topic=topic,
            event={
                **self._build_turn_event_base(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    turn_group_id=turn_group_id,
                    revision=revision,
                ),
                "eventType": ChatConstants.DYNAMIC_VIEW_CLUE_UNLOCKED_EVENT_TYPE,
                "clueKeys": clue_keys,
            },
        )

    # 执行publish turn failed相关逻辑。
    async def publish_turn_failed(self, request: ChatProcessRequest, error: Exception) -> None:
        """后台处理异常时主动推送失败事件，避免 Flutter 一直停留在打字中。"""
        # 执行publish Turn Event相关逻辑。
        await self._publish_turn_event(
            topic=request.topic,
            event=self._build_turn_event(
                event_type=ChatConstants.ASSISTANT_TURN_FAILED_EVENT_TYPE,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                turn_group_id=request.turn_group_id,
                revision=request.revision,
                error_message=self._resolve_turn_failure_message(error),
            ),
        )

    # 执行publish turn event相关逻辑。
    async def _publish_turn_event(self, *, topic: str, event: dict[str, object]) -> None:
        """统一发送轮次类 MQTT 事件，避免完成/失败分支各自维护发布调用。"""
        # 执行publish相关逻辑。
        await self.mqtt_publish_service.publish(topic, event)

    # 执行build turn event相关逻辑。
    def _build_turn_event(
        self,
        *,
        event_type: str,
        user_id: str,
        conversation_id: str,
        turn_group_id: str | None,
        revision: int,
        error_message: str | None = None,
        reply_options: list[str] | None = None,
    ) -> dict[str, object]:
        """统一构造轮次事件载荷，避免完成/失败事件分别维护同一套公共字段。"""
        turn_event = self._build_turn_event_base(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_group_id=turn_group_id,
            revision=revision,
        )
        turn_event["eventType"] = event_type
        if error_message:
            turn_event["errorMessage"] = error_message
        if reply_options is not None:
            turn_event["replyOptions"] = reply_options
        return turn_event

    # 执行build turn event base相关逻辑。
    def _build_turn_event_base(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_group_id: str | None,
        revision: int,
    ) -> dict[str, object]:
        """统一构造轮次事件公共字段，避免完成/失败事件分别维护同一套协议键名。"""
        return {
            "userId": user_id,
            "conversationId": conversation_id,
            "turnGroupId": turn_group_id,
            "revision": revision,
        }

    # 执行resolve turn failure message相关逻辑。
    def _resolve_turn_failure_message(self, error: Exception) -> str:
        """把后台异常收敛成可直接展示给用户的失败提示。"""
        # 执行strip相关逻辑。
        message = str(error).strip()
        if not message:
            return _DEFAULT_TURN_FAILURE_MESSAGE
        if len(message) <= _MAX_TURN_FAILURE_MESSAGE_LENGTH:
            return message
        return f"{message[:_MAX_TURN_FAILURE_MESSAGE_LENGTH].rstrip()}..."

    # 执行map session to conversation response相关逻辑。
    def _map_session_to_conversation_response(
        self,
        db: Session,
        *,
        session,
        include_messages: bool,
    ) -> ChatConversationResponse:
        """把 MySQL 会话实体映射成 Flutter 会话模型。"""
        messages = (
            self.chat_repository.list_messages(
                db,
                conversation_id=session.conversation_id,
                user_id=session.user_id,
            )
            if include_messages
            else []
        )
        return ChatConversationResponse(
            conversationId=session.conversation_id,
            name=session.title,
            snippet=session.snippet,
            viewName=session.view_name,
            unread=session.unread,
            createdAt=session.created_at,
            updatedAt=session.updated_at,
            messages=[self._map_message_to_response(message) for message in messages],
        )

    # 执行map message to response相关逻辑。
    def _map_message_to_response(self, message) -> ChatMessageResponse:
        """把 MySQL 消息实体映射成 Flutter 消息模型。"""
        return ChatMessageResponse(
            messageId=message.message_id,
            role=message.role,
            content=message.content,
            rawContent=message.raw_content or message.content,
            quotedContent=message.quoted_content,
            replyType=message.reply_type,
            createdAt=message.created_at,
        )

    # 执行persist visible user message相关逻辑。
    def _persist_visible_user_message(self, db: Session, request: ChatRequest) -> None:
        """把本轮用户可见消息先写入会话和消息表，再交给后台生成。"""
        # 执行get or create session相关逻辑。
        session = self.chat_repository.get_or_create_session(
            db,
            request.conversation_id,
            request.user_id,
            request.conversation_title,
            request.view_name or "",
        )
        session.snippet = request.content
        session.unread = 0
        # 执行add相关逻辑。
        db.add(session)
        # 执行save user message相关逻辑。
        self.chat_repository.save_user_message(
            db,
            message_id=request.message_id,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            content=request.content,
            reply_type=ChatConstants.DEFAULT_REPLY_TYPE,
            quoted_content=request.quoted_content,
            raw_content=self._build_message_xml_payload(
                speaker="用户",
                content=request.content,
            ),
        )
        # 执行commit相关逻辑。
        db.commit()

    # 执行build user topic相关逻辑。
    def _build_user_topic(self, user_id: str) -> str:
        """按当前 MQTT 配置生成用户专属 topic。"""
        return (
            f"{settings.mqtt.topic_prefix}/{user_id}/{self._sign_user_topic(user_id)}"
        )

    # 执行build client id相关逻辑。
    def _build_client_id(self, user_id: str) -> str:
        """按配置生成 Flutter 连接 MQTT 使用的 clientId。"""
        return f"{settings.mqtt.client_id_prefix}-{user_id}"

    # 执行sign user topic相关逻辑。
    def _sign_user_topic(self, user_id: str) -> str:
        """只暴露 userId 的签名摘要，不把 topic secret 直接下发给 Flutter。"""
        raw_text = f"{user_id}|{settings.mqtt.topic_secret}".encode("utf-8")
        return hashlib.sha256(raw_text).hexdigest()[:16]

    # 执行build message xml payload相关逻辑。
    def _build_message_xml_payload(self, *, speaker: str, content: str) -> str:
        """统一把消息正文编码成 XML 载荷，保证多角色上下文格式一致。"""
        return (
            f'<message speaker="{escape(speaker.strip(), quote=True)}">'
            f"{escape(content, quote=True)}</message>"
        )


# 定义_TurnMarker。
@dataclass(frozen=True)
class _TurnMarker:
    """记录会话当前最新处理版本，供后台软中断逻辑复用。"""

    turn_group_id: str
    revision: int


# 定义_RoleExecutionResult。
@dataclass(frozen=True)
class _RoleExecutionResult:
    """承接单个角色并发执行后的最终结果，避免主流程重复维护角色与正文映射。"""

    role_profile: RoleProfileData
    final_content: str
    reply_options: list[str]
