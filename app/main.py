# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\main.py。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo
from sqlalchemy import text

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.response import ajax_error, ajax_success
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.dynamic_view import router as dynamic_view_router
from app.api.routes.payment import router as payment_router
from app.api.routes.website import router as website_router
from app.core.container import build_app_container
from app.core.http_logging import log_http_exchange
from app.core.logging_config import build_log_config
from app.core.settings import settings
from app.core.url_catalog import PythonUrl
from app.db.session import SessionLocal
from app.repositories.dynamic_view_game_repository import DynamicViewGameRepository
from app.repositories.dynamic_view_knowledge_repository import DynamicViewKnowledgeRepository
from app.repositories.dynamic_view_task_repository import DynamicViewTaskRepository
from app.services.credit_service import CreditService
logger = logging.getLogger(__name__)
_DYNAMIC_VIEW_SCHEDULE_LOCK_NAME = "zda_dynamic_view_schedule_dispatch"
_CREDIT_EXPIRATION_LOCK_NAME = "zda_credit_expiration_daily"
_WEBSITE_TOPIC_BATCH_LOCK_NAME = "zda_website_topic_batch_daily"
_WEBSITE_UNIVERSE_LOCK_NAME = "zda_website_universe_refresh"
_DYNAMIC_VIEW_RECYCLE_CLEANUP_LOCK_NAME = "zda_dynamic_view_recycle_cleanup"
_WEBSITE_UNIVERSE_REFRESH_SECONDS = 6 * 60 * 60
_DYNAMIC_VIEW_RECYCLE_CLEANUP_SECONDS = 60 * 60


# 执行install asyncio connection reset filter相关逻辑。
def _install_asyncio_connection_reset_filter() -> None:
    """屏蔽 Windows Proactor 在客户端断开连接时输出的无效回调异常。"""
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    # 执行handle asyncio exception相关逻辑。
    def handle_asyncio_exception(loop, context):
        """只吞掉 Proactor 连接被远端重置的日志噪声，其余异常保持原有处理。"""
        exception = context.get("exception")
        handle = str(context.get("handle", ""))
        if (
            isinstance(exception, ConnectionResetError)
            and "_ProactorBasePipeTransport._call_connection_lost" in handle
        ):
            logger.debug("Windows Proactor connection reset ignored: %s", exception)
            return
        if previous_handler is not None:
            # 执行previous handler相关逻辑。
            previous_handler(loop, context)
            return
        # 执行default exception handler相关逻辑。
        loop.default_exception_handler(context)

    loop.set_exception_handler(handle_asyncio_exception)


# 执行mark unfinished dynamic view tasks failed related logic。
def _mark_unfinished_dynamic_view_tasks_failed() -> None:
    """服务启动时把历史未完成任务和孤立存档统一改成失败终态。"""
    db = SessionLocal()
    try:
        message = "服务已重启，当前视频创建已中断，请重新创建。"
        # 执行mark unfinished tasks failed相关逻辑。
        updated_count = DynamicViewTaskRepository().mark_unfinished_tasks_failed(
            db,
            message=message,
        )
        failed_archive_count = (
            DynamicViewGameRepository().mark_unfinished_archives_failed(
                db,
                error_message=message,
            )
            + DynamicViewKnowledgeRepository().mark_unfinished_archives_failed(
                db,
                error_message=message,
            )
        )
        if updated_count > 0 or failed_archive_count > 0:
            logger.info(
                "Dynamic view unfinished records marked failed on startup: taskCount=%s archiveCount=%s",
                updated_count,
                failed_archive_count,
            )
    finally:
        # 执行close相关逻辑。
        db.close()


