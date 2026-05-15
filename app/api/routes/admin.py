# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\routes\admin.py。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy import String, Text, inspect, or_
from sqlalchemy.dialects.mysql import LONGTEXT, TIMESTAMP
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import BigInteger, Integer, Numeric

from app.api.deps import get_app_container, get_db
from app.api.response import ajax_success, table_data
from app.core.container import AppContainer
from app.core.url_catalog import PythonUrl
from app.db.models import (
    AppUser,
    ChatMessage,
    ChatSession,
    DynamicViewArchive,
    DynamicViewCharacterArchive,
    DynamicViewClueArchive,
    DynamicViewComment,
    DynamicViewGenerationErrorLog,
    DynamicViewGenerationRequest,
    DynamicViewModelProfile,
    DynamicViewProgressArchive,
    DynamicViewTaskArchive,
    EmailAuthAccount,
    EmailAuthSession,
    EmailVerificationCode,
    WebsiteContent,
    WebsiteGenerationSession,
    WebsiteGenerationSessionTask,
    ZdaCreditLedger,
    ZdaCreditRedeemCode,
    ZdaCreditRedeemRecord,
    ZdaCreditUsageLog,
    ZdaMembershipEntitlement,
    ZpayPaymentEvent,
    ZpayPaymentOrder,
    ZpayPaymentRateLimit,
)
from app.repositories.runtime_secret_repository import RuntimeSecretRepository


router = APIRouter(prefix=PythonUrl.ADMIN_API_PREFIX.value, tags=["admin"])
ROLE_LEVELS = {"auditor": 1, "admin": 2, "super_admin": 3}
READONLY_COLUMN_NAMES = {"id", "created_at", "updated_at", "last_login_at", "paid_at", "last_notify_at", "consumed_at"}
SKIP_ASSIGN = object()
runtime_secret_repository = RuntimeSecretRepository()


@dataclass(frozen=True)
class AdminPrincipal:
    """当前后台账号身份。"""

    user_key: str
    email: str
    role: str


@dataclass(frozen=True)
class AdminResource:
    """后台可管理资源配置。"""

    key: str
    title: str
    group: str
    model: type
    search_fields: tuple[str, ...]
    write_role: str = "admin"
    delete_role: str = "super_admin"


