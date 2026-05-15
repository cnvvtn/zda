# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\routes\dynamic_view.py。"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import logging
import secrets
import time
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, Response, UploadFile
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_dynamic_view_service, get_dynamic_view_task_service, get_generation_service
from app.api.response import ajax_success, table_data
from app.core.settings import settings
from app.core.url_catalog import PythonUrl
from app.features.dynamic_view.schemas import (
    DynamicViewAudioConfig,
    DynamicViewCommentCreateRequest,
    DynamicViewCreateRequest,
    DynamicViewDetailBootstrap,
    DynamicViewListItem,
    DynamicViewPayload,
    DynamicViewRevealPrincipleRequest,
    DynamicViewStreamEvent,
    DynamicViewTaskSnapshot,
    DynamicViewTaskCreateRequest,
    DynamicViewTaskGenerateRequest,
)
from app.features.dynamic_view.service import DynamicViewService
from app.features.dynamic_view.html_builder import (
    inject_dynamic_view_audio_config,
)
from app.services.credit_service import CreditService
from app.services.generation_service import GenerationService
from app.features.dynamic_view.task_service import DynamicViewTaskService
from app.db.models import DynamicViewGenerationRequest, DynamicViewTaskArchive


router = APIRouter(prefix=PythonUrl.DYNAMIC_VIEW_API_PREFIX.value, tags=["dynamic-view"])
logger = logging.getLogger(__name__)
credit_service = CreditService()
_DYNAMIC_VIEW_AUDIO_TOKEN_TTL_SECONDS = 600
_GUEST_GENERATION_COOKIE = "zdaGuestId"
_GUEST_GENERATION_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365
_DYNAMIC_VIEW_KNOWLEDGE_QA_IMAGE_MAX_BYTES = 2 * 1024 * 1024


# 执行list dynamic views相关逻辑。
@router.get("")
async def list_dynamic_views(
    http_request: Request,
    cursor_id: int | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """返回首页动态视图列表，只暴露可直接播放的真实数据。"""
    rows = _attach_playable_html_url_to_list_items(
        http_request,
        dynamic_view_service.list_home_archives(
            db,
            cursor_id=cursor_id,
            limit=limit,
        ),
    )
    return table_data(rows)


# 执行answer dynamic view knowledge question相关逻辑。
@router.post("/knowledge-qa")
async def answer_dynamic_view_knowledge_question(
    image: UploadFile = File(...),
    vivid: str = Form(""),
    ext: str = Form(""),
    en: str = Form(""),
    user_input: str = Form(""),
    generation_service: GenerationService = Depends(get_generation_service),
) -> dict[str, object]:
    """用 generation 模型结合当前知识画面回答用户输入。"""
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="当前画面截图不能为空")
    if len(image_bytes) > _DYNAMIC_VIEW_KNOWLEDGE_QA_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="当前画面截图过大")
    content_type = (image.content_type or "image/webp").strip()
    encoded_image = base64.b64encode(image_bytes).decode("ascii")
    answer = await generation_service.answer_dynamic_view_knowledge_question(
        image_data_url=f"data:{content_type};base64,{encoded_image}",
        vivid=vivid,
        ext=ext,
        en=en,
        user_input=user_input,
    )
    return ajax_success({"answer": answer})