# 执行seconds until next shanghai midnight相关逻辑。
def _seconds_until_next_shanghai_midnight() -> float:
    """计算距离下一个上海时间 00:00 的秒数。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    next_midnight = datetime.combine(
        now.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    return max(1.0, (next_midnight - now).total_seconds())


# 执行run daily credit expiration cleanup once相关逻辑。
def _run_daily_credit_expiration_cleanup_once() -> None:
    """领取每日 Credit 过期清理任务并执行一次全局清零。"""
    db = SessionLocal()
    lock_acquired = False
    try:
        acquired = db.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": _CREDIT_EXPIRATION_LOCK_NAME},
        ).scalar()
        if acquired != 1:
            logger.info("Credit expiration cleanup skipped: lock busy")
            return
        lock_acquired = True
        now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        result = CreditService().expire_all_invalid_credits(db, now=now)
        logger.info(
            "Credit expiration cleanup completed: ledger=%s entitlement=%s",
            result["ledger"],
            result["entitlement"],
        )
    finally:
        try:
            if lock_acquired:
                db.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": _CREDIT_EXPIRATION_LOCK_NAME},
                )
        finally:
            db.close()


# 执行run daily credit expiration cleanup相关逻辑。
async def _run_daily_credit_expiration_cleanup() -> None:
    """每天上海时间 00:00 检测过期 Credit 并清零。"""
    while True:
        try:
            await asyncio.sleep(_seconds_until_next_shanghai_midnight())
            _run_daily_credit_expiration_cleanup_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Credit expiration cleanup failed")


# 执行run daily website topic batch refresh once相关逻辑。
async def _run_daily_website_topic_batch_refresh_once(app: FastAPI) -> None:
    """领取官网话题批次生成锁，并调用 topic 节点刷新当天五批话题。"""
    db = SessionLocal()
    lock_acquired = False
    try:
        acquired = db.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": _WEBSITE_TOPIC_BATCH_LOCK_NAME},
        ).scalar()
        if acquired != 1:
            logger.info("Website topic batch refresh skipped: lock busy")
            return
        lock_acquired = True
        await app.state.container.website_topic_batch_service.refresh_topic_batches()
        logger.info("Website topic batch refresh completed")
    finally:
        try:
            if lock_acquired:
                db.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": _WEBSITE_TOPIC_BATCH_LOCK_NAME},
                )
        finally:
            db.close()


# 执行run daily website topic batch refresh相关逻辑。
async def _run_daily_website_topic_batch_refresh(app: FastAPI) -> None:
    """每天上海时间 00:00 刷新官网五批话题。"""
    while True:
        try:
            await asyncio.sleep(_seconds_until_next_shanghai_midnight())
            await _run_daily_website_topic_batch_refresh_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Website topic batch refresh failed")


# 执行run website universe refresh once相关逻辑。
def _run_website_universe_refresh_once(app: FastAPI) -> None:
    """领取官网大观刷新锁，并覆盖五类大观展示数据。"""
    db = SessionLocal()
    lock_acquired = False
    try:
        acquired = db.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": _WEBSITE_UNIVERSE_LOCK_NAME},
        ).scalar()
        if acquired != 1:
            logger.info("Website universe refresh skipped: lock busy")
            return
        lock_acquired = True
        app.state.container.website_universe_service.refresh_universe_cases()
        logger.info("Website universe refresh completed")
    finally:
        try:
            if lock_acquired:
                db.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": _WEBSITE_UNIVERSE_LOCK_NAME},
                )
        finally:
            db.close()


# 执行run website universe refresh相关逻辑。
async def _run_website_universe_refresh(app: FastAPI) -> None:
    """每 6 小时刷新一次官网沉浸式大观数据。"""
    while True:
        try:
            _run_website_universe_refresh_once(app)
            await asyncio.sleep(_WEBSITE_UNIVERSE_REFRESH_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Website universe refresh failed")
            await asyncio.sleep(_WEBSITE_UNIVERSE_REFRESH_SECONDS)


# 执行run dynamic view recycle cleanup once相关逻辑。
def _run_dynamic_view_recycle_cleanup_once(app: FastAPI) -> None:
    """领取动态视图回收站清理锁，并物理删除到期视图记录和文件。"""
    db = SessionLocal()
    lock_acquired = False
    try:
        acquired = db.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": _DYNAMIC_VIEW_RECYCLE_CLEANUP_LOCK_NAME},
        ).scalar()
        if acquired != 1:
            logger.info("Dynamic view recycle cleanup skipped: lock busy")
            return
        lock_acquired = True
        deleted_count = app.state.container.dynamic_view_service.hard_delete_expired_recycled_archives(
            db,
            now=datetime.now(),
        )
        if deleted_count > 0:
            logger.info("Dynamic view recycle cleanup completed: count=%s", deleted_count)
    finally:
        try:
            if lock_acquired:
                db.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": _DYNAMIC_VIEW_RECYCLE_CLEANUP_LOCK_NAME},
                )
        finally:
            db.close()


# 执行run dynamic view recycle cleanup相关逻辑。
async def _run_dynamic_view_recycle_cleanup(app: FastAPI) -> None:
    """每小时清理一次超过 24 小时保留期的动态视图回收站。"""
    while True:
        try:
            _run_dynamic_view_recycle_cleanup_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Dynamic view recycle cleanup failed")
        await asyncio.sleep(_DYNAMIC_VIEW_RECYCLE_CLEANUP_SECONDS)


# 执行try acquire dynamic view schedule lock相关逻辑。
def _try_acquire_dynamic_view_schedule_lock():
    """多 worker 模式下只允许一个进程领取动态视图定时任务。"""
    db = SessionLocal()
    try:
        acquired = db.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": _DYNAMIC_VIEW_SCHEDULE_LOCK_NAME},
        ).scalar()
    except Exception:
        # 执行close相关逻辑。
        db.close()
        raise
    if acquired == 1:
        return db
    # 执行close相关逻辑。
    db.close()
    return None


# 执行release dynamic view schedule lock相关逻辑。
def _release_dynamic_view_schedule_lock(db) -> None:
    """定时任务轮次结束后释放 MySQL 命名锁。"""
    try:
        # 执行execute相关逻辑。
        db.execute(
            text("SELECT RELEASE_LOCK(:lock_name)"),
            {"lock_name": _DYNAMIC_VIEW_SCHEDULE_LOCK_NAME},
        )
    finally:
        # 执行close相关逻辑。
        db.close()


# 执行run scheduled dynamic view generation相关逻辑。
async def _run_scheduled_dynamic_view_generation(app: FastAPI) -> None:
    """按固定周期领取一批数据库主题，并分发游戏动态视图后台任务。"""
    schedule_config = settings.dynamic_view_schedule
    if not schedule_config.enabled:
        # 执行info相关逻辑。
        logger.info("Dynamic view schedule disabled")
        return
    while True:
        lock_session = None
        try:
            # 执行try acquire dynamic view schedule lock相关逻辑。
            lock_session = _try_acquire_dynamic_view_schedule_lock()
            if lock_session is None:
                logger.info("Dynamic view schedule skipped: lock busy")
            else:
                created_snapshots = app.state.container.dynamic_view_task_service.dispatch_scheduled_batch(
                    scene_count_min=schedule_config.scene_count_min,
                    batch_size=2,
                )
                if created_snapshots:
                    logger.info(
                        "Dynamic view schedule dispatched batch: count=%s sceneCountMin=%s firstTaskId=%s",
                        len(created_snapshots),
                        schedule_config.scene_count_min,
                        created_snapshots[0].task_id,
                    )
                elif app.state.container.dynamic_view_service.get_active_generation_task_count() > 0:
                    logger.info("Dynamic view schedule skipped: previous batch still running")
        except asyncio.CancelledError:
            raise
        except Exception:
            # 执行exception相关逻辑。
            logger.exception(
                "Dynamic view schedule trigger failed: sceneCountMin=%s",
                schedule_config.scene_count_min,
            )
        finally:
            if lock_session is not None:
                # 执行release dynamic view schedule lock相关逻辑。
                _release_dynamic_view_schedule_lock(lock_session)
        # 执行sleep相关逻辑。
        await asyncio.sleep(schedule_config.interval_seconds)


# 执行lifespan相关逻辑。
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时统一装配依赖容器，关闭时按统一出口释放资源。"""
    # 执行install asyncio connection reset filter相关逻辑。
    _install_asyncio_connection_reset_filter()
    # 执行info相关逻辑。
    logger.info(
        "Python service starting: host=%s port=%s workers=%s limitConcurrency=%s",
        settings.app.host,
        settings.app.port,
        settings.uvicorn.workers,
        settings.uvicorn.limit_concurrency,
    )
    app.state.container = build_app_container()
    # 执行mark unfinished dynamic view tasks failed related logic。
    _mark_unfinished_dynamic_view_tasks_failed()
    # 执行create task相关逻辑。
    scheduled_dynamic_view_task = asyncio.create_task(
        _run_scheduled_dynamic_view_generation(app)
    )
    # 执行create task相关逻辑。
    daily_credit_expiration_task = asyncio.create_task(
        _run_daily_credit_expiration_cleanup()
    )
    # 执行create task相关逻辑。
    daily_website_topic_batch_task = asyncio.create_task(
        _run_daily_website_topic_batch_refresh(app)
    )
    # 执行create task相关逻辑。
    website_universe_refresh_task = asyncio.create_task(
        _run_website_universe_refresh(app)
    )
    # 执行create task相关逻辑。
    dynamic_view_recycle_cleanup_task = asyncio.create_task(
        _run_dynamic_view_recycle_cleanup(app)
    )
    try:
        yield
    finally:
        # 执行cancel相关逻辑。
        scheduled_dynamic_view_task.cancel()
        # 执行cancel相关逻辑。
        daily_credit_expiration_task.cancel()
        # 执行cancel相关逻辑。
        daily_website_topic_batch_task.cancel()
        # 执行cancel相关逻辑。
        website_universe_refresh_task.cancel()
        # 执行cancel相关逻辑。
        dynamic_view_recycle_cleanup_task.cancel()
        # 执行gather相关逻辑。
        await asyncio.gather(
            scheduled_dynamic_view_task,
            daily_credit_expiration_task,
            daily_website_topic_batch_task,
            website_universe_refresh_task,
            dynamic_view_recycle_cleanup_task,
            return_exceptions=True,
        )
        # 执行aclose相关逻辑。
        await app.state.container.aclose()
        # 执行info相关逻辑。
        logger.info("Python service stopped")