ADMIN_RESOURCES: dict[str, AdminResource] = {
    "website_content": AdminResource("website_content", "官网配置", "官网", WebsiteContent, ("content_key", "content_json")),
    "website_generation_session": AdminResource("website_generation_session", "生成会话", "官网", WebsiteGenerationSession, ("user_id", "topic")),
    "website_generation_session_task": AdminResource("website_generation_session_task", "生成会话任务", "官网", WebsiteGenerationSessionTask, ("user_id", "topic", "task_id")),
    "dynamic_view_generation_request": AdminResource("dynamic_view_generation_request", "生成请求", "官网", DynamicViewGenerationRequest, ("request_id", "user_id", "ip_address", "topic", "task_id", "model_name"), "super_admin", "super_admin"),
    "dynamic_view": AdminResource("dynamic_view", "动态视图", "内容", DynamicViewArchive, ("topic", "source_topic", "author_id", "status")),
    "dynamic_view_task": AdminResource("dynamic_view_task", "生成任务", "内容", DynamicViewTaskArchive, ("task_id", "author_id", "topic", "stage", "message")),
    "dynamic_view_comment": AdminResource("dynamic_view_comment", "评论", "内容", DynamicViewComment, ("content", "ip_address", "ip_location")),
    "dynamic_view_clue": AdminResource("dynamic_view_clue", "游戏线索", "内容", DynamicViewClueArchive, ("clue_key", "clue_title", "clue_content")),
    "dynamic_view_progress": AdminResource("dynamic_view_progress", "线索进度", "内容", DynamicViewProgressArchive, ("user_id", "clue_key", "matched_message_id")),
    "dynamic_view_character": AdminResource("dynamic_view_character", "角色存档", "内容", DynamicViewCharacterArchive, ("role_name", "category_name", "author")),
    "dynamic_view_model_profile": AdminResource("dynamic_view_model_profile", "模型配置", "内容", DynamicViewModelProfile, ("model_level", "node_key", "router_type", "credit_cost", "base_url", "model_name"), "super_admin", "super_admin"),
    "dynamic_view_generation_error_log": AdminResource("dynamic_view_generation_error_log", "生成错误日志", "内容", DynamicViewGenerationErrorLog, ("task_id", "request_id", "user_id", "topic", "error_message"), "super_admin", "super_admin"),
    "chat_session": AdminResource("chat_session", "聊天会话", "聊天", ChatSession, ("conversation_id", "user_id", "title", "snippet")),
    "chat_message": AdminResource("chat_message", "聊天消息", "聊天", ChatMessage, ("message_id", "conversation_id", "user_id", "role", "content")),
    "app_user": AdminResource("app_user", "应用用户", "账号", AppUser, ("user_key", "nickname", "ip_address", "ip_location", "bio")),
    "email_auth_account": AdminResource("email_auth_account", "邮箱账号", "账号", EmailAuthAccount, ("email", "user_key"), "super_admin", "super_admin"),
    "email_verification_code": AdminResource("email_verification_code", "邮箱验证码", "账号", EmailVerificationCode, ("email", "request_ip", "browser_fingerprint"), "super_admin", "super_admin"),
    "email_auth_session": AdminResource("email_auth_session", "登录会话", "账号", EmailAuthSession, ("user_key", "request_ip", "browser_fingerprint"), "super_admin", "super_admin"),
    "zpay_payment_order": AdminResource("zpay_payment_order", "支付订单", "支付", ZpayPaymentOrder, ("out_trade_no", "zpay_trade_no", "plan_name", "user_id", "status")),
    "zpay_payment_event": AdminResource("zpay_payment_event", "支付事件", "支付", ZpayPaymentEvent, ("out_trade_no", "event_type", "request_ip"), "super_admin", "super_admin"),
    "zpay_payment_rate_limit": AdminResource("zpay_payment_rate_limit", "支付限流", "支付", ZpayPaymentRateLimit, ("scope", "bucket_key"), "super_admin", "super_admin"),
    "zda_membership_entitlement": AdminResource("zda_membership_entitlement", "会员权益", "支付", ZdaMembershipEntitlement, ("user_id", "plan_code", "plan_name", "source_order_no")),
    "zda_credit_ledger": AdminResource("zda_credit_ledger", "Credit 账户", "支付", ZdaCreditLedger, ("user_id",), "super_admin", "super_admin"),
    "zda_credit_redeem_code": AdminResource("zda_credit_redeem_code", "兑换码", "支付", ZdaCreditRedeemCode, ("code", "batch_no", "created_by"), "super_admin", "super_admin"),
    "zda_credit_redeem_record": AdminResource("zda_credit_redeem_record", "兑换记录", "支付", ZdaCreditRedeemRecord, ("code", "user_id", "ip_address")),
    "zda_credit_usage_log": AdminResource("zda_credit_usage_log", "Credit 流水", "支付", ZdaCreditUsageLog, ("user_id", "usage_type", "request_id", "model_level")),
}


# 执行get admin principal相关逻辑。
def get_admin_principal(request: Request, db: Session = Depends(get_db)) -> AdminPrincipal:
    """作为 FastAPI 依赖解析后台账号身份。"""
    return resolve_admin_principal(request, db)


