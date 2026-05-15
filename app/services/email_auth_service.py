# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\email_auth_service.py。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import smtplib
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.features.auth.schemas import (
    AuthUserResponse,
    EmailCodeSendRequest,
    EmailCodeVerifyRequest,
    EmailCodeVerifyResponse,
)
from app.repositories.email_auth_repository import EmailAuthRepository
from app.repositories.runtime_secret_repository import RuntimeSecretRepository


logger = logging.getLogger(__name__)


# 定义EmailAuthService。
class EmailAuthService:
    """邮箱验证码登录/注册服务。"""

    def __init__(
        self,
        repository: EmailAuthRepository | None = None,
        runtime_secret_repository: RuntimeSecretRepository | None = None,
    ) -> None:
        self.repository = repository or EmailAuthRepository()
        self.runtime_secret_repository = runtime_secret_repository or RuntimeSecretRepository()

    # 执行send code相关逻辑。
    def send_code(
        self,
        db: Session,
        *,
        request_body: EmailCodeSendRequest,
        http_request: Request,
    ) -> dict[str, object]:
        """发送邮箱验证码。"""
        self._ensure_enabled()
        secret_data = self.runtime_secret_repository.get_secret_data(db)
        if "EMAIL_SMTP_PASSWORD" not in secret_data or not secret_data["EMAIL_SMTP_PASSWORD"]:
            raise HTTPException(status_code=500, detail="数据库 runtime_secrets.EMAIL_SMTP_PASSWORD 未配置")
        smtp_password = secret_data["EMAIL_SMTP_PASSWORD"]
        token_secret = self._resolve_token_secret(secret_data)
        request_ip = self._resolve_client_ip(http_request)
        email_hash = self._email_hash(request_body.email, token_secret=token_secret)
        self._assert_send_rate_limit(db, email_hash=email_hash, request_ip=request_ip)
        code = f"{secrets.randbelow(900000) + 100000}"
        code_hash = self._code_hash(request_body.email, code, token_secret=token_secret)
        expires_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) + timedelta(minutes=settings.email_auth.code_ttl_minutes)
        archive = self.repository.create_code(
            db,
            email=request_body.email,
            email_hash=email_hash,
            code_hash=code_hash,
            request_ip=request_ip,
            browser_fingerprint=request_body.browser_fingerprint,
            expires_at=expires_at,
        )
        try:
            self._send_verification_email(request_body.email, code, smtp_password=smtp_password)
        except HTTPException:
            archive.consumed = 1
            db.commit()
            raise
        return {"sent": True, "expiresAt": expires_at}

    # 执行verify code相关逻辑。
    def verify_code(
        self,
        db: Session,
        *,
        request_body: EmailCodeVerifyRequest,
        http_request: Request,
    ) -> EmailCodeVerifyResponse:
        """校验邮箱验证码，通过后自动注册或登录。"""
        self._ensure_enabled()
        token_secret = self._resolve_token_secret(self.runtime_secret_repository.get_secret_data(db))
        request_ip = self._resolve_client_ip(http_request)
        email_hash = self._email_hash(request_body.email, token_secret=token_secret)
        self._assert_verify_rate_limit(db, email_hash=email_hash)
        code_record = self.repository.get_latest_code_for_update(db, email_hash=email_hash)
        if code_record is None:
            raise HTTPException(status_code=400, detail="验证码不存在或已过期")
        code_record.attempt_count += 1
        if code_record.attempt_count > 5:
            db.commit()
            raise HTTPException(status_code=429, detail="验证码尝试次数过多，请重新获取")
        expected_hash = self._code_hash(request_body.email, request_body.code, token_secret=token_secret)
        if not hmac.compare_digest(code_record.code_hash, expected_hash):
            db.commit()
            raise HTTPException(status_code=400, detail="验证码不正确")
        code_record.consumed = 1
        code_record.consumed_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

        account, user = self._get_or_create_account(
            db,
            email=request_body.email,
            email_hash=email_hash,
            request_ip=request_ip,
        )
        self.repository.touch_account_login(db, account=account, request_ip=request_ip)
        expires_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) + timedelta(days=settings.email_auth.session_ttl_days)
        token = self._build_token(user.user_key, request_body.email, expires_at, token_secret=token_secret)
        self.repository.create_session(
            db,
            user_key=user.user_key,
            token_hash=self._token_hash(token),
            request_ip=request_ip,
            browser_fingerprint=request_body.browser_fingerprint,
            expires_at=expires_at,
        )
        db.commit()
        return EmailCodeVerifyResponse.model_validate(
            {
                "token": token,
                "expiresAt": expires_at,
                "user": {
                    "userId": user.user_key,
                    "email": request_body.email,
                    "nickname": user.nickname,
                },
            }
        )

    # 执行ensure enabled相关逻辑。
    def _ensure_enabled(self) -> None:
        if not settings.email_auth.enabled:
            raise HTTPException(status_code=503, detail="邮箱登录暂未启用")

    # 执行resolve token secret相关逻辑。
    def _resolve_token_secret(self, secret_data: dict[str, str]) -> str:
        """从数据库读取邮箱登录签名密钥。"""
        if "AUTH_TOKEN_SECRET" not in secret_data or not secret_data["AUTH_TOKEN_SECRET"]:
            raise HTTPException(status_code=500, detail="数据库 runtime_secrets.AUTH_TOKEN_SECRET 未配置")
        return secret_data["AUTH_TOKEN_SECRET"]

    # 执行send verification email相关逻辑。
    def _send_verification_email(self, email: str, code: str, *, smtp_password: str) -> None:
        subject = "知搭 ZDA 登录验证码"
        html = (
            "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "line-height:1.8;color:#1e2336;\">"
            "<h2 style=\"margin:0 0 12px;\">知搭 ZDA 登录验证码</h2>"
            "<p>你的验证码是：</p>"
            f"<p style=\"font-size:28px;font-weight:800;letter-spacing:6px;margin:12px 0;\">{code}</p>"
            f"<p>验证码 {settings.email_auth.code_ttl_minutes} 分钟内有效。若非本人操作，请忽略本邮件。</p>"
            "</div>"
        )
        message = MIMEText(html, "html", "utf-8")
        message["Subject"] = Header(subject, "utf-8")
        message["From"] = formataddr((str(Header(settings.email_auth.from_name, "utf-8")), settings.email_auth.from_email))
        message["To"] = email
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=settings.email_auth.from_email.split("@")[-1])
        try:
            if settings.email_auth.smtp_ssl:
                with smtplib.SMTP_SSL(
                    settings.email_auth.smtp_host,
                    settings.email_auth.smtp_port,
                    timeout=10,
                ) as smtp:
                    smtp.login(settings.email_auth.username, smtp_password)
                    refused = smtp.sendmail(settings.email_auth.from_email, [email], message.as_string())
                    logger.info("Email verification code accepted by SMTP: email=%s refused=%s", email, refused)
                return
            with smtplib.SMTP(settings.email_auth.smtp_host, settings.email_auth.smtp_port, timeout=10) as smtp:
                smtp.starttls()
                smtp.login(settings.email_auth.username, smtp_password)
                refused = smtp.sendmail(settings.email_auth.from_email, [email], message.as_string())
                logger.info("Email verification code accepted by SMTP: email=%s refused=%s", email, refused)
        except Exception as error:
            logger.exception("Email verification code send failed: email=%s", email)
            raise HTTPException(status_code=502, detail="验证码邮件发送失败") from error

    # 执行get or create account相关逻辑。
    def _get_or_create_account(
        self,
        db: Session,
        *,
        email: str,
        email_hash: str,
        request_ip: str,
    ):
        lock_name = f"zda_email_auth_{email_hash[:32]}"
        if not self.repository.acquire_lock(db, lock_name=lock_name):
            raise HTTPException(status_code=429, detail="账号创建繁忙，请稍后重试")
        try:
            account = self.repository.get_account_by_email_hash(db, email_hash=email_hash)
            if account is not None:
                user = self.repository.get_app_user(db, user_key=account.user_key)
                if user is None or user.deleted or user.status != 1:
                    raise HTTPException(status_code=403, detail="账号不可用")
                return account, user
            user_key = self._build_user_key(email_hash)
            nickname = self._build_nickname(email)
            user = self.repository.create_app_user(
                db,
                user_key=user_key,
                nickname=nickname,
                ip_address=request_ip,
            )
            account = self.repository.create_account(
                db,
                email=email,
                email_hash=email_hash,
                user_key=user_key,
                request_ip=request_ip,
            )
            return account, user
        finally:
            self.repository.release_lock(db, lock_name=lock_name)

    # 执行assert send rate limit相关逻辑。
    def _assert_send_rate_limit(self, db: Session, *, email_hash: str, request_ip: str) -> None:
        minute_since = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) - timedelta(minutes=1)
        hour_since = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) - timedelta(hours=1)
        if self.repository.count_sent_by_ip_since(db, request_ip=request_ip, since=minute_since) >= settings.email_auth.rate_limit.send_ip_per_minute:
            raise HTTPException(status_code=429, detail="验证码发送过于频繁，请稍后重试")
        if self.repository.count_sent_by_email_since(db, email_hash=email_hash, since=hour_since) >= settings.email_auth.rate_limit.send_email_per_hour:
            raise HTTPException(status_code=429, detail="该邮箱验证码发送过于频繁，请稍后重试")

    # 执行assert verify rate limit相关逻辑。
    def _assert_verify_rate_limit(self, db: Session, *, email_hash: str) -> None:
        minute_since = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) - timedelta(minutes=1)
        if self.repository.count_verify_attempts_since(db, email_hash=email_hash, since=minute_since) >= settings.email_auth.rate_limit.verify_email_per_minute:
            raise HTTPException(status_code=429, detail="验证码验证过于频繁，请稍后重试")

    # 执行email hash相关逻辑。
    def _email_hash(self, email: str, *, token_secret: str) -> str:
        return hmac.new(
            token_secret.encode("utf-8"),
            email.lower().encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # 执行code hash相关逻辑。
    def _code_hash(self, email: str, code: str, *, token_secret: str) -> str:
        raw_text = f"{email.lower()}|{code}"
        return hmac.new(
            token_secret.encode("utf-8"),
            raw_text.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # 执行token hash相关逻辑。
    def _token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # 执行build token相关逻辑。
    def _build_token(self, user_key: str, email: str, expires_at: datetime, *, token_secret: str) -> str:
        payload = {
            "sub": user_key,
            "email": email,
            "exp": int(expires_at.timestamp()),
            "iat": int(datetime.now(ZoneInfo("Asia/Shanghai")).timestamp()),
        }
        payload_text = self._base64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        signature = hmac.new(
            token_secret.encode("utf-8"),
            payload_text.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return f"{payload_text}.{self._base64url(signature)}"

    # 执行base64url相关逻辑。
    def _base64url(self, value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    # 执行build user key相关逻辑。
    def _build_user_key(self, email_hash: str) -> str:
        return f"email_{email_hash[:24]}"

    # 执行build nickname相关逻辑。
    def _build_nickname(self, email: str) -> str:
        local_part = email.split("@", 1)[0].strip() or "用户"
        return local_part[:24]

    # 执行resolve client ip相关逻辑。
    def _resolve_client_ip(self, request: Request) -> str:
        return request.client.host if request.client else ""
