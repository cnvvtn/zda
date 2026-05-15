# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\payment\schemas.py。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


SUPPORTED_ZPAY_PAY_TYPES = {"alipay"}


# 定义ZpayOrderCreateRequest。
class ZpayOrderCreateRequest(BaseModel):
    """官网套餐支付创建订单请求。"""

    model_config = ConfigDict(populate_by_name=True)

    plan_code: str = Field(alias="planCode", min_length=1, max_length=32)
    pay_type: str = Field(alias="payType", min_length=1, max_length=16)
    user_id: str = Field(default="", alias="userId", max_length=64)
    browser_fingerprint: str = Field(default="", alias="browserFingerprint", max_length=128)
    page_url: str = Field(default="", alias="pageUrl", max_length=1000)

    # 执行validate plan code相关逻辑。
    @field_validator("plan_code")
    @classmethod
    def validate_plan_code(cls, value: str) -> str:
        """清洗套餐编码。"""
        return str(value or "").strip()

    # 执行validate pay type相关逻辑。
    @field_validator("pay_type")
    @classmethod
    def validate_pay_type(cls, value: str) -> str:
        """限制支付方式，当前仅开放支付宝。"""
        normalized_value = str(value or "").strip().lower()
        if normalized_value not in SUPPORTED_ZPAY_PAY_TYPES:
            raise ValueError("payType 当前仅支持 alipay")
        return normalized_value

    # 执行validate optional text相关逻辑。
    @field_validator("user_id", "browser_fingerprint", "page_url")
    @classmethod
    def validate_optional_text(cls, value: str) -> str:
        """清洗可选字符串。"""
        return str(value or "").strip()


# 定义ZpayOrderCreateResponse。
class ZpayOrderCreateResponse(BaseModel):
    """官网套餐支付创建订单返回。"""

    model_config = ConfigDict(populate_by_name=True)

    out_trade_no: str = Field(alias="outTradeNo")
    status: str
    pay_url: str = Field(default="", alias="payUrl")
    qrcode: str = ""
    img: str = ""
    trade_no: str = Field(default="", alias="tradeNo")
    zpay_order_id: str = Field(default="", alias="zpayOrderId")
    money: str
    plan_name: str = Field(alias="planName")


# 定义ZpayOrderStatusResponse。
class ZpayOrderStatusResponse(BaseModel):
    """官网支付订单状态返回。"""

    model_config = ConfigDict(populate_by_name=True)

    out_trade_no: str = Field(alias="outTradeNo")
    plan_code: str = Field(alias="planCode")
    plan_name: str = Field(alias="planName")
    money: str
    pay_type: str = Field(alias="payType")
    status: str
    paid_at: datetime | None = Field(default=None, alias="paidAt")


# 定义MembershipEntitlementResponse。
class MembershipEntitlementResponse(BaseModel):
    """会员权益查询返回。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    plan_code: str = Field(alias="planCode")
    plan_name: str = Field(alias="planName")
    credit_total: int = Field(alias="creditTotal")
    credit_remaining: int = Field(alias="creditRemaining")
    model_level: str = Field(alias="modelLevel")
    priority_level: int = Field(alias="priorityLevel")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    status: int


# 定义CreditRedeemRequest。
class CreditRedeemRequest(BaseModel):
    """官网兑换码兑换 Credit 请求。"""

    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(min_length=3, max_length=64)
    user_id: str = Field(alias="userId", min_length=1, max_length=64)

    # 执行validate code相关逻辑。
    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        """清洗兑换码，并限制只能使用大小写英文和数字。"""
        normalized_code = str(value or "").strip()
        if not normalized_code:
            raise ValueError("兑换码不能为空")
        if not normalized_code.isalnum() or not normalized_code.isascii():
            raise ValueError("兑换码仅允许大小写英文和数字")
        return normalized_code

    # 执行validate user id相关逻辑。
    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        """清洗用户 ID。"""
        normalized_user_id = str(value or "").strip()
        if not normalized_user_id:
            raise ValueError("请先登录后再兑换")
        return normalized_user_id


# 定义CreditRedeemResponse。
class CreditRedeemResponse(BaseModel):
    """官网兑换码兑换 Credit 返回。"""

    model_config = ConfigDict(populate_by_name=True)

    code: str
    credit_total: int = Field(alias="creditTotal")
    credit_remaining: int = Field(alias="creditRemaining")
    expires_at: datetime = Field(alias="expiresAt")


# 定义CreditUsageLogItem。
class CreditUsageLogItem(BaseModel):
    """用户 Credit 使用明细返回项。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    user_id: str = Field(alias="userId")
    ledger_id: int = Field(alias="ledgerId")
    usage_type: str = Field(alias="usageType")
    request_id: str = Field(alias="requestId")
    model_level: str = Field(alias="modelLevel")
    credit_amount: int = Field(alias="creditAmount")
    balance_before: int = Field(alias="balanceBefore")
    balance_after: int = Field(alias="balanceAfter")
    created_at: datetime = Field(alias="createdAt")


# 定义CreditLedgerLogItem。
class CreditLedgerLogItem(BaseModel):
    """用户 Credit 完整流水返回项。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_id: str = Field(alias="userId")
    direction: str
    event_type: str = Field(alias="eventType")
    title: str
    credit_amount: int = Field(alias="creditAmount")
    balance_before: int | None = Field(default=None, alias="balanceBefore")
    balance_after: int | None = Field(default=None, alias="balanceAfter")
    change_before: int = Field(alias="changeBefore")
    change_after: int = Field(alias="changeAfter")
    expiring_credit: int = Field(alias="expiringCredit")
    request_id: str = Field(default="", alias="requestId")
    model_level: str = Field(default="", alias="modelLevel")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    created_at: datetime = Field(alias="createdAt")
