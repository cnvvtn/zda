# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\auth\schemas.py。"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


_EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)


# 定义EmailCodeSendRequest。
class EmailCodeSendRequest(BaseModel):
    """邮箱验证码发送请求。"""

    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(min_length=3, max_length=255)
    browser_fingerprint: str = Field(default="", alias="browserFingerprint", max_length=128)

    # 执行validate email相关逻辑。
    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        """校验并规整邮箱。"""
        normalized_email = str(value or "").strip().lower()
        if not _EMAIL_PATTERN.match(normalized_email):
            raise ValueError("邮箱格式不正确")
        return normalized_email

    # 执行validate browser fingerprint相关逻辑。
    @field_validator("browser_fingerprint")
    @classmethod
    def validate_browser_fingerprint(cls, value: str) -> str:
        """清洗浏览器指纹。"""
        return str(value or "").strip()


# 定义EmailCodeVerifyRequest。
class EmailCodeVerifyRequest(EmailCodeSendRequest):
    """邮箱验证码校验请求。"""

    code: str = Field(min_length=4, max_length=12)

    # 执行validate code相关逻辑。
    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        """验证码只允许数字。"""
        normalized_code = str(value or "").strip()
        if not normalized_code.isdigit():
            raise ValueError("验证码格式不正确")
        return normalized_code


# 定义AuthUserResponse。
class AuthUserResponse(BaseModel):
    """认证用户响应。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    email: str
    nickname: str


# 定义EmailCodeVerifyResponse。
class EmailCodeVerifyResponse(BaseModel):
    """验证码登录/注册成功响应。"""

    model_config = ConfigDict(populate_by_name=True)

    token: str
    expires_at: datetime = Field(alias="expiresAt")
    user: AuthUserResponse
