# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\dynamic_view\task_service.py。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from random import choice

from sqlalchemy.orm import Session

from app.features.dynamic_view.prompt_builder import build_dynamic_view_task_prompt
from app.features.dynamic_view.schemas import (
    DynamicViewCreateRequest,
    DynamicViewSourceTaskItem,
    DynamicViewTaskCreateItem,
    DynamicViewTaskGenerateResponse,
    DynamicViewTaskGenerationBundle,
    DynamicViewTaskSnapshot,
    resolve_dynamic_view_type_by_task_type_code,
)
from app.features.dynamic_view.service import DynamicViewService
from app.repositories.dynamic_view_source_task_repository import DynamicViewSourceTaskRepository

logger = logging.getLogger(__name__)

QUESTION_TOPIC_PATTERN = re.compile(r"[？?]|^(为什么|如何|什么是|啥是|怎么|怎样|为何|是否)")
TASK_TYPE_CODE_POOL = ("1-1", "1-2", "2-1", "2-2")


# 定义DynamicViewTaskService。
class DynamicViewTaskService:
    """动态视图任务服务，负责任务生成入库与定时批量分发。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        *,
        dynamic_view_service: DynamicViewService,
        dynamic_view_source_task_repository: DynamicViewSourceTaskRepository,
        session_factory: Callable[[], Session],
    ) -> None:
        """执行init相关逻辑。"""
        self.dynamic_view_service = dynamic_view_service
        self.dynamic_view_source_task_repository = dynamic_view_source_task_repository
        self.session_factory = session_factory

    # 执行generate and store tasks相关逻辑。
    async def generate_and_store_tasks(
        self,
        *,
        count: int,
    ) -> DynamicViewTaskGenerateResponse:
        """调用专用 task 模型批量生成任务，并写入数据库任务来源表。"""
        task_messages = build_dynamic_view_task_prompt(
            count,
            prompt_version=1,
        )
        task_runner = self.dynamic_view_service._build_latest_runtime_runner("system", "task")
        try:
            generated_bundle = await task_runner.run_structured_messages(
                task_messages,
                schema=DynamicViewTaskGenerationBundle,
                stage_name="dynamic_view_task_source",
            )
        finally:
            # 执行close runtime model clients相关逻辑。
            await self.dynamic_view_service._close_runtime_model_clients(task_runner)
        normalized_tasks = self._normalize_tasks(
            generated_bundle.tasks,
            requested_count=count,
        )
        with self.session_factory() as db:
            inserted_count = self.dynamic_view_source_task_repository.create_tasks(
                db,
                tasks=normalized_tasks,
                source_type="llm",
                source_model=task_runner.client.profile.model,
            )
        return DynamicViewTaskGenerateResponse(
            requestedCount=count,
            generatedCount=len(normalized_tasks),
            insertedCount=inserted_count,
            skippedCount=max(0, len(normalized_tasks) - inserted_count),
            tasks=normalized_tasks,
        )

    # 执行create and store tasks相关逻辑。
    async def create_and_store_tasks(
        self,
        *,
        raw_tasks: list[DynamicViewTaskCreateItem],
    ) -> DynamicViewTaskGenerateResponse:
        """把手动输入的任务列表写入数据库任务来源表。"""
        normalized_tasks: list[DynamicViewSourceTaskItem] = []
        seen_task_keys: set[str] = set()
        for raw_task_item in raw_tasks:
            normalized_topic = str(raw_task_item.topic).strip()
            if not normalized_topic:
                continue
            normalized_topic = normalized_topic.removeprefix("-").strip()
            if not normalized_topic:
                continue
            normalized_type_code = raw_task_item.type_code.strip()
            task_key = f"{normalized_topic}|{normalized_type_code}"
            if task_key in seen_task_keys:
                continue
            seen_task_keys.add(task_key)
            normalized_tasks.append(
                DynamicViewSourceTaskItem(
                    topic=normalized_topic,
                    view_type=resolve_dynamic_view_type_by_task_type_code(normalized_type_code),
                    author_id=raw_task_item.author_id,
                    typeCode=normalized_type_code,
                )
            )
        with self.session_factory() as db:
            inserted_count = self.dynamic_view_source_task_repository.create_tasks(
                db,
                tasks=normalized_tasks,
                source_type="manual",
                source_model="manual_input",
            )
        return DynamicViewTaskGenerateResponse(
            requestedCount=len(raw_tasks),
            generatedCount=len(normalized_tasks),
            insertedCount=inserted_count,
            skippedCount=max(0, len(normalized_tasks) - inserted_count),
            tasks=normalized_tasks,
        )

    # 执行dispatch scheduled batch相关逻辑。
    def dispatch_scheduled_batch(
        self,
        *,
        scene_count_min: int,
        batch_size: int,
    ) -> list[DynamicViewTaskSnapshot]:
        """当且仅当前一批已全部结束时，领取下一批待处理任务并分发动态视图后台任务。"""
        if self.dynamic_view_service.get_active_generation_task_count() > 0:
            return []
        with self.session_factory() as db:
            claimed_tasks = self.dynamic_view_source_task_repository.claim_pending_tasks(
                db,
                limit=batch_size,
            )
        if not claimed_tasks:
            return []
        created_snapshots: list[DynamicViewTaskSnapshot] = []
        for task_record in claimed_tasks:
            try:
                normalized_type_code = str(task_record.type_code or "").strip()
                template_type, flow_version = self._resolve_template_type_and_flow_version(
                    type_code=normalized_type_code,
                )
                created_snapshots.append(
                    self.dynamic_view_service.create_generation_task(
                        DynamicViewCreateRequest(
                            topic=task_record.topic,
                            view_type=resolve_dynamic_view_type_by_task_type_code(
                                str(task_record.type_code or "").strip()
                            ),
                            author_id=str(task_record.author_id or "").strip(),
                            sceneCountMin=scene_count_min,
                            templateType=template_type,
                        ),
                        source_task_record_id=int(task_record.id),
                        forced_flow_version=flow_version,
                        source_type=str(task_record.source_type or "").strip(),
                        source_model=str(task_record.source_model or "").strip(),
                        type_code=normalized_type_code,
                    )
                )
            except Exception as error:
                with self.session_factory() as db:
                    self.dynamic_view_source_task_repository.mark_task_failed(
                        db,
                        task_record_id=int(task_record.id),
                        error_message=str(error).strip() or "动态视图任务创建失败",
                    )
                logger.exception(
                    "Dynamic view scheduled task dispatch failed: taskRecordId=%s topic=%s",
                    task_record.id,
                    task_record.topic,
                )
        return created_snapshots

    # 执行normalize tasks相关逻辑。
    def _normalize_tasks(
        self,
        tasks: list[DynamicViewSourceTaskItem],
        *,
        requested_count: int,
    ) -> list[DynamicViewSourceTaskItem]:
        """统一清洗并去重任务列表，避免把空字符串和重复主题写进数据库。"""
        normalized_tasks: list[DynamicViewSourceTaskItem] = []
        seen_topics: set[str] = set()
        for raw_task_item in tasks:
            if len(normalized_tasks) >= requested_count:
                break
            normalized_topic = raw_task_item.topic.strip()
            if not normalized_topic:
                continue
            normalized_topic = normalized_topic.removeprefix("-").strip()
            normalized_topic = self._strip_topic_punctuation(normalized_topic)
            if not normalized_topic or self._is_question_topic(normalized_topic):
                continue
            if normalized_topic in seen_topics:
                continue
            seen_topics.add(normalized_topic)
            normalized_tasks.append(self._build_random_task_item(normalized_topic))
        return normalized_tasks

    # 执行is question topic相关逻辑。
    def _is_question_topic(self, topic: str) -> bool:
        """判断主题是否仍然是问句表达，避免把错误选题写入数据库。"""
        return bool(QUESTION_TOPIC_PATTERN.search(topic))

    # 执行strip topic punctuation相关逻辑。
    def _strip_topic_punctuation(self, topic: str) -> str:
        """清理主题首尾的常见标点，只保留核心知识点名称。"""
        return topic.strip("，,。.!！；;：:、 ")

    # 执行build random task item相关逻辑。
    def _build_random_task_item(self, topic: str) -> DynamicViewSourceTaskItem:
        """为任务随机分配类型编码，并固化对应流程版本。"""
        type_code = choice(TASK_TYPE_CODE_POOL)
        return DynamicViewSourceTaskItem(
            topic=topic,
            view_type=resolve_dynamic_view_type_by_task_type_code(type_code),
            author_id="system",
            typeCode=type_code,
        )

    # 执行resolve template type and flow version相关逻辑。
    def _resolve_template_type_and_flow_version(
        self,
        *,
        type_code: str,
    ) -> tuple[str, int]:
        """根据类型编码解析模板方向和流程版本。"""
        normalized_type_code = type_code.strip()
        if normalized_type_code == "1-1":
            return "landscape_16_9", 1
        if normalized_type_code == "1-2":
            return "portrait_9_16", 1
        if normalized_type_code == "2-1":
            return "landscape_16_9", 2
        if normalized_type_code == "2-2":
            return "portrait_9_16", 2
        return "landscape_16_9", 1
