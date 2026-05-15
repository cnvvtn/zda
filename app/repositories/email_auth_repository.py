# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\email_auth_repository.py。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from app.db.models import AppUser, EmailAuthAccount, EmailAuthSession, EmailVerificationCode


# 定义EmailAuthRepository。
class EmailAuthRepository:
    """邮箱认证仓储，封装验证码、账号和会话持久化。"""

    # 执行count sent by ip相关逻辑。
    def count_sent_by_ip_since(self, db: Session, *, request_ip: str, since: datetime) -> int:
        """统计某 IP 在窗口内发送验证码次数。"""
        return int(
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.request_ip == request_ip,
                EmailVerificationCode.created_at >= since,
            )
            .count()
        )

    # 执行count sent by email since相关逻辑。
    def count_sent_by_email_since(self, db: Session, *, email_hash: str, since: datetime) -> int:
        """统计某邮箱在窗口内发送验证码次数。"""
        return int(
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.email_hash == email_hash,
                EmailVerificationCode.created_at >= since,
            )
            .count()
        )

    # 执行count verify attempts since相关逻辑。
    def count_verify_attempts_since(self, db: Session, *, email_hash: str, since: datetime) -> int:
        """统计某邮箱在窗口内验证码尝试次数。"""
        return int(
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.email_hash == email_hash,
                EmailVerificationCode.created_at >= since,
                EmailVerificationCode.attempt_count > 0,
            )
            .count()
        )

    # 执行create code相关逻辑。
    def create_code(
        self,
        db: Session,
        *,
        email: str,
        email_hash: str,
        code_hash: str,
        request_ip: str,
        browser_fingerprint: str,
        expires_at: datetime,
    ) -> EmailVerificationCode:
        """保存验证码哈希。"""
        archive = EmailVerificationCode(
            email=email,
            email_hash=email_hash,
            code_hash=code_hash,
            request_ip=request_ip,
            browser_fingerprint=browser_fingerprint,
            expires_at=expires_at,
            purpose="login",
        )
        db.add(archive)
        db.commit()
        db.refresh(archive)
        return archive

    # 执行get latest code for update相关逻辑。
    def get_latest_code_for_update(
        self,
        db: Session,
        *,
        email_hash: str,
    ) -> EmailVerificationCode | None:
        """锁定最近一条未使用验证码。"""
        return (
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.email_hash == email_hash,
                EmailVerificationCode.consumed == 0,
                EmailVerificationCode.expires_at >= datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None),
            )
            .order_by(desc(EmailVerificationCode.created_at), desc(EmailVerificationCode.id))
            .with_for_update()
            .first()
        )

    # 执行get account相关逻辑。
    def get_account_by_email_hash(
        self,
        db: Session,
        *,
        email_hash: str,
    ) -> EmailAuthAccount | None:
        """按邮箱摘要读取账号。"""
        return (
            db.query(EmailAuthAccount)
            .filter(EmailAuthAccount.email_hash == email_hash)
            .first()
        )

    # 执行get app user相关逻辑。
    def get_app_user(self, db: Session, *, user_key: str) -> AppUser | None:
        """按用户业务 ID 读取应用用户。"""
        return db.query(AppUser).filter(AppUser.user_key == user_key).first()

    # 执行create app user相关逻辑。
    def create_app_user(
        self,
        db: Session,
        *,
        user_key: str,
        nickname: str,
        ip_address: str,
    ) -> AppUser:
        """创建应用用户。"""
        user = AppUser(
            user_key=user_key,
            nickname=nickname,
            ip_address=ip_address,
            status=1,
            deleted=0,
            last_active_at=datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None),
        )
        db.add(user)
        db.flush()
        return user

    # 执行create account相关逻辑。
    def create_account(
        self,
        db: Session,
        *,
        email: str,
        email_hash: str,
        user_key: str,
        request_ip: str,
    ) -> EmailAuthAccount:
        """创建邮箱登录账号。"""
        account = EmailAuthAccount(
            email=email,
            email_hash=email_hash,
            user_key=user_key,
            last_login_ip=request_ip,
            last_login_at=datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None),
            status=1,
        )
        db.add(account)
        db.flush()
        return account

    # 执行touch account login相关逻辑。
    def touch_account_login(
        self,
        db: Session,
        *,
        account: EmailAuthAccount,
        request_ip: str,
    ) -> None:
        """更新账号最近登录信息。"""
        account.last_login_ip = request_ip
        account.last_login_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    # 执行create session相关逻辑。
    def create_session(
        self,
        db: Session,
        *,
        user_key: str,
        token_hash: str,
        request_ip: str,
        browser_fingerprint: str,
        expires_at: datetime,
    ) -> None:
        """创建登录会话。"""
        db.add(
            EmailAuthSession(
                user_key=user_key,
                token_hash=token_hash,
                request_ip=request_ip,
                browser_fingerprint=browser_fingerprint,
                expires_at=expires_at,
                status=1,
            )
        )

    # 执行upsert account transaction related logic。
    def acquire_lock(self, db: Session, *, lock_name: str, timeout_seconds: int = 5) -> bool:
        """使用 MySQL 命名锁避免同一邮箱并发注册。"""
        acquired = db.execute(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            {"lock_name": lock_name, "timeout_seconds": int(timeout_seconds)},
        ).scalar()
        return acquired == 1

    # 执行release lock相关逻辑。
    def release_lock(self, db: Session, *, lock_name: str) -> None:
        """释放 MySQL 命名锁。"""
        db.execute(text("SELECT RELEASE_LOCK(:lock_name)"), {"lock_name": lock_name})