# 执行create dynamic view task相关逻辑。
@router.post("/tasks")
async def create_dynamic_view_task(
    http_request: Request,
    http_response: Response,
    request: DynamicViewCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """创建后台动态视图任务，并交给后端自动完成主题分析和生成链路。"""
    generation_request_context = _resolve_generation_request_context(
        db,
        http_request=http_request,
        request=request,
    )
    if not generation_request_context["user_id"]:
        _assert_anonymous_generation_model(request)
        _enforce_anonymous_generation_request_limit(db, context=generation_request_context)
        if generation_request_context["set_guest_cookie"]:
            _set_guest_generation_cookie(http_response, str(generation_request_context["guest_id"]))
    logger.info(
        "Dynamic view async task requested: topic=%s, sceneCountMin=%s",
        request.topic.strip(),
        request.scene_count_min,
    )
    generation_credit_cost = dynamic_view_service.resolve_generation_model_credit_cost(db, request.model_level)
    task_request = request.model_copy(update={"start_immediately": False})
    charged = False
    try:
        snapshot = dynamic_view_service.create_generation_task(
            task_request,
            source_type="dynamic_view",
            source_model=request.model_level,
        )
        if generation_request_context["user_id"]:
            credit_service.consume_credits(
                db,
                user_id=str(generation_request_context["user_id"]),
                amount=generation_credit_cost,
                model_level=request.model_level,
                usage_type="dynamic_view",
                request_id=snapshot.request_id,
            )
            db.commit()
            charged = True
        _record_generation_request(
            db,
            context=generation_request_context,
            request=request,
            snapshot=snapshot,
            dynamic_view_service=dynamic_view_service,
            credit_cost=generation_credit_cost if generation_request_context["user_id"] else 0,
        )
        _schedule_generation_after_topic_analysis(
            background_tasks=background_tasks,
            request=request,
            task_id=snapshot.task_id,
            request_id=snapshot.request_id,
            user_id=str(generation_request_context["user_id"]),
            credit_cost=generation_credit_cost if generation_request_context["user_id"] else 0,
            dynamic_view_service=dynamic_view_service,
        )
    except Exception:
        if charged:
            _refund_topic_analysis_credits(
                db,
                user_id=str(generation_request_context["user_id"]),
                request_id=snapshot.request_id,
                credit_cost=generation_credit_cost,
                reason="生成任务创建失败退回",
            )
        raise
    return ajax_success(
        _attach_playable_urls_to_task_snapshot(
            http_request,
            snapshot,
            credit_cost=generation_credit_cost if generation_request_context["user_id"] else 0,
        )
    )


# 执行schedule generation after topic analysis相关逻辑。
def _schedule_generation_after_topic_analysis(
    *,
    background_tasks: BackgroundTasks,
    request: DynamicViewCreateRequest,
    task_id: str,
    request_id: str,
    user_id: str,
    credit_cost: int,
    dynamic_view_service: DynamicViewService,
) -> None:
    """任务创建成功后在响应发送后启动前置分析，避免创建接口等待主题分析。"""
    background_tasks.add_task(
        _run_generation_after_topic_analysis_with_log,
        request=request,
        task_id=task_id,
        request_id=request_id,
        user_id=user_id,
        credit_cost=credit_cost,
        dynamic_view_service=dynamic_view_service,
    )


# 执行run generation after topic analysis with log相关逻辑。
async def _run_generation_after_topic_analysis_with_log(
    *,
    request: DynamicViewCreateRequest,
    task_id: str,
    request_id: str,
    user_id: str,
    credit_cost: int,
    dynamic_view_service: DynamicViewService,
) -> None:
    """执行响应后的主题分析链路，并记录未捕获异常。"""
    try:
        await _run_generation_after_topic_analysis(
            request=request,
            task_id=task_id,
            request_id=request_id,
            user_id=user_id,
            credit_cost=credit_cost,
            dynamic_view_service=dynamic_view_service,
        )
    except Exception:
        logger.exception(
            "Dynamic view topic analysis pipeline failed: task_id=%s",
            task_id,
        )


# 执行run generation after topic analysis相关逻辑。
async def _run_generation_after_topic_analysis(
    *,
    request: DynamicViewCreateRequest,
    task_id: str,
    request_id: str,
    user_id: str,
    credit_cost: int,
    dynamic_view_service: DynamicViewService,
) -> None:
    """后端自动完成主题违规分析；通过后继续启动当前 taskId 的生成任务。"""
    dynamic_view_service.update_generation_task_stage(
        task_id,
        stage="topic_analysis",
        message="正在分析主题合规性",
        node_title="主题分析",
        node_status="processing",
        payload_status="topic_analysis",
        generation_status="topic_analyzing",
    )
    try:
        analysis_result = await dynamic_view_service.analyze_generation_topic(
            request.topic,
            model_level=request.model_level,
        )
    except Exception as error:
        dynamic_view_service.mark_generation_task_failed(
            task_id,
            message="主题分析失败，请重新输入主题。",
        )
        if user_id and request_id:
            with dynamic_view_service.session_factory() as db:
                _refund_topic_analysis_credits(
                    db,
                    user_id=user_id,
                    request_id=request_id,
                    credit_cost=credit_cost,
                    reason="主题分析失败退回",
                )
        raise error
    blocked_by_topic_analysis = analysis_result.analysis_status == "violation"
    dynamic_view_service.update_generation_task_stage(
        task_id,
        stage="awaiting_topic_action" if blocked_by_topic_analysis else "topic_analysis",
        message=analysis_result.decision_summary or "主题分析已完成",
        node_title="主题分析",
        node_status="waiting" if blocked_by_topic_analysis else "completed",
        payload_status="waiting_user_action" if blocked_by_topic_analysis else "topic_analysis_completed",
        generation_status=f"topic_{analysis_result.analysis_status}",
    )
    if blocked_by_topic_analysis:
        if user_id and request_id:
            with dynamic_view_service.session_factory() as db:
                _refund_topic_analysis_credits(
                    db,
                    user_id=user_id,
                    request_id=request_id,
                    credit_cost=credit_cost,
                    reason="主题违规退回",
                )
        return
    dynamic_view_service.start_existing_generation_task(
        task_id,
        request,
        source_type="dynamic_view",
        source_model=request.model_level,
    )


# 执行start dynamic view task相关逻辑。
@router.post("/tasks/{task_id}/start")
async def start_dynamic_view_task(
    task_id: str,
    http_request: Request,
    request: DynamicViewCreateRequest,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """启动已创建的后台动态视图任务，供官网先建 task 后分析再启动。"""
    generation_request_context = _resolve_generation_request_context(
        db,
        http_request=http_request,
        request=request,
    )
    try:
        current_snapshot = dynamic_view_service.get_generation_task_snapshot(task_id)
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    if dynamic_view_service.is_generation_task_running(task_id):
        return ajax_success(
            _attach_playable_urls_to_task_snapshot(
                http_request,
                current_snapshot,
                credit_cost=_resolve_snapshot_credit_cost(db, dynamic_view_service, current_snapshot),
            )
        )
    restart_request_id = ""
    restart_charged = False
    if _should_charge_generation_restart(current_snapshot, generation_request_context):
        restart_request_id = uuid4().hex
        generation_credit_cost = dynamic_view_service.resolve_generation_model_credit_cost(db, request.model_level)
        credit_service.consume_credits(
            db,
            user_id=str(generation_request_context["user_id"]),
            amount=generation_credit_cost,
            model_level=request.model_level,
            usage_type="dynamic_view",
            request_id=restart_request_id,
        )
        db.commit()
        restart_charged = True
    try:
        snapshot = dynamic_view_service.start_existing_generation_task(
            task_id,
            request,
            source_type="dynamic_view",
            source_model=request.model_level,
            request_id=restart_request_id,
        )
    except ValueError as error:
        if restart_charged:
            _refund_topic_analysis_credits(
                db,
                user_id=str(generation_request_context["user_id"]),
                request_id=restart_request_id,
                credit_cost=dynamic_view_service.resolve_generation_model_credit_cost(db, request.model_level),
                reason="重新生成启动失败退回",
            )
        raise _build_http_exception(status_code=404, error=error) from error
    except Exception:
        if restart_charged:
            _refund_topic_analysis_credits(
                db,
                user_id=str(generation_request_context["user_id"]),
                request_id=restart_request_id,
                credit_cost=dynamic_view_service.resolve_generation_model_credit_cost(db, request.model_level),
                reason="重新生成启动失败退回",
            )
        elif generation_request_context["user_id"]:
            _refund_topic_analysis_credits(
                db,
                user_id=str(generation_request_context["user_id"]),
                request_id=current_snapshot.request_id,
                credit_cost=dynamic_view_service.resolve_generation_model_credit_cost(db, request.model_level),
                reason="生成任务启动失败退回",
            )
        raise
    return ajax_success(
        _attach_playable_urls_to_task_snapshot(
            http_request,
            snapshot,
            credit_cost=_resolve_snapshot_credit_cost(db, dynamic_view_service, snapshot),
        )
    )


# 执行get latest dynamic view task相关逻辑。
@router.get("/tasks/latest")
async def get_latest_dynamic_view_task(
    http_request: Request,
    author_id: str,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """读取当前用户最近一条动态视图任务快照，供 Flutter 直接从后端恢复创建页。"""
    try:
        snapshot = dynamic_view_service.get_latest_generation_task_snapshot(author_id)
        return ajax_success(
            _attach_playable_urls_to_task_snapshot(
                http_request,
                snapshot,
                credit_cost=_resolve_snapshot_credit_cost(db, dynamic_view_service, snapshot),
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行get dynamic view task相关逻辑。
@router.get("/tasks/{task_id}")
async def get_dynamic_view_task(
    task_id: str,
    http_request: Request,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """读取后台动态视图任务快照，供前端恢复创建状态。"""
    try:
        snapshot = dynamic_view_service.get_generation_task_snapshot(task_id)
        return ajax_success(
            _attach_playable_urls_to_task_snapshot(
                http_request,
                snapshot,
                credit_cost=_resolve_snapshot_credit_cost(db, dynamic_view_service, snapshot),
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行cancel dynamic view task相关逻辑。
@router.post("/tasks/{task_id}/cancel")
async def cancel_dynamic_view_task(
    task_id: str,
    http_request: Request,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """取消后台动态视图任务，并返回取消后的终态快照。"""
    try:
        snapshot_before_cancel = dynamic_view_service.get_generation_task_snapshot(task_id)
        cancel_payload = await _read_dynamic_view_cancel_payload(http_request)
        _assert_dynamic_view_cancel_owner(snapshot_before_cancel, cancel_payload)
        cancelled_snapshot = await dynamic_view_service.cancel_generation_task(task_id)
        _refund_cancelled_generation_credits(
            db,
            dynamic_view_service=dynamic_view_service,
            snapshot=snapshot_before_cancel,
            task_id=task_id,
        )
        return ajax_success(
            _attach_playable_urls_to_task_snapshot(
                http_request,
                cancelled_snapshot,
                credit_cost=_resolve_snapshot_credit_cost(db, dynamic_view_service, cancelled_snapshot),
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行read dynamic view cancel payload相关逻辑。
async def _read_dynamic_view_cancel_payload(http_request: Request) -> dict[str, object]:
    """读取取消请求体，空请求体按匿名取消处理。"""
    try:
        payload = await http_request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


# 执行assert dynamic view cancel owner相关逻辑。
def _assert_dynamic_view_cancel_owner(
    snapshot: DynamicViewTaskSnapshot,
    payload: dict[str, object],
) -> None:
    """登录用户只能取消自己 authorId 下的动态视图任务。"""
    task_author_id = snapshot.author_id.strip()
    if task_author_id.lower() in {"", "system", "website"}:
        return
    request_author_id = str(
        payload.get("authorId")
        or payload.get("author_id")
        or payload.get("userId")
        or payload.get("user_id")
        or ""
    ).strip()
    if request_author_id != task_author_id:
        raise HTTPException(status_code=403, detail="不能取消其他用户的生成任务")


@router.get("/{game_view_id}")
async def get_dynamic_view_detail(
    game_view_id: int,
    http_request: Request,
    user_id: str | None = None,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """返回单条动态视图详情，并在用户打开时累加一次观看次数。"""
    try:
        return ajax_success(
            _attach_playable_urls_to_payload(
                http_request,
                dynamic_view_service.build_game_payload(
                    db,
                    game_view_id=game_view_id,
                    increase_view_count=True,
                    user_id=user_id,
                ),
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行get dynamic view audio相关逻辑。
@router.get("/{game_view_id}/audio")
async def get_dynamic_view_audio(
    game_view_id: int,
    expires: int | None = None,
    signature: str = "",
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> FileResponse:
    """返回单条游戏动态视图的固定背景音乐文件。"""
    _verify_dynamic_view_audio_signature(
        view_type="game",
        archive_id=game_view_id,
        kind="background",
        expires=expires,
        signature=signature,
    )
    try:
        audio_path = dynamic_view_service.resolve_audio_file_path(
            db,
            view_type="game",
            archive_id=game_view_id,
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    return FileResponse(
        path=audio_path,
        media_type=mimetypes.guess_type(str(audio_path))[0] or "audio/mpeg",
        headers={"Content-Disposition": "inline"},
    )


# 执行get dynamic view audio config相关逻辑。
@router.get("/{game_view_id}/audio-config")
async def get_dynamic_view_audio_config(
    game_view_id: int,
    http_request: Request,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """播放前实时返回游戏动态视图的背景音乐地址和音量。"""
    try:
        payload = dynamic_view_service.build_game_payload(
            db,
            game_view_id=game_view_id,
            increase_view_count=False,
            user_id=None,
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    return ajax_success(
        {
            "background": _build_dynamic_view_audio_config_item(
                http_request,
                view_type="game",
                archive_id=game_view_id,
                kind="background",
                audio=payload.audio,
            ),
            "subtitle": None,
        }
    )


# 执行get dynamic view html related logic。
@router.get("/{game_view_id}/html", response_class=HTMLResponse)
async def get_dynamic_view_html(
    http_request: Request,
    game_view_id: int,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> HTMLResponse:
    """返回单条游戏动态视图的 HTML 页面，支持通过查询参数切换封面或完整播放态。"""
    try:
        html_path = dynamic_view_service.resolve_archive_html_file_path(
            db,
            view_type="game",
            archive_id=game_view_id,
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    # 执行build game payload相关逻辑。
    payload = dynamic_view_service.build_game_payload(
        db,
        game_view_id=game_view_id,
        increase_view_count=False,
        user_id=None,
    )
    return HTMLResponse(
        content=inject_dynamic_view_audio_config(
            html_path.read_text(encoding="utf-8"),
            audio=payload.audio,
            subtitle_audio=payload.subtitle_audio,
            base_url=str(http_request.base_url).rstrip("/"),
        )
    )


# 执行get dynamic view knowledge html related logic。
@router.get("/knowledge-view/{knowledge_view_id}/html", response_class=HTMLResponse)
async def get_dynamic_view_knowledge_html(
    http_request: Request,
    knowledge_view_id: int,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> HTMLResponse:
    """返回单条知识动态视图的 HTML 页面，支持通过查询参数切换封面或完整播放态。"""
    try:
        html_path = dynamic_view_service.resolve_archive_html_file_path(
            db,
            view_type="knowledge",
            archive_id=knowledge_view_id,
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    # 执行build knowledge payload相关逻辑。
    payload = dynamic_view_service.build_knowledge_payload(
        db,
        knowledge_view_id=knowledge_view_id,
        increase_view_count=False,
    )
    return HTMLResponse(
        content=inject_dynamic_view_audio_config(
            html_path.read_text(encoding="utf-8"),
            audio=payload.audio,
            subtitle_audio=payload.subtitle_audio,
            base_url=str(http_request.base_url).rstrip("/"),
        )
    )


# 执行get dynamic view knowledge detail相关逻辑。
@router.get("/knowledge-view/{knowledge_view_id}")
async def get_dynamic_view_knowledge_detail(
    knowledge_view_id: int,
    http_request: Request,
    increase_view_count: bool = False,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """返回单条知识动态视图详情。"""
    try:
        return ajax_success(
            _attach_playable_urls_to_payload(
                http_request,
                dynamic_view_service.build_knowledge_payload(
                    db,
                    knowledge_view_id=knowledge_view_id,
                    increase_view_count=increase_view_count,
                ),
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行get dynamic view knowledge audio相关逻辑。
@router.get("/knowledge-view/{knowledge_view_id}/audio")
async def get_dynamic_view_knowledge_audio(
    knowledge_view_id: int,
    expires: int | None = None,
    signature: str = "",
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> FileResponse:
    """返回单条知识动态视图的固定背景音乐文件。"""
    _verify_dynamic_view_audio_signature(
        view_type="knowledge",
        archive_id=knowledge_view_id,
        kind="background",
        expires=expires,
        signature=signature,
    )
    try:
        audio_path = dynamic_view_service.resolve_audio_file_path(
            db,
            view_type="knowledge",
            archive_id=knowledge_view_id,
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    return FileResponse(
        path=audio_path,
        media_type=mimetypes.guess_type(str(audio_path))[0] or "audio/mpeg",
        headers={"Content-Disposition": "inline"},
    )


# 执行get dynamic view knowledge audio config相关逻辑。
@router.get("/knowledge-view/{knowledge_view_id}/audio-config")
async def get_dynamic_view_knowledge_audio_config(
    knowledge_view_id: int,
    http_request: Request,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """播放前实时返回知识动态视图的背景音乐、字幕音频地址和音量。"""
    try:
        payload = dynamic_view_service.build_knowledge_payload(
            db,
            knowledge_view_id=knowledge_view_id,
            increase_view_count=False,
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    return ajax_success(
        {
            "background": _build_dynamic_view_audio_config_item(
                http_request,
                view_type="knowledge",
                archive_id=knowledge_view_id,
                kind="background",
                audio=payload.audio,
            ),
            "subtitle": _build_dynamic_view_audio_config_item(
                http_request,
                view_type="knowledge",
                archive_id=knowledge_view_id,
                kind="subtitle",
                audio=payload.subtitle_audio,
            ),
        }
    )


# 执行get dynamic view knowledge subtitle audio相关逻辑。
@router.get("/knowledge-view/{knowledge_view_id}/subtitle-audio")
async def get_dynamic_view_knowledge_subtitle_audio(
    knowledge_view_id: int,
    expires: int | None = None,
    signature: str = "",
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> FileResponse:
    """返回单条知识动态视图的字幕音频文件。"""
    _verify_dynamic_view_audio_signature(
        view_type="knowledge",
        archive_id=knowledge_view_id,
        kind="subtitle",
        expires=expires,
        signature=signature,
    )
    try:
        subtitle_audio_path = dynamic_view_service.resolve_subtitle_audio_file_path(
            db,
            knowledge_archive_id=knowledge_view_id,
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error
    return FileResponse(
        path=subtitle_audio_path,
        media_type=mimetypes.guess_type(str(subtitle_audio_path))[0] or "audio/wav",
        headers={"Content-Disposition": "inline"},
    )


# 执行get dynamic view detail bootstrap相关逻辑。
@router.get("/{game_view_id}/bootstrap")
async def get_dynamic_view_detail_bootstrap(
    game_view_id: int,
    http_request: Request,
    comment_limit: int = 10,
    increase_view_count: bool = True,
    user_id: str | None = None,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """返回动态视图详情页首屏初始化数据，并在进入详情时累加一次观看次数。"""
    try:
        return ajax_success(
            _attach_playable_urls_to_bootstrap(
                http_request,
                dynamic_view_service.build_game_detail_bootstrap(
                    db,
                    game_view_id=game_view_id,
                    comment_limit=comment_limit,
                    increase_view_count=increase_view_count,
                    user_id=user_id,
                ),
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行list dynamic view comments相关逻辑。
@router.get("/{game_view_id}/comments")
async def list_dynamic_view_comments(
    game_view_id: int,
    cursor_id: int | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """按游标分页返回动态视图评论，供详情页上拉继续加载。"""
    try:
        return ajax_success(
            dynamic_view_service.list_archive_comments(
                db,
                archive_id=game_view_id,
                view_type="game",
                cursor_id=cursor_id,
                limit=limit,
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行create dynamic view comment相关逻辑。
@router.post("/{game_view_id}/comments")
async def create_dynamic_view_comment(
    game_view_id: int,
    request: DynamicViewCommentCreateRequest,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """创建动态视图评论或回复评论。"""
    try:
        return ajax_success(
            dynamic_view_service.create_archive_comment(
                db,
                archive_id=game_view_id,
                view_type="game",
                request=request,
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=400, error=error) from error


# 执行list dynamic view knowledge comments相关逻辑。
@router.get("/knowledge-view/{knowledge_view_id}/comments")
async def list_dynamic_view_knowledge_comments(
    knowledge_view_id: int,
    cursor_id: int | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """按游标分页返回知识动态视图评论。"""
    try:
        return ajax_success(
            dynamic_view_service.list_archive_comments(
                db,
                archive_id=knowledge_view_id,
                view_type="knowledge",
                cursor_id=cursor_id,
                limit=limit,
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=404, error=error) from error


# 执行create dynamic view knowledge comment相关逻辑。
@router.post("/knowledge-view/{knowledge_view_id}/comments")
async def create_dynamic_view_knowledge_comment(
    knowledge_view_id: int,
    request: DynamicViewCommentCreateRequest,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """创建知识动态视图评论或回复评论。"""
    try:
        return ajax_success(
            dynamic_view_service.create_archive_comment(
                db,
                archive_id=knowledge_view_id,
                view_type="knowledge",
                request=request,
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=400, error=error) from error


# 执行reveal dynamic view principle相关逻辑。
@router.post("/{game_view_id}/reveal-principle")
async def reveal_dynamic_view_principle(
    game_view_id: int,
    request: DynamicViewRevealPrincipleRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """为当前用户直接揭开指定动态视图的全部线索，并立刻触发知识视图生成。"""
    try:
        return ajax_success(
            _attach_playable_urls_to_payload(
                http_request,
                dynamic_view_service.reveal_principle(
                    db,
                    game_view_id=game_view_id,
                    user_id=request.user_id,
                ),
            )
        )
    except ValueError as error:
        raise _build_http_exception(status_code=400, error=error) from error


# 执行create dynamic view相关逻辑。
@router.post("")
async def create_dynamic_view(
    http_request: Request,
    request: DynamicViewCreateRequest,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> dict[str, object]:
    """执行一次完整动态视图创建，并直接返回最终 HTML 结果。"""
    logger.info(
        "Dynamic view create requested: topic=%s, sceneCountMin=%s",
        request.topic.strip(),
        request.scene_count_min,
    )
    return ajax_success(
        _attach_playable_urls_to_payload(
            http_request,
            await dynamic_view_service.create_dynamic_view_payload(db, request),
        )
    )


# 执行generate dynamic view source tasks相关逻辑。
@router.post("/task-sources/generate")
async def generate_dynamic_view_source_tasks(
    request: DynamicViewTaskGenerateRequest,
    dynamic_view_task_service: DynamicViewTaskService = Depends(get_dynamic_view_task_service),
) -> dict[str, object]:
    """调用专用任务模型批量生成任务，并把新任务写入数据库来源表。"""
    logger.info(
        "Dynamic view source task generation requested: count=%s",
        request.count,
    )
    return ajax_success(
        await dynamic_view_task_service.generate_and_store_tasks(
            count=request.count,
        )
    )


# 执行create dynamic view source tasks相关逻辑。
@router.post("/task-sources/create")
async def create_dynamic_view_source_tasks(
    request: DynamicViewTaskCreateRequest,
    dynamic_view_task_service: DynamicViewTaskService = Depends(get_dynamic_view_task_service),
) -> dict[str, object]:
    """把手动输入的任务列表直接写入动态视图任务来源表。"""
    logger.info(
        "Dynamic view source task manual create requested: taskCount=%s",
        len(request.tasks),
    )
    return ajax_success(
        await dynamic_view_task_service.create_and_store_tasks(
            raw_tasks=request.tasks,
        )
    )


# 执行create dynamic view stream相关逻辑。
@router.post("/stream")
async def create_dynamic_view_stream(
    http_request: Request,
    request: DynamicViewCreateRequest,
    db: Session = Depends(get_db),
    dynamic_view_service: DynamicViewService = Depends(get_dynamic_view_service),
) -> StreamingResponse:
    """按 NDJSON 流式返回创建状态，供 Flutter 创建页渲染节点卡片数据流。"""
    logger.info(
        "Dynamic view stream requested: topic=%s, sceneCountMin=%s",
        request.topic.strip(),
        request.scene_count_min,
    )
    return StreamingResponse(
        _iter_dynamic_view_stream_lines(
            http_request=http_request,
            db=db,
            request=request,
            dynamic_view_service=dynamic_view_service,
        ),
        media_type="application/x-ndjson; charset=utf-8",
    )


# 执行iter dynamic view stream lines相关逻辑。
async def _iter_dynamic_view_stream_lines(
    *,
    http_request: Request,
    db: Session,
    request: DynamicViewCreateRequest,
    dynamic_view_service: DynamicViewService,
) -> AsyncIterator[str]:
    """把服务层动态视图阶段结果序列化成 NDJSON 行，便于前端逐条消费。"""
    async for stream_chunk in dynamic_view_service.stream_dynamic_view(
        db,
        request,
        disconnect_checker=http_request.is_disconnected,
    ):
        if await http_request.is_disconnected():
            return
        stream_event = DynamicViewStreamEvent(
            stage=stream_chunk.stage,
            message=stream_chunk.payload.preview_text.strip(),
            nodeTitle=stream_chunk.node_title,
            nodeStatus=stream_chunk.node_status,
            streamCharCount=stream_chunk.stream_char_count,
            progress=1.0
            if stream_chunk.is_final and stream_chunk.payload.status == "ready"
            else None,
            generationStatus=stream_chunk.stage,
            isFinal=stream_chunk.is_final,
            payload=_attach_playable_urls_to_payload(
                http_request,
                stream_chunk.payload,
            ),
        )
        yield json.dumps(ajax_success(stream_event), ensure_ascii=False) + "\n"
        if stream_chunk.is_final:
            return


# 执行attach Playable Html Url To List Items相关逻辑。
def _attach_playable_html_url_to_list_items(
    http_request: Request,
    items: list[DynamicViewListItem],
) -> list[DynamicViewListItem]:
    """把首页列表项里的 HTML 地址补成当前请求可直接访问的绝对链接。"""
    return [
        _attach_playable_html_url_to_list_item(http_request, item)
        for item in items
    ]


# 执行attach Playable Html Url To List Item相关逻辑。
def _attach_playable_html_url_to_list_item(
    http_request: Request,
    item: DynamicViewListItem,
) -> DynamicViewListItem:
    """把单个首页列表项的 HTML 地址补成当前请求可直接访问的绝对链接。"""
    normalized_base_url = str(http_request.base_url).rstrip("/")
    normalized_html_url = item.html_url.strip()
    if not normalized_html_url:
        return item
    if normalized_html_url.startswith(("http://", "https://")):
        return item
    return item.model_copy(update={"html_url": f"{normalized_base_url}{normalized_html_url}"})


# 执行build dynamic view audio config item相关逻辑。
def _build_dynamic_view_audio_config_item(
    http_request: Request,
    *,
    view_type: str,
    archive_id: int,
    kind: str,
    audio: DynamicViewAudioConfig | None,
) -> dict[str, object] | None:
    """生成带短期签名的音频播放配置。"""
    if audio is None or not audio.path.strip():
        return None
    expires = int(time.time()) + _DYNAMIC_VIEW_AUDIO_TOKEN_TTL_SECONDS
    signed_url = _build_signed_dynamic_view_audio_url(
        http_request,
        path=audio.path,
        view_type=view_type,
        archive_id=archive_id,
        kind=kind,
        expires=expires,
    )
    return {
        "src": signed_url,
        "volume": max(0, min(100, int(audio.volume))),
    }


# 执行build signed dynamic view audio url相关逻辑。
def _build_signed_dynamic_view_audio_url(
    http_request: Request,
    *,
    path: str,
    view_type: str,
    archive_id: int,
    kind: str,
    expires: int,
) -> str:
    """把音频接口路径补成带签名查询参数的绝对 URL。"""
    signature = _sign_dynamic_view_audio_token(
        view_type=view_type,
        archive_id=archive_id,
        kind=kind,
        expires=expires,
    )
    separator = "&" if "?" in path else "?"
    signed_path = f"{path}{separator}{urlencode({'expires': expires, 'signature': signature})}"
    if signed_path.startswith(("http://", "https://")):
        return signed_path
    return f"{str(http_request.base_url).rstrip('/')}{signed_path}"


# 执行verify dynamic view audio signature相关逻辑。
def _verify_dynamic_view_audio_signature(
    *,
    view_type: str,
    archive_id: int,
    kind: str,
    expires: int | None,
    signature: str,
) -> None:
    """校验音频文件访问签名，防止直接盗链固定文件接口。"""
    if expires is None or not signature.strip():
        raise HTTPException(status_code=403, detail="缺少音频访问签名")
    if expires < int(time.time()):
        raise HTTPException(status_code=403, detail="音频访问签名已过期")
    expected_signature = _sign_dynamic_view_audio_token(
        view_type=view_type,
        archive_id=archive_id,
        kind=kind,
        expires=expires,
    )
    if not hmac.compare_digest(signature.strip(), expected_signature):
        raise HTTPException(status_code=403, detail="音频访问签名无效")


# 执行sign dynamic view audio token相关逻辑。
def _sign_dynamic_view_audio_token(
    *,
    view_type: str,
    archive_id: int,
    kind: str,
    expires: int,
) -> str:
    """用服务端密钥签出动态视图音频访问令牌。"""
    payload = f"{view_type}|{archive_id}|{kind}|{expires}".encode("utf-8")
    digest = hmac.new(
        settings.mqtt.topic_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# 执行attach Playable Urls To Bootstrap相关逻辑。
def _attach_playable_urls_to_bootstrap(
    http_request: Request,
    bootstrap: DynamicViewDetailBootstrap,
) -> DynamicViewDetailBootstrap:
    """把详情首屏里的可播放地址补成当前请求可直接访问的绝对链接。"""
    return bootstrap.model_copy(
        update={
            "payload": _attach_playable_urls_to_payload(
                http_request,
                bootstrap.payload,
            )
        }
    )


# 执行attach Playable Urls To Task Snapshot相关逻辑。
def _attach_playable_urls_to_task_snapshot(
    http_request: Request,
    snapshot: DynamicViewTaskSnapshot,
    *,
    credit_cost: int = 0,
) -> DynamicViewTaskSnapshot:
    """把任务快照里的可播放地址补成当前请求可直接访问的绝对链接。"""
    return snapshot.model_copy(
        update={
            "credit_cost": int(credit_cost),
            "payload": _attach_playable_urls_to_payload(
                http_request,
                snapshot.payload,
            )
        }
    )


# 执行resolve snapshot credit cost相关逻辑。
def _resolve_snapshot_credit_cost(
    db: Session,
    dynamic_view_service: DynamicViewService,
    snapshot: DynamicViewTaskSnapshot,
) -> int:
    """按任务作者和模型配置表解析快照显示用 Credit。"""
    if snapshot.author_id.strip() in {"", "website", "system"}:
        return 0
    return dynamic_view_service.resolve_generation_model_credit_cost(db, snapshot.model_level)


# 执行should charge generation restart相关逻辑。
def _should_charge_generation_restart(
    snapshot: DynamicViewTaskSnapshot,
    context: dict[str, object],
) -> bool:
    """登录用户对已结束任务重新生成时，需要按新尝试重新扣除 Credit。"""
    if not context["user_id"]:
        return False
    return snapshot.is_terminal


# 执行attach Playable Urls To Payload相关逻辑。
def _attach_playable_urls_to_payload(
    http_request: Request,
    payload: DynamicViewPayload,
) -> DynamicViewPayload:
    """把载荷里的 HTML、背景音乐和字幕音频路径补成当前请求可直接消费的绝对链接。"""
    normalized_base_url = str(http_request.base_url).rstrip("/")
    next_payload = payload
    normalized_html_url = next_payload.html_url.strip()
    if normalized_html_url and not normalized_html_url.startswith(("http://", "https://")):
        next_payload = next_payload.model_copy(
            update={"html_url": f"{normalized_base_url}{normalized_html_url}"}
        )
    if next_payload.audio is not None:
        normalized_audio_path = next_payload.audio.path.strip()
        if normalized_audio_path and not normalized_audio_path.startswith(("http://", "https://")):
            next_payload = next_payload.model_copy(
                update={
                    "audio": next_payload.audio.model_copy(
                        update={"path": f"{normalized_base_url}{normalized_audio_path}"}
                    )
                }
            )
    if next_payload.subtitle_audio is not None:
        normalized_subtitle_audio_path = next_payload.subtitle_audio.path.strip()
        if normalized_subtitle_audio_path and not normalized_subtitle_audio_path.startswith(("http://", "https://")):
            next_payload = next_payload.model_copy(
                update={
                    "subtitle_audio": next_payload.subtitle_audio.model_copy(
                        update={"path": f"{normalized_base_url}{normalized_subtitle_audio_path}"}
                    )
                }
            )
    return next_payload


# 执行resolve client ip相关逻辑。
def _resolve_client_ip(http_request: Request) -> str:
    """从代理头或连接信息里解析请求 IP。"""
    if http_request.client is None:
        return ""
    client_host = str(http_request.client.host or "").strip()
    forwarded_for = http_request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for and _is_trusted_proxy_address(client_host):
        return forwarded_for.split(",")[0].strip()[:64]
    return client_host[:64]


# 执行is trusted proxy address相关逻辑。
def _is_trusted_proxy_address(ip_address: str) -> bool:
    """判断当前连接来源是否允许提交代理 IP 头。"""
    return str(ip_address or "").strip() in set(settings.app.trusted_proxy_ips)


# 执行resolve ip prefix相关逻辑。
def _resolve_ip_prefix(ip_address: str) -> str:
    """把 IP 归并到用于匿名限流的网段。"""
    try:
        parsed_ip = ipaddress.ip_address(str(ip_address or "").strip())
    except ValueError:
        return ""
    prefix_length = 24 if parsed_ip.version == 4 else 64
    return str(ipaddress.ip_network(f"{parsed_ip}/{prefix_length}", strict=False))[:64]


# 执行hash user agent相关逻辑。
def _hash_user_agent(user_agent: str) -> str:
    """把 User-Agent 转成短哈希，避免直接保存完整 UA。"""
    normalized_user_agent = str(user_agent or "").strip()
    if not normalized_user_agent:
        return ""
    return hashlib.sha256(normalized_user_agent.encode("utf-8")).hexdigest()[:64]


# 执行resolve guest generation id相关逻辑。
def _resolve_guest_generation_id(http_request: Request) -> tuple[str, bool]:
    """读取或生成未登录生成使用的访客 ID。"""
    guest_id = str(http_request.cookies.get(_GUEST_GENERATION_COOKIE) or "").strip()[:64]
    if guest_id:
        return guest_id, False
    return secrets.token_urlsafe(24)[:64], True


# 执行set guest generation cookie相关逻辑。
def _set_guest_generation_cookie(http_response: Response, guest_id: str) -> None:
    """把后端生成的未登录访客 ID 写入 HttpOnly Cookie。"""
    http_response.set_cookie(
        key=_GUEST_GENERATION_COOKIE,
        value=guest_id,
        max_age=_GUEST_GENERATION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )


# 执行assert anonymous generation model相关逻辑。
def _assert_anonymous_generation_model(request: DynamicViewCreateRequest) -> None:
    """限制未登录用户只能使用体验模型。"""
    if request.model_level.strip().lower() != "experience":
        raise HTTPException(status_code=403, detail="请先登录后再使用更高级模型。")


# 执行resolve logged in generation user id相关逻辑。
def _resolve_logged_in_generation_user_id(request: DynamicViewCreateRequest) -> str:
    """用 authorId 判断当前生成请求是否来自登录用户。"""
    normalized_author_id = request.author_id.strip()
    if normalized_author_id.lower() in {"", "system", "website"}:
        return ""
    return normalized_author_id[:64]


# 执行count generation requests相关逻辑。
def _count_generation_requests(
    db: Session,
    *,
    since: datetime,
    user_id: str,
    ip_address: str,
    guest_id: str = "",
    browser_fingerprint: str = "",
    ip_prefix: str = "",
    user_agent_hash: str = "",
) -> int:
    """统计指定身份在时间窗口内已经发起的生成请求数。"""
    ignored_statuses = ("failed",)
    query = db.query(DynamicViewGenerationRequest).outerjoin(
        DynamicViewTaskArchive,
        DynamicViewGenerationRequest.task_id == DynamicViewTaskArchive.task_id,
    ).filter(
        DynamicViewGenerationRequest.created_at >= since
    ).filter(
        or_(
            DynamicViewTaskArchive.id.is_(None),
            and_(
                ~DynamicViewTaskArchive.stage.in_(ignored_statuses),
                ~DynamicViewTaskArchive.payload_status.in_(ignored_statuses),
                or_(
                    DynamicViewTaskArchive.node_status.is_(None),
                    ~DynamicViewTaskArchive.node_status.in_(ignored_statuses),
                ),
            ),
        )
    )
    if user_id:
        query = query.filter(DynamicViewGenerationRequest.user_id == user_id)
        query = query.filter(DynamicViewGenerationRequest.is_logged_in == 1)
    else:
        query = query.filter(DynamicViewGenerationRequest.is_logged_in == 0)
        if guest_id:
            query = query.filter(DynamicViewGenerationRequest.guest_id == guest_id)
        if browser_fingerprint:
            query = query.filter(DynamicViewGenerationRequest.browser_fingerprint == browser_fingerprint)
        if ip_prefix:
            query = query.filter(DynamicViewGenerationRequest.ip_prefix == ip_prefix)
        if user_agent_hash:
            query = query.filter(DynamicViewGenerationRequest.user_agent_hash == user_agent_hash)
        if ip_address:
            query = query.filter(DynamicViewGenerationRequest.ip_address == ip_address)
    return query.count()


# 执行resolve generation request context相关逻辑。
def _resolve_generation_request_context(
    db: Session,
    *,
    http_request: Request,
    request: DynamicViewCreateRequest,
) -> dict[str, object]:
    """解析当前生成请求的登录态和 IP。"""
    user_id = _resolve_logged_in_generation_user_id(request)
    ip_address = _resolve_client_ip(http_request)
    guest_id, set_guest_cookie = _resolve_guest_generation_id(http_request)
    user_agent_hash = _hash_user_agent(http_request.headers.get("user-agent", ""))
    return {
        "user_id": user_id,
        "is_logged_in": 1 if user_id else 0,
        "ip_address": ip_address,
        "ip_prefix": _resolve_ip_prefix(ip_address),
        "guest_id": "" if user_id else guest_id,
        "set_guest_cookie": False if user_id else set_guest_cookie,
        "browser_fingerprint": "" if user_id else request.browser_fingerprint.strip(),
        "user_agent_hash": "" if user_id else user_agent_hash,
    }


# 执行enforce anonymous generation request limit相关逻辑。
def _enforce_anonymous_generation_request_limit(
    db: Session,
    *,
    context: dict[str, object],
) -> None:
    """未登录生成仍保留频率限制，已登录用户统一走 Credit 扣费。"""
    now = datetime.now()
    ip_address = str(context["ip_address"])
    ip_prefix = str(context["ip_prefix"])
    guest_id = str(context["guest_id"])
    browser_fingerprint = str(context["browser_fingerprint"])
    user_agent_hash = str(context["user_agent_hash"])
    if guest_id and _count_generation_requests(
        db,
        since=now - timedelta(days=1),
        user_id="",
        ip_address="",
        guest_id=guest_id,
    ) >= 1:
        raise HTTPException(status_code=429, detail="未登录用户今天只能生成一次。")
    if browser_fingerprint and _count_generation_requests(
        db,
        since=now - timedelta(days=1),
        user_id="",
        ip_address="",
        browser_fingerprint=browser_fingerprint,
    ) >= 1:
        raise HTTPException(status_code=429, detail="未登录用户今天只能生成一次。")
    hour_count = _count_generation_requests(
        db,
        since=now - timedelta(hours=1),
        user_id="",
        ip_address=ip_address,
    ) if ip_address else 0
    if hour_count >= 1:
        raise HTTPException(status_code=429, detail="未登录用户一小时只能生成一次。")
    today_count = _count_generation_requests(
        db,
        since=now - timedelta(days=1),
        user_id="",
        ip_address=ip_address,
    ) if ip_address else 0
    if today_count >= 3:
        raise HTTPException(status_code=429, detail="未登录用户今天最多生成三次。")
    if ip_prefix and _count_generation_requests(
        db,
        since=now - timedelta(days=1),
        user_id="",
        ip_address="",
        ip_prefix=ip_prefix,
    ) >= 10:
        raise HTTPException(status_code=429, detail="当前网络今天未登录生成次数已达上限。")
    if user_agent_hash and _count_generation_requests(
        db,
        since=now - timedelta(days=1),
        user_id="",
        ip_address=ip_address,
        user_agent_hash=user_agent_hash,
    ) >= 2:
        raise HTTPException(status_code=429, detail="当前设备今天未登录生成次数已达上限。")


# 执行record generation request相关逻辑。
def _record_generation_request(
    db: Session,
    *,
    context: dict[str, object],
    request: DynamicViewCreateRequest,
    snapshot: DynamicViewTaskSnapshot,
    dynamic_view_service: DynamicViewService,
    credit_cost: int,
) -> None:
    """把已通过限制并成功创建的生成请求写入数据库。"""
    model_profile = dynamic_view_service.resolve_generation_model_profile(db, request.model_level)
    db.add(
        DynamicViewGenerationRequest(
            request_id=snapshot.request_id,
            user_id=str(context["user_id"]),
            is_logged_in=int(context["is_logged_in"]),
            ip_address=str(context["ip_address"]),
            ip_prefix=str(context["ip_prefix"]),
            guest_id=str(context["guest_id"]),
            browser_fingerprint=str(context["browser_fingerprint"]),
            user_agent_hash=str(context["user_agent_hash"]),
            topic=request.topic.strip(),
            task_id=snapshot.task_id,
            model_level=request.model_level,
            model_name=model_profile.model,
            temperature=model_profile.resolved_temperature,
            top_p=model_profile.top_p,
            max_tokens=model_profile.max_tokens,
            stream=1 if model_profile.stream else 0,
            view_type=request.view_type,
            template_type=request.template_type,
            scene_count_min=request.scene_count_min,
            source_type="dynamic_view",
            plan_code=request.plan_code,
            credit_cost=credit_cost,
            generation_status=snapshot.stage,
        )
    )
    db.commit()


# 执行refund topic analysis credits相关逻辑。
def _refund_topic_analysis_credits(
    db: Session,
    *,
    user_id: str,
    request_id: str,
    credit_cost: int,
    reason: str,
) -> None:
    """主题分析阶段未进入生成时，把已扣 Credit 按 requestId 幂等退回。"""
    if credit_cost <= 0:
        return
    credit_service.refund_credits(
        db,
        user_id=user_id,
        amount=credit_cost,
        source_key=request_id,
        reason=reason,
    )
    db.commit()


# 执行refund cancelled generation credits相关逻辑。
def _refund_cancelled_generation_credits(
    db: Session,
    *,
    dynamic_view_service: DynamicViewService,
    snapshot: DynamicViewTaskSnapshot,
    task_id: str,
) -> None:
    """生成任务被用户中断时统一退回 50% Credit。"""
    normalized_user_id = snapshot.author_id.strip()
    if not normalized_user_id or normalized_user_id == "website":
        return
    total_cost = dynamic_view_service.resolve_generation_model_credit_cost(db, snapshot.model_level)
    refund_amount = total_cost // 2
    if refund_amount <= 0:
        return
    credit_service.refund_credits(
        db,
        user_id=normalized_user_id,
        amount=refund_amount,
        source_key=snapshot.request_id or snapshot.task_id,
        reason="生成中断退回",
    )
    db.commit()


# 执行build http exception相关逻辑。
def _build_http_exception(*, status_code: int, error: Exception) -> HTTPException:
    """统一把领域异常映射成 HTTPException，避免路由层重复拼装相同错误响应。"""
    return HTTPException(status_code=status_code, detail=str(error))
