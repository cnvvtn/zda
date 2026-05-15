# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\dynamic_view\service.py。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
from pathlib import Path
from random import choice
import struct
import threading
import traceback
from uuid import uuid4

import dashscope
from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer

from langchain_core.messages import BaseMessage
from sqlalchemy.orm import Session

from app.clients.api_key_provider import ApiKeyProvider
from app.clients.langchain_chat_client import LangChainChatClient
from app.core.settings import ModelProfile, settings
from app.core.url_catalog import PythonUrl
from app.db.models import DynamicViewGameArchive, DynamicViewGenerationErrorLog, DynamicViewTaskArchive
from app.features.chat.schemas import (
    ConversationContextMessage,
    RoleProfileData,
)
from app.features.dynamic_view.gemini_client import (
    DynamicViewNode2Client,
    GeminiPromptClient,
    HistoryItem,
    build_dynamic_view_node2_client,
)
from app.features.dynamic_view.html_builder import (
    assemble_dynamic_view_html,
    inject_dynamic_view_audio_config,
)
from app.features.dynamic_view.model_output_support import (
    calculate_dynamic_durations,
    clean_model_output_text,
    extract_code_block_text,
    extract_final_question,
    extract_scene_texts_from_node1_output,
    extract_scene_texts_from_timeline_code,
    extract_timeline_assets_from_node1_output,
    inject_dynamic_durations_into_timeline_code,
    normalize_clue_key,
    normalize_create_request,
    resolve_dynamic_durations_from_scene_texts,
    runner_allows_streaming,
    sanitize_dynamic_view_subtitle_text,
    sanitize_timeline_text_literals,
    try_parse_structured_document,
)
from app.features.dynamic_view.html_storage import (
    resolve_dynamic_view_html_path_from_relative_path,
    write_dynamic_view_html_file,
)
from app.features.dynamic_view.payload_builder import build_payload
from app.features.dynamic_view.prompt_builder import (
    DynamicViewScriptMode,
    build_scene_character_prompt,
    build_clue_match_prompt,
    build_game_metadata_prompt,
    build_knowledge_detail_prompt,
    build_metadata_prompt,
    build_node1_prompt,
    build_node2_step1_prompt,
    build_node2_step2_prompt,
    build_dynamic_view_topic_analysis_prompt,
)
from app.features.dynamic_view.schemas import (
    DynamicViewAudioConfig,
    DynamicViewCharacter,
    DynamicViewCharacterSceneBundle,
    DynamicViewClueItem,
    DynamicViewClueMatchResult,
    DynamicViewCommentCreateRequest,
    DynamicViewCommentItem,
    DynamicViewCommentPage,
    DynamicViewCreateRequest,
    DynamicViewDetailBootstrap,
    DynamicViewGameMetadataBundle,
    DynamicViewKnowledgeDetail,
    DynamicViewListItem,
    DynamicViewMetadata,
    DynamicViewMetadataClue,
    DynamicViewPayload,
    DynamicViewTopicAnalysisResult,
    DynamicViewTaskSnapshot,
    normalize_dynamic_view_author_id,
    normalize_dynamic_view_flow_version,
    normalize_dynamic_view_subtitle_languages,
)
from app.features.dynamic_view.subject_type_support import (
    infer_subject_taxonomy,
    infer_subject_type,
    resolve_dynamic_view_subject_parent_type,
    validate_dynamic_view_subject_type,
)
from app.features.dynamic_view.task_snapshot_support import (
    DynamicViewStageDescriptor,
    DynamicViewStreamChunk,
    build_stage_chunk,
    build_stream_chunk,
    build_task_snapshot,
    build_terminal_task_snapshot,
)
from app.features.dynamic_view.theme_support import (
    extract_theme_colors_from_node1_output,
)
from app.repositories.dynamic_view_character_repository import DynamicViewCharacterRepository
from app.repositories.dynamic_view_clue_repository import DynamicViewClueRepository
from app.repositories.dynamic_view_comment_repository import DynamicViewCommentRepository
from app.repositories.dynamic_view_game_repository import DynamicViewGameRepository
from app.repositories.dynamic_view_knowledge_repository import (
    DynamicViewKnowledgeRepository,
)
from app.repositories.dynamic_view_model_profile_repository import DynamicViewModelProfileRepository
from app.repositories.dynamic_view_progress_repository import DynamicViewProgressRepository
from app.repositories.dynamic_view_task_repository import DynamicViewTaskRepository
from app.repositories.dynamic_view_source_task_repository import DynamicViewSourceTaskRepository
from app.services.credit_service import CreditService
from app.services.prompt_runner import PromptRunner


logger = logging.getLogger(__name__)
_DYNAMIC_VIEW_MUSIC_ROOT = Path(__file__).resolve().parents[3] / "music"
_DYNAMIC_VIEW_AUDIO_DIR = _DYNAMIC_VIEW_MUSIC_ROOT / "view"
_DYNAMIC_VIEW_SUBTITLE_AUDIO_DIR = _DYNAMIC_VIEW_MUSIC_ROOT / "subtitles"
_DASHSCOPE_TTS_MODEL = "cosyvoice-v3-flash"
_DASHSCOPE_TTS_VOICE_BY_SUBTITLE_LANGUAGE = {
    "zh": "longqiang_v3",
    "en": "longqiang_v3",
    "ja": "loongyuuma_v3",
    "ko": "loongkyong_v3",
}
_DASHSCOPE_TTS_SAMPLE_RATE = 16000
_DASHSCOPE_TTS_SPEECH_RATE = 1
_DASHSCOPE_TTS_CONNECT_TIMEOUT_SECONDS = 30
_DASHSCOPE_TTS_PRICE_PER_10000_CHARS = 1.0
_DASHSCOPE_TTS_PCM_BYTES_PER_MS = 32
_DOCUMENT_API_RETRY_COUNT = 3
_MODEL_RETRY_LEVELS = ("experience", "basic", "advanced")


class ModelOutputDocumentApiError(RuntimeError):
    """模型输出包含禁止的 document API。"""


@dataclass(frozen=True)
class KnowledgeSubtitleAudioResult:
    """知识视图字幕音频生成结果。"""

    audio_name: str
    scene_subtitles: list[dict[str, str]]
    total_duration_ms: int


# 执行contains timeline data assignment相关逻辑。
def _contains_timeline_data_assignment(timeline_code: str) -> bool:
    """判断时间轴脚本里是否包含 TimelineData 赋值语句。"""
    normalized_timeline_code = timeline_code.strip()
    if not normalized_timeline_code:
        return False
    lowered_timeline_code = normalized_timeline_code.lower()
    return "timelinedata" in lowered_timeline_code and "=" in lowered_timeline_code


# 执行raise if model output contains document api相关逻辑。
def _raise_if_model_output_contains_document_api(raw_text: str, stage_name: str) -> None:
    """模型输出中禁止出现 document API，命中后交给现有失败重试链路处理。"""
    document_api_matches = re.findall(
        r"\bdocument\s*\.\s*([A-Za-z_$][\w$]*)",
        raw_text,
    )
    if document_api_matches:
        matched_api_text = "、".join(
            f"document.{api_name}" for api_name in dict.fromkeys(document_api_matches)
        )
        raise ModelOutputDocumentApiError(f"{stage_name} 模型输出包含 {matched_api_text}，触发重试。")
    if "document." in raw_text:
        raise ModelOutputDocumentApiError(f"{stage_name} 模型输出包含 document.，触发重试。")


# 执行resolve model retry levels相关逻辑。
def _resolve_model_retry_levels(model_level: str) -> list[str]:
    """按当前等级生成 node1/node2 的模型自动重试链路，不把 top 放入后备。"""
    normalized_model_level = str(model_level or "").strip().lower()
    if normalized_model_level not in _MODEL_RETRY_LEVELS:
        return [normalized_model_level]
    start_index = _MODEL_RETRY_LEVELS.index(normalized_model_level)
    return list(_MODEL_RETRY_LEVELS[start_index:])


# 执行log model level failover warning相关逻辑。
def _log_model_level_failover_warning(
    *,
    node_key: str,
    model_level: str,
    error: Exception,
) -> None:
    """记录 node1/node2 模型等级切换日志，方便确认失败发生在哪一档。"""
    logger.warning(
        "Dynamic view model level failover | node=%s | modelLevel=%s | error=%s",
        node_key,
        model_level,
        str(error).strip() or error.__class__.__name__,
    )


# 定义_DynamicViewSubtitleTTSCallback。
class _DynamicViewSubtitleTTSCallback(ResultCallback):
    """收集单段 PCM 字幕音频，并用事件通知同步合成结束。"""

    # 执行init相关逻辑。
    def __init__(self) -> None:
        self.pcm_data = bytearray()
        self.finished_event = threading.Event()
        self.error: str | None = None

    # 执行on open相关逻辑。
    def on_open(self) -> None:
        """处理 DashScope 连接打开事件。"""

    # 执行on data相关逻辑。
    def on_data(self, data: bytes) -> None:
        """收集 DashScope 返回的 PCM 数据。"""
        self.pcm_data.extend(data)

    # 执行on event相关逻辑。
    def on_event(self, message) -> None:
        """处理 DashScope 普通事件。"""

    # 执行on complete相关逻辑。
    def on_complete(self) -> None:
        """标记当前分段合成完成。"""
        self.finished_event.set()

    # 执行on error相关逻辑。
    def on_error(self, message: str) -> None:
        """记录当前分段合成错误。"""
        self.error = message
        self.finished_event.set()

    # 执行on close相关逻辑。
    def on_close(self) -> None:
        """标记 DashScope 连接关闭。"""
        self.finished_event.set()


# 定义_DynamicViewGameClueContext。
@dataclass(frozen=True)
class _DynamicViewGameClueContext:
    """统一承接游戏动态视图存档、线索列表与当前会话点亮状态。"""

    archive: DynamicViewGameArchive
    clues: list[DynamicViewClueItem]
    unlocked_clue_keys: set[str]
    unlocked_clue_steps: dict[str, int]

    @property
    def merged_clues(self) -> list[DynamicViewClueItem]:
        """把当前会话的线索点亮状态直接合并到线索列表里。"""
        return [
            clue.model_copy(
                update={
                    "unlocked": clue.clue_key in self.unlocked_clue_keys,
                    "unlock_step": self.unlocked_clue_steps.get(clue.clue_key, 0),
                }
            )
            for clue in self.clues
        ]

    @property
    def unlocked_clues(self) -> list[DynamicViewClueItem]:
        """返回当前会话已经点亮的线索清单。"""
        return [clue for clue in self.clues if clue.clue_key in self.unlocked_clue_keys]

    @property
    def unresolved_clues(self) -> list[DynamicViewClueItem]:
        """返回当前会话尚未点亮的线索清单。"""
        return [clue for clue in self.clues if clue.clue_key not in self.unlocked_clue_keys]

    @property
    def current_unlocked_clue_count(self) -> int:
        """返回当前会话已点亮线索数量。"""
        return len(self.unlocked_clues)

    @property
    def total_clue_count(self) -> int:
        """返回当前视图总线索数量。"""
        return len(self.clues)

    @property
    def knowledge_ready(self) -> bool:
        """返回当前游戏动态视图是否已经产出可播放的知识视图。"""
        return bool(
            self.archive.knowledge_archive_id
            and self.archive.knowledge_generation_status == "ready"
        )