# 执行get admin me相关逻辑。
@router.get("/me")
async def get_admin_me(
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """返回当前后台账号和权限等级。"""
    return ajax_success(_serialize_principal(principal))


# 执行list admin resources相关逻辑。
@router.get("/resources")
async def list_admin_resources(
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """返回当前账号可见的后台资源清单。"""
    return ajax_success(
        [
            _build_resource_meta(resource, principal)
            for resource in ADMIN_RESOURCES.values()
        ]
    )


# 执行list admin resource rows相关逻辑。
@router.get("/resources/{resource_key}")
async def list_admin_resource_rows(
    resource_key: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    successful_only: int = 0,
    recycled_only: int = 0,
    db: Session = Depends(get_db),
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """分页读取指定资源数据。"""
    resource = _resolve_resource(resource_key)
    query = db.query(resource.model)
    if resource.key == "dynamic_view":
        query = query.filter(DynamicViewArchive.is_deleted == (1 if recycled_only == 1 else 0))
    if resource.key == "dynamic_view" and successful_only == 1:
        query = query.filter(
            DynamicViewArchive.status == "ready",
            DynamicViewArchive.html_content != "",
        )
    normalized_keyword = keyword.strip()
    if normalized_keyword:
        query = query.filter(_build_keyword_filter(resource, normalized_keyword))
    total = query.count()
    query = _apply_default_order(query, resource)
    rows = query.offset((max(page, 1) - 1) * max(page_size, 1)).limit(min(max(page_size, 1), 100)).all()
    return table_data([_serialize_model(row) for row in rows], total=total)


# 执行get admin resource row相关逻辑。
@router.get("/resources/{resource_key}/{row_id}")
async def get_admin_resource_row(
    resource_key: str,
    row_id: int,
    db: Session = Depends(get_db),
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """读取指定资源单条数据。"""
    resource = _resolve_resource(resource_key)
    row = _get_row_or_404(db, resource, row_id)
    return ajax_success(_serialize_model(row))


# 执行create admin resource row相关逻辑。
@router.post("/resources/{resource_key}")
async def create_admin_resource_row(
    resource_key: str,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """新增指定资源数据。"""
    resource = _resolve_resource(resource_key)
    _assert_role(principal, resource.write_role)
    row = resource.model()
    _assign_payload(row, payload, creating=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return ajax_success(_serialize_model(row))


# 执行update admin resource row相关逻辑。
@router.put("/resources/{resource_key}/{row_id}")
async def update_admin_resource_row(
    resource_key: str,
    row_id: int,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """更新指定资源数据。"""
    resource = _resolve_resource(resource_key)
    _assert_role(principal, resource.write_role)
    row = _get_row_or_404(db, resource, row_id)
    _assign_payload(row, payload, creating=False)
    db.commit()
    db.refresh(row)
    return ajax_success(_serialize_model(row))


# 执行delete admin resource row相关逻辑。
@router.delete("/resources/{resource_key}/{row_id}")
async def delete_admin_resource_row(
    resource_key: str,
    row_id: int,
    db: Session = Depends(get_db),
    app_container: AppContainer = Depends(get_app_container),
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """删除指定资源数据。"""
    resource = _resolve_resource(resource_key)
    _assert_role(principal, resource.delete_role)
    if resource.key == "dynamic_view":
        row = _get_row_or_404(db, resource, row_id)
        if row.type in {"game", "knowledge"}:
            return ajax_success(
                app_container.dynamic_view_service.move_archive_to_recycle_bin(
                    db,
                    view_type=row.type,
                    archive_id=row_id,
                )
            )
    else:
        row = _get_row_or_404(db, resource, row_id)
    db.delete(row)
    db.commit()
    return ajax_success({"deleted": True})


# 执行restore admin dynamic view row相关逻辑。
@router.post("/resources/dynamic_view/{row_id}/restore")
async def restore_admin_dynamic_view_row(
    row_id: int,
    db: Session = Depends(get_db),
    app_container: AppContainer = Depends(get_app_container),
    principal: AdminPrincipal = Depends(get_admin_principal),
) -> dict[str, object]:
    """把后台动态视图从回收站恢复。"""
    _assert_role(principal, ADMIN_RESOURCES["dynamic_view"].delete_role)
    row = db.get(DynamicViewArchive, row_id)
    if row is None or row.type not in {"game", "knowledge"} or row.is_deleted != 1:
        raise HTTPException(status_code=404, detail="回收站视图不存在")
    return ajax_success(
        app_container.dynamic_view_service.restore_archive_from_recycle_bin(
            db,
            view_type=row.type,
            archive_id=row_id,
        )
    )


# 执行resolve admin principal相关逻辑。
def resolve_admin_principal(request: Request, db: Session) -> AdminPrincipal:
    """校验登录 token 并解析后台角色。"""
    token = _resolve_bearer_token(request)
    secret_data = runtime_secret_repository.get_secret_data(db)
    if "AUTH_TOKEN_SECRET" not in secret_data or not secret_data["AUTH_TOKEN_SECRET"]:
        raise HTTPException(status_code=500, detail="数据库 runtime_secrets.AUTH_TOKEN_SECRET 未配置")
    token_secret = secret_data["AUTH_TOKEN_SECRET"]
    payload = _decode_auth_token(token, token_secret=token_secret)
    user_key = str(payload.get("sub") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    if not user_key or not email:
        raise HTTPException(status_code=401, detail="登录信息无效")
    session = (
        db.query(EmailAuthSession)
        .filter(
            EmailAuthSession.user_key == user_key,
            EmailAuthSession.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest(),
            EmailAuthSession.status == 1,
            EmailAuthSession.expires_at > datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None),
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=401, detail="登录已过期")
    account = (
        db.query(EmailAuthAccount)
        .filter(
            EmailAuthAccount.user_key == user_key,
            EmailAuthAccount.email == email,
            EmailAuthAccount.status == 1,
        )
        .first()
    )
    if account is None:
        raise HTTPException(status_code=403, detail="账号不可用")
    role = _resolve_admin_role(secret_data, user_key=user_key)
    if not role:
        raise HTTPException(status_code=403, detail="当前用户 ID 没有后台权限")
    return AdminPrincipal(user_key=user_key, email=email, role=role)


# 执行resolve bearer token相关逻辑。
def _resolve_bearer_token(request: Request) -> str:
    """从 Authorization 请求头提取 Bearer token。"""
    authorization = request.headers.get("authorization", "").strip()
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    return token


# 执行decode auth token相关逻辑。
def _decode_auth_token(token: str, *, token_secret: str) -> dict[str, Any]:
    """校验邮箱登录 token 签名和过期时间。"""
    try:
        payload_text, signature_text = token.split(".", 1)
        expected_signature = hmac.new(
            token_secret.encode("utf-8"),
            payload_text.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        actual_signature = _base64url_decode(signature_text)
        if not hmac.compare_digest(expected_signature, actual_signature):
            raise ValueError("token signature mismatch")
        payload = json.loads(_base64url_decode(payload_text).decode("utf-8"))
    except Exception as error:
        raise HTTPException(status_code=401, detail="登录信息无效") from error
    if int(payload.get("exp") or 0) <= int(datetime.now(ZoneInfo("Asia/Shanghai")).timestamp()):
        raise HTTPException(status_code=401, detail="登录已过期")
    return payload


# 执行base64url decode相关逻辑。
def _base64url_decode(value: str) -> bytes:
    """解码无 padding 的 base64url 文本。"""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


# 执行resolve admin role相关逻辑。
def _resolve_admin_role(secret_data: dict[str, str], *, user_key: str) -> str:
    """根据数据库中的用户 ID 白名单解析后台权限等级。"""
    normalized_user_key = user_key.strip().lower()
    super_admins = _read_identity_set(secret_data, "ZDA_SUPER_ADMIN_USER_KEYS")
    admins = _read_identity_set(secret_data, "ZDA_ADMIN_USER_KEYS")
    auditors = _read_identity_set(secret_data, "ZDA_AUDITOR_USER_KEYS")
    if normalized_user_key in super_admins:
        return "super_admin"
    if normalized_user_key in admins:
        return "admin"
    if normalized_user_key in auditors:
        return "auditor"
    return ""


# 执行read identity set相关逻辑。
def _read_identity_set(secret_data: dict[str, str], *secret_keys: str) -> set[str]:
    """读取逗号分隔的后台账号白名单。"""
    values: set[str] = set()
    for secret_key in secret_keys:
        if secret_key not in secret_data:
            raise HTTPException(status_code=500, detail=f"数据库 runtime_secrets.{secret_key} 未配置")
        raw_value = secret_data[secret_key]
        values.update(item.strip().lower() for item in raw_value.split(",") if item.strip())
    return values


# 执行serialize principal相关逻辑。
def _serialize_principal(principal: AdminPrincipal) -> dict[str, str]:
    """把后台账号对象转成响应结构。"""
    return {
        "userKey": principal.user_key,
        "email": principal.email,
        "role": principal.role,
        "roleName": _role_name(principal.role),
    }


# 执行role name相关逻辑。
def _role_name(role: str) -> str:
    """返回权限等级中文名称。"""
    return {
        "super_admin": "超级管理员",
        "admin": "管理员",
        "auditor": "审计员",
    }.get(role, "无权限")


# 执行assert role相关逻辑。
def _assert_role(principal: AdminPrincipal, required_role: str) -> None:
    """校验当前账号是否满足指定权限等级。"""
    if ROLE_LEVELS.get(principal.role, 0) < ROLE_LEVELS.get(required_role, 99):
        raise HTTPException(status_code=403, detail="当前账号权限不足")


# 执行resolve resource相关逻辑。
def _resolve_resource(resource_key: str) -> AdminResource:
    """根据资源 key 获取后台资源配置。"""
    resource = ADMIN_RESOURCES.get(resource_key)
    if resource is None:
        raise HTTPException(status_code=404, detail="后台资源不存在")
    return resource


# 执行build resource meta相关逻辑。
def _build_resource_meta(resource: AdminResource, principal: AdminPrincipal) -> dict[str, Any]:
    """生成前端渲染表格和表单需要的资源元数据。"""
    columns = [_build_column_meta(column) for column in inspect(resource.model).columns]
    return {
        "key": resource.key,
        "title": resource.title,
        "group": resource.group,
        "canCreate": ROLE_LEVELS[principal.role] >= ROLE_LEVELS[resource.write_role],
        "canUpdate": ROLE_LEVELS[principal.role] >= ROLE_LEVELS[resource.write_role],
        "canDelete": ROLE_LEVELS[principal.role] >= ROLE_LEVELS[resource.delete_role],
        "columns": columns,
        "listFields": _resolve_list_fields(columns),
    }


# 执行build column meta相关逻辑。
def _build_column_meta(column) -> dict[str, Any]:
    """生成单个数据库字段的前端元数据。"""
    column_type = _resolve_column_type(column.type)
    return {
        "name": column.name,
        "label": column.comment or column.name,
        "type": column_type,
        "primaryKey": column.primary_key,
        "nullable": column.nullable,
        "readonly": column.primary_key or column.name in READONLY_COLUMN_NAMES,
        "longText": column_type == "text" or isinstance(column.type, (Text, LONGTEXT)),
    }


# 执行resolve column type相关逻辑。
def _resolve_column_type(column_type) -> str:
    """把 SQLAlchemy 字段类型映射成前端输入类型。"""
    if isinstance(column_type, (Integer, BigInteger)):
        return "number"
    if isinstance(column_type, Numeric):
        return "decimal"
    if isinstance(column_type, TIMESTAMP):
        return "datetime"
    if isinstance(column_type, (Text, LONGTEXT)):
        return "text"
    if isinstance(column_type, String):
        return "string"
    return "string"


# 执行resolve list fields相关逻辑。
def _resolve_list_fields(columns: list[dict[str, Any]]) -> list[str]:
    """选择表格默认展示字段。"""
    preferred_names = ["id", "user_id", "user_key", "email", "topic", "title", "status", "stage", "created_at", "updated_at"]
    available_names = [column["name"] for column in columns]
    selected = [name for name in preferred_names if name in available_names]
    for name in available_names:
        if len(selected) >= 8:
            break
        if name not in selected:
            selected.append(name)
    return selected[:8]


# 执行build keyword filter相关逻辑。
def _build_keyword_filter(resource: AdminResource, keyword: str):
    """构建文本字段模糊搜索条件。"""
    filters = []
    mapper = inspect(resource.model)
    column_map = {column.name: column for column in mapper.columns}
    for field_name in resource.search_fields:
        column = column_map.get(field_name)
        if column is not None:
            filters.append(column.ilike(f"%{keyword}%"))
    if not filters:
        return True
    return or_(*filters)


# 执行apply default order相关逻辑。
def _apply_default_order(query, resource: AdminResource):
    """按常用时间或主键字段倒序展示后台数据。"""
    mapper = inspect(resource.model)
    column_map = {column.name: column for column in mapper.columns}
    order_column = column_map.get("updated_at")
    if order_column is None:
        order_column = column_map.get("created_at")
    if order_column is None:
        order_column = column_map.get("id")
    if order_column is None:
        return query
    return query.order_by(order_column.desc())


# 执行get row or 404相关逻辑。
def _get_row_or_404(db: Session, resource: AdminResource, row_id: int):
    """按自增主键读取资源数据。"""
    row = db.get(resource.model, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据不存在")
    return row


# 执行serialize model相关逻辑。
def _serialize_model(row) -> dict[str, Any]:
    """把 SQLAlchemy 模型实例转成普通字典。"""
    result: dict[str, Any] = {}
    for column in inspect(row.__class__).columns:
        value = getattr(row, column.name)
        if isinstance(value, Decimal):
            result[column.name] = str(value)
        elif isinstance(value, datetime):
            result[column.name] = value.strftime("%Y-%m-%d %H:%M:%S")
        else:
            result[column.name] = value
    return result


# 执行assign payload相关逻辑。
def _assign_payload(row, payload: dict[str, Any], *, creating: bool) -> None:
    """把前端表单数据写入模型实例。"""
    for column in inspect(row.__class__).columns:
        if column.primary_key or column.name in READONLY_COLUMN_NAMES:
            continue
        if column.name not in payload:
            continue
        value = _coerce_column_value(column, payload[column.name], creating=creating)
        if value is SKIP_ASSIGN:
            continue
        setattr(row, column.name, value)


# 执行coerce column value相关逻辑。
def _coerce_column_value(column, value: Any, *, creating: bool) -> Any:
    """按数据库字段类型转换表单值。"""
    if value == "" and column.nullable:
        return None
    if value == "" and creating and (column.default is not None or column.server_default is not None):
        return SKIP_ASSIGN
    if isinstance(column.type, (Integer, BigInteger)):
        return int(value or 0)
    if isinstance(column.type, Numeric):
        return Decimal(str(value or "0"))
    if isinstance(column.type, TIMESTAMP):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    return str(value or "")
