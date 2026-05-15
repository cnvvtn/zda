# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\routes\payment.py。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.response import ajax_success
from app.api.response import table_data
from app.core.url_catalog import PythonUrl
from app.features.payment.schemas import CreditRedeemRequest
from app.features.payment.schemas import ZpayOrderCreateRequest
from app.services.credit_service import CreditService
from app.services.zpay_payment_service import ZpayPaymentService


router = APIRouter(prefix=PythonUrl.ZPAY_PAYMENT_PREFIX.value, tags=["payments"])
logger = logging.getLogger(__name__)
zpay_payment_service = ZpayPaymentService()
credit_service = CreditService()


# 执行create zpay order相关逻辑。
@router.post(PythonUrl.ZPAY_ORDER_ROUTE.value)
async def create_zpay_order(
    request_body: ZpayOrderCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """创建 ZPAY 套餐支付订单。"""
    return ajax_success(
        zpay_payment_service.create_order(
            db,
            request=request_body,
            http_request=request,
        )
    )


# 执行get zpay order相关逻辑。
@router.get(f"{PythonUrl.ZPAY_ORDER_ROUTE.value}/{{out_trade_no}}")
async def get_zpay_order(
    out_trade_no: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """查询 ZPAY 套餐支付订单状态。"""
    return ajax_success(zpay_payment_service.get_order_status(db, out_trade_no=out_trade_no))


# 执行zpay notify相关逻辑。
@router.get(PythonUrl.ZPAY_NOTIFY_ROUTE.value, response_class=PlainTextResponse)
async def zpay_notify(
    request: Request,
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """处理 ZPAY 支付结果通知。"""
    params = {key: value for key, value in request.query_params.items()}
    try:
        result = zpay_payment_service.handle_notify(
            db,
            params=params,
            http_request=request,
        )
    except Exception:
        logger.exception("ZPAY notify handler error: params=%s", params)
        result = "fail"
    status_code = 200 if result == "success" else 400
    return PlainTextResponse(result, status_code=status_code)


# 执行get membership entitlement相关逻辑。
@router.get(f"{PythonUrl.PAYMENT_MEMBERSHIP_ROUTE.value}/{{user_id}}")
async def get_membership_entitlement(
    user_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """查询用户当前会员权益。"""
    entitlement = zpay_payment_service.get_entitlement(db, user_id=user_id)
    return ajax_success(entitlement)


# 执行redeem credits相关逻辑。
@router.post(PythonUrl.PAYMENT_CREDIT_REDEEM_ROUTE.value)
async def redeem_credits(
    request_body: CreditRedeemRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """使用兑换码兑换 Credit。"""
    result = credit_service.redeem_credits(
        db,
        user_id=request_body.user_id,
        code=request_body.code,
    )
    db.commit()
    return ajax_success(result)


# 执行list credit usage logs相关逻辑。
@router.get(f"{PythonUrl.PAYMENT_CREDIT_USAGE_ROUTE.value}/{{user_id}}")
async def list_credit_usage_logs(
    user_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """查询用户最近 Credit 完整流水。"""
    result = table_data(
        credit_service.list_ledger_logs(
            db,
            user_id=user_id,
            limit=limit,
        )
    )
    result["expireSummary"] = credit_service.get_nearest_expiring_credit(db, user_id=user_id)
    return result