# 定义DynamicViewService。
class DynamicViewService:
    """动态视图服务，负责游戏视图生成、知识视图生成和线索问答联动。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        *,
        dynamic_view_game_repository: DynamicViewGameRepository,
        dynamic_view_knowledge_repository: DynamicViewKnowledgeRepository,
        dynamic_view_model_profile_repository: DynamicViewModelProfileRepository,
        dynamic_view_character_repository: DynamicViewCharacterRepository,
        dynamic_view_clue_repository: DynamicViewClueRepository,
        dynamic_view_comment_repository: DynamicViewCommentRepository,
        dynamic_view_progress_repository: DynamicViewProgressRepository,
        dynamic_view_task_repository: DynamicViewTaskRepository,
        dynamic_view_source_task_repository: DynamicViewSourceTaskRepository,
        api_key_provider: ApiKeyProvider,
        session_factory: Callable[[], Session],
    ) -> None:
        """执行init相关逻辑。"""
        self.dynamic_view_game_repository = dynamic_view_game_repository
        self.dynamic_view_knowledge_repository = dynamic_view_knowledge_repository
        self.dynamic_view_model_profile_repository = dynamic_view_model_profile_repository
        self.dynamic_view_character_repository = dynamic_view_character_repository
        self.dynamic_view_clue_repository = dynamic_view_clue_repository
        self.dynamic_view_comment_repository = dynamic_view_comment_repository
        self.dynamic_view_progress_repository = dynamic_view_progress_repository
        self.dynamic_view_task_repository = dynamic_view_task_repository
        self.dynamic_view_source_task_repository = dynamic_view_source_task_repository
        self.session_factory = session_factory
        self.api_key_provider = api_key_provider
        self.credit_service = CreditService()
        self._generation_task_snapshots: dict[str, DynamicViewTaskSnapshot] = {}
        self._generation_tasks: dict[str, asyncio.Task[None]] = {}
        self._generation_task_source_record_ids: dict[str, int] = {}
        self._knowledge_generation_tasks: dict[int, asyncio.Task[None]] = {}
        self._knowledge_generation_duration_stats: dict[str, tuple[int, int]] = {}

    # 执行resolve runtime model profile相关逻辑。
    def _resolve_runtime_model_profile(
        self,
        db: Session,
        model_level: str,
        node_key: str,
    ) -> ModelProfile:
        """按请求的模型等级和调用节点实时读取当前动态视图模型配置。"""
        profile = self.dynamic_view_model_profile_repository.resolve_active_profile(
            db,
            model_level,
            node_key,
        )
        if profile is None:
            raise RuntimeError(f"当前模型节点暂不可用：{model_level}/{node_key}")
        return profile

    # 执行resolve latest runtime model profile相关逻辑。
    def _resolve_latest_runtime_model_profile(self, model_level: str, node_key: str) -> ModelProfile:
        """每次实际调用 LLM 前按节点从 dynamic_view_model_profile 读取最新模型配置。"""
        with self.session_factory() as db:
            return self._resolve_runtime_model_profile(db, model_level, node_key)

    # 执行resolve generation model profile相关逻辑。
    def resolve_generation_model_profile(self, db: Session, model_level: str) -> ModelProfile:
        """给路由层记录生成请求时读取 node1 模型配置快照。"""
        return self._resolve_runtime_model_profile(db, model_level, "node1")

    # 执行resolve generation model credit cost相关逻辑。
    def resolve_generation_model_credit_cost(self, db: Session, model_level: str) -> int:
        """从模型配置表读取动态视图生成消耗的 Credit。"""
        credit_cost = self.dynamic_view_model_profile_repository.resolve_model_credit_cost(
            db,
            model_level,
            "node1",
        )
        if credit_cost <= 0:
            raise RuntimeError(f"当前模型 Credit 未配置：{model_level}")
        return credit_cost

    # 执行list generation model options相关逻辑。
    def list_generation_model_options(self, db: Session) -> list[dict[str, object]]:
        """返回官网自定义选项可用的模型等级和 Credit。"""
        return self.dynamic_view_model_profile_repository.list_model_options(db, "node1")

    # 执行build runtime node1 runner相关逻辑。
    def _build_runtime_node1_runner(self, profile: ModelProfile) -> PromptRunner:
        """按数据库模型配置构建本次生成专用 node1 runner。"""
        if str(profile.router_type or "").strip().lower() == "gemini":
            return PromptRunner(GeminiPromptClient(profile, self.api_key_provider))
        return PromptRunner(LangChainChatClient(profile, self.api_key_provider))

    # 执行build latest runtime runner相关逻辑。
    def _build_latest_runtime_runner(self, model_level: str, node_key: str) -> PromptRunner:
        """按最新数据库节点配置构建一次性 LLM runner。"""
        profile = self._resolve_latest_runtime_model_profile(model_level, node_key)
        return self._build_runtime_node1_runner(profile)

    # 执行build runtime node2 client相关逻辑。
    def _build_runtime_node2_client(self, profile: ModelProfile) -> DynamicViewNode2Client:
        """按数据库模型配置构建本次生成专用 node2 客户端。"""
        return build_dynamic_view_node2_client(profile, self.api_key_provider)

    # 执行build latest runtime node2 client相关逻辑。
    def _build_latest_runtime_node2_client(self, model_level: str) -> DynamicViewNode2Client:
        """按最新数据库 node2 配置构建一次性 node2 客户端。"""
        return self._build_runtime_node2_client(
            self._resolve_latest_runtime_model_profile(model_level, "node2")
        )

    # 执行close runtime model client相关逻辑。
    async def _close_runtime_model_client(self, client: object | None) -> None:
        """关闭本次生成临时创建的模型客户端。"""
        if client is None:
            return
        if hasattr(client, "client"):
            # 执行getattr相关逻辑。
            client = getattr(client, "client")
        if hasattr(client, "aclose"):
            # 执行aclose相关逻辑。
            await client.aclose()

    # 执行close runtime model clients相关逻辑。
    async def _close_runtime_model_clients(
        self,
        *clients: object | None,
    ) -> None:
        """批量关闭本次生成临时创建的模型客户端。"""
        for client in clients:
            # 执行close runtime model client相关逻辑。
            await self._close_runtime_model_client(client)

    # 执行build archive html url相关逻辑。
    def _build_archive_html_url(
        self,
        *,
        view_type: str,
        archive_id: int,
        html_relative_path: str,
    ) -> str:
        """根据视图类型、存档 ID 与相对路径构造统一 HTML 访问地址。"""
        normalized_relative_path = html_relative_path.strip()
        if not normalized_relative_path:
            return ""
        if view_type == "knowledge":
            return PythonUrl.DYNAMIC_VIEW_KNOWLEDGE_HTML_TEMPLATE.format_path(archive_id=archive_id)
        return PythonUrl.DYNAMIC_VIEW_HTML_TEMPLATE.format_path(archive_id=archive_id)

    # 执行build subtitle audio config相关逻辑。
    def _build_subtitle_audio_config(
        self,
        *,
        archive_id: int | None,
        subtitle_audio_name: str,
        subtitle_audio_volume: int,
    ) -> DynamicViewAudioConfig | None:
        """把数据库里的字幕音频字段转换成前端可直接播放的配置对象。"""
        normalized_subtitle_audio_name = subtitle_audio_name.strip()
        if archive_id is None or archive_id <= 0 or not normalized_subtitle_audio_name:
            return None
        return DynamicViewAudioConfig(
            name=Path(normalized_subtitle_audio_name).name,
            startTime=0,
            endTime=0,
            volume=max(0, min(100, int(subtitle_audio_volume))),
            path=PythonUrl.DYNAMIC_VIEW_KNOWLEDGE_SUBTITLE_AUDIO_TEMPLATE.format_path(archive_id=archive_id),
            kind="subtitle",
        )

    # 执行update flow archive timeline data相关逻辑。
    def _update_flow_archive_timeline_data(
        self,
        db: Session,
        *,
        is_knowledge_flow: bool,
        archive_id: int,
        scene_subtitles: list[dict[str, str]],
        total_duration_ms: int,
        final_question: str,
    ) -> None:
        """按当前流程类型更新分镜字幕和总时长。"""
        if is_knowledge_flow:
            self.dynamic_view_knowledge_repository.update_archive_timeline_data(
                db,
                archive_id=archive_id,
                scene_subtitles=scene_subtitles,
                total_duration_ms=total_duration_ms,
            )
            return
        self.dynamic_view_game_repository.update_archive_timeline_data(
            db,
            archive_id=archive_id,
            scene_subtitles=scene_subtitles,
            total_duration_ms=total_duration_ms,
            final_question=final_question,
            clue_count=0,
        )

    # 执行resolve archive html file path相关逻辑。
    def resolve_archive_html_file_path(
        self,
        db: Session,
        *,
        view_type: str,
        archive_id: int,
    ) -> Path:
        """按视图类型与存档 ID 解析最终 HTML 文件路径。"""
        if view_type == "game":
            archive = self.dynamic_view_game_repository.get_ready_archive_detail(
                db,
                archive_id=archive_id,
                increase_view_count=False,
            )
        elif view_type == "knowledge":
            archive = self.dynamic_view_knowledge_repository.get_ready_archive_detail(
                db,
                archive_id=archive_id,
                increase_view_count=False,
            )
        else:
            raise ValueError(f"未知的动态视图 HTML 类型：view_type={view_type}")
        html_relative_path = archive.html_content.strip()
        if not html_relative_path:
            raise ValueError(f"动态视图 HTML 路径为空：view_type={view_type}, archive_id={archive_id}")
        resolved_path = resolve_dynamic_view_html_path_from_relative_path(html_relative_path)
        if not resolved_path.is_file():
            raise ValueError(f"动态视图 HTML 文件不存在：view_type={view_type}, archive_id={archive_id}")
        return resolved_path

    # 执行list home archives相关逻辑。
    def list_home_archives(
        self,
        db: Session,
        *,
        cursor_id: int | None = None,
        limit: int = 20,
    ) -> list[DynamicViewListItem]:
        """返回首页混合列表，统一合并 game 与 knowledge 两类 ready 记录。"""
        normalized_limit = max(1, min(50, int(limit)))
        game_items = self.dynamic_view_game_repository.list_ready_archives(
            db,
            cursor_id=cursor_id,
            limit=normalized_limit,
        )
        knowledge_items = self.dynamic_view_knowledge_repository.list_ready_archives(
            db,
            cursor_id=cursor_id,
            limit=normalized_limit,
        )
        merged_items = sorted(
            [*game_items, *knowledge_items],
            key=lambda item: int(item.id),
            reverse=True,
        )
        return merged_items[:normalized_limit]

    # 执行move archive to recycle bin相关逻辑。
    def move_archive_to_recycle_bin(
        self,
        db: Session,
        *,
        view_type: str,
        archive_id: int,
    ) -> dict[str, object]:
        """把后台指定动态视图移入回收站，24 小时后再物理删除。"""
        if view_type == "knowledge":
            archive = self.dynamic_view_knowledge_repository.move_archive_to_recycle_bin(
                db,
                archive_id=archive_id,
            )
            if archive.game_archive_id is not None:
                try:
                    game_archive = self.dynamic_view_game_repository.get_archive_or_raise(
                        db,
                        int(archive.game_archive_id),
                    )
                    game_archive.knowledge_archive_id = None
                    game_archive.knowledge_generation_status = "deleted"
                    db.commit()
                except ValueError:
                    pass
            return {
                "recycled": True,
                "viewType": "knowledge",
                "archiveId": archive_id,
                "physicalDeleteAfter": archive.physical_delete_after,
            }
        archive = self.dynamic_view_game_repository.move_archive_to_recycle_bin(
            db,
            archive_id=archive_id,
        )
        if archive.knowledge_archive_id is not None:
            self.dynamic_view_knowledge_repository.move_archive_to_recycle_bin(
                db,
                archive_id=int(archive.knowledge_archive_id),
            )
        return {
            "recycled": True,
            "viewType": "game",
            "archiveId": archive_id,
            "physicalDeleteAfter": archive.physical_delete_after,
        }

    # 执行restore archive from recycle bin相关逻辑。
    def restore_archive_from_recycle_bin(
        self,
        db: Session,
        *,
        view_type: str,
        archive_id: int,
    ) -> dict[str, object]:
        """把后台指定动态视图从回收站恢复。"""
        if view_type == "knowledge":
            archive = self.dynamic_view_knowledge_repository.restore_archive_from_recycle_bin(
                db,
                archive_id=archive_id,
            )
            if archive.game_archive_id is not None:
                try:
                    game_archive = self.dynamic_view_game_repository.get_archive_or_raise(
                        db,
                        int(archive.game_archive_id),
                    )
                    game_archive.knowledge_archive_id = int(archive.id)
                    game_archive.knowledge_generation_status = "ready"
                    db.commit()
                except ValueError:
                    pass
            return {"restored": True, "viewType": "knowledge", "archiveId": archive_id}
        archive = self.dynamic_view_game_repository.restore_archive_from_recycle_bin(
            db,
            archive_id=archive_id,
        )
        if archive.knowledge_archive_id is not None:
            self.dynamic_view_knowledge_repository.restore_archive_from_recycle_bin(
                db,
                archive_id=int(archive.knowledge_archive_id),
            )
            archive.knowledge_generation_status = "ready"
            db.commit()
        return {"restored": True, "viewType": "game", "archiveId": archive_id}

    # 执行hard delete expired recycled archives相关逻辑。
    def hard_delete_expired_recycled_archives(self, db: Session, *, now: datetime) -> int:
        """物理删除已超过回收站保留时间的动态视图记录和文件。"""
        expired_archives = (
            db.query(DynamicViewGameArchive)
            .filter(
                DynamicViewGameArchive.type.in_(("game", "knowledge")),
                DynamicViewGameArchive.is_deleted == 1,
                DynamicViewGameArchive.physical_delete_after <= now,
            )
            .all()
        )
        deleted_count = 0
        for archive in expired_archives:
            if archive.type == "knowledge":
                self.dynamic_view_comment_repository.delete_archive_comments(
                    db,
                    archive_id=int(archive.id),
                    view_type="knowledge",
                )
                self.dynamic_view_character_repository.delete_archive_characters(
                    db,
                    owner_type="knowledge",
                    owner_id=int(archive.id),
                )
                self.dynamic_view_knowledge_repository.hard_delete_recycled_archive(
                    db,
                    archive_id=int(archive.id),
                )
            else:
                self.dynamic_view_comment_repository.delete_archive_comments(
                    db,
                    archive_id=int(archive.id),
                    view_type="game",
                )
                self.dynamic_view_character_repository.delete_archive_characters(
                    db,
                    owner_type="game",
                    owner_id=int(archive.id),
                )
                self.dynamic_view_progress_repository.delete_game_progress(
                    db,
                    game_archive_id=int(archive.id),
                )
                self.dynamic_view_clue_repository.delete_game_clues(
                    db,
                    game_archive_id=int(archive.id),
                )
                self.dynamic_view_game_repository.hard_delete_recycled_archive(
                    db,
                    archive_id=int(archive.id),
                )
            deleted_count += 1
        return deleted_count

    # 执行build detail bootstrap相关逻辑。
    def build_game_detail_bootstrap(
        self,
        db: Session,
        *,
        game_view_id: int,
        comment_limit: int = 10,
        user_id: str | None = None,
        increase_view_count: bool = True,
    ) -> DynamicViewDetailBootstrap:
        """聚合游戏动态视图详情页首屏所需的播放信息、线索、角色和首屏评论。"""
        payload = self.build_game_payload(
            db,
            game_view_id=game_view_id,
            increase_view_count=increase_view_count,
            user_id=user_id,
        )
        comment_page = self.dynamic_view_comment_repository.list_archive_comments(
            db,
            archive_id=game_view_id,
            view_type="game",
            limit=comment_limit,
        )
        comment_count, comment_count_changed = self._sync_archive_comment_count(
            db,
            archive_id=game_view_id,
            view_type="game",
            stored_comment_count=payload.comment_count,
        )
        if comment_count_changed:
            payload = payload.model_copy(update={"comment_count": comment_count})
        return DynamicViewDetailBootstrap(
            payload=payload,
            roles=self.dynamic_view_character_repository.list_archive_roles(
                db,
                owner_type="game",
                owner_id=game_view_id,
            ),
            comments=comment_page.items,
            nextCommentCursor=comment_page.next_cursor,
            hasMoreComments=comment_page.has_more,
        )


    # 执行build game payload相关逻辑。
    def build_game_payload(
        self,
        db: Session,
        *,
        game_view_id: int,
        increase_view_count: bool,
        user_id: str | None,
    ) -> DynamicViewPayload:
        """读取游戏动态视图详情并拼装线索状态。"""
        # 执行load game clue context相关逻辑。
        game_context = self._load_game_clue_context(
            db,
            archive_id=game_view_id,
            user_id=user_id,
            require_ready_archive=True,
            increase_view_count=increase_view_count,
        )
        archive = game_context.archive
        return build_payload(
            topic=archive.topic,
            title=archive.subtitle,
            view_type="game",
            template_type=archive.template_type,
            status=archive.status,
            preview_text=archive.summary,
            game_view_id=int(archive.id),
            knowledge_view_id=archive.knowledge_archive_id,
            knowledge_generation_status=archive.knowledge_generation_status,
            knowledge_ready=game_context.knowledge_ready,
            summary=archive.summary,
            subject_parent_type=resolve_dynamic_view_subject_parent_type(archive.subject_type),
            subject_type=archive.subject_type,
            detail=archive.detail,
            final_question=archive.final_question,
            clues=game_context.merged_clues,
            current_unlocked_clue_count=game_context.current_unlocked_clue_count,
            total_clue_count=game_context.total_clue_count,
            all_clues_unlocked=game_context.total_clue_count > 0
            and game_context.current_unlocked_clue_count >= game_context.total_clue_count,
            view_count=archive.view_count,
            comment_count=archive.comment_count,
            scene_subtitles=self._load_owner_scene_subtitles(
                owner_type="game",
                owner_id=int(archive.id),
            ),
            total_duration_ms=archive.total_duration_ms,
            html_url=self._build_archive_html_url(
                view_type="game",
                archive_id=int(archive.id),
                html_relative_path=archive.html_content,
            ),
            audio=self._build_archive_audio_config(
                view_type="game",
                archive_id=int(archive.id),
                audio_name=archive.audio_name,
                audio_start_time=archive.audio_start_time,
                audio_end_time=archive.audio_end_time,
                audio_volume=archive.audio_volume,
            ),
        )


    # 执行build knowledge payload相关逻辑。
    def build_knowledge_payload(
        self,
        db: Session,
        *,
        knowledge_view_id: int,
        increase_view_count: bool = False,
    ) -> DynamicViewPayload:
        """读取知识动态视图详情并构造返回载荷。"""
        # 执行get ready archive detail相关逻辑。
        archive = self.dynamic_view_knowledge_repository.get_ready_archive_detail(
            db,
            archive_id=knowledge_view_id,
            increase_view_count=increase_view_count,
        )
        payload = build_payload(
            topic=archive.topic,
            title=archive.subtitle,
            view_type="knowledge",
            template_type=archive.template_type,
            status=archive.status,
            preview_text=archive.summary,
            knowledge_view_id=int(archive.id),
            summary=archive.summary,
            subject_parent_type=resolve_dynamic_view_subject_parent_type(archive.subject_type),
            subject_type=archive.subject_type,
            detail=archive.detail,
            view_count=archive.view_count,
            comment_count=archive.comment_count,
            scene_subtitles=self._load_owner_scene_subtitles(
                owner_type="knowledge",
                owner_id=int(archive.id),
            ),
            total_duration_ms=archive.total_duration_ms,
            html_url=self._build_archive_html_url(
                view_type="knowledge",
                archive_id=int(archive.id),
                html_relative_path=archive.html_content,
            ),
            audio=self._build_archive_audio_config(
                view_type="knowledge",
                archive_id=int(archive.id),
                audio_name=archive.audio_name,
                audio_start_time=archive.audio_start_time,
                audio_end_time=archive.audio_end_time,
                audio_volume=archive.audio_volume,
            ),
            subtitle_audio=self._build_subtitle_audio_config(
                archive_id=int(archive.id),
                subtitle_audio_name=archive.subtitle_audio_name,
                subtitle_audio_volume=archive.subtitle_audio_volume,
            ),
        )

        comment_count, comment_count_changed = self._sync_archive_comment_count(
            db,
            archive_id=knowledge_view_id,
            view_type="knowledge",
            stored_comment_count=payload.comment_count,
        )
        if comment_count_changed:
            payload = payload.model_copy(update={"comment_count": comment_count})
        return payload

    # 执行resolve subtitle audio file path相关逻辑。
    def resolve_subtitle_audio_file_path(
        self,
        db: Session,
        *,
        knowledge_archive_id: int,
    ) -> Path:
        """按知识视图存档 ID 解析字幕音频文件路径。"""
        archive = self.dynamic_view_knowledge_repository.get_ready_archive_detail(
            db,
            archive_id=knowledge_archive_id,
            increase_view_count=False,
        )
        subtitle_audio_name = archive.subtitle_audio_name.strip()
        if not subtitle_audio_name:
            raise ValueError(f"知识视图未配置字幕音频：archive_id={knowledge_archive_id}")
        music_root = _DYNAMIC_VIEW_MUSIC_ROOT.resolve()
        subtitle_audio_path = (music_root / subtitle_audio_name).resolve()
        try:
            subtitle_audio_path.relative_to(music_root)
        except ValueError as error:
            raise ValueError(
                f"知识视图字幕音频路径非法：archive_id={knowledge_archive_id}"
            ) from error
        if not subtitle_audio_path.is_file():
            raise ValueError(
                f"知识视图字幕音频文件不存在：archive_id={knowledge_archive_id}"
            )
        return subtitle_audio_path

    # 执行resolve audio file path相关逻辑。
    def resolve_audio_file_path(
        self,
        db: Session,
        *,
        view_type: str,
        archive_id: int,
    ) -> Path:
        """按视图类型与存档 ID 解析固定背景音乐文件路径。"""
        if view_type == "game":
            archive = self.dynamic_view_game_repository.get_ready_archive_detail(
                db,
                archive_id=archive_id,
                increase_view_count=False,
            )
        elif view_type == "knowledge":
            archive = self.dynamic_view_knowledge_repository.get_ready_archive_detail(
                db,
                archive_id=archive_id,
            )
        else:
            raise ValueError(f"未知的动态视图音频类型：view_type={view_type}")
        audio_name = archive.audio_name.strip()
        if not audio_name:
            raise ValueError(f"动态视图未配置背景音乐：view_type={view_type}, archive_id={archive_id}")
        music_root = _DYNAMIC_VIEW_MUSIC_ROOT.resolve()
        resolved_path = (music_root / audio_name).resolve()
        try:
            resolved_path.relative_to(music_root)
        except ValueError as error:
            raise ValueError(
                f"动态视图音频路径非法：view_type={view_type}, archive_id={archive_id}"
            ) from error
        if not resolved_path.is_file():
            raise ValueError(
                f"动态视图音频文件不存在：view_type={view_type}, archive_id={archive_id}"
            )
        return resolved_path

    # 执行list archive comments相关逻辑。
    def list_archive_comments(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
        cursor_id: int | None = None,
        limit: int = 10,
    ) -> DynamicViewCommentPage:
        """返回指定动态视图评论分页，供详情页上拉加载继续追加。"""
        self._ensure_archive_commentable(
            db,
            archive_id=archive_id,
            view_type=view_type,
        )
        return self.dynamic_view_comment_repository.list_archive_comments(
            db,
            archive_id=archive_id,
            view_type=view_type,
            cursor_id=cursor_id,
            limit=limit,
        )

    # 执行create archive comment相关逻辑。
    def create_archive_comment(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
        request: DynamicViewCommentCreateRequest,
    ) -> DynamicViewCommentItem:
        """创建动态视图评论或回复，并同步刷新主存档评论计数。"""
        self._ensure_archive_commentable(
            db,
            archive_id=archive_id,
            view_type=view_type,
        )
        try:
            created_comment = self.dynamic_view_comment_repository.create_archive_comment(
                db,
                archive_id=archive_id,
                view_type=view_type,
                user_key=request.user_id.strip(),
                content=request.content,
                pid=request.pid,
            )
            # 执行sync archive comment count相关逻辑。
            self._sync_archive_comment_count(
                db,
                archive_id=archive_id,
                view_type=view_type,
                stored_comment_count=None,
            )
            # 评论写入、父评论回复数更新、主存档评论总数必须属于同一事务。
            db.commit()
            return created_comment
        except Exception:
            db.rollback()
            raise

    # 执行resolve role profile for chat相关逻辑。
    def resolve_role_profile_for_chat(
        self,
        db: Session,
        *,
        user_id: str,
        role_profile: RoleProfileData,
    ) -> RoleProfileData:
        """按当前用户在该视图里的线索进度补充角色问答上下文。"""
        if role_profile.dynamic_view_game_archive_id is None:
            return role_profile
        normalized_role_profile = role_profile
        normalized_role_key = role_profile.role_key.strip()
        dynamic_role_prefix = "dynamic-role-"
        dynamic_role_id: int | None = None
        if normalized_role_key.startswith(dynamic_role_prefix):
            role_id_suffix = normalized_role_key[len(dynamic_role_prefix):].strip()
            if role_id_suffix.isdigit():
                dynamic_role_id = int(role_id_suffix)
        if dynamic_role_id is not None:
            # 执行get archive role by id相关逻辑。
            role_archive = self.dynamic_view_character_repository.get_archive_role_by_id(
                db,
                role_id=dynamic_role_id,
            )
            if (
                role_archive is not None
                and role_archive.owner_type == "game"
                and int(role_archive.owner_id) == int(role_profile.dynamic_view_game_archive_id)
            ):
                normalized_role_profile = normalized_role_profile.model_copy(
                    update={
                        "name": role_archive.role_name.strip(),
                        "persona": role_archive.persona_prompt.strip(),
                        "role_description": role_archive.personality.strip(),
                        "category_name": role_archive.category_name.strip(),
                        "icon": role_archive.icon.strip(),
                        "personality": role_archive.personality.strip(),
                        "scene": role_archive.scenario.strip(),
                        "nsfw_setting": role_archive.nsfw_setting.strip(),
                        "author": role_archive.author.strip(),
                    }
                )
        # 执行load game clue context相关逻辑。
        game_context = self._load_game_clue_context(
            db,
            archive_id=int(normalized_role_profile.dynamic_view_game_archive_id),
            user_id=user_id,
        )
        # 执行build story overview from scene subtitles json相关逻辑。
        story_overview = self._build_story_overview_from_scene_subtitles_json(
            game_context.archive.scene_subtitles_json,
            archive_id=int(normalized_role_profile.dynamic_view_game_archive_id),
        )
        # 角色聊天揭晓态只由线索是否全部点亮决定，不能受知识视图预生成状态影响。
        is_revealed = (
            game_context.total_clue_count > 0
            and game_context.current_unlocked_clue_count >= game_context.total_clue_count
        )
        if is_revealed:
            dynamic_view_chinese_subtitles = (
                normalized_role_profile.dynamic_view_chinese_subtitles.strip()
                if normalized_role_profile.dynamic_view_chinese_subtitles
                else (story_overview or "")
            ) or None
        else:
            context_text = "\n".join(
                [
                    "【动态视图解谜约束】",
                    f"真实主题：{game_context.archive.source_topic.strip()}",
                    "1. 不要直接公布答案。",
                    "2. 优先围绕未点亮线索继续追问或回应。",
                    "3. 对已点亮线索可以自然承认，但不要一次性摊开全部答案。",
                    "4. 全部线索点亮后，可以自然提醒用户额外查看知识视图。",
                    "【已点亮线索】",
                    *(
                        [
                            f"- {clue.clue_title or clue.clue_key}：{clue.clue_content}"
                            for clue in game_context.unlocked_clues
                        ]
                        or ["- 暂无"]
                    ),
                    "【未点亮线索】",
                    *(
                        [
                            f"- {clue.clue_title or clue.clue_key}：{clue.clue_content}"
                            for clue in game_context.unresolved_clues
                        ]
                        or ["- 无"]
                    ),
                ]
            )
            dynamic_view_chinese_subtitles = story_overview
        normalized_supplement_parts = [
            normalized_role_profile.supplement.strip() if normalized_role_profile.supplement else "",
            context_text if not is_revealed else "",
        ]
        return normalized_role_profile.model_copy(
            update={
                "supplement": "\n\n".join(
                    part for part in normalized_supplement_parts if part.strip()
                ),
                "dynamic_view_chinese_subtitles": dynamic_view_chinese_subtitles,
                "dynamic_view_knowledge_ready": is_revealed,
            }
        )

    # 执行process clue unlock相关逻辑。
    async def process_clue_unlock(
        self,
        db: Session,
        *,
        user_id: str,
        message_id: str,
        user_input: str,
        user_mention: str,
        role_profile: RoleProfileData,
    ) -> list[str]:
        """按用户输入判定线索命中，仅更新当前用户线索点亮进度。"""
        if role_profile.dynamic_view_game_archive_id is None:
            return []
        if not user_input.strip():
            return []
        # 执行info相关逻辑。
        logger.info(
            "Dynamic view clue unlock started: archive_id=%s user_id=%s message_id=%s",
            role_profile.dynamic_view_game_archive_id,
            user_id,
            message_id,
        )
        # 执行load game clue context相关逻辑。
        game_context = self._load_game_clue_context(
            db,
            archive_id=int(role_profile.dynamic_view_game_archive_id),
            user_id=user_id,
        )
        if not game_context.clues:
            # 执行info相关逻辑。
            logger.info(
                "Dynamic view clue unlock skipped: archive_id=%s no_clues",
                game_context.archive.id,
            )
            return []
        unresolved_clues = game_context.unresolved_clues
        if not unresolved_clues:
            # 执行info相关逻辑。
            logger.info(
                "Dynamic view clue unlock skipped: archive_id=%s all_clues_unlocked",
                game_context.archive.id,
            )
            return []
        # 线索判定只使用当前用户提问，避免 AI 回复反向放大误判并一次性点亮过多线索。
        clue_match_chat_messages: list[ConversationContextMessage] = [
            {
                "role": "user",
                "content": user_input.strip(),
                "speaker": "用户",
                **({"mention": user_mention.strip()} if user_mention.strip() else {}),
            },
        ]
        clue_match_messages = build_clue_match_prompt(
            final_question=game_context.archive.final_question,
            chat_messages=clue_match_chat_messages,
            unresolved_clues=unresolved_clues,
            prompt_version=1,
        )
        character_runner = self._build_latest_runtime_runner(game_context.archive.source_model, "character")
        try:
            # 执行run structured messages相关逻辑。
            clue_match_result = await character_runner.run_structured_messages(
                clue_match_messages,
                schema=DynamicViewClueMatchResult,
                stage_name="dynamic_view_clue_match",
            )
        except Exception:
            # 执行exception相关逻辑。
            logger.exception(
                "Dynamic view clue match failed: archive_id=%s user_id=%s",
                game_context.archive.id,
                user_id,
            )
            return []
        finally:
            # 执行close runtime model clients相关逻辑。
            await self._close_runtime_model_clients(character_runner)
        unresolved_clue_key_set = {clue.clue_key for clue in unresolved_clues}
        matched_clue_keys = [
            clue_key
            for clue_key in clue_match_result.matched_clue_keys
            if clue_key in unresolved_clue_key_set
        ]
        # 单轮最多点亮 1 条线索，避免一次提问直接把全部线索解锁。
        matched_clue_keys = [
            clue.clue_key
            for clue in unresolved_clues
            if clue.clue_key in set(matched_clue_keys)
        ][:1]
        if not matched_clue_keys:
            # 执行info相关逻辑。
            logger.info(
                "Dynamic view clue unlock no_match: archive_id=%s user_id=%s message_id=%s",
                game_context.archive.id,
                user_id,
                message_id,
            )
            return []
        # 执行unlock clues相关逻辑。
        new_clue_keys = self.dynamic_view_progress_repository.unlock_clues(
            db,
            game_archive_id=int(game_context.archive.id),
            user_id=user_id,
            matched_message_id=message_id,
            clue_keys=matched_clue_keys,
        )
        # 执行info相关逻辑。
        logger.info(
            "Dynamic view clue unlock success: archive_id=%s user_id=%s message_id=%s matched=%s new=%s",
            game_context.archive.id,
            user_id,
            message_id,
            ",".join(matched_clue_keys),
            ",".join(new_clue_keys),
        )
        return new_clue_keys

    # 执行process clue unlock after message相关逻辑。
    async def process_clue_unlock_after_message(
        self,
        *,
        user_id: str,
        message_id: str,
        user_input: str,
        user_mention: str,
        role_profile: RoleProfileData,
    ) -> list[str]:
        """在聊天消息正常发出后再执行线索命中判定，并使用独立数据库会话隔离事务。"""
        if role_profile.dynamic_view_game_archive_id is None:
            # 执行info相关逻辑。
            logger.info(
                "Dynamic view clue unlock skipped: user_id=%s message_id=%s no_game_archive_id",
                user_id,
                message_id,
            )
            return []
        if not user_input.strip():
            # 执行info相关逻辑。
            logger.info(
                "Dynamic view clue unlock skipped: archive_id=%s user_id=%s message_id=%s empty_user_input",
                role_profile.dynamic_view_game_archive_id,
                user_id,
                message_id,
            )
            return []
        background_db = self.session_factory()
        try:
            return await self.process_clue_unlock(
                background_db,
                user_id=user_id,
                message_id=message_id,
                user_input=user_input,
                user_mention=user_mention,
                role_profile=role_profile,
            )
        except Exception:
            background_db.rollback()
            logger.exception(
                "Dynamic view clue unlock background task failed: archive_id=%s user_id=%s message_id=%s",
                role_profile.dynamic_view_game_archive_id,
                user_id,
                message_id,
            )
            return []
        finally:
            background_db.close()

    # 执行build story overview from scene subtitles json相关逻辑。
    def _build_story_overview_from_scene_subtitles_json(
        self,
        raw_scene_subtitles: str,
        *,
        archive_id: int,
    ) -> str | None:
        """从 scene_subtitles_json 里提取每幕 vivid 文本，并按顺序拼接成故事概览。"""
        # 执行strip相关逻辑。
        normalized_scene_subtitles = raw_scene_subtitles.strip()
        if not normalized_scene_subtitles:
            return None
        try:
            # 执行loads相关逻辑。
            loaded_scene_subtitles = json.loads(normalized_scene_subtitles)
        except json.JSONDecodeError:
            logger.exception(
                "Dynamic view story overview decode failed: archive_id=%s",
                archive_id,
            )
            return None
        if not isinstance(loaded_scene_subtitles, list):
            return None
        story_overview_lines: list[str] = []
        for scene_subtitle in loaded_scene_subtitles:
            if not isinstance(scene_subtitle, dict):
                continue
            # scene_subtitles_json 里的 vivid 就是每幕主文本。
            vivid_text = str(scene_subtitle.get("vivid", "")).strip()
            if vivid_text:
                # 执行append相关逻辑。
                story_overview_lines.append(vivid_text)
        if not story_overview_lines:
            return None
        return "\n".join(story_overview_lines)

    # 执行reveal principle相关逻辑。
    def reveal_principle(
        self,
        db: Session,
        *,
        game_view_id: int,
        user_id: str,
    ) -> DynamicViewPayload:
        """为当前用户直接揭开指定动态视图全部线索，并立刻触发知识视图生成。"""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("缺少有效的用户标识，暂时无法揭晓原理。")
        # 执行load game clue context相关逻辑。
        game_context = self._load_game_clue_context(
            db,
            archive_id=game_view_id,
            user_id=normalized_user_id,
            require_ready_archive=True,
            increase_view_count=False,
        )
        if not game_context.clues:
            raise ValueError("当前动态视图还没有可揭晓的线索。")
        unresolved_clue_keys = [
            clue.clue_key for clue in game_context.unresolved_clues if clue.clue_key.strip()
        ]
        if unresolved_clue_keys:
            # 执行unlock clues相关逻辑。
            self.dynamic_view_progress_repository.unlock_clues(
                db,
                game_archive_id=game_view_id,
                user_id=normalized_user_id,
                matched_message_id="reveal-principle",
                clue_keys=unresolved_clue_keys,
            )
        # 执行trigger knowledge generation相关逻辑。
        self._trigger_knowledge_generation(
            db,
            archive_id=game_view_id,
        )
        return self.build_game_payload(
            db,
            game_view_id=game_view_id,
            increase_view_count=False,
            user_id=normalized_user_id,
        )

    # 执行trigger knowledge generation相关逻辑。
    def _trigger_knowledge_generation(
        self,
        db: Session,
        *,
        archive_id: int,
    ) -> None:
        """统一启动知识视图后台生成任务，避免不同入口各自维护一套任务状态更新逻辑。"""
        # 执行get archive or raise相关逻辑。
        archive = self.dynamic_view_game_repository.get_archive_or_raise(db, archive_id)
        if archive.knowledge_generation_status in {"processing", "ready"}:
            return
        if archive_id in self._knowledge_generation_tasks:
            return
        # 执行update archive knowledge status相关逻辑。
        self.dynamic_view_game_repository.update_archive_knowledge_status(
            db,
            archive_id=archive_id,
            knowledge_generation_status="processing",
        )
        knowledge_task = asyncio.create_task(
            self._generate_knowledge_view_background(archive_id=archive_id)
        )
        self._knowledge_generation_tasks[archive_id] = knowledge_task
        # 执行add done callback相关逻辑。
        knowledge_task.add_done_callback(
            lambda finished_task: self._handle_knowledge_generation_done(
                archive_id=archive_id,
                task=finished_task,
            )
        )

    # 执行analyze generation topic相关逻辑。
    async def analyze_generation_topic(
        self,
        topic: str,
        *,
        model_level: str,
    ) -> DynamicViewTopicAnalysisResult:
        """使用 analyze 节点判断主题是否违规。"""
        normalized_topic = str(topic or "").strip()
        if not normalized_topic:
            raise ValueError("topic 不能为空")
        analysis_messages = build_dynamic_view_topic_analysis_prompt(normalized_topic)
        analyzer_runner = self._build_latest_runtime_runner(model_level, "analyze")
        try:
            analysis_result = await analyzer_runner.run_structured_messages(
                analysis_messages,
                schema=DynamicViewTopicAnalysisResult,
                temperature=0.2,
                stage_name="dynamic_view_topic_analysis",
            )
        finally:
            # 执行close runtime model clients相关逻辑。
            await self._close_runtime_model_clients(analyzer_runner)
        analysis_status = "passed"
        displayable_reason = analysis_result.displayable_reason
        decision_summary = analysis_result.decision_summary
        if analysis_result.is_violation:
            analysis_status = "violation"
            displayable_reason = displayable_reason or "主题包含违规内容，请修改后再生成。"
            decision_summary = decision_summary or displayable_reason
        else:
            decision_summary = decision_summary or "主题未违规。"
        return analysis_result.model_copy(
            update={
                "topic": normalized_topic,
                "is_complete": True,
                "displayable": analysis_status == "passed",
                "displayable_reason": "" if analysis_status == "passed" else displayable_reason,
                "analysis_status": analysis_status,
                "decision_summary": decision_summary,
            }
        )

    # 执行create generation task相关逻辑。
    def create_generation_task(
        self,
        request: DynamicViewCreateRequest,
        *,
        source_task_record_id: int | None = None,
        forced_flow_version: int | None = None,
        source_type: str = "",
        source_model: str = "",
        type_code: str = "",
    ) -> DynamicViewTaskSnapshot:
        """创建后台动态视图任务，并立即返回首个可轮询快照。"""
        normalized_request = normalize_create_request(request)
        flow_version = normalize_dynamic_view_flow_version(
            forced_flow_version
            if forced_flow_version is not None
            else settings.llm.resolve_task_flow_version()
        )
        view_type = "knowledge" if flow_version == 2 else "game"
        queued_preview_text = (
            "知识动态视图已进入生成队列。"
            if flow_version == 2
            else "游戏动态视图已进入生成队列。"
        )
        task_id = uuid4().hex
        request_id = uuid4().hex
        current_time = _resolve_current_utc_time()
        snapshot = build_task_snapshot(
            task_id=task_id,
            request_id=request_id,
            author_id=normalized_request.author_id,
            scene_count_min=normalized_request.scene_count_min,
            stream_chunk=build_stream_chunk(
                template_type=normalized_request.template_type,
                stage="queued",
                preview_text=queued_preview_text,
                topic=normalized_request.topic,
                view_type=view_type,
                is_final=False,
            ),
            created_at=current_time,
            updated_at=current_time,
            model_level=normalized_request.model_level,
        )
        snapshot = self._attach_author_average_generation_duration(snapshot)
        self._generation_task_snapshots[task_id] = snapshot
        # 执行persist task snapshot相关逻辑。
        self._persist_task_snapshot(snapshot)
        if normalized_request.start_immediately:
            # 执行start generation task runtime相关逻辑。
            self._start_generation_task_runtime(
                task_id=task_id,
                request=normalized_request,
                flow_version=flow_version,
                source_type=source_type,
                source_model=source_model,
                type_code=type_code,
            )
        if source_task_record_id is not None:
            self._generation_task_source_record_ids[task_id] = source_task_record_id
            with self.session_factory() as db:
                self.dynamic_view_source_task_repository.bind_generation_task(
                    db,
                    task_record_id=source_task_record_id,
                    generation_task_id=task_id,
                )
        return snapshot

    # 执行start existing generation task相关逻辑。
    def start_existing_generation_task(
        self,
        task_id: str,
        request: DynamicViewCreateRequest,
        *,
        source_type: str = "",
        source_model: str = "",
        type_code: str = "",
        request_id: str = "",
    ) -> DynamicViewTaskSnapshot:
        """启动已存在的后台动态视图任务，失败终态复用原 taskId 重新进入队列。"""
        snapshot = self.get_generation_task_snapshot(task_id)
        if task_id in self._generation_tasks:
            return snapshot
        normalized_request = normalize_create_request(request)
        flow_version = normalize_dynamic_view_flow_version(
            settings.llm.resolve_task_flow_version()
        )
        view_type = "knowledge" if flow_version == 2 else "game"
        queued_preview_text = (
            "知识动态视图已重新进入生成队列。"
            if flow_version == 2
            else "游戏动态视图已重新进入生成队列。"
        )
        current_time = _resolve_current_utc_time()
        next_request_id = str(request_id or "").strip()
        if not next_request_id:
            next_request_id = uuid4().hex if snapshot.is_terminal else snapshot.request_id or uuid4().hex
        # 执行cleanup generation task outputs相关逻辑。
        self._cleanup_generation_task_outputs(task_id)
        snapshot = build_task_snapshot(
            task_id=task_id,
            request_id=next_request_id,
            author_id=normalized_request.author_id,
            scene_count_min=normalized_request.scene_count_min,
            stream_chunk=build_stream_chunk(
                template_type=normalized_request.template_type,
                stage="queued",
                preview_text=queued_preview_text,
                topic=normalized_request.topic,
                view_type=view_type,
                is_final=False,
            ),
            created_at=current_time,
            updated_at=current_time,
            model_level=normalized_request.model_level,
        )
        # 执行persist generation task snapshot相关逻辑。
        self._persist_generation_task_snapshot(snapshot)
        # 执行delete generation error logs by task相关逻辑。
        self._delete_generation_error_logs_by_task(task_id)
        # 执行start generation task runtime相关逻辑。
        self._start_generation_task_runtime(
            task_id=task_id,
            request=normalized_request,
            flow_version=flow_version,
            source_type=source_type,
            source_model=source_model,
            type_code=type_code,
        )
        return snapshot

    # 执行mark generation task failed相关逻辑。
    def mark_generation_task_failed(
        self,
        task_id: str,
        *,
        message: str,
    ) -> DynamicViewTaskSnapshot:
        """把前置后台流程失败写成终态，供前端只按 taskId 轮询恢复。"""
        snapshot = self.get_generation_task_snapshot(task_id)
        failed_snapshot = build_terminal_task_snapshot(
            task_id=task_id,
            scene_count_min=snapshot.scene_count_min,
            previous_snapshot=snapshot,
            stage="failed",
            message=message,
            node_status="failed",
            payload_status="failed",
            updated_at=_resolve_current_utc_time(),
        )
        # 执行persist generation task snapshot相关逻辑。
        self._persist_generation_task_snapshot(failed_snapshot)
        return failed_snapshot

    # 执行start generation task runtime相关逻辑。
    def _start_generation_task_runtime(
        self,
        *,
        task_id: str,
        request: DynamicViewCreateRequest,
        flow_version: int,
        source_type: str = "",
        source_model: str = "",
        type_code: str = "",
    ) -> None:
        """为已存在任务创建后台执行协程。"""
        task = asyncio.create_task(
            self._run_generation_task(
                task_id=task_id,
                request=request,
                flow_version=flow_version,
                source_type=source_type,
                source_model=source_model,
                type_code=type_code,
            )
        )
        # 执行add done callback相关逻辑。
        task.add_done_callback(
            lambda finished_task: self._handle_generation_task_done(
                task_id=task_id,
                task=finished_task,
            )
        )
        self._generation_tasks[task_id] = task

    # 执行get active generation task count相关逻辑。
    def get_active_generation_task_count(self) -> int:
        """返回当前仍在执行中的动态视图后台任务数量。"""
        return len(self._generation_tasks)

    # 执行is generation task running相关逻辑。
    def is_generation_task_running(self, task_id: str) -> bool:
        """判断指定动态视图任务是否已经进入后台执行。"""
        return task_id in self._generation_tasks

    # 执行assert task can continue相关逻辑。
    def _assert_task_can_continue(self, task_id: str) -> DynamicViewTaskSnapshot:
        """每个关键阶段继续前都从 task 表确认任务仍可执行，避免取消后链路继续跑到下一个节点。"""
        snapshot = self.get_generation_task_snapshot(task_id)
        if (snapshot.stage == "completed" and snapshot.payload.status == "ready") or snapshot.payload.status == "ready":
            raise RuntimeError(f"动态视图任务已完成，禁止继续执行：task_id={task_id}")
        if snapshot.stage == "cancelled" or snapshot.payload.status == "cancelled":
            raise asyncio.CancelledError
        if snapshot.stage == "failed" or snapshot.payload.status == "failed":
            raise RuntimeError(f"动态视图任务已失败，禁止继续执行：task_id={task_id}")
        if snapshot.is_terminal:
            raise RuntimeError(f"动态视图任务已结束，禁止继续执行：task_id={task_id}")
        return snapshot

    # 执行persist generation task snapshot related logic。
    def _persist_generation_task_snapshot(self, snapshot: DynamicViewTaskSnapshot) -> None:
        """后台任务快照统一同时写入内存与数据库，避免取消接口和阶段门禁读到不一致状态。"""
        snapshot = self._attach_author_average_generation_duration(snapshot)
        self._generation_task_snapshots[snapshot.task_id] = snapshot
        # 执行persist task snapshot相关逻辑。
        self._persist_task_snapshot(snapshot)

    # 执行clear generation task snapshot related logic。
    def _clear_generation_task_snapshot(self, task_id: str) -> None:
        """任务终态收尾后只清掉内存快照，数据库任务记录永久保留。"""
        self._generation_task_snapshots.pop(task_id, None)

    # 执行get generation task snapshot相关逻辑。
    def get_generation_task_snapshot(self, task_id: str) -> DynamicViewTaskSnapshot:
        """读取后台动态视图任务快照，缺失时直接抛错。"""
        snapshot = self._generation_task_snapshots.get(task_id)
        if snapshot is not None:
            return self._attach_author_average_generation_duration(self._refresh_ready_task_snapshot_payload(snapshot))
        with self.session_factory() as db:
            # 执行get task snapshot相关逻辑。
            persisted_snapshot = self.dynamic_view_task_repository.get_task_snapshot(
                db,
                task_id=task_id,
            )
        if persisted_snapshot is None:
            raise ValueError(f"未找到动态视图任务：task_id={task_id}")
        return self._attach_author_average_generation_duration(self._refresh_ready_task_snapshot_payload(persisted_snapshot))

    # 执行get latest generation task snapshot相关逻辑。
    def get_latest_generation_task_snapshot(self, author_id: str) -> DynamicViewTaskSnapshot:
        """读取当前用户最近一条未归档动态视图任务快照，供 Flutter 重进创建页恢复。"""
        normalized_author_id = normalize_dynamic_view_author_id(author_id)
        with self.session_factory() as db:
            # 执行get latest task snapshot相关逻辑。
            persisted_snapshot = self.dynamic_view_task_repository.get_latest_task_snapshot(
                db,
                author_id=normalized_author_id,
            )
        if persisted_snapshot is None:
            raise ValueError(f"未找到动态视图任务：author_id={normalized_author_id}")
        return self._attach_author_average_generation_duration(self._refresh_ready_task_snapshot_payload(persisted_snapshot))

    # 执行update generation task stage相关逻辑。
    def update_generation_task_stage(
        self,
        task_id: str,
        *,
        stage: str,
        message: str,
        node_title: str,
        node_status: str,
        payload_status: str,
        generation_status: str | None = None,
    ) -> DynamicViewTaskSnapshot:
        """按 taskId 写入当前生成阶段，保证前端切换对话只按数据库快照恢复。"""
        snapshot = self.get_generation_task_snapshot(task_id)
        if snapshot.is_terminal:
            return snapshot
        payload = snapshot.payload.model_copy(
            update={
                "status": payload_status,
                "preview_text": message,
            }
        )
        next_snapshot = snapshot.model_copy(
            update={
                "stage": stage,
                "message": message,
                "node_title": node_title,
                "node_status": node_status,
                "generation_status": generation_status or payload_status,
                "payload": payload,
                "updated_at": _resolve_current_utc_time(),
            }
        )
        self._persist_generation_task_snapshot(next_snapshot)
        return next_snapshot

    # 执行attach author average generation duration相关逻辑。
    def _attach_author_average_generation_duration(
        self,
        snapshot: DynamicViewTaskSnapshot,
    ) -> DynamicViewTaskSnapshot:
        """把当前用户的内存平均生成耗时写入任务快照。"""
        normalized_author_id = normalize_dynamic_view_author_id(snapshot.author_id)
        average_duration_ms = self._knowledge_generation_duration_stats.get(
            normalized_author_id,
            (0, 150000),
        )[1]
        return snapshot.model_copy(
            update={"average_generation_duration_ms": int(average_duration_ms)}
        )

    # 执行record knowledge generation duration相关逻辑。
    def _record_knowledge_generation_duration(
        self,
        snapshot: DynamicViewTaskSnapshot,
    ) -> DynamicViewTaskSnapshot:
        """按用户在 Python 内存里动态更新知识视图平均生成耗时。"""
        if snapshot.payload.view_type != "knowledge" or snapshot.payload.status != "ready":
            return self._attach_author_average_generation_duration(snapshot)
        duration_ms = int(max(0, (snapshot.updated_at - snapshot.created_at).total_seconds() * 1000))
        if duration_ms <= 0:
            return self._attach_author_average_generation_duration(snapshot)
        normalized_author_id = normalize_dynamic_view_author_id(snapshot.author_id)
        count, average_duration_ms = self._knowledge_generation_duration_stats.get(
            normalized_author_id,
            (0, 150000),
        )
        next_count = count + 1
        next_average_duration_ms = int(round(((average_duration_ms * count) + duration_ms) / next_count))
        self._knowledge_generation_duration_stats[normalized_author_id] = (
            next_count,
            next_average_duration_ms,
        )
        return snapshot.model_copy(
            update={"average_generation_duration_ms": next_average_duration_ms}
        )

    # 执行refresh ready task snapshot payload相关逻辑。
    def _refresh_ready_task_snapshot_payload(
        self,
        snapshot: DynamicViewTaskSnapshot,
    ) -> DynamicViewTaskSnapshot:
        """已完成任务读取时从视图主表重建 payload，避免返回任务快照里的旧音频配置。"""
        if snapshot.payload.status != "ready":
            return snapshot
        with self.session_factory() as db:
            if snapshot.payload.view_type == "knowledge" and snapshot.payload.knowledge_view_id:
                refreshed_payload = self.build_knowledge_payload(
                    db,
                    knowledge_view_id=snapshot.payload.knowledge_view_id,
                    increase_view_count=False,
                )
            elif snapshot.payload.view_type == "game" and snapshot.payload.game_view_id:
                refreshed_payload = self.build_game_payload(
                    db,
                    game_view_id=snapshot.payload.game_view_id,
                    increase_view_count=False,
                    user_id=None,
                )
            else:
                return snapshot
        return snapshot.model_copy(update={"payload": refreshed_payload})

    # 执行cancel generation task相关逻辑。
    async def cancel_generation_task(self, task_id: str) -> DynamicViewTaskSnapshot:
        """取消后台动态视图任务，并返回任务当前最新快照。"""
        snapshot = self.get_generation_task_snapshot(task_id)
        if (snapshot.stage == "completed" and snapshot.payload.status == "ready") or snapshot.payload.status == "ready":
            raise ValueError(f"当前视频创建已完成，不能取消：task_id={task_id}")
        if snapshot.is_terminal:
            return snapshot
        cancelled_snapshot = build_terminal_task_snapshot(
            task_id=task_id,
            scene_count_min=snapshot.scene_count_min,
            previous_snapshot=snapshot,
            stage="cancelled",
            message="当前视频创建已取消。",
            node_status="cancelled",
            payload_status="cancelled",
            updated_at=_resolve_current_utc_time(),
        )
        if cancelled_snapshot.payload.knowledge_view_id is not None or cancelled_snapshot.payload.game_view_id is not None:
            with self.session_factory() as db:
                if cancelled_snapshot.payload.view_type == "knowledge":
                    if cancelled_snapshot.payload.knowledge_view_id is not None:
                        # 执行update archive status相关逻辑。
                        self.dynamic_view_knowledge_repository.update_archive_status(
                            db,
                            archive_id=cancelled_snapshot.payload.knowledge_view_id,
                            status="cancelled",
                        )
                elif cancelled_snapshot.payload.game_view_id is not None:
                    # 执行update archive status相关逻辑。
                    self.dynamic_view_game_repository.update_archive_status(
                        db,
                        archive_id=cancelled_snapshot.payload.game_view_id,
                        status="cancelled",
                    )
        # 执行persist generation task snapshot相关逻辑。
        self._persist_generation_task_snapshot(cancelled_snapshot)
        task = self._generation_tasks.get(task_id)
        if task is not None and not task.done():
            # 执行cancel相关逻辑。
            task.cancel()
            # 执行gather相关逻辑。
            await asyncio.gather(task, return_exceptions=True)
        return cancelled_snapshot

    # 执行soft delete generation task相关逻辑。
    async def soft_delete_generation_task(self, task_id: str, *, user_id: str) -> bool:
        """删除近期对话时软删除对应任务和生成视图，并停止仍在运行的任务。"""
        normalized_task_id = str(task_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        if not normalized_task_id:
            return False
        with self.session_factory() as db:
            archive = (
                db.query(DynamicViewTaskArchive)
                .filter(DynamicViewTaskArchive.task_id == normalized_task_id)
                .first()
            )
            if archive is None:
                self._generation_task_snapshots.pop(normalized_task_id, None)
                return False
            if normalized_user_id and archive.author_id != normalized_user_id:
                raise PermissionError("不能删除其他用户的生成任务")
        task = self._generation_tasks.get(normalized_task_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with self.session_factory() as db:
            archive = (
                db.query(DynamicViewTaskArchive)
                .filter(DynamicViewTaskArchive.task_id == normalized_task_id)
                .first()
            )
            if archive is None:
                self._generation_task_snapshots.pop(normalized_task_id, None)
                return False
            self._soft_delete_task_archives(db, archive=archive)
            archive.stage = "archived"
            archive.message = "已删除生成记录。"
            archive.node_status = "deleted"
            archive.payload_status = "deleted"
            archive.progress = None
            archive.is_final = 1
            archive.is_terminal = 1
            archive.updated_at = _resolve_current_utc_time()
            db.commit()
        self._generation_task_snapshots.pop(normalized_task_id, None)
        return True

    # 执行soft delete task archives相关逻辑。
    def _soft_delete_task_archives(
        self,
        db: Session,
        *,
        archive: DynamicViewTaskArchive,
    ) -> None:
        """把任务直接生成或关联的动态视图统一标记为 deleted。"""
        archive_ids = {
            int(value)
            for value in (archive.game_archive_id, archive.knowledge_archive_id)
            if value is not None
        }
        linked_archives = (
            db.query(DynamicViewGameArchive)
            .filter(DynamicViewGameArchive.generation_task_id == archive.task_id)
            .all()
        )
        archive_ids.update(int(item.id) for item in linked_archives)
        if not archive_ids:
            return
        rows = (
            db.query(DynamicViewGameArchive)
            .filter(DynamicViewGameArchive.id.in_(archive_ids))
            .all()
        )
        for row in rows:
            row.status = "deleted"
            if row.knowledge_archive_id is not None:
                archive_ids.add(int(row.knowledge_archive_id))
            if row.game_archive_id is not None:
                archive_ids.add(int(row.game_archive_id))
        extra_rows = (
            db.query(DynamicViewGameArchive)
            .filter(DynamicViewGameArchive.id.in_(archive_ids))
            .all()
        )
        for row in extra_rows:
            row.status = "deleted"
            if row.knowledge_generation_status not in {"", "idle"}:
                row.knowledge_generation_status = "deleted"

    # 执行build public generation error message相关逻辑。
    def _build_public_generation_error_message(self, *, task_id: str, request_id: str) -> str:
        """生成给前端展示的安全错误文案，不暴露异常堆栈和内部路径。"""
        normalized_task_id = str(task_id or "").strip() or "unknown"
        normalized_request_id = str(request_id or "").strip() or "unknown"
        return f"知识视图生成失败。taskId：{normalized_task_id}，requestId：{normalized_request_id}。"

    # 执行record generation error log相关逻辑。
    def _record_generation_error_log(
        self,
        *,
        task_id: str,
        request_id: str,
        user_id: str,
        topic: str,
        stage: str | None,
        node_key: str | None,
        node_title: str | None,
        error: Exception,
    ) -> None:
        """把生成异常写入管理员排查表，前端只拿 taskId/requestId。"""
        try:
            with self.session_factory() as db:
                # 执行add相关逻辑。
                db.add(
                    DynamicViewGenerationErrorLog(
                        task_id=str(task_id or "")[:64],
                        request_id=str(request_id or "")[:64],
                        user_id=str(user_id or "")[:64],
                        topic=str(topic or "")[:255],
                        stage=str(stage or "")[:64],
                        node_key=str(node_key or "")[:128],
                        node_title=str(node_title or "")[:128],
                        error_type=error.__class__.__name__[:128],
                        error_message=str(error or "")[:4000],
                        stack_trace=traceback.format_exc(),
                    )
                )
                # 执行commit相关逻辑。
                db.commit()
        except Exception:
            # 执行exception相关逻辑。
            logger.exception(
                "Dynamic view generation error log persist failed: task_id=%s request_id=%s",
                task_id,
                request_id,
            )

    # 执行delete generation error logs by task相关逻辑。
    def _delete_generation_error_logs_by_task(self, task_id: str) -> None:
        """重新启动同一 taskId 前清理旧错误日志，避免管理员排查时混淆。"""
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return
        with self.session_factory() as db:
            # 执行delete相关逻辑。
            db.query(DynamicViewGenerationErrorLog).filter(
                DynamicViewGenerationErrorLog.task_id == normalized_task_id
            ).delete(synchronize_session=False)
            # 执行commit相关逻辑。
            db.commit()

    # 执行cleanup generation task outputs相关逻辑。
    def _cleanup_generation_task_outputs(self, task_id: str) -> None:
        """复用同一 taskId 重新生成前清理旧视图产物。"""
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return
        with self.session_factory() as db:
            archive = (
                db.query(DynamicViewTaskArchive)
                .filter(DynamicViewTaskArchive.task_id == normalized_task_id)
                .first()
            )
            if archive is not None:
                # 执行soft delete task archives相关逻辑。
                self._soft_delete_task_archives(db, archive=archive)
                archive.game_archive_id = None
                archive.knowledge_archive_id = None
                archive.html_url = ""
                archive.updated_at = _resolve_current_utc_time()
                # 执行commit相关逻辑。
                db.commit()

    # 执行refund failed generation credits相关逻辑。
    def _refund_failed_generation_credits(
        self,
        *,
        task_id: str,
        snapshot: DynamicViewTaskSnapshot,
    ) -> None:
        """生成失败时按任务维度把本次扣除的 Credit 幂等退回。"""
        normalized_user_id = str(snapshot.author_id or "").strip()
        if not normalized_user_id or normalized_user_id == "website":
            return
        try:
            with self.session_factory() as db:
                refund_amount = self.resolve_generation_model_credit_cost(db, snapshot.model_level)
                # 执行refund credits相关逻辑。
                self.credit_service.refund_credits(
                    db,
                    user_id=normalized_user_id,
                    amount=refund_amount,
                    source_key=snapshot.request_id or task_id,
                    reason="生成失败退回",
                )
                # 执行commit相关逻辑。
                db.commit()
        except Exception:
            # 执行exception相关逻辑。
            logger.exception(
                "Dynamic view failed generation credit refund failed: task_id=%s",
                task_id,
            )

    # 执行create dynamic view payload相关逻辑。
    async def create_dynamic_view_payload(
        self,
        db: Session,
        request: DynamicViewCreateRequest,
    ) -> DynamicViewPayload:
        """为非流式接口收口最终结果，只返回最后一个完成态载荷。"""
        final_payload: DynamicViewPayload | None = None
        async for stream_chunk in self.stream_dynamic_view(db, request):
            if stream_chunk.is_final:
                final_payload = stream_chunk.payload
        if final_payload is not None:
            return final_payload
        return build_payload(
            topic=request.topic,
            status="failed",
            preview_text="动态视图生成失败。",
            summary="动态视图生成失败。",
            subject_parent_type=resolve_dynamic_view_subject_parent_type(""),
            subject_type=infer_subject_type(request.topic),
        )

    # 执行run generation task相关逻辑。
    async def _run_generation_task(
        self,
        *,
        task_id: str,
        request: DynamicViewCreateRequest,
        flow_version: int,
        source_type: str = "",
        source_model: str = "",
        type_code: str = "",
    ) -> None:
        """在后台执行游戏动态视图生成，并把阶段结果持续写入任务快照。"""
        db = self.session_factory()
        current_node_key = ""
        current_node_title = ""
        try:
            # 执行assert task can continue相关逻辑。
            self._assert_task_can_continue(task_id)
            async for stream_chunk in self.stream_dynamic_view(
                db,
                request,
                flow_version=flow_version,
                task_id=task_id,
                source_type=source_type,
                source_model=source_model,
                type_code=type_code,
            ):
                if stream_chunk.is_final and stream_chunk.payload.status == "ready":
                    # 执行assert final payload persisted related logic。
                    if stream_chunk.payload.view_type == "knowledge":
                        if stream_chunk.payload.knowledge_view_id is None:
                            raise RuntimeError("知识视图任务完成但未关联知识视图 ID。")
                        self.dynamic_view_knowledge_repository.get_ready_archive_detail(
                            db,
                            archive_id=stream_chunk.payload.knowledge_view_id,
                            increase_view_count=False,
                        )
                    else:
                        if stream_chunk.payload.game_view_id is None:
                            raise RuntimeError("游戏视图任务完成但未关联游戏视图 ID。")
                        self.dynamic_view_game_repository.get_ready_archive_detail(
                            db,
                            archive_id=stream_chunk.payload.game_view_id,
                            increase_view_count=False,
                        )
                current_snapshot = self.get_generation_task_snapshot(task_id)
                next_snapshot = build_task_snapshot(
                    task_id=task_id,
                    request_id=current_snapshot.request_id,
                    author_id=request.author_id,
                    scene_count_min=request.scene_count_min,
                    stream_chunk=stream_chunk,
                    created_at=current_snapshot.created_at,
                    updated_at=_resolve_current_utc_time(),
                    model_level=current_snapshot.model_level or request.model_level,
                )
                if next_snapshot.is_terminal and next_snapshot.payload.status == "ready":
                    next_snapshot = self._record_knowledge_generation_duration(next_snapshot)
                # 执行persist generation task snapshot相关逻辑。
                self._persist_generation_task_snapshot(next_snapshot)
                if next_snapshot.is_terminal and next_snapshot.payload.status == "failed":
                    # 执行refund failed generation credits相关逻辑。
                    self._refund_failed_generation_credits(
                        task_id=task_id,
                        snapshot=next_snapshot,
                    )
        except asyncio.CancelledError:
            snapshot = self.get_generation_task_snapshot(task_id)
            archive_id = (
                snapshot.payload.knowledge_view_id
                if snapshot.payload.view_type == "knowledge"
                else snapshot.payload.game_view_id
            )
            if archive_id is not None:
                try:
                    if snapshot.payload.view_type == "knowledge":
                        # 执行update archive status相关逻辑。
                        self.dynamic_view_knowledge_repository.update_archive_status(
                            db,
                            archive_id=archive_id,
                            status="cancelled",
                        )
                    else:
                        # 执行update archive status相关逻辑。
                        self.dynamic_view_game_repository.update_archive_status(
                            db,
                            archive_id=archive_id,
                            status="cancelled",
                        )
                except Exception:
                    # 执行exception相关逻辑。
                    logger.exception(
                        "Dynamic view archive cancel status update failed: task_id=%s",
                        task_id,
                    )
            cancelled_snapshot = build_terminal_task_snapshot(
                task_id=task_id,
                scene_count_min=request.scene_count_min,
                previous_snapshot=snapshot,
                stage="cancelled",
                message="当前视频创建已取消。",
                node_status="cancelled",
                payload_status="cancelled",
                updated_at=_resolve_current_utc_time(),
            )
            # 执行persist generation task snapshot相关逻辑。
            self._persist_generation_task_snapshot(cancelled_snapshot)
            raise
        except Exception as error:
            # 执行exception相关逻辑。
            logger.exception(
                "Dynamic view background generation failed: task_id=%s",
                task_id,
            )
            previous_snapshot = self.get_generation_task_snapshot(task_id)
            self._record_generation_error_log(
                task_id=task_id,
                request_id=previous_snapshot.request_id,
                user_id=request.author_id,
                topic=request.topic,
                stage=previous_snapshot.stage,
                node_key=current_node_key,
                node_title=current_node_title or previous_snapshot.node_title,
                error=error,
            )
            is_knowledge_payload = previous_snapshot.payload.view_type == "knowledge"
            archive_id = (
                previous_snapshot.payload.knowledge_view_id
                if is_knowledge_payload
                else previous_snapshot.payload.game_view_id
            )
            if archive_id is not None:
                try:
                    if is_knowledge_payload:
                        # 执行delete knowledge archive data相关逻辑。
                        self._delete_knowledge_archive_data(
                            db,
                            knowledge_archive_id=archive_id,
                        )
                    else:
                        # 执行delete game archive data相关逻辑。
                        self._delete_game_archive_data(
                            db,
                            archive_id=archive_id,
                        )
                    previous_snapshot = previous_snapshot.model_copy(
                        update={
                            "payload": previous_snapshot.payload.model_copy(
                                update=(
                                    {"knowledge_view_id": None}
                                    if is_knowledge_payload
                                    else {"game_view_id": None}
                                )
                            )
                        }
                    )
                except Exception:
                    # 执行exception相关逻辑。
                    logger.exception(
                        "Dynamic view archive cleanup failed: task_id=%s",
                        task_id,
                    )
            failed_snapshot = build_terminal_task_snapshot(
                task_id=task_id,
                scene_count_min=request.scene_count_min,
                previous_snapshot=previous_snapshot,
                stage="failed",
                message=self._build_public_generation_error_message(
                    task_id=task_id,
                    request_id=previous_snapshot.request_id,
                ),
                node_status="failed",
                payload_status="failed",
                updated_at=_resolve_current_utc_time(),
            )
            # 执行persist generation task snapshot相关逻辑。
            self._persist_generation_task_snapshot(failed_snapshot)
            # 执行refund failed generation credits相关逻辑。
            self._refund_failed_generation_credits(
                task_id=task_id,
                snapshot=failed_snapshot,
            )
        finally:
            # 执行close相关逻辑。
            db.close()

    # 执行stream dynamic view相关逻辑。
    async def stream_dynamic_view(
        self,
        db: Session,
        request: DynamicViewCreateRequest,
        *,
        disconnect_checker: Callable[[], Awaitable[bool]] | None = None,
        flow_version: int | None = None,
        task_id: str | None = None,
        source_type: str = "",
        source_model: str = "",
        type_code: str = "",
    ):
        """按固定节点顺序流式执行游戏动态视图生成，并持续输出节点卡片所需数据。"""
        normalized_request = normalize_create_request(request)
        topic = normalized_request.topic
        # 执行resolve flow version相关逻辑。
        resolved_flow_version = normalize_dynamic_view_flow_version(
            flow_version if flow_version is not None else settings.llm.resolve_task_flow_version()
        )
        flow_prompt_version = resolved_flow_version
        normalized_source_type = str(source_type or "").strip()
        normalized_source_model = str(source_model or normalized_request.model_level).strip()
        normalized_type_code = str(type_code or "").strip() or _resolve_generated_view_type_code(
            flow_version=resolved_flow_version,
            template_type=normalized_request.template_type,
        )
        is_knowledge_flow = resolved_flow_version == 2
        stream_view_type = "knowledge" if is_knowledge_flow else "game"
        # 执行resolve node script mode相关逻辑。
        node_script_mode = (
            DynamicViewScriptMode.KNOWLEDGE
            if resolved_flow_version == 2
            else DynamicViewScriptMode.GAME
        )
        # 执行resolve node1 stage text相关逻辑。
        node1_stage_text_suffix = "知识视图" if resolved_flow_version == 2 else "游戏剧本"
        node1_stage = DynamicViewStageDescriptor(
            stage="node1",
            node_key="dynamic_view_node1",
            node_title=node1_stage_text_suffix,
            ready_preview_text=f"准备生成{node1_stage_text_suffix}。",
            processing_preview_text=f"正在生成{node1_stage_text_suffix}。",
            completed_preview_text=f"{node1_stage_text_suffix}已生成完成。",
        )
        node2_timeline_stage = DynamicViewStageDescriptor(
            stage="node2_timeline_script",
            node_key="dynamic_view_node2_timeline_script",
            node_title="时间轴脚本",
            ready_preview_text="准备生成时间轴脚本。",
            processing_preview_text="正在生成时间轴脚本。",
            completed_preview_text="时间轴脚本已生成完成。",
        )
        node2_dynamic_css_stage = DynamicViewStageDescriptor(
            stage="node2_dynamic_css",
            node_key="dynamic_view_node2_dynamic_css",
            node_title="动态样式",
            ready_preview_text="准备生成动态样式。",
            processing_preview_text="正在生成动态样式。",
            completed_preview_text="动态样式已生成完成。",
        )
        metadata_stage = DynamicViewStageDescriptor(
            stage="metadata",
            node_key="dynamic_view_metadata",
            node_title="元数据整理" if is_knowledge_flow else "元数据与线索",
            ready_preview_text="准备整理视图元数据。" if is_knowledge_flow else "准备整理元数据与线索。",
            processing_preview_text="正在整理视图元数据。" if is_knowledge_flow else "正在整理元数据与线索。",
            completed_preview_text="视图元数据已整理完成。" if is_knowledge_flow else "元数据与线索已整理完成。",
        )
        subtitle_audio_stage = DynamicViewStageDescriptor(
            stage="subtitle_audio",
            node_key="dynamic_view_subtitle_audio",
            node_title="字幕音频",
            ready_preview_text="准备生成字幕音频。",
            processing_preview_text="正在生成字幕音频。",
            completed_preview_text="字幕音频已生成完成。",
        )
        final_html_stage = DynamicViewStageDescriptor(
            stage="final_html",
            node_key="dynamic_view_final_html",
            node_title="成片组装",
            ready_preview_text="准备组装最终成片。",
            processing_preview_text="正在组装最终成片。",
            completed_preview_text="最终成片已组装完成。",
        )
        archive_id: int | None = None
        current_node_key = node1_stage.node_key
        current_node_title = node1_stage.node_title
        character_task: asyncio.Task[list[DynamicViewCharacter]] | None = None
        game_metadata_task: asyncio.Task[
            tuple[str, DynamicViewMetadata, list[DynamicViewClueItem]]
        ] | None = None
        metadata_only_task: asyncio.Task[DynamicViewMetadata] | None = None
        knowledge_detail_task: asyncio.Task[DynamicViewKnowledgeDetail] | None = None
        subtitle_audio_task: asyncio.Task[str] | None = None
        node1_scene_texts: list[dict[str, str]] = []
        timeline_scene_texts: list[dict[str, str]] = []
        clues: list[DynamicViewClueItem] = []
        final_question = ""
        _, node1_subject_type = infer_subject_taxonomy(topic)
        selected_audio_name = ""
        selected_audio_start_time = 0
        selected_audio_end_time = 0
        selected_audio_volume = 25
        subtitle_audio_volume = 100
        subtitle_audio_name = ""
        node1_stream_enabled = False
        node1_raw_output = ""
        node1_structured_output = None
        node1_clean_text = topic
        node1_timeline_code = ""
        node1_dynamic_css_code = ""
        theme_colors = extract_theme_colors_from_node1_output("", None)
        timeline_code = ""
        dynamic_css_code = ""
        total_duration_ms = 0
        try:
            # 执行raise if client disconnected相关逻辑。
            await _raise_if_client_disconnected(disconnect_checker)
            # 执行assert task can continue相关逻辑。
            self._assert_task_can_continue(task_id)
            if task_id is not None:
                # 执行assert task can continue相关逻辑。
                self._assert_task_can_continue(task_id)
            yield build_stream_chunk(
                template_type=normalized_request.template_type,
                stage="queued",
                preview_text=f"《{topic}》已进入动态视图生成队列。",
                topic=topic,
                view_type=stream_view_type,
                is_final=False,
            )

            if task_id is not None:
                # 执行assert task can continue相关逻辑。
                self._assert_task_can_continue(task_id)
            node1_messages = build_node1_prompt(
                topic,
                normalized_request.scene_count_min,
                script_mode=node_script_mode,
                request=normalized_request,
                prompt_version=flow_prompt_version,
            )
            # 执行raise if client disconnected相关逻辑。
            await _raise_if_client_disconnected(disconnect_checker)
            yield build_stage_chunk(
                template_type=normalized_request.template_type,
                descriptor=node1_stage,
                node_status="ready",
                topic=topic,
                view_type=stream_view_type,
            )
            yield build_stage_chunk(
                template_type=normalized_request.template_type,
                descriptor=node1_stage,
                node_status="processing",
                topic=topic,
                view_type=stream_view_type,
            )
            node1_model_levels = _resolve_model_retry_levels(normalized_request.model_level)
            node1_last_error: Exception | None = None
            for node1_model_level in node1_model_levels:
                node1_attempt_index = 0
                while True:
                    # 执行build latest runtime runner相关逻辑。
                    node1_runner = self._build_latest_runtime_runner(node1_model_level, "node1")
                    try:
                        # 执行runner allows streaming相关逻辑。
                        node1_stream_enabled = runner_allows_streaming(node1_runner)
                        if node1_stream_enabled:
                            node1_chunks: list[str] = []
                            async for node1_chunk_text in self._run_prompt_stream(
                                runner=node1_runner,
                                messages=node1_messages,
                                stage_name="dynamic_view_node1",
                            ):
                                # 执行raise if client disconnected相关逻辑。
                                await _raise_if_client_disconnected(disconnect_checker)
                                # 执行append相关逻辑。
                                node1_chunks.append(node1_chunk_text)
                                current_node1_output = "".join(node1_chunks).strip()
                                if not current_node1_output:
                                    continue
                                yield build_stage_chunk(
                                    template_type=normalized_request.template_type,
                                    descriptor=node1_stage,
                                    node_status="processing",
                                    topic=topic,
                                    view_type=stream_view_type,
                                    stream_char_count=len(current_node1_output),
                                )
                            node1_raw_output = "".join(node1_chunks).strip()
                        else:
                            node1_raw_output = await self._run_prompt(
                                runner=node1_runner,
                                messages=node1_messages,
                                stage_name="dynamic_view_node1",
                            )
                        # 执行raise if model output contains document api相关逻辑。
                        _raise_if_model_output_contains_document_api(
                            node1_raw_output,
                            "dynamic_view_node1",
                        )
                        node1_last_error = None
                        break
                    except ModelOutputDocumentApiError:
                        logger.warning(
                            "Dynamic view node1 output contains document API: stage=%s | modelLevel=%s | attempt=%s/%s",
                            "dynamic_view_node1",
                            node1_model_level,
                            node1_attempt_index + 1,
                            _DOCUMENT_API_RETRY_COUNT + 1,
                        )
                        if node1_attempt_index >= _DOCUMENT_API_RETRY_COUNT:
                            raise
                        node1_attempt_index += 1
                    except Exception as error:
                        node1_last_error = error
                        _log_model_level_failover_warning(
                            node_key="node1",
                            model_level=node1_model_level,
                            error=error,
                        )
                        break
                    finally:
                        # 执行close runtime model clients相关逻辑。
                        await self._close_runtime_model_clients(node1_runner)
                if node1_last_error is None:
                    break
            if node1_last_error is not None:
                raise node1_last_error
            # 执行try parse structured document相关逻辑。
            node1_structured_output = try_parse_structured_document(node1_raw_output)
            # 执行extract final question相关逻辑。
            final_question = extract_final_question(node1_structured_output, node1_raw_output)
            # 执行extract scene texts from node1 output相关逻辑。
            node1_scene_texts = extract_scene_texts_from_node1_output(node1_structured_output)
            # 执行clean model output text相关逻辑。
            node1_clean_text = clean_model_output_text(node1_raw_output)
            # 执行extract timeline assets from node1 output相关逻辑。
            node1_timeline_code, node1_dynamic_css_code = (
                extract_timeline_assets_from_node1_output(node1_raw_output)
            )
            if not node1_scene_texts and node1_timeline_code:
                # 执行extract scene texts from timeline code相关逻辑。
                node1_scene_texts = extract_scene_texts_from_timeline_code(
                    node1_timeline_code
                )
            # 执行create metadata task相关逻辑。
            if is_knowledge_flow:
                metadata_only_task = asyncio.create_task(
                    self._generate_metadata_only(
                        topic=topic,
                        scene_texts=node1_scene_texts,
                        request=normalized_request,
                        prompt_version=flow_prompt_version,
                        model_level=normalized_request.model_level,
                    )
                )
                knowledge_detail_task = asyncio.create_task(
                    self._generate_knowledge_detail(
                        topic=topic,
                        scene_texts=node1_scene_texts,
                        request=normalized_request,
                        prompt_version=flow_prompt_version,
                        model_level=normalized_request.model_level,
                    )
                )
            else:
                game_metadata_task = asyncio.create_task(
                    self._generate_game_metadata_bundle_with_retry(
                        topic=topic,
                        scene_texts=node1_scene_texts,
                        final_question=final_question,
                        request=normalized_request,
                        prompt_version=flow_prompt_version,
                        model_level=normalized_request.model_level,
                    )
                )
            (
                selected_audio_name,
                selected_audio_start_time,
                selected_audio_end_time,
                selected_audio_volume,
            ) = self._select_dynamic_view_audio()
            if is_knowledge_flow:
                archive_id = self.dynamic_view_knowledge_repository.create_archive(
                    db,
                    game_archive_id=None,
                    author_id=normalized_request.author_id,
                    template_type=normalized_request.template_type,
                    topic=topic,
                    status="processing",
                    audio_name=selected_audio_name,
                    audio_start_time=selected_audio_start_time,
                    audio_end_time=selected_audio_end_time,
                    audio_volume=selected_audio_volume,
                    source_type=normalized_source_type,
                    source_model=normalized_source_model,
                    type_code=normalized_type_code,
                )
            else:
                archive_id = self.dynamic_view_game_repository.create_archive(
                    db,
                    topic=topic,
                    author_id=normalized_request.author_id,
                    template_type=normalized_request.template_type,
                    scene_count_min=normalized_request.scene_count_min,
                    scene_subtitles=node1_scene_texts,
                    status="processing",
                    audio_name=selected_audio_name,
                    audio_start_time=selected_audio_start_time,
                    audio_end_time=selected_audio_end_time,
                    audio_volume=selected_audio_volume,
                    source_type=normalized_source_type,
                    source_model=normalized_source_model,
                    type_code=normalized_type_code,
                )
            stage_archive_id = archive_id
            if not is_knowledge_flow and node1_scene_texts:
                # 执行start archive character task相关逻辑。
                character_task = self._start_archive_character_task(
                    owner_id=archive_id,
                    topic=topic,
                    subject_type=node1_subject_type,
                    prompt_version=flow_prompt_version,
                    model_level=normalized_request.model_level,
                )
            yield build_stage_chunk(
                template_type=normalized_request.template_type,
                descriptor=node1_stage,
                node_status="completed",
                topic=topic,
                archive_id=stage_archive_id,
                view_type=stream_view_type,
                stream_char_count=len(node1_raw_output) if node1_stream_enabled else None,
            )
            if is_knowledge_flow:
                current_node_key = metadata_stage.node_key
                current_node_title = metadata_stage.node_title
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=metadata_stage,
                    node_status="ready",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=metadata_stage,
                    node_status="processing",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )

            if resolved_flow_version == 1:
                node2_history: list[HistoryItem] = []
                if task_id is not None:
                    # 执行assert task can continue相关逻辑。
                    self._assert_task_can_continue(task_id)
                node2_step1_prompt = build_node2_step1_prompt(
                    node1_clean_text=node1_clean_text,
                    topic=topic,
                    scene_count_min=normalized_request.scene_count_min,
                    script_mode=node_script_mode,
                    prompt_version=flow_prompt_version,
                )
                current_node_key = node2_timeline_stage.node_key
                current_node_title = node2_timeline_stage.node_title
                # 执行raise if client disconnected相关逻辑。
                await _raise_if_client_disconnected(disconnect_checker)
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=node2_timeline_stage,
                    node_status="ready",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=node2_timeline_stage,
                    node_status="processing",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                # 执行generate node2 text without document api相关逻辑。
                node2_step1_raw_output, node2_history = await self._generate_node2_text_without_document_api(
                    model_level=normalized_request.model_level,
                    history=node2_history,
                    prompt=node2_step1_prompt,
                    stage_name="dynamic_view_node2_timeline_script",
                )
                # 执行extract code block text相关逻辑。
                timeline_code = sanitize_timeline_text_literals(
                    extract_code_block_text(node2_step1_raw_output)
                )
                if not _contains_timeline_data_assignment(timeline_code):
                    raise RuntimeError("v1 node2 未输出有效 TimelineData。")
                # 执行extract scene texts from timeline code相关逻辑。
                timeline_scene_texts = extract_scene_texts_from_timeline_code(timeline_code)
                # 执行calculate dynamic durations相关逻辑。
                dynamic_durations = calculate_dynamic_durations(timeline_scene_texts)
                # 执行sum相关逻辑。
                total_duration_ms = sum(dynamic_durations)
                if archive_id is not None:
                    # 执行update flow archive timeline data相关逻辑。
                    self._update_flow_archive_timeline_data(
                        db,
                        is_knowledge_flow=is_knowledge_flow,
                        archive_id=archive_id,
                        scene_subtitles=timeline_scene_texts,
                        total_duration_ms=total_duration_ms,
                        final_question=final_question,
                    )
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=node2_timeline_stage,
                    node_status="completed",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                if task_id is not None:
                    # 执行assert task can continue相关逻辑。
                    self._assert_task_can_continue(task_id)
                node2_step2_prompt = build_node2_step2_prompt(
                    node2_step1_output=timeline_code,
                    topic=topic,
                    scene_count_min=normalized_request.scene_count_min,
                    script_mode=node_script_mode,
                    prompt_version=flow_prompt_version,
                )
                current_node_key = node2_dynamic_css_stage.node_key
                current_node_title = node2_dynamic_css_stage.node_title
                # 执行raise if client disconnected相关逻辑。
                await _raise_if_client_disconnected(disconnect_checker)
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=node2_dynamic_css_stage,
                    node_status="ready",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=node2_dynamic_css_stage,
                    node_status="processing",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                # 执行generate node2 text without document api相关逻辑。
                node2_step2_raw_output, _ = await self._generate_node2_text_without_document_api(
                    model_level=normalized_request.model_level,
                    history=node2_history,
                    prompt=node2_step2_prompt,
                    stage_name="dynamic_view_node2_dynamic_css",
                )
                # 执行extract code block text相关逻辑。
                dynamic_css_code = extract_code_block_text(node2_step2_raw_output)
                if not dynamic_css_code.strip() and node1_dynamic_css_code.strip():
                    dynamic_css_code = node1_dynamic_css_code
                # 执行inject dynamic durations into timeline code相关逻辑。
                timeline_code = inject_dynamic_durations_into_timeline_code(
                    timeline_code,
                    dynamic_durations,
                )
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=node2_dynamic_css_stage,
                    node_status="completed",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                current_node_key = metadata_stage.node_key
                current_node_title = metadata_stage.node_title
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=metadata_stage,
                    node_status="ready",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=metadata_stage,
                    node_status="processing",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
            else:
                timeline_code = sanitize_timeline_text_literals(
                    extract_code_block_text(node1_raw_output)
                )
                dynamic_css_code = ""
                timeline_scene_texts = extract_scene_texts_from_timeline_code(timeline_code)
                if not timeline_scene_texts:
                    timeline_scene_texts = node1_scene_texts
                # 执行calculate dynamic durations相关逻辑。
                dynamic_durations = calculate_dynamic_durations(timeline_scene_texts)
                # 执行sum相关逻辑。
                total_duration_ms = sum(dynamic_durations)
                if not is_knowledge_flow:
                    # 执行inject dynamic durations into timeline code相关逻辑。
                    timeline_code = inject_dynamic_durations_into_timeline_code(
                        timeline_code,
                        dynamic_durations,
                    )
                if archive_id is not None:
                    # 执行update flow archive timeline data相关逻辑。
                    self._update_flow_archive_timeline_data(
                        db,
                        is_knowledge_flow=is_knowledge_flow,
                        archive_id=archive_id,
                        scene_subtitles=timeline_scene_texts,
                        total_duration_ms=total_duration_ms,
                        final_question=final_question,
                    )
                if is_knowledge_flow and archive_id is not None:
                    # 执行start knowledge subtitle audio task相关逻辑。
                    subtitle_audio_task = asyncio.create_task(
                        self._generate_knowledge_subtitle_audio_file(
                            knowledge_archive_id=archive_id,
                            scene_subtitles=timeline_scene_texts,
                            subtitle_language=normalized_request.subtitle_languages[0],
                        )
                    )
                    current_node_key = subtitle_audio_stage.node_key
                    current_node_title = subtitle_audio_stage.node_title
                    yield build_stage_chunk(
                        template_type=normalized_request.template_type,
                        descriptor=subtitle_audio_stage,
                        node_status="ready",
                        topic=topic,
                        archive_id=stage_archive_id,
                        view_type=stream_view_type,
                    )
                    yield build_stage_chunk(
                        template_type=normalized_request.template_type,
                        descriptor=subtitle_audio_stage,
                        node_status="processing",
                        topic=topic,
                        archive_id=stage_archive_id,
                        view_type=stream_view_type,
                    )

            if task_id is not None:
                # 执行assert task can continue相关逻辑。
                self._assert_task_can_continue(task_id)
            if not _contains_timeline_data_assignment(timeline_code):
                raise RuntimeError("动态视图时间轴脚本为空，无法注入 MODULE_TIMELINE_START。")
            # 执行raise if client disconnected相关逻辑。
            await _raise_if_client_disconnected(disconnect_checker)
            if archive_id is None:
                raise RuntimeError("动态视图主存档未创建，无法写入最终 HTML。")
            if is_knowledge_flow:
                try:
                    if metadata_only_task is None:
                        raise RuntimeError("知识视图 metadata 任务未创建。")
                    ready_metadata = await metadata_only_task
                    if knowledge_detail_task is None:
                        raise RuntimeError("知识视图 detail 任务未创建。")
                    # 执行await相关逻辑。
                    knowledge_detail = await knowledge_detail_task
                    ready_metadata = ready_metadata.model_copy(
                        update={"detail": knowledge_detail.detail},
                    )
                    masked_topic = topic
                    clues = []
                except Exception:
                    await self._cancel_background_tasks(
                        topic=topic,
                        tasks=[knowledge_detail_task],
                    )
                    logger.exception(
                        "Dynamic view metadata generation failed and fallback is used: topic=%s",
                        topic,
                    )
                    masked_topic = topic
                    ready_metadata = self._build_fallback_metadata(
                        topic=topic,
                        scene_texts=timeline_scene_texts or node1_scene_texts,
                        subject_type=node1_subject_type,
                    )
                    clues = []
            else:
                try:
                    if game_metadata_task is None:
                        raise RuntimeError("游戏视图 metadata 任务未创建。")
                    masked_topic, ready_metadata, clues = await game_metadata_task
                except Exception:
                    logger.exception(
                        "Dynamic view metadata generation failed and fallback is used: topic=%s",
                        topic,
                    )
                    masked_topic = topic
                    ready_metadata = self._build_fallback_metadata(
                        topic=topic,
                        scene_texts=timeline_scene_texts or node1_scene_texts,
                        subject_type=node1_subject_type,
                    )
                    clues = []
            yield build_stage_chunk(
                template_type=normalized_request.template_type,
                descriptor=metadata_stage,
                node_status="completed",
                topic=topic,
                archive_id=stage_archive_id,
                view_type=stream_view_type,
            )
            # 统一使用 node1 里的 global_settings.palette 回填 HTML 主题色。
            theme_colors = extract_theme_colors_from_node1_output(
                node1_raw_output,
                node1_structured_output,
            )
            if is_knowledge_flow:
                self.dynamic_view_knowledge_repository.update_archive_metadata(
                    db,
                    archive_id=archive_id,
                    subtitle=ready_metadata.subtitle,
                    detail=ready_metadata.detail,
                    summary=ready_metadata.summary,
                    subject_type=ready_metadata.subject_type,
                )
                if subtitle_audio_task is None:
                    # 执行load owner scene subtitles相关逻辑。
                    persisted_scene_texts = self._load_owner_scene_subtitles(
                        owner_type="knowledge",
                        owner_id=archive_id,
                    )
                    # 执行start knowledge subtitle audio task相关逻辑。
                    subtitle_audio_task = asyncio.create_task(
                        self._generate_knowledge_subtitle_audio_file(
                            knowledge_archive_id=archive_id,
                            scene_subtitles=persisted_scene_texts,
                            subtitle_language=normalized_request.subtitle_languages[0],
                        )
                    )
                    current_node_key = subtitle_audio_stage.node_key
                    current_node_title = subtitle_audio_stage.node_title
                    yield build_stage_chunk(
                        template_type=normalized_request.template_type,
                        descriptor=subtitle_audio_stage,
                        node_status="ready",
                        topic=topic,
                        archive_id=stage_archive_id,
                        view_type=stream_view_type,
                    )
                    yield build_stage_chunk(
                        template_type=normalized_request.template_type,
                        descriptor=subtitle_audio_stage,
                        node_status="processing",
                        topic=topic,
                        archive_id=stage_archive_id,
                        view_type=stream_view_type,
                    )
                # 执行await knowledge subtitle audio task相关逻辑。
                current_node_key = subtitle_audio_stage.node_key
                current_node_title = subtitle_audio_stage.node_title
                subtitle_audio_result = await subtitle_audio_task
                subtitle_audio_name = subtitle_audio_result.audio_name
                timeline_scene_texts = subtitle_audio_result.scene_subtitles
                total_duration_ms = subtitle_audio_result.total_duration_ms
                # 执行update knowledge archive timeline data相关逻辑。
                self.dynamic_view_knowledge_repository.update_archive_timeline_data(
                    db,
                    archive_id=archive_id,
                    scene_subtitles=timeline_scene_texts,
                    total_duration_ms=total_duration_ms,
                )
                # 执行load owner scene subtitles相关逻辑。
                timeline_scene_texts = self._load_owner_scene_subtitles(
                    owner_type="knowledge",
                    owner_id=archive_id,
                )
                # 执行resolve dynamic durations from scene texts相关逻辑。
                dynamic_durations = resolve_dynamic_durations_from_scene_texts(
                    timeline_scene_texts
                )
                # 执行sum相关逻辑。
                total_duration_ms = sum(dynamic_durations)
                # 执行inject dynamic durations into timeline code相关逻辑。
                timeline_code = inject_dynamic_durations_into_timeline_code(
                    timeline_code,
                    dynamic_durations,
                )
                yield build_stage_chunk(
                    template_type=normalized_request.template_type,
                    descriptor=subtitle_audio_stage,
                    node_status="completed",
                    topic=topic,
                    archive_id=stage_archive_id,
                    view_type=stream_view_type,
                )
                self.dynamic_view_knowledge_repository.update_archive_subtitle_audio(
                    db,
                    archive_id=archive_id,
                    subtitle_audio_name=subtitle_audio_name,
                    subtitle_audio_volume=subtitle_audio_volume,
                )
            else:
                self.dynamic_view_game_repository.update_archive_metadata(
                    db,
                    archive_id=archive_id,
                    topic=masked_topic,
                    subtitle=ready_metadata.subtitle,
                    detail=ready_metadata.detail,
                    summary=ready_metadata.summary,
                    subject_type=ready_metadata.subject_type,
                )
                self.dynamic_view_clue_repository.replace_game_clues(
                    db,
                    game_archive_id=archive_id,
                    clues=clues,
                )
                self.dynamic_view_game_repository.update_archive_clue_count(
                    db,
                    archive_id=archive_id,
                    clue_count=len(clues),
                )
            if not is_knowledge_flow and character_task is None:
                # 执行start archive character task相关逻辑。
                character_task = self._start_archive_character_task(
                    owner_id=archive_id,
                    topic=topic,
                    subject_type=ready_metadata.subject_type,
                    prompt_version=flow_prompt_version,
                    model_level=normalized_request.model_level,
                )
            if character_task is not None:
                # 视图进入 ready 前先等角色落库完成；角色失败只记录，不阻断主视图产出。
                try:
                    # 执行await相关逻辑。
                    await character_task
                except Exception:
                    logger.exception(
                        "Dynamic view character generation failed but game render continues: topic=%s archive_id=%s",
                        topic,
                        archive_id,
                    )
            final_html = assemble_dynamic_view_html(
                timeline_code,
                dynamic_css_code,
                theme_colors,
                template_type=normalized_request.template_type,
                prompt_version=flow_prompt_version,
            )
            archive_audio_config = self._build_archive_audio_config(
                view_type="knowledge" if is_knowledge_flow else "game",
                archive_id=archive_id,
                audio_name=selected_audio_name,
                audio_start_time=selected_audio_start_time,
                audio_end_time=selected_audio_end_time,
                audio_volume=selected_audio_volume,
            )
            archive_subtitle_audio_config = (
                self._build_subtitle_audio_config(
                    archive_id=archive_id,
                    subtitle_audio_name=subtitle_audio_name,
                    subtitle_audio_volume=subtitle_audio_volume,
                )
                if is_knowledge_flow
                else None
            )
            # 执行inject dynamic view audio config相关逻辑。
            final_html = inject_dynamic_view_audio_config(
                final_html,
                audio=archive_audio_config,
                subtitle_audio=archive_subtitle_audio_config,
            )
            current_node_key = final_html_stage.node_key
            current_node_title = final_html_stage.node_title
            yield build_stage_chunk(
                template_type=normalized_request.template_type,
                descriptor=final_html_stage,
                node_status="ready",
                topic=topic,
                archive_id=stage_archive_id,
                view_type=stream_view_type,
            )
            yield build_stage_chunk(
                template_type=normalized_request.template_type,
                descriptor=final_html_stage,
                node_status="processing",
                topic=topic,
                archive_id=stage_archive_id,
                view_type=stream_view_type,
            )
            final_html_relative_path = write_dynamic_view_html_file(
                view_type="knowledge" if is_knowledge_flow else "game",
                archive_id=archive_id,
                html=final_html,
            )
            if is_knowledge_flow:
                self.dynamic_view_knowledge_repository.update_archive_render_result(
                    db,
                    archive_id=archive_id,
                    html_relative_path=final_html_relative_path,
                    status="ready",
                )
            else:
                self.dynamic_view_game_repository.update_archive_render_result(
                    db,
                    archive_id=archive_id,
                    html_relative_path=final_html_relative_path,
                    status="ready",
                )
            knowledge_generation_status = "idle"
            if resolved_flow_version == 1:
                try:
                    # 执行trigger knowledge generation相关逻辑。
                    self._trigger_knowledge_generation(
                        db,
                        archive_id=archive_id,
                    )
                    knowledge_generation_status = "processing"
                except Exception:
                    # 执行exception相关逻辑。
                    logger.exception(
                        "Dynamic view knowledge generation trigger failed after game ready: archive_id=%s",
                        archive_id,
                    )
            if is_knowledge_flow:
                final_payload = build_payload(
                    topic=masked_topic,
                    title=ready_metadata.subtitle,
                    view_type="knowledge",
                    template_type=normalized_request.template_type,
                    status="ready",
                    preview_text="知识动态视图已生成完成。",
                    knowledge_view_id=archive_id,
                    summary=ready_metadata.summary,
                    subject_parent_type=ready_metadata.subject_parent_type,
                    subject_type=ready_metadata.subject_type,
                    detail=ready_metadata.detail,
                    scene_subtitles=timeline_scene_texts,
                    total_duration_ms=total_duration_ms,
                    html_url=self._build_archive_html_url(
                        view_type="knowledge",
                        archive_id=archive_id,
                        html_relative_path=final_html_relative_path,
                    ),
                    audio=archive_audio_config,
                    subtitle_audio=archive_subtitle_audio_config,
                )
            else:
                final_payload = build_payload(
                    topic=masked_topic,
                    title=ready_metadata.subtitle,
                    view_type="game",
                    template_type=normalized_request.template_type,
                    status="ready",
                    preview_text="游戏动态视图已生成完成。",
                    game_view_id=archive_id,
                    knowledge_generation_status=knowledge_generation_status,
                    knowledge_ready=False,
                    summary=ready_metadata.summary,
                    subject_parent_type=ready_metadata.subject_parent_type,
                    subject_type=ready_metadata.subject_type,
                    detail=ready_metadata.detail,
                    final_question=final_question,
                    clues=clues,
                    current_unlocked_clue_count=0,
                    total_clue_count=len(clues),
                    all_clues_unlocked=False,
                    view_count=0,
                    comment_count=0,
                    scene_subtitles=timeline_scene_texts,
                    total_duration_ms=total_duration_ms,
                    html_url=self._build_archive_html_url(
                        view_type="game",
                        archive_id=archive_id,
                        html_relative_path=final_html_relative_path,
                    ),
                    audio=archive_audio_config,
                )
            yield DynamicViewStreamChunk(
                payload=final_payload,
                is_final=True,
                stage="completed",
                node_key=final_html_stage.node_key,
                node_title=final_html_stage.node_title,
                node_status="completed",
            )
        except asyncio.CancelledError:
            await self._cancel_background_tasks(
                topic=topic,
                tasks=[
                    character_task,
                    game_metadata_task,
                    metadata_only_task,
                    knowledge_detail_task,
                    subtitle_audio_task,
                ],
            )
            logger.info("Dynamic view generation cancelled by client: topic=%s", topic)
            raise
        except Exception as error:
            await self._cancel_background_tasks(
                topic=topic,
                tasks=[
                    character_task,
                    game_metadata_task,
                    metadata_only_task,
                    knowledge_detail_task,
                    subtitle_audio_task,
                ],
            )
            logger.exception("Dynamic view generation failed: topic=%s", topic)
            error_request_id = uuid4().hex
            if task_id:
                try:
                    # 执行get generation task snapshot相关逻辑。
                    task_snapshot = self.get_generation_task_snapshot(task_id)
                    error_request_id = task_snapshot.request_id or error_request_id
                except Exception:
                    task_snapshot = None
            else:
                task_snapshot = None
            self._record_generation_error_log(
                task_id=task_id or "",
                request_id=error_request_id,
                user_id=normalized_request.author_id,
                topic=topic,
                stage=task_snapshot.stage if task_snapshot is not None else "failed",
                node_key=current_node_key,
                node_title=current_node_title,
                error=error,
            )
            if archive_id is not None:
                try:
                    if is_knowledge_flow:
                        # 执行delete knowledge archive data相关逻辑。
                        self._delete_knowledge_archive_data(
                            db,
                            knowledge_archive_id=archive_id,
                        )
                    else:
                        # 执行delete game archive data相关逻辑。
                        self._delete_game_archive_data(
                            db,
                            archive_id=archive_id,
                        )
                    archive_id = None
                except Exception:
                    # 执行exception相关逻辑。
                    logger.exception(
                        "Dynamic view archive cleanup failed: topic=%s", topic
                    )
            yield DynamicViewStreamChunk(
                payload=build_payload(
                    topic=topic,
                    view_type=stream_view_type,
                    template_type=normalized_request.template_type,
                    status="failed",
                    preview_text=(
                        str(error)
                        if str(error).strip() == "当前模型暂不可用"
                        else self._build_public_generation_error_message(
                            task_id=task_id or "",
                            request_id=error_request_id,
                        )
                    ),
                    game_view_id=archive_id if not is_knowledge_flow else None,
                    knowledge_view_id=archive_id if is_knowledge_flow else None,
                    summary="动态视图生成失败。",
                    subject_parent_type=resolve_dynamic_view_subject_parent_type(""),
                    subject_type=infer_subject_type(topic),
                ),
                is_final=True,
                stage="failed",
                node_key=current_node_key,
                node_title=current_node_title,
                node_status="failed",
            )

    # 执行generate knowledge view background相关逻辑。
    async def _generate_knowledge_view_background(self, *, archive_id: int) -> None:
        """在后台异步生成知识动态视图，并回写游戏主表关联。"""
        with self.session_factory() as db:
            (
                selected_audio_name,
                selected_audio_start_time,
                selected_audio_end_time,
                selected_audio_volume,
            ) = self._select_dynamic_view_audio()
            # 执行get archive or raise相关逻辑。
            game_archive = self.dynamic_view_game_repository.get_archive_or_raise(db, archive_id)
            existing_knowledge_archive = self.dynamic_view_knowledge_repository.get_by_game_archive_id(
                db,
                game_archive_id=archive_id,
            )
            if existing_knowledge_archive is None:
                knowledge_archive_id = self.dynamic_view_knowledge_repository.create_archive(
                    db,
                    game_archive_id=archive_id,
                    author_id=game_archive.author_id,
                    template_type=game_archive.template_type,
                    topic=game_archive.source_topic,
                    status="processing",
                    audio_name=selected_audio_name,
                    audio_start_time=selected_audio_start_time,
                    audio_end_time=selected_audio_end_time,
                    audio_volume=selected_audio_volume,
                    source_type=game_archive.source_type,
                    source_model=game_archive.source_model,
                    type_code=game_archive.type_code,
                )
            else:
                knowledge_archive_id = int(existing_knowledge_archive.id)
                if not existing_knowledge_archive.audio_name.strip():
                    self.dynamic_view_knowledge_repository.update_archive_audio(
                        db,
                        archive_id=knowledge_archive_id,
                        audio_name=selected_audio_name,
                        audio_start_time=selected_audio_start_time,
                        audio_end_time=selected_audio_end_time,
                        audio_volume=selected_audio_volume,
                    )
                self.dynamic_view_knowledge_repository.update_archive_status(
                    db,
                    archive_id=knowledge_archive_id,
                    status="processing",
                )
            self.dynamic_view_game_repository.bind_knowledge_archive(
                db,
                archive_id=archive_id,
                knowledge_archive_id=knowledge_archive_id,
                knowledge_generation_status="processing",
            )
        try:
            knowledge_payload = await self._generate_knowledge_view_payload(
                archive_id=archive_id,
                knowledge_archive_id=knowledge_archive_id,
            )
            with self.session_factory() as db:
                self.dynamic_view_game_repository.bind_knowledge_archive(
                    db,
                    archive_id=archive_id,
                    knowledge_archive_id=knowledge_payload.knowledge_view_id
                    or knowledge_archive_id,
                    knowledge_generation_status="ready",
                )
        except Exception:
            logger.exception(
                "Dynamic view knowledge generation failed: archive_id=%s",
                archive_id,
            )
            with self.session_factory() as db:
                self._delete_knowledge_archive_data(
                    db,
                    knowledge_archive_id=knowledge_archive_id,
                )
                self.dynamic_view_game_repository.bind_knowledge_archive(
                    db,
                    archive_id=archive_id,
                    knowledge_archive_id=None,
                    knowledge_generation_status="idle",
                )
            raise

    # 执行generate knowledge view payload相关逻辑。
    async def _generate_knowledge_view_payload(
        self,
        *,
        archive_id: int,
        knowledge_archive_id: int,
    ) -> DynamicViewPayload:
        """生成知识动态视图并直接落库。"""
        with self.session_factory() as db:
            # 执行get archive or raise相关逻辑。
            game_archive = self.dynamic_view_game_repository.get_archive_or_raise(db, archive_id)
        model_level = game_archive.source_model
        theme_colors = extract_theme_colors_from_node1_output("", None)
        node1_clean_text = game_archive.source_topic
        node1_timeline_code = ""
        node1_dynamic_css_code = ""
        node1_messages = build_node1_prompt(
            game_archive.source_topic,
            game_archive.scene_count_min,
            script_mode=DynamicViewScriptMode.KNOWLEDGE,
            prompt_version=1,
        )
        # 执行run prompt without document api相关逻辑。
        node1_raw_output = await self._run_prompt_without_document_api(
            model_level=model_level,
            runner_role="node1",
            messages=node1_messages,
            stage_name="dynamic_view_knowledge_node1",
        )
        # 执行try parse structured document相关逻辑。
        node1_structured_output = try_parse_structured_document(node1_raw_output)
        # 执行clean model output text相关逻辑。
        node1_clean_text = clean_model_output_text(node1_raw_output)
        # 执行extract timeline assets from node1 output相关逻辑。
        node1_timeline_code, node1_dynamic_css_code = (
            extract_timeline_assets_from_node1_output(node1_raw_output)
        )
        timeline_code = ""
        dynamic_css_code = ""
        timeline_scene_texts: list[dict[str, str]] = []
        total_duration_ms = 0
        node2_history: list[HistoryItem] = []
        # 执行generate node2 text without document api相关逻辑。
        node2_step1_raw_output, node2_history = await self._generate_node2_text_without_document_api(
            model_level=model_level,
            history=node2_history,
            prompt=build_node2_step1_prompt(
                node1_clean_text=node1_clean_text,
                topic=game_archive.source_topic,
                scene_count_min=game_archive.scene_count_min,
                script_mode=DynamicViewScriptMode.KNOWLEDGE,
                prompt_version=1,
            ),
            stage_name="dynamic_view_knowledge_node2_timeline_script",
        )
        # 执行extract code block text相关逻辑。
        timeline_code = sanitize_timeline_text_literals(
            extract_code_block_text(node2_step1_raw_output)
        )
        if not _contains_timeline_data_assignment(timeline_code):
            raise RuntimeError("知识视图 node2 未输出有效 TimelineData。")
        # 执行extract scene texts from timeline code相关逻辑。
        timeline_scene_texts = extract_scene_texts_from_timeline_code(timeline_code)
        # 执行calculate dynamic durations相关逻辑。
        dynamic_durations = calculate_dynamic_durations(timeline_scene_texts)
        # 执行sum相关逻辑。
        total_duration_ms = sum(dynamic_durations)
        # 执行generate node2 text without document api相关逻辑。
        node2_step2_raw_output, _ = await self._generate_node2_text_without_document_api(
            model_level=model_level,
            history=node2_history,
            prompt=build_node2_step2_prompt(
                node2_step1_output=timeline_code,
                topic=game_archive.source_topic,
                scene_count_min=game_archive.scene_count_min,
                script_mode=DynamicViewScriptMode.KNOWLEDGE,
                prompt_version=1,
            ),
            stage_name="dynamic_view_knowledge_node2_dynamic_css",
        )
        # 执行extract code block text相关逻辑。
        dynamic_css_code = extract_code_block_text(node2_step2_raw_output)
        if not dynamic_css_code.strip() and node1_dynamic_css_code.strip():
            dynamic_css_code = node1_dynamic_css_code
        if not _contains_timeline_data_assignment(timeline_code):
            raise RuntimeError("知识动态视图时间轴脚本为空，无法注入 MODULE_TIMELINE_START。")
        with self.session_factory() as db:
            self.dynamic_view_knowledge_repository.update_archive_timeline_data(
                db,
                archive_id=knowledge_archive_id,
                scene_subtitles=timeline_scene_texts,
                total_duration_ms=total_duration_ms,
            )
        persisted_scene_texts = self._load_owner_scene_subtitles(
            owner_type="knowledge",
            owner_id=knowledge_archive_id,
        )
        subtitle_audio_result = await self._generate_knowledge_subtitle_audio_file(
            knowledge_archive_id=knowledge_archive_id,
            scene_subtitles=persisted_scene_texts,
            subtitle_language="zh",
        )
        subtitle_audio_name = subtitle_audio_result.audio_name
        persisted_scene_texts = subtitle_audio_result.scene_subtitles
        total_duration_ms = subtitle_audio_result.total_duration_ms
        with self.session_factory() as db:
            self.dynamic_view_knowledge_repository.update_archive_timeline_data(
                db,
                archive_id=knowledge_archive_id,
                scene_subtitles=persisted_scene_texts,
                total_duration_ms=total_duration_ms,
            )
        persisted_scene_texts = self._load_owner_scene_subtitles(
            owner_type="knowledge",
            owner_id=knowledge_archive_id,
        )
        dynamic_durations = resolve_dynamic_durations_from_scene_texts(persisted_scene_texts)
        total_duration_ms = sum(dynamic_durations)
        # 执行inject dynamic durations into timeline code相关逻辑。
        timeline_code = inject_dynamic_durations_into_timeline_code(
            timeline_code,
            dynamic_durations,
        )
        metadata = await self._generate_metadata_only(
            topic=game_archive.source_topic,
            scene_texts=persisted_scene_texts,
            prompt_version=1,
            model_level=model_level,
        )
        knowledge_detail = self._build_knowledge_detail_from_scene_texts(
            persisted_scene_texts,
            fallback_text=node1_clean_text,
        )
        theme_colors = extract_theme_colors_from_node1_output(
            node1_raw_output,
            node1_structured_output,
        )
        final_html = assemble_dynamic_view_html(
            timeline_code,
            dynamic_css_code,
            theme_colors,
            template_type=game_archive.template_type,
            prompt_version=1,
        )
        characters = await self._generate_characters_with_retry(
            topic=game_archive.source_topic,
            scene_texts=persisted_scene_texts,
            subject_type=metadata.subject_type,
            prompt_version=1,
            model_level=model_level,
        )
        with self.session_factory() as db:
            previous_subtitle_audio_name = (
                self.dynamic_view_knowledge_repository.get_archive_or_raise(
                    db,
                    knowledge_archive_id,
                ).subtitle_audio_name.strip()
            )
            self.dynamic_view_knowledge_repository.update_archive_subtitle_audio(
                db,
                archive_id=knowledge_archive_id,
                subtitle_audio_name=subtitle_audio_name,
                subtitle_audio_volume=100,
            )
            if (
                previous_subtitle_audio_name
                and previous_subtitle_audio_name != subtitle_audio_name
            ):
                self._delete_dynamic_view_subtitle_audio_file(previous_subtitle_audio_name)
            self.dynamic_view_knowledge_repository.update_archive_metadata(
                db,
                archive_id=knowledge_archive_id,
                subtitle=metadata.subtitle,
                detail=knowledge_detail,
                summary=metadata.summary,
                subject_type=metadata.subject_type,
            )
            current_archive = self.dynamic_view_knowledge_repository.get_archive_or_raise(
                db,
                knowledge_archive_id,
            )
            archive_audio_config = self._build_archive_audio_config(
                view_type="knowledge",
                archive_id=knowledge_archive_id,
                audio_name=current_archive.audio_name,
                audio_start_time=current_archive.audio_start_time,
                audio_end_time=current_archive.audio_end_time,
                audio_volume=current_archive.audio_volume,
            )
            archive_subtitle_audio_config = self._build_subtitle_audio_config(
                archive_id=knowledge_archive_id,
                subtitle_audio_name=current_archive.subtitle_audio_name,
                subtitle_audio_volume=current_archive.subtitle_audio_volume,
            )
            # 执行inject dynamic view audio config相关逻辑。
            final_html = inject_dynamic_view_audio_config(
                final_html,
                audio=archive_audio_config,
                subtitle_audio=archive_subtitle_audio_config,
            )
            final_html_relative_path = write_dynamic_view_html_file(
                view_type="knowledge",
                archive_id=knowledge_archive_id,
                html=final_html,
            )
            self.dynamic_view_knowledge_repository.update_archive_render_result(
                db,
                archive_id=knowledge_archive_id,
                html_relative_path=final_html_relative_path,
                status="ready",
            )
            self.dynamic_view_character_repository.replace_archive_characters(
                db,
                owner_type="knowledge",
                owner_id=knowledge_archive_id,
                characters=characters,
            )
        return build_payload(
            topic=game_archive.source_topic,
            title=metadata.subtitle,
            view_type="knowledge",
            template_type=current_archive.template_type,
            status="ready",
            preview_text=f"《{game_archive.source_topic}》知识动态视图已生成完成。",
            knowledge_view_id=knowledge_archive_id,
            summary=metadata.summary,
            subject_parent_type=metadata.subject_parent_type,
            subject_type=metadata.subject_type,
            detail=knowledge_detail,
            scene_subtitles=timeline_scene_texts,
            total_duration_ms=current_archive.total_duration_ms,
            html_url=self._build_archive_html_url(
                view_type="knowledge",
                archive_id=knowledge_archive_id,
                html_relative_path=final_html_relative_path,
            ),
            audio=archive_audio_config,
            subtitle_audio=archive_subtitle_audio_config,
        )


    # 执行handle generation task done相关逻辑。
    def _handle_generation_task_done(
        self,
        *,
        task_id: str,
        task: asyncio.Task[None],
    ) -> None:
        """后台任务结束后移除运行句柄，并补充记录未捕获异常。"""
        self._generation_tasks.pop(task_id, None)
        source_task_record_id = self._generation_task_source_record_ids.pop(task_id, None)
        snapshot = self._generation_task_snapshots.get(task_id)
        if source_task_record_id is not None and snapshot is not None:
            with self.session_factory() as db:
                if snapshot.payload.status == "ready":
                    self.dynamic_view_source_task_repository.mark_task_completed(
                        db,
                        task_record_id=source_task_record_id,
                        game_archive_id=snapshot.payload.game_view_id,
                        knowledge_archive_id=snapshot.payload.knowledge_view_id,
                    )
                else:
                    self.dynamic_view_source_task_repository.mark_task_failed(
                        db,
                        task_record_id=source_task_record_id,
                        game_archive_id=snapshot.payload.game_view_id,
                        knowledge_archive_id=snapshot.payload.knowledge_view_id,
                        error_message=snapshot.message.strip() or "动态视图生成失败",
                    )
        if task.cancelled():
            return
        try:
            # 执行result相关逻辑。
            task.result()
        except Exception:
            # 执行exception相关逻辑。
            logger.exception(
                "Dynamic view background task completed with exception: task_id=%s",
                task_id,
            )
        finally:
            if snapshot is not None and snapshot.payload.status == "ready":
                # 执行clear generation task snapshot related logic。
                self._clear_generation_task_snapshot(task_id)

    # 执行handle knowledge generation done相关逻辑。
    def _handle_knowledge_generation_done(
        self,
        *,
        archive_id: int,
        task: asyncio.Task[None],
    ) -> None:
        """知识动态视图后台任务结束后移除运行句柄，并补充记录未捕获异常。"""
        self._knowledge_generation_tasks.pop(archive_id, None)
        if task.cancelled():
            return
        try:
            # 执行result相关逻辑。
            task.result()
        except Exception:
            # 执行exception相关逻辑。
            logger.exception(
                "Dynamic view knowledge background task completed with exception: archive_id=%s",
                archive_id,
            )

    # 执行persist task snapshot相关逻辑。
    def _persist_task_snapshot(
        self,
        snapshot: DynamicViewTaskSnapshot,
    ) -> None:
        """把最新任务快照同步写入数据库，避免 Python 进程重启后轮询和取消直接失效。"""
        with self.session_factory() as db:
            # 执行save task snapshot相关逻辑。
            self.dynamic_view_task_repository.save_task_snapshot(
                db,
                snapshot=snapshot,
            )

    # 执行load game clue context相关逻辑。
    def _load_game_clue_context(
        self,
        db: Session,
        *,
        archive_id: int,
        user_id: str | None,
        require_ready_archive: bool = False,
        increase_view_count: bool = False,
    ) -> _DynamicViewGameClueContext:
        """统一装配游戏动态视图存档、线索列表和当前用户在该视图里的点亮状态。"""
        normalized_user_id = user_id.strip() if user_id else ""
        if require_ready_archive:
            archive = self.dynamic_view_game_repository.get_ready_archive_detail(
                db,
                archive_id=archive_id,
                increase_view_count=increase_view_count,
            )
        else:
            archive = self.dynamic_view_game_repository.get_archive_or_raise(
                db,
                archive_id,
            )
        clues = self.dynamic_view_clue_repository.list_game_clues(
            db,
            game_archive_id=int(archive.id),
        )
        unlocked_clue_keys: set[str] = set()
        unlocked_clue_steps: dict[str, int] = {}
        if normalized_user_id:
            unlocked_clue_keys = self.dynamic_view_progress_repository.list_unlocked_clue_keys(
                db,
                game_archive_id=int(archive.id),
                user_id=normalized_user_id,
            )
            unlocked_clue_steps = self.dynamic_view_progress_repository.list_unlocked_clue_steps(
                db,
                game_archive_id=int(archive.id),
                user_id=normalized_user_id,
            )
        return _DynamicViewGameClueContext(
            archive=archive,
            clues=clues,
            unlocked_clue_keys=unlocked_clue_keys,
            unlocked_clue_steps=unlocked_clue_steps,
        )

    # 执行ensure archive commentable相关逻辑。
    def _ensure_archive_commentable(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
    ) -> None:
        """确认当前动态视图可评论。"""
        if view_type == "knowledge":
            self.dynamic_view_knowledge_repository.get_ready_archive_detail(
                db,
                archive_id=archive_id,
                increase_view_count=False,
            )
            return
        self.dynamic_view_game_repository.get_ready_archive_detail(
            db,
            archive_id=archive_id,
            increase_view_count=False,
        )

    # 执行sync archive comment count相关逻辑。
    def _sync_archive_comment_count(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
        stored_comment_count: int | None,
    ) -> tuple[int, bool]:
        """统一回写评论总数，并返回是否发生过更新。"""
        comment_count = self.dynamic_view_comment_repository.count_archive_comments(
            db,
            archive_id=archive_id,
            view_type=view_type,
        )
        if stored_comment_count == comment_count:
            return comment_count, False
        if view_type == "knowledge":
            self.dynamic_view_knowledge_repository.update_archive_comment_count(
                db,
                archive_id=archive_id,
                comment_count=comment_count,
            )
        else:
            self.dynamic_view_game_repository.update_archive_comment_count(
                db,
                archive_id=archive_id,
                comment_count=comment_count,
            )
        return comment_count, True

    # 执行run prompt相关逻辑。
    async def _run_prompt(
        self,
        *,
        runner: PromptRunner,
        messages: Sequence[BaseMessage],
        stage_name: str,
    ) -> str:
        """执行一次标准 messages 请求。"""
        return await runner.run_text_messages(messages, stage_name=stage_name)

    # 执行run prompt without document api相关逻辑。
    async def _run_prompt_without_document_api(
        self,
        *,
        model_level: str,
        runner_role: str,
        messages: Sequence[BaseMessage],
        stage_name: str,
    ) -> str:
        """执行文本模型请求，命中 document API 时重新生成。"""
        model_levels = _resolve_model_retry_levels(model_level) if runner_role == "node1" else [model_level]
        last_error: Exception | None = None
        for current_model_level in model_levels:
            attempt_index = 0
            while True:
                # 执行build latest runtime runner相关逻辑。
                runner = self._build_latest_runtime_runner(current_model_level, runner_role)
                try:
                    # 执行run prompt相关逻辑。
                    raw_output = await self._run_prompt(
                        runner=runner,
                        messages=messages,
                        stage_name=stage_name,
                    )
                    # 执行raise if model output contains document api相关逻辑。
                    _raise_if_model_output_contains_document_api(raw_output, stage_name)
                    return raw_output
                except ModelOutputDocumentApiError:
                    logger.warning(
                        "Dynamic view model output contains document API: stage=%s | modelLevel=%s | attempt=%s/%s",
                        stage_name,
                        current_model_level,
                        attempt_index + 1,
                        _DOCUMENT_API_RETRY_COUNT + 1,
                    )
                    if attempt_index >= _DOCUMENT_API_RETRY_COUNT:
                        raise
                    attempt_index += 1
                except Exception as error:
                    last_error = error
                    _log_model_level_failover_warning(
                        node_key=runner_role,
                        model_level=current_model_level,
                        error=error,
                    )
                    break
                finally:
                    # 执行close runtime model clients相关逻辑。
                    await self._close_runtime_model_clients(runner)
        raise last_error if last_error is not None else RuntimeError("Dynamic view prompt request failed")

    # 执行run prompt stream相关逻辑。
    async def _run_prompt_stream(
        self,
        *,
        runner: PromptRunner,
        messages: Sequence[BaseMessage],
        stage_name: str,
    ):
        """执行一次流式 messages 请求，逐段返回模型输出增量。"""
        async for chunk_text in runner.run_text_messages_stream(
            messages,
            stage_name=stage_name,
        ):
            yield chunk_text

    # 执行generate node2 text without document api相关逻辑。
    async def _generate_node2_text_without_document_api(
        self,
        *,
        model_level: str,
        history: Sequence[HistoryItem],
        prompt: str,
        stage_name: str,
    ) -> tuple[str, list[HistoryItem]]:
        """执行 node2 会话请求，命中 document API 时重新生成。"""
        last_error: Exception | None = None
        for current_model_level in _resolve_model_retry_levels(model_level):
            attempt_index = 0
            while True:
                # 执行build latest runtime node2 client相关逻辑。
                node2_client = self._build_latest_runtime_node2_client(current_model_level)
                # 执行create session相关逻辑。
                node2_session = node2_client.create_session(list(history))
                try:
                    # 执行generate text相关逻辑。
                    raw_output = await node2_session.generate_text(prompt, stage_name)
                    # 执行raise if model output contains document api相关逻辑。
                    _raise_if_model_output_contains_document_api(raw_output, stage_name)
                    # 执行snapshot history相关逻辑。
                    return raw_output, node2_session.snapshot_history()
                except ModelOutputDocumentApiError:
                    logger.warning(
                        "Dynamic view node2 output contains document API: stage=%s | modelLevel=%s | attempt=%s/%s",
                        stage_name,
                        current_model_level,
                        attempt_index + 1,
                        _DOCUMENT_API_RETRY_COUNT + 1,
                    )
                    if attempt_index >= _DOCUMENT_API_RETRY_COUNT:
                        raise
                    attempt_index += 1
                except Exception as error:
                    last_error = error
                    _log_model_level_failover_warning(
                        node_key="node2",
                        model_level=current_model_level,
                        error=error,
                    )
                    break
                finally:
                    # 执行aclose相关逻辑。
                    await node2_session.aclose()
                    # 执行close runtime model clients相关逻辑。
                    await self._close_runtime_model_clients(node2_client)
        raise last_error if last_error is not None else RuntimeError("Dynamic view node2 request failed")

    # 执行build fallback metadata相关逻辑。
    def _build_fallback_metadata(
        self,
        *,
        topic: str,
        scene_texts: list[dict[str, str]],
        subject_type: str | None = None,
    ) -> DynamicViewMetadata:
        """在 metadata 节点缺失时构造最小可用元数据。"""
        normalized_topic = topic.strip() or "当前主题"
        resolved_subject_parent_type, resolved_subject_type = infer_subject_taxonomy(
            normalized_topic
        )
        if (subject_type or "").strip():
            resolved_subject_type = validate_dynamic_view_subject_type(subject_type)
            resolved_subject_parent_type = resolve_dynamic_view_subject_parent_type(
                resolved_subject_type
            )
        detail_source = " ".join(
            scene_text.get("vivid", "").strip() for scene_text in scene_texts if scene_text.get("vivid")
        ).strip()
        return DynamicViewMetadata(
            subtitle=normalized_topic[:28] or "动态视图",
            detail=(detail_source[:1200] or f"《{normalized_topic}》动态视图内容。"),
            summary=normalized_topic[:60] or "动态视图",
            subject_parent_type=resolved_subject_parent_type,
            subject_type=resolved_subject_type,
        )

    # 执行generate metadata only相关逻辑。
    async def _generate_metadata_only(
        self,
        *,
        topic: str,
        scene_texts: list[dict[str, str]],
        request: DynamicViewCreateRequest | None = None,
        prompt_version: int,
        model_level: str,
    ) -> DynamicViewMetadata:
        """只生成元数据结果，不直接写库。"""
        metadata_messages = build_metadata_prompt(
            topic,
            scene_texts,
            request=request,
            prompt_version=prompt_version,
        )
        metadata_runner = self._build_latest_runtime_runner(model_level, "metadata")
        try:
            return await metadata_runner.run_structured_messages(
                metadata_messages,
                schema=DynamicViewMetadata,
                stage_name="dynamic_view_metadata",
            )
        finally:
            # 执行close runtime model clients相关逻辑。
            await self._close_runtime_model_clients(metadata_runner)

    # 执行generate knowledge detail相关逻辑。
    async def _generate_knowledge_detail(
        self,
        *,
        topic: str,
        scene_texts: list[dict[str, str]],
        request: DynamicViewCreateRequest | None = None,
        prompt_version: int,
        model_level: str,
    ) -> DynamicViewKnowledgeDetail:
        """生成知识视图详情讲解，不直接写库。"""
        # 执行build knowledge detail prompt相关逻辑。
        detail_messages = build_knowledge_detail_prompt(
            topic=topic,
            scene_texts=scene_texts,
            request=request,
            prompt_version=prompt_version,
        )
        metadata_runner = self._build_latest_runtime_runner(model_level, "metadata")
        try:
            return await metadata_runner.run_structured_messages(
                detail_messages,
                schema=DynamicViewKnowledgeDetail,
                stage_name="dynamic_view_knowledge_detail",
            )
        finally:
            # 执行close runtime model clients相关逻辑。
            await self._close_runtime_model_clients(metadata_runner)

    # 执行generate game metadata bundle with retry相关逻辑。
    async def _generate_game_metadata_bundle_with_retry(
        self,
        *,
        topic: str,
        scene_texts: list[dict[str, str]],
        final_question: str,
        request: DynamicViewCreateRequest | None = None,
        prompt_version: int,
        model_level: str,
    ) -> tuple[str, DynamicViewMetadata, list[DynamicViewClueItem]]:
        """从 scene_subtitles_json 生成游戏视图元数据与线索，并在空线索时重试。"""
        retry_count = 1
        game_metadata_messages = build_game_metadata_prompt(
            topic=topic,
            final_question=final_question,
            scene_texts=scene_texts,
            request=request,
            prompt_version=prompt_version,
        )
        for attempt_index in range(retry_count + 1):
            metadata_runner = self._build_latest_runtime_runner(model_level, "metadata")
            try:
                game_metadata_bundle = await metadata_runner.run_structured_messages(
                    game_metadata_messages,
                    schema=DynamicViewGameMetadataBundle,
                    stage_name="dynamic_view_game_metadata",
                )
                clues = self._normalize_game_metadata_clues(game_metadata_bundle.clues)
                if clues:
                    return (
                        game_metadata_bundle.topic,
                        DynamicViewMetadata(
                            subtitle=game_metadata_bundle.subtitle,
                            detail=game_metadata_bundle.detail,
                            summary=game_metadata_bundle.summary,
                            subject_parent_type=game_metadata_bundle.subject_parent_type,
                            subject_type=game_metadata_bundle.subject_type,
                        ),
                        clues,
                    )
                logger.warning(
                    "Dynamic view game metadata returned empty clues: topic=%s | attempt=%s/%s",
                    topic,
                    attempt_index + 1,
                    retry_count + 1,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Dynamic view game metadata generation failed: topic=%s | attempt=%s/%s",
                    topic,
                    attempt_index + 1,
                    retry_count + 1,
                )
            finally:
                # 执行close runtime model clients相关逻辑。
                await self._close_runtime_model_clients(metadata_runner)
            if attempt_index >= retry_count:
                break
        raise RuntimeError(f"动态视图元数据线索抽取失败：topic={topic}")

    # 执行generate and store characters相关逻辑。
    async def _generate_and_store_characters(
        self,
        *,
        owner_type: str,
        owner_id: int,
        topic: str,
        subject_type: str,
        prompt_version: int,
        model_level: str,
    ) -> list[DynamicViewCharacter]:
        """在 scene_subtitles_json 落库后异步生成角色清单，并直接写回角色表。"""
        scene_texts = self._load_owner_scene_subtitles(
            owner_type=owner_type,
            owner_id=owner_id,
        )
        characters = await self._generate_characters_with_retry(
            topic=topic,
            scene_texts=scene_texts,
            subject_type=subject_type,
            prompt_version=prompt_version,
            model_level=model_level,
        )
        with self.session_factory() as background_db:
            self.dynamic_view_character_repository.replace_archive_characters(
                background_db,
                owner_type=owner_type,
                owner_id=owner_id,
                characters=characters,
            )
        return characters

    # 执行generate characters with retry相关逻辑。
    async def _generate_characters_with_retry(
        self,
        *,
        topic: str,
        scene_texts: list[dict[str, str]],
        subject_type: str,
        prompt_version: int,
        model_level: str,
    ) -> list[DynamicViewCharacter]:
        """基于分镜文本一次性抽取角色列表，并统一规范关键字段。"""
        retry_count = 1
        character_messages = build_scene_character_prompt(
            topic=topic,
            subject_type=subject_type,
            scene_texts=scene_texts,
            prompt_version=prompt_version,
        )
        for attempt_index in range(retry_count + 1):
            character_runner = self._build_latest_runtime_runner(model_level, "character")
            try:
                generated_bundle = await character_runner.run_structured_messages(
                    character_messages,
                    schema=DynamicViewCharacterSceneBundle,
                    stage_name="dynamic_view_character",
                )
                normalized_characters = [
                    generated_character.model_copy(
                        update={
                            "category_name": generated_character.category_name.strip()
                            or subject_type,
                            "author": generated_character.author.strip() or "system",
                        }
                    )
                    for generated_character in generated_bundle.characters
                    if generated_character.role_name.strip()
                ]
                if normalized_characters:
                    return normalized_characters
                logger.warning(
                    "Dynamic view character generation returned empty result: attempt=%s/%s",
                    attempt_index + 1,
                    retry_count + 1,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Dynamic view character generation failed: attempt=%s/%s",
                    attempt_index + 1,
                    retry_count + 1,
                )
            finally:
                # 执行close runtime model clients相关逻辑。
                await self._close_runtime_model_clients(character_runner)
            if attempt_index >= retry_count:
                break
        logger.error(
            "Dynamic view character generation exhausted retries with empty result: topic=%s",
            topic,
        )
        # 角色抽取依赖外部模型，偶发空结果时不阻断主流程，统一降级为空角色列表。
        return []

    # 执行start archive character task相关逻辑。
    def _start_archive_character_task(
        self,
        *,
        owner_id: int,
        topic: str,
        subject_type: str,
        prompt_version: int,
        model_level: str,
    ) -> asyncio.Task[list[DynamicViewCharacter]] | None:
        """在 scene_subtitles_json 就绪后启动角色后处理任务。"""
        character_task = asyncio.create_task(
            self._generate_and_store_characters(
                owner_type="game",
                owner_id=owner_id,
                topic=topic,
                subject_type=subject_type,
                prompt_version=prompt_version,
                model_level=model_level,
            )
        )
        character_task.add_done_callback(
            lambda finished_task: _log_background_task_exception(
                finished_task,
                topic=topic,
                task_name="dynamic_view_character",
            )
        )
        return character_task

    # 执行delete game archive data相关逻辑。
    def _delete_game_archive_data(
        self,
        db: Session,
        *,
        archive_id: int,
    ) -> None:
        """删除失败游戏视图产生的主档、线索、进度、角色和关联知识视图，避免残留脏数据。"""
        game_archive = self.dynamic_view_game_repository.get_archive_or_raise(db, archive_id)
        knowledge_archive_id = game_archive.knowledge_archive_id
        if knowledge_archive_id is None:
            knowledge_archive = self.dynamic_view_knowledge_repository.get_by_game_archive_id(
                db,
                game_archive_id=archive_id,
            )
            if knowledge_archive is not None:
                knowledge_archive_id = int(knowledge_archive.id)
        if knowledge_archive_id is not None:
            # 执行delete knowledge archive data相关逻辑。
            self._delete_knowledge_archive_data(
                db,
                knowledge_archive_id=knowledge_archive_id,
            )
        self.dynamic_view_character_repository.delete_archive_characters(
            db,
            owner_type="game",
            owner_id=archive_id,
        )
        self.dynamic_view_progress_repository.delete_game_progress(
            db,
            game_archive_id=archive_id,
        )
        self.dynamic_view_clue_repository.delete_game_clues(
            db,
            game_archive_id=archive_id,
        )
        self.dynamic_view_game_repository.delete_archive(
            db,
            archive_id=archive_id,
        )

    # 执行delete knowledge archive data相关逻辑。
    def _delete_knowledge_archive_data(
        self,
        db: Session,
        *,
        knowledge_archive_id: int,
    ) -> None:
        """删除失败知识视图产生的主档和角色，避免游戏主表继续挂着无效知识视图。"""
        archive = self.dynamic_view_knowledge_repository.get_archive_or_raise(
            db,
            knowledge_archive_id,
        )
        self._delete_dynamic_view_subtitle_audio_file(archive.subtitle_audio_name)
        self.dynamic_view_character_repository.delete_archive_characters(
            db,
            owner_type="knowledge",
            owner_id=knowledge_archive_id,
        )
        self.dynamic_view_knowledge_repository.delete_archive(
            db,
            archive_id=knowledge_archive_id,
        )

    # 执行load owner scene subtitles相关逻辑。
    def _load_owner_scene_subtitles(
        self,
        *,
        owner_type: str,
        owner_id: int,
    ) -> list[dict[str, str]]:
        """从视图存档里的 scene_subtitles_json 读取标准分镜文本，作为角色与元数据生成的唯一输入。"""
        with self.session_factory() as db:
            if owner_type == "game":
                archive = self.dynamic_view_game_repository.get_archive_or_raise(db, owner_id)
            elif owner_type == "knowledge":
                archive = self.dynamic_view_knowledge_repository.get_archive_or_raise(
                    db,
                    owner_id,
                )
            else:
                raise ValueError(f"未知的动态视图归属类型：owner_type={owner_type}")
        raw_scene_subtitles = archive.scene_subtitles_json.strip()
        if not raw_scene_subtitles:
            return []
        try:
            loaded_scene_subtitles = json.loads(raw_scene_subtitles)
        except json.JSONDecodeError:
            logger.exception(
                "Dynamic view scene_subtitles_json decode failed: owner_type=%s owner_id=%s",
                owner_type,
                owner_id,
            )
            return []
        if not isinstance(loaded_scene_subtitles, list):
            return []
        normalized_scene_subtitles: list[dict[str, str]] = []
        for scene_subtitle in loaded_scene_subtitles:
            if not isinstance(scene_subtitle, dict):
                continue
            fallback_text = str(scene_subtitle.get("text", "")).strip()
            if fallback_text:
                normalized_scene_subtitle = {
                    "vivid": fallback_text,
                    "ext": fallback_text,
                }
                duration_ms = str(scene_subtitle.get("durationMs", "")).strip()
                if duration_ms:
                    normalized_scene_subtitle["durationMs"] = duration_ms
                normalized_scene_subtitles.append(normalized_scene_subtitle)
                continue
            normalized_scene_subtitles.append(
                {
                    field_name: str(scene_subtitle.get(field_name, "")).strip()
                    for field_name in ("vivid", "ext", "sub", "durationMs")
                    if str(scene_subtitle.get(field_name, "")).strip()
                }
            )
        return normalized_scene_subtitles

    # 执行build knowledge detail from scene texts相关逻辑。
    def _build_knowledge_detail_from_scene_texts(
        self,
        scene_texts: list[dict[str, str]],
        *,
        fallback_text: str = "",
    ) -> str:
        """把 knowledge 节点产出的分镜正文整理成知识详情主文本。"""
        detail_sections: list[str] = []
        for scene_text in scene_texts:
            scene_detail = str(scene_text.get("ext", "")).strip()
            if not scene_detail:
                scene_detail = str(scene_text.get("vivid", "")).strip()
            if scene_detail:
                detail_sections.append(scene_detail)
        if detail_sections:
            return "\n\n".join(detail_sections)[:1200].strip()
        return fallback_text.strip()[:1200].strip()

    # 执行generate knowledge subtitle audio file相关逻辑。
    async def _generate_knowledge_subtitle_audio_file(
        self,
        *,
        knowledge_archive_id: int,
        scene_subtitles: list[dict[str, str]],
        subtitle_language: str,
    ) -> KnowledgeSubtitleAudioResult:
        """按分镜字幕逐段合成知识视图字幕音频，并返回相对路径。"""
        # 执行to thread相关逻辑。
        return await asyncio.to_thread(
            self._generate_knowledge_subtitle_audio_file_sync,
            knowledge_archive_id=knowledge_archive_id,
            scene_subtitles=scene_subtitles,
            subtitle_language=subtitle_language,
        )

    # 执行resolve knowledge subtitle tts voice相关逻辑。
    def _resolve_knowledge_subtitle_tts_voice(self, subtitle_language: str) -> str:
        """根据主字幕语言选择字幕口播音色。"""
        normalized_languages = normalize_dynamic_view_subtitle_languages(
            [subtitle_language]
        )
        normalized_language = normalized_languages[0] if normalized_languages else "zh"
        return _DASHSCOPE_TTS_VOICE_BY_SUBTITLE_LANGUAGE[normalized_language]

    # 执行generate knowledge subtitle audio file sync相关逻辑。
    def _generate_knowledge_subtitle_audio_file_sync(
        self,
        *,
        knowledge_archive_id: int,
        scene_subtitles: list[dict[str, str]],
        subtitle_language: str,
    ) -> KnowledgeSubtitleAudioResult:
        """按 scratch.py 的分段 PCM 合成逻辑生成知识视图字幕音频。"""
        if not scene_subtitles:
            raise RuntimeError("知识视图分镜字幕为空，无法生成字幕音频。")
        # 执行resolve knowledge subtitle tts voice相关逻辑。
        subtitle_tts_voice = self._resolve_knowledge_subtitle_tts_voice(
            subtitle_language
        )
        api_key = self._resolve_dashscope_tts_api_key()
        dashscope.api_key = api_key
        total_vivid_chars = sum(
            len(str(scene_subtitle.get("vivid", "")).strip())
            for scene_subtitle in scene_subtitles
        )
        estimated_cost = (
            total_vivid_chars / 10000
        ) * _DASHSCOPE_TTS_PRICE_PER_10000_CHARS
        logger.info(
            "Knowledge subtitle TTS cost estimate: archiveId=%s voice=%s vividChars=%s estimatedCost=%.4f yuan",
            knowledge_archive_id,
            subtitle_tts_voice,
            total_vivid_chars,
            estimated_cost,
        )
        # 每段都按前端时间轴最小坑位补尾部静音，避免字幕口播跑得比动画快。
        combined_pcm = bytearray()
        timeline_alignment_report: list[dict[str, int | str]] = []
        timed_scene_subtitles: list[dict[str, str]] = []
        expected_scene_count = len(scene_subtitles)
        # 执行calculate dynamic durations相关逻辑。
        expected_total_duration_ms = sum(calculate_dynamic_durations(scene_subtitles))
        timeline_cursor_ms = 0
        for scene_index, scene_subtitle in enumerate(scene_subtitles, start=1):
            vivid_text = str(scene_subtitle.get("vivid", "")).strip()
            if not vivid_text:
                raise RuntimeError(
                    f"知识视图第{scene_index}幕缺少 vivid 文本，无法生成字幕音频。"
                )
            # 执行sanitize subtitle audio text相关逻辑。
            safe_ssml_text = self._sanitize_subtitle_audio_text(vivid_text)
            ssml_payload = f'<speak rate="{_DASHSCOPE_TTS_SPEECH_RATE}">{safe_ssml_text}</speak>'
            callback = _DynamicViewSubtitleTTSCallback()
            synthesizer = SpeechSynthesizer(
                model=_DASHSCOPE_TTS_MODEL,
                voice=subtitle_tts_voice,
                format=AudioFormat.PCM_16000HZ_MONO_16BIT,
                callback=callback,
            )
            try:
                # 执行dashscope tts connect相关逻辑。
                synthesizer._SpeechSynthesizer__connect(  # noqa: SLF001
                    _DASHSCOPE_TTS_CONNECT_TIMEOUT_SECONDS
                )
                # 执行dashscope tts call相关逻辑。
                synthesizer.call(ssml_payload)
                callback.finished_event.wait()
                if callback.error:
                    logger.error(
                        "Knowledge subtitle TTS segment failed: archiveId=%s sceneIndex=%s error=%s",
                        knowledge_archive_id,
                        scene_index,
                        callback.error,
                    )
                    raise RuntimeError(
                        f"知识视图第{scene_index}幕字幕音频合成失败：{callback.error}"
                    )
                actual_duration_ms = len(callback.pcm_data) / _DASHSCOPE_TTS_PCM_BYTES_PER_MS
                # 执行calculate scene subtitle duration ms相关逻辑。
                target_duration_ms = self._calculate_scene_subtitle_duration_ms(scene_subtitle)
                final_duration_ms = actual_duration_ms
                if actual_duration_ms < target_duration_ms:
                    pad_ms = target_duration_ms - actual_duration_ms
                    callback.pcm_data.extend(
                        b"\x00" * int(pad_ms * _DASHSCOPE_TTS_PCM_BYTES_PER_MS)
                    )
                    final_duration_ms = target_duration_ms
                rounded_final_duration_ms = int(round(final_duration_ms))
                timed_scene_subtitle = {
                    key: str(value).strip()
                    for key, value in scene_subtitle.items()
                    if str(value).strip()
                }
                timed_scene_subtitle["durationMs"] = str(rounded_final_duration_ms)
                timed_scene_subtitles.append(timed_scene_subtitle)
                timeline_alignment_report.append(
                    {
                        "sceneIndex": scene_index,
                        "textPreview": f"{vivid_text[:12]}...",
                        "startMs": timeline_cursor_ms,
                        "endMs": timeline_cursor_ms + rounded_final_duration_ms,
                        "timelineMinMs": target_duration_ms,
                        "actualVoiceMs": int(round(actual_duration_ms)),
                        "finalPaddedMs": rounded_final_duration_ms,
                    }
                )
                timeline_cursor_ms += rounded_final_duration_ms
                combined_pcm.extend(callback.pcm_data)
            except Exception as error:
                logger.exception(
                    "Knowledge subtitle TTS segment exception: archiveId=%s sceneIndex=%s error=%s",
                    knowledge_archive_id,
                    scene_index,
                    error,
                )
                raise RuntimeError(
                    f"知识视图第{scene_index}幕字幕音频合成异常：{error}"
                ) from error
        if not combined_pcm:
            raise RuntimeError("知识视图字幕音频为空，无法写入最终文件。")
        # 执行validate knowledge subtitle audio integrity相关逻辑。
        self._validate_knowledge_subtitle_audio_integrity(
            knowledge_archive_id=knowledge_archive_id,
            expected_scene_count=expected_scene_count,
            expected_total_duration_ms=expected_total_duration_ms,
            actual_duration_ms=len(combined_pcm) / _DASHSCOPE_TTS_PCM_BYTES_PER_MS,
            timeline_alignment_report=timeline_alignment_report,
        )
        _migrate_dynamic_view_audio_directories()
        _DYNAMIC_VIEW_SUBTITLE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        relative_audio_path = (
            f"subtitles/knowledge_{knowledge_archive_id}_{uuid4().hex[:12]}.wav"
        )
        output_audio_path = (_DYNAMIC_VIEW_MUSIC_ROOT / relative_audio_path).resolve()
        with output_audio_path.open("wb") as audio_file:
            data_length = len(combined_pcm)
            audio_file.write(b"RIFF")
            audio_file.write(struct.pack("<I", 36 + data_length))
            audio_file.write(b"WAVE")
            audio_file.write(b"fmt ")
            audio_file.write(struct.pack("<I", 16))
            audio_file.write(struct.pack("<H", 1))
            audio_file.write(struct.pack("<H", 1))
            audio_file.write(struct.pack("<I", _DASHSCOPE_TTS_SAMPLE_RATE))
            audio_file.write(struct.pack("<I", _DASHSCOPE_TTS_SAMPLE_RATE * 2))
            audio_file.write(struct.pack("<H", 2))
            audio_file.write(struct.pack("<H", 16))
            audio_file.write(b"data")
            audio_file.write(struct.pack("<I", data_length))
            audio_file.write(combined_pcm)
        logger.info(
            "Knowledge subtitle TTS timeline alignment: archiveId=%s audioPath=%s report=%s",
            knowledge_archive_id,
            relative_audio_path,
            json.dumps(timeline_alignment_report, ensure_ascii=False),
        )
        return KnowledgeSubtitleAudioResult(
            audio_name=relative_audio_path,
            scene_subtitles=timed_scene_subtitles,
            total_duration_ms=sum(
                int(scene_subtitle["durationMs"]) for scene_subtitle in timed_scene_subtitles
            ),
        )

    # 执行validate knowledge subtitle audio integrity相关逻辑。
    def _validate_knowledge_subtitle_audio_integrity(
        self,
        *,
        knowledge_archive_id: int,
        expected_scene_count: int,
        expected_total_duration_ms: int,
        actual_duration_ms: float,
        timeline_alignment_report: list[dict[str, int | str]],
    ) -> None:
        """校验并发生成后的字幕音频段数和总时长是否完整。"""
        actual_scene_indexes = [
            int(str(item["sceneIndex"]))
            for item in timeline_alignment_report
            if "sceneIndex" in item
        ]
        expected_scene_indexes = list(range(1, expected_scene_count + 1))
        if actual_scene_indexes != expected_scene_indexes:
            raise RuntimeError(
                f"知识视图字幕音频段不完整：archiveId={knowledge_archive_id} expected={expected_scene_indexes} actual={actual_scene_indexes}"
            )
        rounded_actual_duration_ms = int(round(actual_duration_ms))
        if rounded_actual_duration_ms < expected_total_duration_ms:
            raise RuntimeError(
                f"知识视图字幕音频时长不完整：archiveId={knowledge_archive_id} expectedMs={expected_total_duration_ms} actualMs={rounded_actual_duration_ms}"
            )

    # 执行resolve dashscope tts api key相关逻辑。
    def _resolve_dashscope_tts_api_key(self) -> str:
        """从数据库 tts 节点读取字幕音频生成密钥。"""
        # 执行resolve latest runtime model profile相关逻辑。
        tts_profile = self._resolve_latest_runtime_model_profile("system", "tts")
        api_keys = tts_profile.api_keys
        if api_keys:
            return api_keys[0]
        raise RuntimeError("未配置 system/tts API Key，无法生成字幕音频。")

    # 执行sanitize subtitle audio text相关逻辑。
    def _sanitize_subtitle_audio_text(self, text: str) -> str:
        """清理字幕文本里的高位字符与尖括号，避免影响 TTS 请求。"""
        # 执行sanitize dynamic view subtitle text相关逻辑。
        return sanitize_dynamic_view_subtitle_text(text)

    # 执行calculate scene subtitle duration ms相关逻辑。
    def _calculate_scene_subtitle_duration_ms(self, scene_subtitle: dict[str, str]) -> int:
        """按后端动态视图时长规则计算单幕字幕音频需要补齐到的最小时长。"""
        # 执行calculate dynamic durations相关逻辑。
        return calculate_dynamic_durations([scene_subtitle])[0]

    # 执行delete dynamic view subtitle audio file相关逻辑。
    def _delete_dynamic_view_subtitle_audio_file(self, subtitle_audio_name: str) -> None:
        """删除知识视图生成过的字幕音频文件，避免失败任务残留孤儿文件。"""
        normalized_subtitle_audio_name = subtitle_audio_name.strip()
        if not normalized_subtitle_audio_name:
            return
        music_root = _DYNAMIC_VIEW_MUSIC_ROOT.resolve()
        subtitle_audio_path = (music_root / normalized_subtitle_audio_name).resolve()
        try:
            subtitle_audio_path.relative_to(music_root)
        except ValueError:
            logger.warning(
                "Skip deleting subtitle audio outside music root: subtitleAudioName=%s",
                normalized_subtitle_audio_name,
            )
            return
        if subtitle_audio_path.is_file():
            subtitle_audio_path.unlink()

    # 执行select dynamic view audio相关逻辑。
    def _select_dynamic_view_audio(self) -> tuple[str, int, int, int]:
        """从 music 目录随机选择一首固定背景音乐，并返回相对路径与默认播放参数。"""
        if not _DYNAMIC_VIEW_AUDIO_DIR.is_dir():
            _migrate_dynamic_view_audio_directories()
        if not _DYNAMIC_VIEW_AUDIO_DIR.is_dir():
            raise RuntimeError(f"动态视图音频目录不存在：{_DYNAMIC_VIEW_AUDIO_DIR}")
        subtitle_audio_dir = _DYNAMIC_VIEW_SUBTITLE_AUDIO_DIR.resolve()
        candidate_files = sorted(
            path
            for path in _DYNAMIC_VIEW_AUDIO_DIR.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
        )
        if not candidate_files:
            raise RuntimeError(f"动态视图音频目录为空：{_DYNAMIC_VIEW_AUDIO_DIR}")
        selected_file = choice(candidate_files)
        return selected_file.relative_to(_DYNAMIC_VIEW_MUSIC_ROOT).as_posix(), 0, 0, 25

    # 执行build archive audio config相关逻辑。
    def _build_archive_audio_config(
        self,
        *,
        view_type: str,
        archive_id: int | None,
        audio_name: str,
        audio_start_time: int,
        audio_end_time: int,
        audio_volume: int,
    ) -> DynamicViewAudioConfig | None:
        """把数据库里的固定音频字段转换成前端可直接播放的配置对象。"""
        normalized_audio_name = audio_name.strip()
        if archive_id is None or archive_id <= 0 or not normalized_audio_name:
            return None
        if view_type == "game":
            audio_path = PythonUrl.DYNAMIC_VIEW_AUDIO_TEMPLATE.format_path(archive_id=archive_id)
        elif view_type == "knowledge":
            audio_path = PythonUrl.DYNAMIC_VIEW_KNOWLEDGE_AUDIO_TEMPLATE.format_path(archive_id=archive_id)
        else:
            raise ValueError(f"未知的动态视图音频类型：view_type={view_type}")
        return DynamicViewAudioConfig(
            name=Path(normalized_audio_name).name,
            startTime=max(0, int(audio_start_time)),
            endTime=max(0, int(audio_end_time)),
            volume=max(0, min(100, int(audio_volume))),
            path=audio_path,
        )

    # 执行normalize game metadata clues相关逻辑。
    def _normalize_game_metadata_clues(
        self,
        raw_clues: list[DynamicViewMetadataClue],
    ) -> list[DynamicViewClueItem]:
        """把元数据阶段生成的线索统一规范成可落库的动态视图线索结构。"""
        normalized_clues: list[DynamicViewClueItem] = []
        seen_clue_keys: set[str] = set()
        for index, raw_clue in enumerate(raw_clues, start=1):
            clue_key = normalize_clue_key(raw_clue.clue_key)
            if not clue_key:
                fallback_title = raw_clue.clue_title.strip() or f"线索{index}"
                clue_key = normalize_clue_key(f"{fallback_title}:{raw_clue.clue_content.strip()}")
            if not clue_key:
                clue_key = f"线索{index}:关键现象"
            if clue_key in seen_clue_keys:
                continue
            clue_title = raw_clue.clue_title.strip() or f"线索{index}"
            clue_content = raw_clue.clue_content.strip()
            if not clue_content:
                continue
            seen_clue_keys.add(clue_key)
            normalized_clues.append(
                DynamicViewClueItem(
                    clueKey=clue_key,
                    clueTitle=clue_title,
                    clueContent=clue_content,
                    unlocked=False,
                )
            )
        return normalized_clues

    # 执行cancel background tasks相关逻辑。
    async def _cancel_background_tasks(
        self,
        *,
        topic: str,
        tasks: list[asyncio.Task[object] | None],
    ) -> None:
        """统一取消动态视图生成期间派生出的后台子任务，并消费已完成任务异常。"""
        normalized_tasks = [current_task for current_task in tasks if current_task is not None]
        running_tasks = [
            current_task
            for current_task in normalized_tasks
            if not current_task.done()
        ]
        if running_tasks:
            for current_task in running_tasks:
                current_task.cancel()
            await asyncio.gather(*running_tasks, return_exceptions=True)
            logger.info(
                "Dynamic view background subtasks cancelled: topic=%s | taskCount=%s",
                topic,
                len(running_tasks),
            )
        for current_task in normalized_tasks:
            if not current_task.done() or current_task.cancelled():
                continue
            try:
                # 执行result相关逻辑。
                current_task.result()
            except Exception:
                continue



# 执行migrate dynamic view audio directories相关逻辑。
def _migrate_dynamic_view_audio_directories() -> None:
    """把旧目录 dynamic_view / dynamic_view_subtitles 迁移到新目录 view / subtitles。"""
    legacy_audio_dir = _DYNAMIC_VIEW_MUSIC_ROOT / "dynamic_view"
    legacy_subtitle_dir = _DYNAMIC_VIEW_MUSIC_ROOT / "dynamic_view_subtitles"
    if legacy_audio_dir.is_dir() and not _DYNAMIC_VIEW_AUDIO_DIR.exists():
        legacy_audio_dir.rename(_DYNAMIC_VIEW_AUDIO_DIR)
    if legacy_subtitle_dir.is_dir() and not _DYNAMIC_VIEW_SUBTITLE_AUDIO_DIR.exists():
        legacy_subtitle_dir.rename(_DYNAMIC_VIEW_SUBTITLE_AUDIO_DIR)


# 执行resolve generated view type code相关逻辑。
def _resolve_generated_view_type_code(*, flow_version: int, template_type: str) -> str:
    """按流程版本和模板方向写入动态视图类型编码。"""
    is_portrait = "portrait" in str(template_type or "").lower() or "9_16" in str(template_type or "")
    if int(flow_version) == 2:
        return "2-2" if is_portrait else "2-1"
    return "1-2" if is_portrait else "1-1"


# 执行raise if client disconnected相关逻辑。
async def _raise_if_client_disconnected(
    disconnect_checker: Callable[[], Awaitable[bool]] | None,
) -> None:
    """在关键节点边界检查客户端是否已断开，断开则立即终止后续模型调用。"""
    if disconnect_checker is None:
        return
    if await disconnect_checker():
        raise asyncio.CancelledError


# 执行resolve current time相关逻辑。
def _resolve_current_utc_time() -> datetime:
    """统一生成数据库直接展示的当前时间。"""
    return datetime.now()


# 执行log background task exception相关逻辑。
def _log_background_task_exception(
    task: asyncio.Task[object],
    *,
    topic: str,
    task_name: str,
) -> None:
    """统一吃掉后台任务异常日志，避免事件循环输出未处理异常警告。"""
    if task.cancelled():
        return
    try:
        # 执行result相关逻辑。
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        # 执行exception相关逻辑。
        logger.exception(
            "Dynamic view background task failed: topic=%s | task=%s", topic, task_name
        )
