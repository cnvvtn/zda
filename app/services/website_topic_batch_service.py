# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\website_topic_batch_service.py。"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.db.models import WebsiteContent, WebsiteTopicBatch
from app.features.dynamic_view.service import DynamicViewService
from app.features.website.schemas import WebsiteTopicBatchItem, WebsiteTopicFlatBatchItem

logger = logging.getLogger(__name__)
_HOME_CONFIG_KEY = "home"
_TOPIC_BATCH_COUNT = 5
_TOPIC_BATCH_GENERATION_ATTEMPTS = 3
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


# 定义WebsiteTopicBatchService。
class WebsiteTopicBatchService:
    """官网话题批次服务，负责每日生成和读取。"""

    # 执行init相关逻辑。
    def __init__(
        self,
        *,
        dynamic_view_service: DynamicViewService,
        session_factory: Callable[[], Session],
    ) -> None:
        """执行init相关逻辑。"""
        self.dynamic_view_service = dynamic_view_service
        self.session_factory = session_factory

    # 执行get random topic batch相关逻辑。
    async def get_random_topic_batch(self) -> dict[str, object]:
        """只从数据库读取已有话题，不触发生成。"""
        today = self._resolve_today()
        with self.session_factory() as db:
            rows = self._list_batches(db, today)
            if not rows:
                rows = self._list_latest_batches(db)
        if not rows:
            raise RuntimeError("官网主题批次为空")
        selected_row = random.choice(rows)
        selected_data = json.loads(selected_row.topics_json)
        return self._build_public_batch_payload(
            batch_date=selected_row.batch_date,
            batch_index=selected_row.batch_index,
            batch_data=selected_data,
        )

    # 执行refresh topic batches相关逻辑。
    async def refresh_topic_batches(self) -> None:
        """调用 topic 节点生成五批话题，并覆盖数据库旧批次。"""
        today = self._resolve_today()
        with self.session_factory() as db:
            config_data = self._load_home_config(db)
        groups = self._resolve_prompt_groups(config_data)
        runner = self.dynamic_view_service._build_latest_runtime_runner("system", "topic")
        try:
            generated_batches: list[tuple[int, WebsiteTopicBatchItem]] = []
            for batch_index in range(1, _TOPIC_BATCH_COUNT + 1):
                batch = None
                for attempt in range(1, _TOPIC_BATCH_GENERATION_ATTEMPTS + 1):
                    try:
                        batch = await self._generate_batch(runner, groups)
                        self._validate_generated_batch(batch, groups)
                        break
                    except Exception:
                        if attempt >= _TOPIC_BATCH_GENERATION_ATTEMPTS:
                            raise
                        logger.warning(
                            "Website topic batch generation retrying: batch_index=%s attempt=%s",
                            batch_index,
                            attempt,
                            exc_info=True,
                        )
                if batch is None:
                    raise RuntimeError("官网话题批次生成失败")
                generated_batches.append((batch_index, batch))
            with self.session_factory() as db:
                self._replace_all_batches(db, today, generated_batches)
        finally:
            # 执行close runtime model clients相关逻辑。
            await self.dynamic_view_service._close_runtime_model_clients(runner)

    # 执行resolve today相关逻辑。
    def _resolve_today(self) -> date:
        """按上海时间返回主题批次日期。"""
        return datetime.now(_SHANGHAI_TZ).date()

    # 执行load home config相关逻辑。
    def _load_home_config(self, db: Session) -> dict[str, object]:
        """读取官网配置，作为主题分组结构输入。"""
        config_record = (
            db.query(WebsiteContent)
            .filter(WebsiteContent.content_key == _HOME_CONFIG_KEY)
            .first()
        )
        if config_record is None:
            raise RuntimeError("官网配置不存在")
        return json.loads(config_record.content_json)

    # 执行list batches相关逻辑。
    def _list_batches(self, db: Session, batch_date: date) -> list[WebsiteTopicBatch]:
        """读取指定日期的所有官网主题批次。"""
        return (
            db.query(WebsiteTopicBatch)
            .filter(WebsiteTopicBatch.batch_date == batch_date)
            .order_by(WebsiteTopicBatch.batch_index)
            .all()
        )

    # 执行list latest batches相关逻辑。
    def _list_latest_batches(self, db: Session) -> list[WebsiteTopicBatch]:
        """读取数据库里最近日期的主题批次。"""
        latest_date = db.query(WebsiteTopicBatch.batch_date).order_by(WebsiteTopicBatch.batch_date.desc()).limit(1).scalar()
        if latest_date is None:
            return []
        return self._list_batches(db, latest_date)

    # 执行generate batch相关逻辑。
    async def _generate_batch(
        self,
        runner: object,
        groups: list[dict[str, object]],
    ) -> WebsiteTopicBatchItem:
        """一次模型请求生成一整批官网话题。"""
        messages = [
            SystemMessage(
                content=(
                    "你是知搭官网的知识话题策划节点，只输出适合生成知识视图的中文话题。"
                    "话题必须用少见细节切入，有反常识感，能让用户想问为什么，不要营销话术。"
                )
            ),
            HumanMessage(content=self._build_generation_prompt(groups)),
        ]
        flat_batch = await runner.run_structured_messages(
            messages,
            schema=WebsiteTopicFlatBatchItem,
            temperature=0.8,
            stage_name="website_topic_batch_generation",
        )
        return self._build_batch_from_flat_batch(flat_batch, groups)

    # 执行validate generated batch相关逻辑。
    def _validate_generated_batch(
        self,
        batch: WebsiteTopicBatchItem,
        groups: list[dict[str, object]],
    ) -> None:
        """校验 topic 节点返回的单批分组结构。"""
        expected_themes = [str(group["theme"]) for group in groups]
        batch_themes = [group.theme for group in batch.groups]
        if batch_themes != expected_themes:
            raise RuntimeError("官网主题批次分组不正确")

    # 执行build batch from flat batch相关逻辑。
    def _build_batch_from_flat_batch(
        self,
        flat_batch: WebsiteTopicFlatBatchItem,
        groups: list[dict[str, object]],
    ) -> WebsiteTopicBatchItem:
        """把扁平模型输出转换成前端沿用的 groups 批次结构。"""
        flat_data = flat_batch.model_dump(mode="json")
        return WebsiteTopicBatchItem.model_validate(
            {
                "groups": [
                    {
                        "theme": str(group["theme"]),
                        "topics": flat_data[str(group["theme"])],
                    }
                    for group in groups
                ]
            }
        )

    # 执行resolve prompt groups相关逻辑。
    def _resolve_prompt_groups(self, config_data: dict[str, object]) -> list[dict[str, object]]:
        """从官网配置中提取主题分组，保留 theme/label/title/summary。"""
        raw_groups = config_data.get("topicGroups")
        if not isinstance(raw_groups, list):
            raise RuntimeError("官网主题分组不存在")
        groups: list[dict[str, object]] = []
        for item in raw_groups[:5]:
            if not isinstance(item, dict):
                continue
            groups.append(
                {
                    "theme": str(item.get("theme") or "").strip(),
                    "label": str(item.get("label") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "summary": str(item.get("summary") or "").strip(),
                }
            )
        if len(groups) != 5:
            raise RuntimeError("官网主题分组数量必须为 5")
        return groups

    # 执行build generation prompt相关逻辑。
    def _build_generation_prompt(
        self,
        groups: list[dict[str, object]],
    ) -> str:
        """构造 topic 节点生成单批话题的提示词。"""
        group_text = "\n".join(
            f"- theme={group['theme']} label={group['label']}"
            for group in groups
        )
        return (
            f"任务：生成知识科普推荐话题\n"
            "目标：这些话题要像冷知识入口，具体、反常识、能吸引用户点开并生成知识科普视频\n"
            "硬性要求：\n"
            "1. 每个分组返回 3 个中文话题，每个话题 8 到 20 个中文字符，不能带引号、序号、标点结尾。\n"
            "2. 话题必须是冷知识式切口，包含一个少见细节、异常现象、隐藏用途、历史巧合或反直觉机制。\n"
            "3. 每个话题必须落到具体物体、现象、场景、人物关系或机制，不要写宽泛抽象内容。\n"
            "4. 不要生成生活建议、选购建议、维修教程或保养教程。\n"
            "5. 不要解释常见物品的基础工作方式或必要条件。\n"
            "6. 优先生成带意外感的具体话题，让用户看到后产生“这里面居然还有原因”的好奇感。\n"
            "7. 不要写栏目标题、课程标题、营销话术、总结型标题，也不要出现“话题”“主题”“知识视图”等说明性词语。\n"
            "8. 话题必须混合不同表达：可以是具体问题、反常识现象、对比关系、机制描述或日常场景。\n"
            "9. 不要制造错误前提、时代错配或荒谬反事实。\n"
            "10. 分组取材边界：\n"
            "general：写公共空间、日用品、商业动线里的隐蔽设计或反常用途，不写普通物性和生活建议。\n"
            "science：写冷门自然现象、生物能力、材料特性或地理现象，不写基础物理和教材问答。\n"
            "history：写古代制度、器物、材料、城市、货币或礼仪细节，不写大事件常识和时代错配问题。\n"
            "mind：写具体认知效应、记忆错觉、注意机制、感官联觉或心理实验现象，不写普通情绪句。\n"
            "tech：写协议机制、硬件细节、工程冗余、隐蔽设计或安全机制，不写手机电脑汽车的基础用法。\n"
            "14. 按 schema 输出 general/science/history/mind/tech 五个字段，每个字段 3 个话题。\n"
            "分组信息：\n"
            f"```\n{group_text}\n```"
        )

    # 执行replace all batches相关逻辑。
    def _replace_all_batches(
        self,
        db: Session,
        batch_date: date,
        batches: list[tuple[int, WebsiteTopicBatchItem]],
    ) -> None:
        """清空旧话题批次，并写入当天五批新话题。"""
        db.query(WebsiteTopicBatch).delete(synchronize_session=False)
        for batch_index, batch in batches:
            topics_json = json.dumps(batch.model_dump(mode="json"), ensure_ascii=False)
            db.add(
                WebsiteTopicBatch(
                    batch_date=batch_date,
                    batch_index=batch_index,
                    topics_json=topics_json,
                )
            )
        db.commit()

    # 执行build public batch payload相关逻辑。
    def _build_public_batch_payload(
        self,
        *,
        batch_date: date,
        batch_index: int,
        batch_data: dict[str, object],
    ) -> dict[str, object]:
        """整理随机批次响应，额外提供生成页可直接使用的前五个主题。"""
        groups = batch_data.get("groups") if isinstance(batch_data, dict) else []
        suggestion_topics: list[str] = []
        if isinstance(groups, list):
            for group in groups:
                topics = group.get("topics") if isinstance(group, dict) else []
                if not isinstance(topics, list):
                    continue
                for topic in topics:
                    normalized_topic = str(topic or "").strip()
                    if normalized_topic:
                        suggestion_topics.append(normalized_topic)
        return {
            "batchDate": batch_date.isoformat(),
            "batchIndex": batch_index,
            "topicGroups": groups,
            "suggestionTopics": suggestion_topics[:5],
        }
