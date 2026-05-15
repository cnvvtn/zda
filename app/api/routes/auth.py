# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\routes\auth.py。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.response import ajax_success
from app.core.url_catalog import PythonUrl
from app.features.auth.schemas import EmailCodeSendRequest, EmailCodeVerifyRequest
from app.services.email_auth_service import EmailAuthService


router = APIRouter(prefix=PythonUrl.AUTH_API_PREFIX.value, tags=["auth"])
email_auth_service = EmailAuthService()


# 执行send email code相关逻辑。
@router.post("/email/code")
async def send_email_code(
    request_body: EmailCodeSendRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """发送邮箱验证码。"""
    return ajax_success(
        email_auth_service.send_code(
            db,
            request_body=request_body,
            http_request=request,
        )
    )


# 执行verify email code相关逻辑。
@router.post("/email/verify")
async def verify_email_code(
    request_body: EmailCodeVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """校验邮箱验证码；账号不存在时自动注册。"""
    return ajax_success(
        email_auth_service.verify_code(
            db,
            request_body=request_body,
            http_request=request,
        )
    )
