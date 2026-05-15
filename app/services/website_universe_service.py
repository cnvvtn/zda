# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\website_universe_service.py。"""

from __future__ import annotations

import json
import logging
import random
import re
from collections.abc import Callable

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.core.url_catalog import PythonUrl
from app.db.models import DynamicViewArchive, DynamicViewComment, WebsiteContent

logger = logging.getLogger(__name__)
_HOME_CONFIG_KEY = "home"
_VIEW_TYPES = ("game", "knowledge")
_PORTRAIT_TEMPLATE_TYPE = "portrait_9_16"
_UNIVERSE_CASE_LIMIT = 12
_UNIVERSE_TOPIC_LIMIT = 5
_ARCHIVE_ID_PATTERN = re.compile(r"/(\d+)/html(?:$|\?)")


# 定义WebsiteUniverseService。
class WebsiteUniverseService:
    """官网沉浸式大观服务，按动态视图分类随机刷新展示视图。"""

    # 执行init相关逻辑。
    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        """执行init相关逻辑。"""
        self.session_factory = session_factory

    # 执行refresh universe cases相关逻辑。
    def refresh_universe_cases(self) -> None:
        """从 dynamic_view 随机读取 subject_type，并覆盖官网大观展示数据。"""
        with self.session_factory() as db:
            config_record = (
                db.query(WebsiteContent)
                .filter(WebsiteContent.content_key == _HOME_CONFIG_KEY)
                .first()
            )
            if config_record is None:
                raise RuntimeError("官网配置不存在")
            config_data = json.loads(config_record.content_json)
            groups = config_data.get("topicGroups")
            if not isinstance(groups, list):
                raise RuntimeError("官网主题分组不存在")
            if not self._is_universe_auto_refresh_enabled(config_data):
                logger.info("Website universe refresh skipped: auto refresh disabled")
                return
            themes = self._resolve_random_themes(groups)
            subject_archive_rows = self._list_random_subject_archive_rows(db, limit=len(themes))
            config_data["universeGroups"] = [
                self._build_universe_group(
                    archive,
                    like_count,
                    theme=themes[index],
                    topics=self._list_subject_topics(db, subject_type=archive.subject_type or archive.type),
                )
                for index, (archive, like_count) in enumerate(subject_archive_rows)
            ]
            config_record.content_json = json.dumps(config_data, ensure_ascii=False)
            db.commit()

    # 执行has manual universe groups相关逻辑。
    def _has_manual_universe_groups(self, config_data: dict[str, object]) -> bool:
        """判断官网大观是否已经由后台手动指定。"""
        groups = config_data.get("universeGroups")
        return isinstance(groups, list) and any(isinstance(group, dict) and group.get("manual") is True for group in groups)

    # 执行is universe auto refresh enabled相关逻辑。
    def _is_universe_auto_refresh_enabled(self, config_data: dict[str, object]) -> bool:
        """读取官网大观自动刷新开关。"""
        setting = config_data.get("universeAutoRefresh")
        if isinstance(setting, bool):
            return setting
        if isinstance(setting, int):
            return setting == 1
        if isinstance(setting, str) and setting.strip():
            return setting.strip().lower() in {"1", "true", "yes", "on"}
        return not self._has_manual_universe_groups(config_data)

    # 执行hydrate universe groups相关逻辑。
    def hydrate_universe_groups(self, db: Session, config_data: dict[str, object]) -> dict[str, object]:
        """按 subject_type 给官网大观分组补齐同类动态视图。"""
        groups = config_data.get("universeGroups")
        if not isinstance(groups, list):
            return config_data
        hydrated_groups = []
        for group in groups:
            if not isinstance(group, dict):
                hydrated_groups.append(group)
                continue
            next_group = {**group}
            next_group["cases"] = self._filter_ready_cases(db, next_group.get("cases"))
            if not self._is_universe_auto_refresh_enabled(config_data) and group.get("manual") is True:
                hydrated_groups.append(next_group)
                continue
            subject_type = self._resolve_group_subject_type(group)
            if not subject_type:
                hydrated_groups.append(next_group)
                continue
            cases = self._list_subject_cases(db, subject_type=subject_type)
            topics = self._list_subject_topics(db, subject_type=subject_type)
            if cases:
                next_group["cases"] = cases
            if topics:
                next_group["topics"] = topics
            hydrated_groups.append(next_group)
        return {**config_data, "universeGroups": hydrated_groups}

    # 执行resolve random themes相关逻辑。
    def _resolve_random_themes(self, groups: list[object]) -> list[str]:
        """随机复用官网现有主题印花。"""
        themes = [
            str(group.get("theme") or "").strip()
            for group in groups
            if isinstance(group, dict) and str(group.get("theme") or "").strip()
        ]
        random.shuffle(themes)
        return themes

    # 执行list random subject archive rows相关逻辑。
    def _list_random_subject_archive_rows(
        self,
        db: Session,
        *,
        limit: int,
    ) -> list[tuple[DynamicViewArchive, int]]:
        """随机选择可播放 subject_type，并为每类随机取一条动态视图。"""
        subject_types = [
            subject_type
            for (subject_type,) in (
                db.query(DynamicViewArchive.subject_type)
                .filter(
                    DynamicViewArchive.type.in_(_VIEW_TYPES),
                    DynamicViewArchive.status == "ready",
                    DynamicViewArchive.html_content != "",
                    DynamicViewArchive.template_type != _PORTRAIT_TEMPLATE_TYPE,
                    DynamicViewArchive.subject_type != "",
                )
                .distinct()
                .order_by(func.rand())
                .limit(limit)
                .all()
            )
        ]
        rows: list[tuple[DynamicViewArchive, int]] = []
        for subject_type in subject_types:
            row = self._query_random_archive(db, subject_type=str(subject_type or "").strip())
            if row is not None:
                rows.append((row[0], int(row[1] or 0)))
        return rows

    # 执行query random archive相关逻辑。
    def _query_random_archive(
        self,
        db: Session,
        *,
        subject_type: str,
    ):
        """按 subject_type 随机查询一条 ready 动态视图。"""
        like_subquery = (
            db.query(
                DynamicViewComment.archive_id.label("archive_id"),
                DynamicViewComment.view_type.label("view_type"),
                func.coalesce(func.sum(DynamicViewComment.like_count), 0).label("like_count"),
            )
            .filter(DynamicViewComment.status == 1)
            .group_by(DynamicViewComment.archive_id, DynamicViewComment.view_type)
            .subquery()
        )
        query = (
            db.query(DynamicViewArchive, func.coalesce(like_subquery.c.like_count, 0).label("like_count"))
            .outerjoin(
                like_subquery,
                and_(
                    like_subquery.c.archive_id == DynamicViewArchive.id,
                    like_subquery.c.view_type == DynamicViewArchive.type,
                ),
            )
            .filter(
                DynamicViewArchive.type.in_(_VIEW_TYPES),
                DynamicViewArchive.status == "ready",
                DynamicViewArchive.html_content != "",
                DynamicViewArchive.template_type != _PORTRAIT_TEMPLATE_TYPE,
                DynamicViewArchive.subject_type == subject_type,
            )
        )
        return (
            query.order_by(
                func.rand(),
            )
            .first()
        )

    # 执行resolve group subject type相关逻辑。
    def _resolve_group_subject_type(self, group: dict[str, object]) -> str:
        """从大观分组中读取 subject_type。"""
        subject_types = group.get("subjectTypes")
        if isinstance(subject_types, list) and subject_types:
            return str(subject_types[0] or "").strip()
        return str(group.get("label") or group.get("title") or "").strip()

    # 执行filter ready cases相关逻辑。
    def _filter_ready_cases(self, db: Session, cases: object) -> list[dict[str, object]]:
        """过滤官网大观手动配置里已不再 ready 的视图。"""
        if not isinstance(cases, list):
            return []
        resolved_cases: list[tuple[int, dict[str, object]]] = []
        for item in cases:
            if not isinstance(item, dict):
                continue
            archive_id = self._resolve_case_archive_id(item)
            if archive_id > 0:
                resolved_cases.append((archive_id, item))
        if not resolved_cases:
            return []
        archive_ids = {archive_id for archive_id, _ in resolved_cases}
        ready_ids = {
            int(archive_id)
            for (archive_id,) in (
                db.query(DynamicViewArchive.id)
                .filter(
                    DynamicViewArchive.id.in_(archive_ids),
                    DynamicViewArchive.type.in_(_VIEW_TYPES),
                    DynamicViewArchive.status == "ready",
                    DynamicViewArchive.html_content != "",
                    DynamicViewArchive.template_type != _PORTRAIT_TEMPLATE_TYPE,
                )
                .all()
            )
        }
        return [item for archive_id, item in resolved_cases if archive_id in ready_ids]

    # 执行resolve case archive id相关逻辑。
    def _resolve_case_archive_id(self, item: dict[str, object]) -> int:
        """从大观展示项里解析动态视图 ID。"""
        archive_id = item.get("archiveId")
        if isinstance(archive_id, int):
            return archive_id
        if isinstance(archive_id, str) and archive_id.strip().isdigit():
            return int(archive_id.strip())
        html_url = str(item.get("htmlUrl") or "").strip()
        match = _ARCHIVE_ID_PATTERN.search(html_url)
        return int(match.group(1)) if match else 0

    # 执行list subject cases相关逻辑。
    def _list_subject_cases(self, db: Session, *, subject_type: str) -> list[dict[str, object]]:
        """读取同 subject_type 下可播放的多条动态视图。"""
        like_subquery = (
            db.query(
                DynamicViewComment.archive_id.label("archive_id"),
                DynamicViewComment.view_type.label("view_type"),
                func.coalesce(func.sum(DynamicViewComment.like_count), 0).label("like_count"),
            )
            .filter(DynamicViewComment.status == 1)
            .group_by(DynamicViewComment.archive_id, DynamicViewComment.view_type)
            .subquery()
        )
        rows = (
            db.query(DynamicViewArchive, func.coalesce(like_subquery.c.like_count, 0).label("like_count"))
            .outerjoin(
                like_subquery,
                and_(
                    like_subquery.c.archive_id == DynamicViewArchive.id,
                    like_subquery.c.view_type == DynamicViewArchive.type,
                ),
            )
            .filter(
                DynamicViewArchive.type.in_(_VIEW_TYPES),
                DynamicViewArchive.status == "ready",
                DynamicViewArchive.html_content != "",
                DynamicViewArchive.template_type != _PORTRAIT_TEMPLATE_TYPE,
                DynamicViewArchive.subject_type == subject_type,
            )
            .order_by(DynamicViewArchive.id.desc())
            .limit(_UNIVERSE_CASE_LIMIT)
            .all()
        )
        return [self._build_universe_case(archive, int(like_count or 0)) for archive, like_count in rows]

    # 执行list subject topics相关逻辑。
    def _list_subject_topics(self, db: Session, *, subject_type: str) -> list[str]:
        """随机读取同 subject_type 下不重复的热门 topic。"""
        rows = (
            db.query(DynamicViewArchive.topic)
            .filter(
                DynamicViewArchive.type.in_(_VIEW_TYPES),
                DynamicViewArchive.status == "ready",
                DynamicViewArchive.html_content != "",
                DynamicViewArchive.template_type != _PORTRAIT_TEMPLATE_TYPE,
                DynamicViewArchive.subject_type == subject_type,
                DynamicViewArchive.topic != "",
            )
            .distinct()
            .order_by(func.rand())
            .limit(_UNIVERSE_TOPIC_LIMIT)
            .all()
        )
        return [str(topic or "").strip() for (topic,) in rows if str(topic or "").strip()]

    # 执行build universe group相关逻辑。
    def _build_universe_group(
        self,
        archive: DynamicViewArchive,
        like_count: int,
        *,
        theme: str,
        topics: list[str],
    ) -> dict[str, object]:
        """按 subject_type 生成官网大观分组。"""
        subject_type = archive.subject_type or archive.type
        return {
            "label": subject_type,
            "theme": theme,
            "title": subject_type,
            "summary": archive.summary or archive.detail or archive.topic,
            "topics": topics,
            "subjectTypes": [subject_type],
            "cases": [self._build_universe_case(archive, like_count)],
        }

    # 执行build universe case相关逻辑。
    def _build_universe_case(self, archive: DynamicViewArchive, like_count: int) -> dict[str, object]:
        """把动态视图存档转换成官网大观展示项。"""
        html_url = (
            PythonUrl.DYNAMIC_VIEW_HTML_TEMPLATE.format_path(archive_id=int(archive.id))
            if archive.type == "game"
            else PythonUrl.DYNAMIC_VIEW_KNOWLEDGE_HTML_TEMPLATE.format_path(archive_id=int(archive.id))
        )
        return {
            "title": archive.subtitle or archive.topic,
            "subtitle": archive.topic,
            "tag": f"{like_count} 点赞" if like_count > 0 else f"{int(archive.view_count or 0)} 次浏览",
            "mode": "question",
            "duration": self._format_duration(int(archive.total_duration_ms or 0)),
            "htmlUrl": html_url,
            "summary": archive.summary or archive.detail or archive.topic,
            "detail": archive.detail or "",
            "points": self._build_case_points(archive, like_count),
        }

    # 执行build case points相关逻辑。
    def _build_case_points(self, archive: DynamicViewArchive, like_count: int) -> list[str]:
        """生成大观视图雷达点文本。"""
        points = [archive.subject_type or archive.type, f"{int(archive.view_count or 0)} 次浏览"]
        if like_count > 0:
            points.append(f"{like_count} 点赞")
        elif int(archive.comment_count or 0) > 0:
            points.append(f"{int(archive.comment_count or 0)} 条讨论")
        return points[:3]

    # 执行format duration相关逻辑。
    def _format_duration(self, total_duration_ms: int) -> str:
        """把毫秒时长格式化为 mm:ss。"""
        total_seconds = max(0, total_duration_ms // 1000)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"
