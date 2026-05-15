# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\zpay_payment_repository.py。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from app.db.models import (
    ZdaMembershipEntitlement,
    ZpayPaymentEvent,
    ZpayPaymentOrder,
)
from app.features.payment.schemas import (
    MembershipEntitlementResponse,
    ZpayOrderStatusResponse,
)


# 定义ZpayPaymentRepository。
class ZpayPaymentRepository:
    """ZPAY 支付仓储，封装订单、审计、限流和权益入库。"""

    # 执行create order相关逻辑。
    def create_order(
        self,
        db: Session,
        *,
        out_trade_no: str,
        pid: str,
        plan_code: str,
        plan_name: str,
        money: Decimal,
        pay_type: str,
        user_id: str,
        client_ip: str,
        browser_fingerprint: str,
        page_url: str,
    ) -> ZpayPaymentOrder:
        """创建待支付订单。"""
        order = ZpayPaymentOrder(
            out_trade_no=out_trade_no,
            pid=pid,
            plan_code=plan_code,
            plan_name=plan_name,
            money=money,
            pay_type=pay_type,
            user_id=user_id,
            client_ip=client_ip,
            browser_fingerprint=browser_fingerprint,
            page_url=page_url,
            status="pending",
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    # 执行get order相关逻辑。
    def get_order(self, db: Session, *, out_trade_no: str) -> ZpayPaymentOrder | None:
        """按商户订单号读取订单。"""
        return (
            db.query(ZpayPaymentOrder)
            .filter(ZpayPaymentOrder.out_trade_no == out_trade_no)
            .first()
        )

    # 执行get order for update相关逻辑。
    def get_order_for_update(self, db: Session, *, out_trade_no: str) -> ZpayPaymentOrder | None:
        """在事务中锁定订单行，保证通知并发幂等。"""
        return (
            db.query(ZpayPaymentOrder)
            .filter(ZpayPaymentOrder.out_trade_no == out_trade_no)
            .with_for_update()
            .first()
        )

    # 执行mark order paid相关逻辑。
    def mark_order_paid(
        self,
        db: Session,
        *,
        order: ZpayPaymentOrder,
        trade_no: str,
        zpay_order_id: str,
        notify_payload: dict[str, Any],
    ) -> None:
        """把订单置为已支付，重复通知不重复变更权益。"""
        if order.status == "paid":
            order.notify_count += 1
            order.last_notify_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            order.raw_notify = json.dumps(notify_payload, ensure_ascii=False, sort_keys=True)
            return
        order.status = "paid"
        order.zpay_trade_no = trade_no
        order.zpay_order_id = zpay_order_id
        order.notify_verified = 1
        order.notify_count += 1
        order.last_notify_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        order.paid_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        order.raw_notify = json.dumps(notify_payload, ensure_ascii=False, sort_keys=True)

    # 执行close expired pending order相关逻辑。
    def close_expired_pending_order(
        self,
        db: Session,
        *,
        out_trade_no: str,
        ttl_minutes: int,
    ) -> ZpayPaymentOrder | None:
        """查询时关闭过期未支付订单。"""
        order = self.get_order(db, out_trade_no=out_trade_no)
        if order is None or order.status != "pending":
            return order
        expired = db.execute(
            text(
                """
                UPDATE zpay_payment_order
                SET status = 'closed'
                WHERE out_trade_no = :out_trade_no
                  AND status = 'pending'
                  AND created_at < DATE_SUB(NOW(), INTERVAL :ttl_minutes MINUTE)
                """
            ),
            {"out_trade_no": out_trade_no, "ttl_minutes": int(ttl_minutes)},
        )
        if expired.rowcount:
            db.commit()
            return self.get_order(db, out_trade_no=out_trade_no)
        return order

    # 执行add event相关逻辑。
    def add_event(
        self,
        db: Session,
        *,
        out_trade_no: str,
        event_type: str,
        request_ip: str,
        verified: bool,
        payload: dict[str, Any],
    ) -> None:
        """记录支付安全审计事件。"""
        raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        db.add(
            ZpayPaymentEvent(
                out_trade_no=out_trade_no,
                event_type=event_type,
                request_ip=request_ip,
                verified=1 if verified else 0,
                payload_hash=hashlib.sha256(raw_payload.encode("utf-8")).hexdigest(),
                raw_payload=raw_payload,
            )
        )

    # 执行increment rate bucket相关逻辑。
    def increment_rate_bucket(
        self,
        db: Session,
        *,
        scope: str,
        bucket_key: str,
        window_start: datetime,
    ) -> int:
        """对限流桶做原子自增，并返回自增后的计数。"""
        db.execute(
            text(
                """
                INSERT INTO zpay_payment_rate_limit (scope, bucket_key, window_start, request_count)
                VALUES (:scope, :bucket_key, :window_start, 1)
                ON DUPLICATE KEY UPDATE
                    request_count = request_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "scope": scope,
                "bucket_key": bucket_key,
                "window_start": window_start,
            },
        )
        count = db.execute(
            text(
                """
                SELECT request_count
                FROM zpay_payment_rate_limit
                WHERE scope = :scope
                  AND bucket_key = :bucket_key
                  AND window_start = :window_start
                """
            ),
            {
                "scope": scope,
                "bucket_key": bucket_key,
                "window_start": window_start,
            },
        ).scalar_one()
        db.commit()
        return int(count)

    # 执行acquire lock相关逻辑。
    def acquire_lock(self, db: Session, *, lock_name: str, timeout_seconds: int) -> bool:
        """使用 MySQL 命名锁协调多 worker 下的通知并发。"""
        acquired = db.execute(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            {
                "lock_name": lock_name,
                "timeout_seconds": int(timeout_seconds),
            },
        ).scalar()
        return acquired == 1

    # 执行release lock相关逻辑。
    def release_lock(self, db: Session, *, lock_name: str) -> None:
        """释放 MySQL 命名锁。"""
        db.execute(text("SELECT RELEASE_LOCK(:lock_name)"), {"lock_name": lock_name})

    # 执行grant entitlement相关逻辑。
    def grant_entitlement(
        self,
        db: Session,
        *,
        user_id: str,
        plan_code: str,
        plan_name: str,
        source_order_no: str,
        credit_total: int,
        model_level: str,
        priority_level: int,
        expires_at: datetime,
    ) -> None:
        """发放或刷新用户会员权益。"""
        db.execute(
            text(
                """
                INSERT INTO zda_membership_entitlement (
                    user_id, plan_code, plan_name, source_order_no,
                    credit_total, credit_remaining,
                    model_level, priority_level,
                    expires_at, status
                )
                VALUES (
                    :user_id, :plan_code, :plan_name, :source_order_no,
                    :credit_total, :credit_total,
                    :model_level, :priority_level,
                    :expires_at, 1
                )
                ON DUPLICATE KEY UPDATE
                    plan_code = VALUES(plan_code),
                    plan_name = VALUES(plan_name),
                    source_order_no = VALUES(source_order_no),
                    credit_total = VALUES(credit_total),
                    credit_remaining = VALUES(credit_remaining),
                    model_level = VALUES(model_level),
                    priority_level = VALUES(priority_level),
                    expires_at = VALUES(expires_at),
                    status = 1,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "user_id": user_id,
                "plan_code": plan_code,
                "plan_name": plan_name,
                "source_order_no": source_order_no,
                "credit_total": int(credit_total),
                "model_level": model_level,
                "priority_level": int(priority_level),
                "expires_at": expires_at,
            },
        )

    # 执行get entitlement相关逻辑。
    def get_entitlement(
        self,
        db: Session,
        *,
        user_id: str,
    ) -> MembershipEntitlementResponse | None:
        """查询用户当前会员权益。"""
        archive = (
            db.query(ZdaMembershipEntitlement)
            .filter(ZdaMembershipEntitlement.user_id == user_id)
            .order_by(desc(ZdaMembershipEntitlement.updated_at))
            .first()
        )
        if archive is None:
            return None
        return MembershipEntitlementResponse.model_validate(
            {
                "userId": archive.user_id,
                "planCode": archive.plan_code,
                "planName": archive.plan_name,
                "creditTotal": archive.credit_total,
                "creditRemaining": archive.credit_remaining,
                "modelLevel": archive.model_level,
                "priorityLevel": archive.priority_level,
                "expiresAt": archive.expires_at,
                "status": archive.status,
            }
        )

    # 执行map order status相关逻辑。
    def map_order_status(self, order: ZpayPaymentOrder) -> ZpayOrderStatusResponse:
        """把订单实体映射为前端状态响应。"""
        return ZpayOrderStatusResponse.model_validate(
            {
                "outTradeNo": order.out_trade_no,
                "planCode": order.plan_code,
                "planName": order.plan_name,
                "money": f"{Decimal(order.money):.2f}",
                "payType": order.pay_type,
                "status": order.status,
                "paidAt": order.paid_at,
            }
        )
