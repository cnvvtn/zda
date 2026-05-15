# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_model_profile_repository.py。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import ModelProfile, ReasoningProfile
from app.db.models import DynamicViewModelProfile

MODEL_LEVEL_NODE_KEYS = {"node1", "node2"}
MODEL_LEVEL_LABELS = {
    "experience": "体验模型",
    "basic": "基础模型",
    "advanced": "高级模型",
    "top": "顶级模型",
}
MODEL_LEVEL_ORDER = ("experience", "basic", "advanced", "top")


# 定义DynamicViewModelProfileRepository。
class DynamicViewModelProfileRepository:
    """动态视图模型配置仓库，仅 node1/node2 按模型等级读取配置。"""

    # 执行resolve effective model level相关逻辑。
    def _resolve_effective_model_level(self, model_level: str, node_key: str) -> str:
        """非 node 节点统一使用 system 配置，不区分 experience/basic/advanced。"""
        if node_key in MODEL_LEVEL_NODE_KEYS:
            return model_level
        return "system"

    # 执行resolve active archive相关逻辑。
    def _resolve_active_archive(
        self,
        db: Session,
        model_level: str,
        node_key: str,
    ) -> DynamicViewModelProfile | None:
        """读取当前可用的模型配置行。"""
        normalized_model_level = str(model_level or "").strip()
        normalized_node_key = str(node_key or "").strip()
        if not normalized_model_level or not normalized_node_key:
            return None
        effective_model_level = self._resolve_effective_model_level(
            normalized_model_level,
            normalized_node_key,
        )
        return (
            db.query(DynamicViewModelProfile)
            .filter(
                DynamicViewModelProfile.model_level == effective_model_level,
                DynamicViewModelProfile.node_key == normalized_node_key,
                DynamicViewModelProfile.enabled == 1,
            )
            .first()
        )

    # 执行resolve active profile相关逻辑。
    def resolve_active_profile(
        self,
        db: Session,
        model_level: str,
        node_key: str,
    ) -> ModelProfile | None:
        """返回可直接调用的模型配置；仅 node1/node2 使用传入模型等级。"""
        normalized_model_level = str(model_level or "").strip()
        normalized_node_key = str(node_key or "").strip()
        if not normalized_model_level or not normalized_node_key:
            return None
        archive = self._resolve_active_archive(db, normalized_model_level, normalized_node_key)
        if archive is None:
            return None
        return ModelProfile(
            router_type=str(archive.router_type or "openai").strip() or "openai",
            base_url=archive.base_url,
            profile_key=f"DB_DYNAMIC_VIEW_MODEL_{archive.model_level.upper()}",
            api_key=archive.api_key,
            model=archive.model_name,
            stream=bool(archive.stream),
            temperature=float(archive.temperature),
            top_p=float(archive.top_p),
            enable_deepthinking=bool(archive.enable_deepthinking),
            reasoning=ReasoningProfile(effort=archive.reasoning_effort),
            max_tokens=int(archive.max_tokens),
            timeout=int(archive.timeout_seconds),
            total_timeout=int(archive.total_timeout_seconds),
        )

    # 执行resolve model credit cost相关逻辑。
    def resolve_model_credit_cost(
        self,
        db: Session,
        model_level: str,
        node_key: str,
    ) -> int:
        """读取模型配置表里的生成 Credit。"""
        archive = self._resolve_active_archive(db, model_level, node_key)
        if archive is None:
            return 0
        return int(archive.credit_cost or 0)

    # 执行list model options相关逻辑。
    def list_model_options(self, db: Session, node_key: str) -> list[dict[str, object]]:
        """返回前端自定义选项需要的模型可用性和 Credit。"""
        normalized_node_key = str(node_key or "node1").strip() or "node1"
        archives = (
            db.query(DynamicViewModelProfile)
            .filter(DynamicViewModelProfile.node_key == normalized_node_key)
            .all()
        )
        archive_by_level = {str(item.model_level or "").strip(): item for item in archives}
        options: list[dict[str, object]] = []
        for model_level in MODEL_LEVEL_ORDER:
            archive = archive_by_level.get(model_level)
            options.append(
                {
                    "modelLevel": model_level,
                    "label": MODEL_LEVEL_LABELS[model_level],
                    "enabled": bool(archive and int(archive.enabled or 0) == 1),
                    "creditCost": int(archive.credit_cost or 0) if archive else 0,
                    "modelName": str(archive.model_name or "") if archive else "",
                }
            )
        return options