app = FastAPI(title="zda-python", lifespan=lifespan)
# 执行enable dev cors相关逻辑。
if settings.app.is_development:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.middleware("http")(log_http_exchange)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(dynamic_view_router)
app.include_router(chat_router)
app.include_router(website_router)
app.include_router(payment_router)


def _is_dynamic_view_html_path(path: str) -> bool:
    """动态视图 HTML 需要允许官网 iframe 预览和弹窗播放。"""
    normalized_path = path.rstrip("/")
    if not normalized_path.endswith("/html"):
        return False
    return (
        normalized_path.startswith(f"{PythonUrl.DYNAMIC_VIEW_KNOWLEDGE_API_PREFIX.value}/")
        or normalized_path.startswith(f"{PythonUrl.DYNAMIC_VIEW_API_PREFIX.value}/")
    )


# 执行security headers middleware相关逻辑。
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """为 API 响应补充基础安全头，减少浏览器侧误用风险。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if not _is_dynamic_view_html_path(request.url.path):
        response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if request.url.path.startswith(PythonUrl.API_PREFIX.value):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# 执行http exception handler相关逻辑。
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """把 Starlette/FastAPI HTTPException 统一成 AjaxResult 错误结构。"""
    return JSONResponse(
        status_code=exc.status_code,
        content=ajax_error(msg=str(exc.detail), code=exc.status_code),
    )


# 执行validation exception handler相关逻辑。
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """把请求校验失败统一成 AjaxResult 错误结构。"""
    return JSONResponse(
        status_code=422,
        content=ajax_error(msg=str(exc), code=422),
    )


# 执行health相关逻辑。
@app.get("/health")
async def health() -> dict[str, object]:
    """提供最小健康检查接口，方便本地联调和进程探活。"""
    return ajax_success({"status": "ok"})


if __name__ == "__main__":
    # 执行run相关逻辑。
    uvicorn.run(
        "app.main:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=False,
        workers=settings.uvicorn.workers,
        backlog=settings.uvicorn.backlog,
        limit_concurrency=settings.uvicorn.limit_concurrency,
        limit_max_requests=settings.uvicorn.max_requests,
        timeout_keep_alive=settings.uvicorn.timeout_keep_alive,
        # 执行build log config相关逻辑。
        log_config=build_log_config(),
        access_log=True,
    )
