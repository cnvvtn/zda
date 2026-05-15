# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\routes\website.py。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_app_container, get_db, get_dynamic_view_service, get_website_topic_batch_service
from app.api.response import ajax_success, table_data
from app.core.container import AppContainer
from app.core.url_catalog import PythonUrl
from app.db.models import WebsiteContent
from app.features.website.schemas import WebsiteGenerationSessionCreateRequest, WebsiteGenerationSessionStatusRequest
from app.features.dynamic_view.schemas import DynamicViewTaskSnapshot
from app.features.dynamic_view.service import DynamicViewService
from app.repositories.website_generation_session_repository import WebsiteGenerationSessionRepository
from app.services.credit_service import CreditService
from app.services.website_topic_batch_service import WebsiteTopicBatchService


router = APIRouter(prefix=PythonUrl.WEBSITE_API_PREFIX.value, tags=["website"])
_HOME_CONFIG_KEY = "home"
website_generation_session_repository = WebsiteGenerationSessionRepository()
credit_service = CreditService()

# 执行get website home config相关逻辑。
@router.get("/home")
async def get_website_home_config(
    db: Session = Depends(get_db),
    container: AppContainer = Depends(get_app_container),
) -> dict[str, object]:
    """读取官网首页与生成页共用配置。"""
    config_record = (
        db.query(WebsiteContent)
        .filter(WebsiteContent.content_key == _HOME_CONFIG_KEY)
        .first()
    )
    if config_record is None:
        raise HTTPException(status_code=404, detail="官网配置不存在")
    try:
        config_data = json.loads(config_record.content_json)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail="官网配置 JSON 格式错误") from error
    config_data = container.website_universe_service.hydrate_universe_groups(db, config_data)
    return ajax_success(config_data)


