# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\credit_service.py。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import desc
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.models import ZdaCreditLedger, ZdaCreditRedeemCode, ZdaCreditRedeemRecord, ZdaCreditUsageLog
from app.features.payment.schemas import CreditRedeemResponse, CreditUsageLogItem


_MODEL_LEVEL_ALIASES = {
    "free": "experience",
    "trial": "experience",
    "experience": "experience",
    "basic": "basic",
    "advanced": "advanced",
    "top": "top",
    "professional": "top",
    "pro": "top",
}


# 定义CreditService。
class CreditService:
    """Credit 计费服务，集中处理额度发放、余额查询和最快过期优先扣减。"""

    # 执行resolve model cost相关逻辑。
    def resolve_model_cost(self, model_level: str) -> int:
        """按模型等级返回单次生成需要消耗的 Credit。"""
        normalized_level = _MODEL_LEVEL_ALIASES.get(
            str(model_level or "").strip().lower(),
            "basic",
        )
        return int(settings.credit_billing.model_costs.get(normalized_level, 10))

    # 执行ensure daily free credits相关逻辑。
    def ensure_daily_free_credits(self, db: Session, *, user_id: str) -> None:
        """为登录用户补齐当天免费 Credit，同一天重复调用不会重复发放。"""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id or settings.credit_billing.free_daily_credits <= 0:
            return
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        expires_at = datetime.combine(today + timedelta(days=1), datetime.min.time())
        db.execute(
            text(
                """
                INSERT INTO zda_credit_ledger (
                    user_id, source_type, source_key, source_order_no,
                    plan_code, plan_name, credit_total, credit_remaining,
                    expires_at, status
                )
                VALUES (
                    :user_id, 'free_daily', :source_key, '',
                    'free', '每日赠送', :credits, :credits,
                    :expires_at, 1
                )
                ON DUPLICATE KEY UPDATE updated_at = updated_at
                """
            ),
            {
                "user_id": normalized_user_id,
                "source_key": today.isoformat(),
                "credits": int(settings.credit_billing.free_daily_credits),
                "expires_at": expires_at,
            },
        )
        db.flush()

    # 执行grant paid credits相关逻辑。
    def grant_paid_credits(
        self,
        db: Session,
        *,
        user_id: str,
        plan_code: str,
        plan_name: str,
        source_order_no: str,
        credit_total: int,
        expires_at: datetime,
    ) -> None:
        """支付成功后写入一条独立 Credit 流水。"""
        db.execute(
            text(
                """
                INSERT INTO zda_credit_ledger (
                    user_id, source_type, source_key, source_order_no,
                    plan_code, plan_name, credit_total, credit_remaining,
                    expires_at, status
                )
                VALUES (
                    :user_id, 'payment', :source_key, :source_order_no,
                    :plan_code, :plan_name, :credit_total, :credit_total,
                    :expires_at, 1
                )
                ON DUPLICATE KEY UPDATE updated_at = updated_at
                """
            ),
            {
                "user_id": user_id,
                "source_key": source_order_no,
                "source_order_no": source_order_no,
                "plan_code": plan_code,
                "plan_name": plan_name,
                "credit_total": int(credit_total),
                "expires_at": expires_at,
            },
        )
        db.flush()

    # 执行redeem credits相关逻辑。
    def redeem_credits(
        self,
        db: Session,
        *,
        user_id: str,
        code: str,
    ) -> CreditRedeemResponse:
        """使用兑换码给当前用户发放 Credit。"""
        normalized_user_id = str(user_id or "").strip()
        normalized_code = str(code or "").strip()
        if not normalized_user_id:
            raise HTTPException(status_code=401, detail="请先登录后再兑换")
        if not normalized_code:
            raise HTTPException(status_code=400, detail="兑换码不能为空")
        if not normalized_code.isalnum() or not normalized_code.isascii():
            raise HTTPException(status_code=400, detail="兑换码仅允许大小写英文和数字")
        redeem_code = (
            db.query(ZdaCreditRedeemCode)
            .filter(ZdaCreditRedeemCode.code == normalized_code)
            .with_for_update()
            .first()
        )
        if redeem_code is None or int(redeem_code.status or 0) != 1:
            raise HTTPException(status_code=404, detail="兑换码不存在或已失效")
        if redeem_code.expires_at is not None and redeem_code.expires_at <= datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None):
            raise HTTPException(status_code=400, detail="兑换码已过期")
        if int(redeem_code.credit_total or 0) <= 0:
            raise HTTPException(status_code=400, detail="兑换码额度配置错误")
        if int(redeem_code.used_count or 0) >= int(redeem_code.max_uses or 1):
            raise HTTPException(status_code=400, detail="兑换码已被使用完")
        expires_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) + timedelta(days=max(1, int(redeem_code.valid_days or 30)))
        source_key = f"{int(redeem_code.id)}:{normalized_user_id}"
        ledger = ZdaCreditLedger(
            user_id=normalized_user_id,
            source_type="redeem_code",
            source_key=source_key,
            source_order_no="",
            plan_code="redeem",
            plan_name=f"兑换码 {normalized_code}",
            credit_total=int(redeem_code.credit_total),
            credit_remaining=int(redeem_code.credit_total),
            expires_at=expires_at,
            status=1,
        )
        db.add(ledger)
        try:
            db.flush()
        except IntegrityError as error:
            db.rollback()
            raise HTTPException(status_code=400, detail="你已经兑换过该兑换码") from error
        redeem_code.used_count = int(redeem_code.used_count or 0) + 1
        record = ZdaCreditRedeemRecord(
            redeem_code_id=int(redeem_code.id),
            user_id=normalized_user_id,
            code=normalized_code,
            credit_total=int(redeem_code.credit_total),
            ledger_id=int(ledger.id),
        )
        db.add(record)
        try:
            db.flush()
        except IntegrityError as error:
            db.rollback()
            raise HTTPException(status_code=400, detail="你已经兑换过该兑换码") from error
        credit_remaining = self.get_available_credits(db, user_id=normalized_user_id)
        return CreditRedeemResponse.model_validate(
            {
                "code": normalized_code,
                "creditTotal": int(redeem_code.credit_total),
                "creditRemaining": credit_remaining,
                "expiresAt": expires_at,
            }
        )

    # 执行get available credits相关逻辑。
    def get_available_credits(self, db: Session, *, user_id: str) -> int:
        """读取当前未过期的可用 Credit 总额。"""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return 0
        self.ensure_daily_free_credits(db, user_id=normalized_user_id)
        self.expire_invalid_credits(db, user_id=normalized_user_id)
        total = (
            db.query(func.coalesce(func.sum(ZdaCreditLedger.credit_remaining), 0))
            .filter(ZdaCreditLedger.user_id == normalized_user_id)
            .filter(ZdaCreditLedger.status == 1)
            .filter(ZdaCreditLedger.credit_remaining > 0)
            .filter(ZdaCreditLedger.expires_at > datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None))
            .scalar()
        )
        return int(total or 0)

    # 执行list usage logs相关逻辑。
    def list_usage_logs(
        self,
        db: Session,
        *,
        user_id: str,
        limit: int = 50,
    ) -> list[CreditUsageLogItem]:
        """按用户读取最近的 Credit 扣减明细。"""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return []
        normalized_limit = max(1, min(int(limit or 50), 200))
        rows = (
            db.query(ZdaCreditUsageLog)
            .filter(ZdaCreditUsageLog.user_id == normalized_user_id)
            .order_by(desc(ZdaCreditUsageLog.created_at), desc(ZdaCreditUsageLog.id))
            .limit(normalized_limit)
            .all()
        )
        return [
            CreditUsageLogItem.model_validate(
                {
                    "id": row.id,
                    "userId": row.user_id,
                    "ledgerId": row.ledger_id,
                    "usageType": row.usage_type,
                    "requestId": row.request_id,
                    "modelLevel": row.model_level,
                    "creditAmount": row.credit_amount,
                    "balanceBefore": row.balance_before,
                    "balanceAfter": row.balance_after,
                    "createdAt": row.created_at,
                }
            )
            for row in rows
        ]

    # 执行list ledger logs相关逻辑。
    def list_ledger_logs(
        self,
        db: Session,
        *,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """按用户读取最近的 Credit 发放与扣减完整流水。"""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return []
        normalized_limit = max(1, min(int(limit or 100), 200))
        ledger_rows = (
            db.query(ZdaCreditLedger)
            .filter(ZdaCreditLedger.user_id == normalized_user_id)
            .order_by(desc(ZdaCreditLedger.created_at), desc(ZdaCreditLedger.id))
            .limit(normalized_limit)
            .all()
        )
        usage_rows = (
            db.query(ZdaCreditUsageLog)
            .filter(ZdaCreditUsageLog.user_id == normalized_user_id)
            .order_by(desc(ZdaCreditUsageLog.created_at), desc(ZdaCreditUsageLog.id))
            .limit(normalized_limit)
            .all()
        )
        usage_ledger_map = {
            row.id: row
            for row in db.query(ZdaCreditLedger)
            .filter(ZdaCreditLedger.id.in_([item.ledger_id for item in usage_rows]))
            .all()
        } if usage_rows else {}
        rows: list[dict[str, object]] = []
        for row in ledger_rows:
            granted_credit = int(row.credit_total or 0)
            expiring_credit = int(row.credit_remaining or 0)
            total_before = int(
                db.execute(
                    text(
                        """
                        SELECT
                            (
                                SELECT COALESCE(SUM(ledger.credit_total), 0)
                                FROM zda_credit_ledger ledger
                                WHERE ledger.user_id = :user_id
                                  AND ledger.expires_at > :event_time
                                  AND (
                                      ledger.created_at < :event_time
                                      OR (ledger.created_at = :event_time AND ledger.id < :ledger_id)
                                  )
                            )
                            -
                            (
                                SELECT COALESCE(SUM(usage_log.credit_amount), 0)
                                FROM zda_credit_usage_log usage_log
                                INNER JOIN zda_credit_ledger ledger ON ledger.id = usage_log.ledger_id
                                WHERE usage_log.user_id = :user_id
                                  AND ledger.expires_at > :event_time
                                  AND usage_log.created_at < :event_time
                            )
                        """
                    ),
                    {
                        "user_id": normalized_user_id,
                        "event_time": row.created_at,
                        "ledger_id": int(row.id),
                    },
                ).scalar()
                or 0
            )
            rows.append(
                {
                    "id": f"ledger-{row.id}",
                    "userId": row.user_id,
                    "direction": "in",
                    "eventType": row.source_type,
                    "title": row.plan_name or row.plan_code or row.source_type,
                    "creditAmount": granted_credit,
                    "balanceBefore": 0,
                    "balanceAfter": granted_credit,
                    "changeBefore": total_before,
                    "changeAfter": total_before + granted_credit,
                    "expiringCredit": expiring_credit,
                    "requestId": row.source_order_no or row.source_key,
                    "sourceType": row.source_type,
                    "sourceKey": row.source_key,
                    "sourceOrderNo": row.source_order_no,
                    "modelLevel": "",
                    "expiresAt": row.expires_at,
                    "createdAt": row.created_at,
                }
            )
        for row in usage_rows:
            ledger_row = usage_ledger_map.get(row.ledger_id)
            balance_before = int(row.balance_before or 0)
            balance_after = int(row.balance_after or 0)
            total_before = int(
                db.execute(
                    text(
                        """
                        SELECT
                            (
                                SELECT COALESCE(SUM(ledger.credit_total), 0)
                                FROM zda_credit_ledger ledger
                                WHERE ledger.user_id = :user_id
                                  AND ledger.expires_at > :event_time
                                  AND ledger.created_at <= :event_time
                            )
                            -
                            (
                                SELECT COALESCE(SUM(usage_log.credit_amount), 0)
                                FROM zda_credit_usage_log usage_log
                                INNER JOIN zda_credit_ledger ledger ON ledger.id = usage_log.ledger_id
                                WHERE usage_log.user_id = :user_id
                                  AND ledger.expires_at > :event_time
                                  AND (
                                      usage_log.created_at < :event_time
                                      OR (usage_log.created_at = :event_time AND usage_log.id < :usage_id)
                                  )
                            )
                        """
                    ),
                    {
                        "user_id": normalized_user_id,
                        "event_time": row.created_at,
                        "usage_id": int(row.id),
                    },
                ).scalar()
                or 0
            )
            credit_amount = int(row.credit_amount or 0)
            rows.append(
                {
                    "id": f"usage-{row.id}",
                    "userId": row.user_id,
                    "direction": "out",
                    "eventType": row.usage_type,
                    "title": row.usage_type,
                    "creditAmount": credit_amount,
                    "balanceBefore": balance_before,
                    "balanceAfter": balance_after,
                    "changeBefore": total_before,
                    "changeAfter": total_before - credit_amount,
                    "expiringCredit": balance_after,
                    "requestId": row.request_id,
                    "sourceType": row.usage_type,
                    "sourceKey": row.request_id,
                    "sourceOrderNo": "",
                    "modelLevel": row.model_level,
                    "expiresAt": ledger_row.expires_at if ledger_row else None,
                    "createdAt": row.created_at,
                }
            )
        return sorted(
            rows,
            key=lambda item: str(item["createdAt"]),
            reverse=True,
        )[:normalized_limit]

    # 执行get nearest expiring credit相关逻辑。
    def get_nearest_expiring_credit(self, db: Session, *, user_id: str) -> dict[str, object] | None:
        """读取当前最早过期且仍可用的 Credit 额度。"""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return None
        row = (
            db.query(ZdaCreditLedger)
            .filter(ZdaCreditLedger.user_id == normalized_user_id)
            .filter(ZdaCreditLedger.status == 1)
            .filter(ZdaCreditLedger.credit_remaining > 0)
            .filter(ZdaCreditLedger.expires_at > datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None))
            .order_by(ZdaCreditLedger.expires_at.asc(), ZdaCreditLedger.id.asc())
            .first()
        )
        if not row:
            return None
        return {
            "expiringCredit": int(row.credit_remaining or 0),
            "expiresAt": row.expires_at,
        }

    # 执行consume credits相关逻辑。
    def consume_credits(
        self,
        db: Session,
        *,
        user_id: str,
        amount: int,
        model_level: str,
        usage_type: str,
        request_id: str,
    ) -> int:
        """按最快过期优先规则扣减 Credit，余额不足时直接抛出 HTTP 错误。"""
        normalized_user_id = str(user_id or "").strip()
        normalized_amount = int(amount)
        normalized_usage_type = str(usage_type or "").strip()
        normalized_request_id = str(request_id or "").strip()
        if not normalized_user_id:
            raise HTTPException(status_code=401, detail="请先登录后再生成")
        if not normalized_usage_type or not normalized_request_id:
            raise HTTPException(status_code=400, detail="Credit 使用 requestId 不能为空")
        if normalized_amount <= 0:
            return 0
        self.ensure_daily_free_credits(db, user_id=normalized_user_id)
        self.expire_invalid_credits(db, user_id=normalized_user_id)
        rows = (
            db.query(ZdaCreditLedger)
            .filter(ZdaCreditLedger.user_id == normalized_user_id)
            .filter(ZdaCreditLedger.status == 1)
            .filter(ZdaCreditLedger.credit_remaining > 0)
            .filter(ZdaCreditLedger.expires_at > datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None))
            .order_by(ZdaCreditLedger.expires_at.asc(), ZdaCreditLedger.id.asc())
            .with_for_update()
            .all()
        )
        available_credits = sum(int(row.credit_remaining or 0) for row in rows)
        if available_credits < normalized_amount:
            raise HTTPException(
                status_code=402,
                detail=f"可用 Credits 不足，本次{self.resolve_model_label(model_level)}需要 {normalized_amount} Credits。",
            )
        remaining_amount = normalized_amount
        for row in rows:
            if remaining_amount <= 0:
                break
            consume_amount = min(int(row.credit_remaining), remaining_amount)
            balance_before = int(row.credit_remaining)
            row.credit_remaining -= consume_amount
            db.add(
                ZdaCreditUsageLog(
                    user_id=normalized_user_id,
                    ledger_id=int(row.id),
                    usage_type=normalized_usage_type,
                    request_id=normalized_request_id,
                    model_level=str(model_level or "").strip(),
                    credit_amount=consume_amount,
                    balance_before=balance_before,
                    balance_after=int(row.credit_remaining),
                )
            )
            remaining_amount -= consume_amount
        db.flush()
        return normalized_amount

    # 执行refund credits相关逻辑。
    def refund_credits(
        self,
        db: Session,
        *,
        user_id: str,
        amount: int,
        source_key: str,
        reason: str,
    ) -> int:
        """按任务维度幂等退回 Credit，避免重复取消重复返还。"""
        normalized_user_id = str(user_id or "").strip()
        normalized_amount = int(amount)
        normalized_source_key = str(source_key or "").strip()
        if not normalized_user_id or normalized_amount <= 0 or not normalized_source_key:
            return 0
        expires_at = (
            db.query(func.max(ZdaCreditLedger.expires_at))
            .filter(ZdaCreditLedger.user_id == normalized_user_id)
            .filter(ZdaCreditLedger.expires_at > datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None))
            .scalar()
        ) or (datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) + timedelta(days=30))
        db.execute(
            text(
                """
                INSERT INTO zda_credit_ledger (
                    user_id, source_type, source_key, source_order_no,
                    plan_code, plan_name, credit_total, credit_remaining,
                    expires_at, status
                )
                VALUES (
                    :user_id, 'generation_refund', :source_key, '',
                    'refund', :reason, :credits, :credits,
                    :expires_at, 1
                )
                ON DUPLICATE KEY UPDATE updated_at = updated_at
                """
            ),
            {
                "user_id": normalized_user_id,
                "source_key": normalized_source_key,
                "reason": str(reason or "生成中断退回"),
                "credits": normalized_amount,
                "expires_at": expires_at,
            },
        )
        db.flush()
        return normalized_amount

    # 执行expire invalid credits相关逻辑。
    def expire_invalid_credits(self, db: Session, *, user_id: str) -> None:
        """把已过期或余额为零的 Credit 流水标记为不可用。"""
        db.execute(
            text(
                """
                UPDATE zda_credit_ledger
                SET status = 0
                WHERE user_id = :user_id
                  AND status = 1
                  AND (credit_remaining <= 0 OR expires_at <= NOW())
                """
            ),
            {"user_id": str(user_id or "").strip()},
        )
        db.flush()

    # 执行expire all invalid credits相关逻辑。
    def expire_all_invalid_credits(self, db: Session, *, now: datetime) -> dict[str, int]:
        """全局清理已过期 Credit，把剩余额度归零并置为不可用。"""
        ledger_result = db.execute(
            text(
                """
                UPDATE zda_credit_ledger
                SET credit_remaining = 0,
                    status = 0
                WHERE status = 1
                  AND (credit_remaining <= 0 OR expires_at <= :now)
                """
            ),
            {"now": now},
        )
        entitlement_result = db.execute(
            text(
                """
                UPDATE zda_membership_entitlement
                SET credit_remaining = 0,
                    status = 0
                WHERE status = 1
                  AND expires_at IS NOT NULL
                  AND expires_at <= :now
                """
            ),
            {"now": now},
        )
        db.commit()
        return {
            "ledger": int(ledger_result.rowcount or 0),
            "entitlement": int(entitlement_result.rowcount or 0),
        }

    # 执行resolve model label相关逻辑。
    def resolve_model_label(self, model_level: str) -> str:
        """把模型等级转成人类可读文案。"""
        normalized_level = _MODEL_LEVEL_ALIASES.get(
            str(model_level or "").strip().lower(),
            "basic",
        )
        labels = {
            "experience": "体验模型",
            "basic": "基础模型",
            "advanced": "高级模型",
            "top": "顶级模型",
        }
        return labels[normalized_level]
