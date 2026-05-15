# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\zpay_payment_service.py。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from secrets import token_hex
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core.settings import ZpayPlanConfig, settings
from app.core.url_catalog import PythonUrl
from app.db.models import WebsiteContent
from app.features.payment.schemas import (
    MembershipEntitlementResponse,
    ZpayOrderCreateRequest,
    ZpayOrderCreateResponse,
    ZpayOrderStatusResponse,
)
from app.repositories.zpay_payment_repository import ZpayPaymentRepository
from app.repositories.runtime_secret_repository import RuntimeSecretRepository
from app.services.credit_service import CreditService


logger = logging.getLogger(__name__)


# 定义ZpayPaymentService。
class ZpayPaymentService:
    """ZPAY Credit 充值服务，负责签名、限流、并发控制和额度发放。"""

    def __init__(
        self,
        repository: ZpayPaymentRepository | None = None,
        credit_service: CreditService | None = None,
        runtime_secret_repository: RuntimeSecretRepository | None = None,
    ) -> None:
        self.repository = repository or ZpayPaymentRepository()
        self.credit_service = credit_service or CreditService()
        self.runtime_secret_repository = runtime_secret_repository or RuntimeSecretRepository()

    # 执行create order相关逻辑。
    def create_order(
        self,
        db: Session,
        *,
        request: ZpayOrderCreateRequest,
        http_request: Request,
    ) -> ZpayOrderCreateResponse:
        """创建支付订单，并通过 ZPAY API 接口返回支付码信息。"""
        self._ensure_enabled()
        merchant_key = self._resolve_merchant_key(db)
        normalized_user_id = request.user_id.strip()
        if not normalized_user_id:
            raise HTTPException(status_code=401, detail="请先登录后再充值 Credit")
        plan = self._resolve_plan(db, request.plan_code)
        client_ip = self._resolve_client_ip(http_request)
        self._assert_order_rate_limit(
            db,
            client_ip=client_ip,
            user_id=normalized_user_id,
            browser_fingerprint=request.browser_fingerprint,
        )
        out_trade_no = self._build_out_trade_no()
        order = self.repository.create_order(
            db,
            out_trade_no=out_trade_no,
            pid=settings.zpay.pid,
            plan_code=request.plan_code,
            plan_name=plan.name,
            money=self._normalize_money(plan.money),
            pay_type=request.pay_type,
            user_id=normalized_user_id,
            client_ip=client_ip,
            browser_fingerprint=request.browser_fingerprint,
            page_url=request.page_url,
        )
        self.repository.add_event(
            db,
            out_trade_no=out_trade_no,
            event_type="order_created",
            request_ip=client_ip,
            verified=True,
            payload={
                "planCode": request.plan_code,
                "payType": request.pay_type,
                "userId": normalized_user_id,
            },
        )
        db.commit()
        # 执行request zpay mapi payment相关逻辑。
        payment_payload = self._request_zpay_mapi_payment(
            order={
                "out_trade_no": order.out_trade_no,
                "plan_code": order.plan_code,
                "plan_name": order.plan_name,
                "money": f"{Decimal(order.money):.2f}",
                "pay_type": order.pay_type,
                "page_url": request.page_url,
            },
            merchant_key=merchant_key,
            http_request=http_request,
            client_ip=client_ip,
        )
        self.repository.add_event(
            db,
            out_trade_no=out_trade_no,
            event_type="order_api_created",
            request_ip=client_ip,
            verified=True,
            payload=payment_payload,
        )
        db.commit()
        return ZpayOrderCreateResponse.model_validate(
            {
                "outTradeNo": order.out_trade_no,
                "status": order.status,
                "payUrl": str(payment_payload.get("payurl") or ""),
                "qrcode": str(payment_payload.get("qrcode") or ""),
                "img": str(payment_payload.get("img") or ""),
                "tradeNo": str(payment_payload.get("trade_no") or ""),
                "zpayOrderId": str(payment_payload.get("O_id") or ""),
                "money": f"{Decimal(order.money):.2f}",
                "planName": order.plan_name,
            }
        )

    # 执行get order status相关逻辑。
    def get_order_status(
        self,
        db: Session,
        *,
        out_trade_no: str,
    ) -> ZpayOrderStatusResponse:
        """读取订单状态；本地未支付时尝试向 ZPAY 查询一次。"""
        normalized_order_no = str(out_trade_no or "").strip()
        if not normalized_order_no:
            raise HTTPException(status_code=400, detail="out_trade_no 不能为空")
        order = self.repository.close_expired_pending_order(
            db,
            out_trade_no=normalized_order_no,
            ttl_minutes=settings.zpay.order_ttl_minutes,
        )
        if order is None:
            raise HTTPException(status_code=404, detail="订单不存在")
        if order.status == "pending":
            self._sync_order_from_zpay(db, out_trade_no=normalized_order_no)
            order = self.repository.get_order(db, out_trade_no=normalized_order_no) or order
        return self.repository.map_order_status(order)

    # 执行handle notify相关逻辑。
    def handle_notify(
        self,
        db: Session,
        *,
        params: dict[str, str],
        http_request: Request,
    ) -> str:
        """处理 ZPAY 异步通知。成功时必须返回纯 success。"""
        self._ensure_enabled()
        merchant_key = self._resolve_merchant_key(db)
        client_ip = self._resolve_client_ip(http_request)
        self._assert_notify_rate_limit(db, client_ip=client_ip)
        out_trade_no = str(params.get("out_trade_no") or "").strip()
        sign_verified = self.verify_sign(params, merchant_key=merchant_key)
        self.repository.add_event(
            db,
            out_trade_no=out_trade_no,
            event_type="notify_received",
            request_ip=client_ip,
            verified=sign_verified,
            payload=params,
        )
        db.commit()
        if not sign_verified or str(params.get("pid")) != settings.zpay.pid:
            logger.warning("ZPAY notify rejected: outTradeNo=%s ip=%s", out_trade_no, client_ip)
            return "fail"
        if params.get("trade_status") != "TRADE_SUCCESS":
            return "success"
        if not out_trade_no:
            return "fail"

        lock_name = f"zda_zpay_order_{out_trade_no}"
        lock_acquired = self.repository.acquire_lock(
            db,
            lock_name=lock_name,
            timeout_seconds=settings.zpay.notify_lock_timeout_seconds,
        )
        if not lock_acquired:
            logger.warning("ZPAY notify lock timeout: outTradeNo=%s", out_trade_no)
            return "fail"
        try:
            order = self.repository.get_order_for_update(db, out_trade_no=out_trade_no)
            if order is None:
                return "fail"
            notify_money = self._normalize_money(params.get("money"))
            if notify_money != Decimal(order.money):
                logger.warning(
                    "ZPAY notify money mismatch: outTradeNo=%s expected=%s actual=%s",
                    out_trade_no,
                    order.money,
                    notify_money,
                )
                return "fail"
            self.repository.mark_order_paid(
                db,
                order=order,
                trade_no=str(params.get("trade_no") or ""),
                zpay_order_id=str(params.get("O_id") or ""),
                notify_payload=params,
            )
            if order.user_id:
                self._grant_order_entitlement(db, order=order)
            db.commit()
            return "success"
        except Exception:
            db.rollback()
            raise
        finally:
            self.repository.release_lock(db, lock_name=lock_name)

    # 执行get entitlement相关逻辑。
    def get_entitlement(
        self,
        db: Session,
        *,
        user_id: str,
    ) -> MembershipEntitlementResponse | None:
        """查询用户 Credit 权益。"""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise HTTPException(status_code=400, detail="user_id 不能为空")
        available_credits = self.credit_service.get_available_credits(db, user_id=normalized_user_id)
        db.commit()
        entitlement = self.repository.get_entitlement(db, user_id=normalized_user_id)
        if entitlement is None:
            return MembershipEntitlementResponse.model_validate(
                {
                    "userId": normalized_user_id,
                    "planCode": "free",
                    "planName": "免费额度",
                    "creditTotal": settings.credit_billing.free_daily_credits,
                    "creditRemaining": available_credits,
                    "modelLevel": "basic",
                    "priorityLevel": 0,
                    "expiresAt": None,
                    "status": 1,
                }
            )
        return entitlement.model_copy(update={"credit_remaining": available_credits})

    # 执行verify sign相关逻辑。
    def verify_sign(self, params: dict[str, Any], *, merchant_key: str) -> bool:
        """验证 ZPAY 回调签名。"""
        provided_sign = str(params.get("sign") or "").lower()
        if not provided_sign:
            return False
        expected_sign = self.sign_params(params, merchant_key=merchant_key)
        return hmac.compare_digest(provided_sign, expected_sign)

    # 执行sign params相关逻辑。
    def sign_params(self, params: dict[str, Any], *, merchant_key: str) -> str:
        """按 ZPAY 文档规则生成 MD5 签名。"""
        filtered_params = {
            key: str(value)
            for key, value in params.items()
            if key not in {"sign", "sign_type"} and str(value) != ""
        }
        signing_text = "&".join(
            f"{key}={filtered_params[key]}" for key in sorted(filtered_params)
        )
        return hashlib.md5((signing_text + merchant_key).encode("utf-8")).hexdigest()

    # 执行ensure enabled相关逻辑。
    def _ensure_enabled(self) -> None:
        if not settings.zpay.enabled:
            raise HTTPException(status_code=503, detail="支付服务未启用")

    # 执行resolve merchant key相关逻辑。
    def _resolve_merchant_key(self, db: Session) -> str:
        return self.runtime_secret_repository.get_secret(db, "ZPAY_KEY")

    # 执行resolve plan相关逻辑。
    def _resolve_plan(self, db: Session, plan_code: str) -> ZpayPlanConfig:
        normalized_plan_code = str(plan_code or "").strip()
        plan = settings.zpay.plan_catalog.get(normalized_plan_code)
        if plan is None:
            raise HTTPException(status_code=400, detail="充值档位不存在或暂不支持购买")
        record = db.query(WebsiteContent).filter(WebsiteContent.content_key == "home").first()
        if record is None:
            return plan
        try:
            config_data = json.loads(record.content_json)
        except json.JSONDecodeError:
            return plan
        pricing_plans = config_data.get("pricingPlans")
        if not isinstance(pricing_plans, list):
            return plan
        website_plan = next(
            (
                item for item in pricing_plans
                if isinstance(item, dict)
                and str(item.get("code") or item.get("planCode") or item.get("paymentCode") or item.get("id") or "").strip() == normalized_plan_code
            ),
            None,
        )
        if not isinstance(website_plan, dict):
            return plan
        plan_data = plan.model_dump()
        if str(website_plan.get("name") or "").strip():
            plan_data["name"] = str(website_plan["name"]).strip()
        if str(website_plan.get("money") or website_plan.get("price") or "").strip():
            plan_data["money"] = str(website_plan.get("money") or website_plan.get("price")).strip()
        credit_total = website_plan.get("creditTotal", website_plan.get("credit_total"))
        if credit_total not in (None, ""):
            plan_data["credit_total"] = int(credit_total)
        return ZpayPlanConfig.model_validate(plan_data)

    # 执行normalize money相关逻辑。
    def _normalize_money(self, value: Any) -> Decimal:
        try:
            amount = Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as error:
            raise HTTPException(status_code=500, detail="充值金额配置错误") from error
        if amount <= 0:
            raise HTTPException(status_code=400, detail="免费额度无需支付")
        return amount

    # 执行build out trade no相关逻辑。
    def _build_out_trade_no(self) -> str:
        return f"ZDA{time.strftime('%y%m%d%H%M%S')}{token_hex(6)}"

    # 执行request zpay mapi payment相关逻辑。
    def _request_zpay_mapi_payment(
        self,
        *,
        order: dict[str, str],
        merchant_key: str,
        http_request: Request,
        client_ip: str,
    ) -> dict[str, Any]:
        """调用 ZPAY API 支付接口，返回二维码、图片或支付链接。"""
        gateway = settings.zpay.gateway.rstrip("/") + "/"
        params = {
            "pid": settings.zpay.pid,
            "type": order["pay_type"],
            "out_trade_no": order["out_trade_no"],
            "notify_url": f"{self._public_api_base_url(http_request)}{PythonUrl.ZPAY_NOTIFY_PATH.value}",
            "name": order["plan_name"],
            "money": order["money"],
            "clientip": client_ip,
            "device": self._resolve_payment_device(http_request),
            "param": order["plan_code"],
        }
        params["sign"] = self.sign_params(params, merchant_key=merchant_key)
        params["sign_type"] = "MD5"
        try:
            response = httpx.post(
                f"{gateway}{PythonUrl.ZPAY_MAPI_PATH.value}",
                data=params,
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise HTTPException(status_code=502, detail="ZPAY API 支付接口请求失败") from error
        if str(payload.get("code")) != "1":
            message = str(payload.get("msg") or "ZPAY API 支付接口返回失败")
            raise HTTPException(status_code=502, detail=message)
        return payload

    # 执行sync order from zpay相关逻辑。
    def _sync_order_from_zpay(self, db: Session, *, out_trade_no: str) -> None:
        """本地未收到通知时，主动查询 ZPAY 订单状态作为补偿。"""
        merchant_key = self._resolve_merchant_key(db)
        gateway = settings.zpay.gateway.rstrip("/") + "/"
        query_params = {
            "act": "order",
            "pid": settings.zpay.pid,
            "key": merchant_key,
            "out_trade_no": out_trade_no,
        }
        try:
            response = httpx.get(
                f"{gateway}{PythonUrl.ZPAY_QUERY_API_PATH.value}",
                params=query_params,
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return
        if str(payload.get("code")) != "1" or str(payload.get("status")) != "1":
            return
        lock_name = f"zda_zpay_order_{out_trade_no}"
        if not self.repository.acquire_lock(db, lock_name=lock_name, timeout_seconds=2):
            return
        try:
            order = self.repository.get_order_for_update(db, out_trade_no=out_trade_no)
            if order is None or order.status == "paid":
                return
            if self._normalize_money(payload.get("money")) != Decimal(order.money):
                return
            self.repository.mark_order_paid(
                db,
                order=order,
                trade_no=str(payload.get("trade_no") or ""),
                zpay_order_id=str(payload.get("O_id") or ""),
                notify_payload=payload,
            )
            if order.user_id:
                self._grant_order_entitlement(db, order=order)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            self.repository.release_lock(db, lock_name=lock_name)

    # 执行grant order entitlement相关逻辑。
    def _grant_order_entitlement(self, db: Session, *, order: Any) -> None:
        plan = self._resolve_plan(db, order.plan_code)
        expires_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) + timedelta(days=plan.days)
        self.repository.grant_entitlement(
            db,
            user_id=order.user_id,
            plan_code=order.plan_code,
            plan_name=order.plan_name,
            source_order_no=order.out_trade_no,
            credit_total=plan.credit_total,
            model_level=plan.model_level,
            priority_level=plan.priority_level,
            expires_at=expires_at,
        )
        self.credit_service.grant_paid_credits(
            db,
            user_id=order.user_id,
            plan_code=order.plan_code,
            plan_name=order.plan_name,
            source_order_no=order.out_trade_no,
            credit_total=plan.credit_total,
            expires_at=expires_at,
        )

    # 执行assert order rate limit相关逻辑。
    def _assert_order_rate_limit(
        self,
        db: Session,
        *,
        client_ip: str,
        user_id: str,
        browser_fingerprint: str,
    ) -> None:
        window_start = self._current_minute_window()
        ip_count = self.repository.increment_rate_bucket(
            db,
            scope="order_ip",
            bucket_key=self._hash_bucket(client_ip),
            window_start=window_start,
        )
        if ip_count > settings.zpay.rate_limit.order_ip_per_minute:
            raise HTTPException(status_code=429, detail="支付请求过于频繁，请稍后重试")
        identity_value = user_id or browser_fingerprint or client_ip
        identity_count = self.repository.increment_rate_bucket(
            db,
            scope="order_identity",
            bucket_key=self._hash_bucket(identity_value),
            window_start=window_start,
        )
        if identity_count > settings.zpay.rate_limit.order_identity_per_minute:
            raise HTTPException(status_code=429, detail="支付请求过于频繁，请稍后重试")

    # 执行assert notify rate limit相关逻辑。
    def _assert_notify_rate_limit(self, db: Session, *, client_ip: str) -> None:
        count = self.repository.increment_rate_bucket(
            db,
            scope="notify_ip",
            bucket_key=self._hash_bucket(client_ip),
            window_start=self._current_minute_window(),
        )
        if count > settings.zpay.rate_limit.notify_ip_per_minute:
            raise HTTPException(status_code=429, detail="支付通知过于频繁")

    # 执行current minute window相关逻辑。
    def _current_minute_window(self) -> datetime:
        now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        return now.replace(second=0, microsecond=0)

    # 执行hash bucket相关逻辑。
    def _hash_bucket(self, value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    # 执行resolve client ip相关逻辑。
    def _resolve_client_ip(self, request: Request) -> str:
        return request.client.host if request.client else ""

    # 执行resolve payment device相关逻辑。
    def _resolve_payment_device(self, request: Request) -> str:
        """按请求 UA 判断 ZPAY API 支付设备类型。"""
        user_agent = request.headers.get("user-agent", "").lower()
        if any(marker in user_agent for marker in ("mobile", "android", "iphone", "ipad")):
            return "mobile"
        return "pc"

    # 执行public api base url相关逻辑。
    def _public_api_base_url(self, request: Request) -> str:
        configured_url = settings.zpay.public_api_base_url.strip().rstrip("/")
        if configured_url:
            return configured_url
        return str(request.base_url).rstrip("/")