# 执行update website home config相关逻辑。
@router.put("/home")
async def update_website_home_config(
    config_data: dict[str, object] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """更新官网首页与生成页共用配置。"""
    config_record = (
        db.query(WebsiteContent)
        .filter(WebsiteContent.content_key == _HOME_CONFIG_KEY)
        .first()
    )
    if config_record is None:
        raise HTTPException(status_code=404, detail="官网配置不存在")
    config_record.content_json = json.dumps(config_data, ensure_ascii=False)
    db.commit()
    return ajax_success(config_data)


# 执行get random website topic batch相关逻辑。
@router.get("/topic-batches/random")
async def get_random_website_topic_batch(
    website_topic_batch_service: WebsiteTopicBatchService = Depends(get_website_topic_batch_service),
) -> dict[str, object]:
    """读取数据库里的官网主题 chip。"""
    return ajax_success(await website_topic_batch_service.get_random_topic_batch())


# 执行list website generation model profiles相关逻辑。
@router.get("/generation-models")
async def list_website_generation_models(
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """读取官网生成页自定义选项里的模型可用性和 Credit。"""
    return ajax_success(dynamic_view_service.list_generation_model_options(db))


# 执行list website generation sessions相关逻辑。
@router.get("/generation-sessions")
async def list_website_generation_sessions(
    user_id: str,
    limit: int = 12,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """读取已登录用户的网站生成会话列表。"""
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise HTTPException(status_code=400, detail="user_id 不能为空")
    sessions = website_generation_session_repository.list_user_sessions(
        db,
        user_id=normalized_user_id,
        limit=limit,
    )
    return table_data(
        _attach_task_snapshots_to_generation_sessions(
            sessions,
            dynamic_view_service=dynamic_view_service,
        )
    )


# 执行attach task snapshots to generation sessions相关逻辑。
def _attach_task_snapshots_to_generation_sessions(
    sessions: list[object],
    *,
    dynamic_view_service: DynamicViewService,
) -> list[dict[str, object]]:
    """近期对话列表以后端 task 快照为准，避免前端旧快照串到别的会话。"""
    rows: list[dict[str, object]] = []
    for session in sessions:
        row = session.model_dump(by_alias=True)
        tasks = row.get("tasks") if isinstance(row.get("tasks"), list) else []
        hydrated_tasks = [
            _attach_task_snapshot_to_generation_session_task(
                task,
                dynamic_view_service=dynamic_view_service,
            )
            for task in tasks
            if isinstance(task, dict)
        ]
        if hydrated_tasks:
            row["tasks"] = hydrated_tasks
            latest_task = hydrated_tasks[0]
            row.update(
                {
                    "topic": latest_task.get("topic") or row.get("topic"),
                    "taskId": latest_task.get("taskId") or "",
                    "stage": latest_task.get("stage") or "",
                    "message": latest_task.get("message") or "",
                    "nodeKey": latest_task.get("nodeKey") or "",
                    "nodeStatus": latest_task.get("nodeStatus") or "",
                    "payloadStatus": latest_task.get("payloadStatus") or "",
                    "isTerminal": latest_task.get("isTerminal") or 0,
                    "htmlUrl": latest_task.get("htmlUrl") or "",
                    "snapshot": latest_task.get("snapshot"),
                    "updatedAt": latest_task.get("updatedAt") or row.get("updatedAt"),
                }
            )
            rows.append(row)
            continue
        task_id = str(row.get("taskId") or "").strip()
        if task_id:
            row = _attach_task_snapshot_to_generation_session_task(
                row,
                dynamic_view_service=dynamic_view_service,
            )
        rows.append(row)
    return rows


# 执行attach task snapshot to generation session task相关逻辑。
def _attach_task_snapshot_to_generation_session_task(
    task: dict[str, object],
    *,
    dynamic_view_service: DynamicViewService,
) -> dict[str, object]:
    """给单条官网生成会话任务补齐后台 task 快照。"""
    row = dict(task)
    task_id = str(row.get("taskId") or "").strip()
    if not task_id:
        return row
    try:
        snapshot = dynamic_view_service.get_generation_task_snapshot(task_id)
    except ValueError:
        return row
    snapshot_data = snapshot.model_dump(by_alias=True)
    payload = snapshot_data.get("payload") if isinstance(snapshot_data.get("payload"), dict) else {}
    row.update(
        {
            "topic": snapshot_data.get("topic") or row.get("topic"),
            "stage": snapshot_data.get("stage") or "",
            "message": snapshot_data.get("message") or "",
            "nodeKey": snapshot_data.get("nodeKey") or "",
            "nodeStatus": snapshot_data.get("nodeStatus") or "",
            "payloadStatus": payload.get("status") or "",
            "isTerminal": 1 if snapshot_data.get("isTerminal") else 0,
            "htmlUrl": payload.get("htmlUrl") or row.get("htmlUrl") or "",
            "snapshot": snapshot_data,
            "updatedAt": snapshot_data.get("updatedAt") or row.get("updatedAt"),
        }
    )
    return row


# 执行create website generation session相关逻辑。
@router.post("/generation-sessions")
async def create_website_generation_session(
    request: WebsiteGenerationSessionCreateRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """保存已登录用户的一条网站生成会话。"""
    return ajax_success(
        website_generation_session_repository.create_session(
            db,
            request=request,
        )
    )


# 执行update website generation session status相关逻辑。
@router.put("/generation-sessions/status")
async def update_website_generation_session_status(
    request: WebsiteGenerationSessionStatusRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """保存已登录用户的一条网站生成会话当前状态。"""
    try:
        return ajax_success(
            website_generation_session_repository.update_session_status(
                db,
                request=request,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# 执行delete website generation session相关逻辑。
@router.delete("/generation-sessions/{session_id}")
async def delete_website_generation_session(
    session_id: int,
    user_id: str,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """删除已登录用户的一条网站生成会话，并软删除对应任务和视图数据。"""
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise HTTPException(status_code=400, detail="user_id 不能为空")
    session = website_generation_session_repository.get_user_session(
        db,
        session_id=session_id,
        user_id=normalized_user_id,
    )
    if session is None:
        return ajax_success({"deleted": False})
    tasks = website_generation_session_repository.list_user_session_tasks(
        db,
        session_id=session_id,
        user_id=normalized_user_id,
    )
    try:
        for task in tasks:
            await _cancel_unfinished_generation_task_before_delete(
                db,
                dynamic_view_service=dynamic_view_service,
                task_id=task.task_id,
                user_id=normalized_user_id,
            )
            await dynamic_view_service.soft_delete_generation_task(
                task.task_id,
                user_id=normalized_user_id,
            )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    deleted = website_generation_session_repository.delete_user_session(db, archive=session)
    return ajax_success({"deleted": deleted})


# 执行cancel unfinished generation task before delete相关逻辑。
async def _cancel_unfinished_generation_task_before_delete(
    db: Session,
    *,
    dynamic_view_service: DynamicViewService,
    task_id: str,
    user_id: str,
) -> None:
    """删除近期对话前先中断未完成任务，保持删除和手动中断的扣费规则一致。"""
    try:
        snapshot = dynamic_view_service.get_generation_task_snapshot(task_id)
    except ValueError:
        return
    if snapshot.is_terminal:
        return
    if snapshot.author_id.strip() != user_id:
        raise PermissionError("不能删除其他用户的生成任务")
    await dynamic_view_service.cancel_generation_task(task_id)
    _refund_cancelled_generation_credits(
        db,
        dynamic_view_service=dynamic_view_service,
        snapshot=snapshot,
    )


# 执行refund cancelled generation credits相关逻辑。
def _refund_cancelled_generation_credits(
    db: Session,
    *,
    dynamic_view_service: DynamicViewService,
    snapshot: DynamicViewTaskSnapshot,
) -> None:
    """生成任务被删除触发中断时退回 50% Credit。"""
    user_id = snapshot.author_id.strip()
    if not user_id or user_id == "website":
        return
    refund_amount = dynamic_view_service.resolve_generation_model_credit_cost(db, snapshot.model_level) // 2
    if refund_amount <= 0:
        return
    credit_service.refund_credits(
        db,
        user_id=user_id,
        amount=refund_amount,
        source_key=snapshot.request_id or snapshot.task_id,
        reason="生成中断退回",
    )
    db.commit()
