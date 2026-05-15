# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\core\settings.py。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.core.url_catalog import PythonUrl


DEFAULT_LLM_TEMPERATURE = 0.2
DEVELOPMENT_PROXY_ENV_NAME = "DEVELOPMENT_PROXY_URL"


# 定义ProviderProfile。
class ProviderProfile(BaseModel):
    """模型网关的 provider 控制项，统一承接诸如 only 之类的路由约束。"""

    only: list[str] = Field(default_factory=list)


# 定义ReasoningProfile。
class ReasoningProfile(BaseModel):
    """模型推理强度配置，统一承接 reasoning.effort。"""

    effort: str = "high"


# 定义ModelProfile。
class ModelProfile(BaseModel):
    """单个远端模型配置，供自定义 LLM 客户端复用。"""

    # router_type 显式声明模型请求路径，目前支持 openai / gemini。
    router_type: str = "openai"
    base_url: str | None = None
    profile_key: str
    api_key: str = ""
    model: str
    stream: bool = False
    # 每个模型都允许单独配置 temperature；未配置时统一回退到默认值 0.2。
    temperature: float | None = None
    # 每个模型都允许单独配置 top_p；未配置时表示不主动下发该参数。
    top_p: float | None = None
    # 每个模型都允许单独配置 top_k；未配置时表示不主动下发该参数。
    top_k: int | None = None
    # reasoning.effort 统一做成正式配置项，默认 high。
    reasoning: ReasoningProfile = Field(default_factory=ReasoningProfile)
    # 是否启用深度思考模式，统一映射到不同供应商的 thinking 配置。
    enable_deepthinking: bool = False
    # timeout 表示首 token 超时，total_timeout 表示整次请求总超时；未配置时表示不上限。
    timeout: int | None = None
    total_timeout: int | None = None
    max_tokens: int | None = None
    # provider.only 等网关级路由限制统一在这里显式配置，避免散落到 extra_body。
    provider: ProviderProfile = Field(default_factory=ProviderProfile)
    # 透传给兼容网关的额外请求体参数，例如显式关闭 Qwen 的 thinking 模式。
    extra_body: dict[str, Any] = Field(default_factory=dict)

    # 执行api keys相关逻辑。
    @property
    def api_keys(self) -> list[str]:
        """只使用数据库直传密钥。"""
        return _split_api_keys(self.api_key)

    # 执行resolved temperature相关逻辑。
    @property
    def resolved_temperature(self) -> float:
        """统一返回当前模型最终生效的 temperature。"""
        if self.temperature is None:
            return DEFAULT_LLM_TEMPERATURE
        return self.temperature


# 执行split api keys相关逻辑。
def _split_api_keys(raw_value: str) -> list[str]:
    """把逗号或换行分隔的 API Key 清洗成有序且去重的列表。"""
    keys: list[str] = []
    seen_keys: set[str] = set()
    for item in str(raw_value or "").replace("\n", ",").split(","):
        # 执行strip相关逻辑。
        api_key = item.strip()
        if not api_key or api_key in seen_keys:
            continue
        seen_keys.add(api_key)
        keys.append(api_key)
    return keys


# 定义RouterFailoverProfile。
class RouterFailoverProfile(BaseModel):
    """多路由模型配置，约定按 router1 -> router2 -> router3 顺序失败切换。"""

    router1: ModelProfile
    router2: ModelProfile | None = None
    router3: ModelProfile | None = None

    # 执行list profiles相关逻辑。
    def list_profiles(self) -> list[ModelProfile]:
        """按优先级返回可执行的路由配置列表。"""
        profiles = [self.router1]
        if self.router2 is not None:
            # 执行append相关逻辑。
            profiles.append(self.router2)
        if self.router3 is not None:
            # 执行append相关逻辑。
            profiles.append(self.router3)
        return profiles


# 定义DynamicViewNode2StepProfile。
class DynamicViewNode2StepProfile(BaseModel):
    """动态视图 node2 的双阶段配置，约定 step1/step2 共享同一条会话历史。"""

    step1: ModelProfile
    step2: ModelProfile


# 定义DynamicViewNode2RouterProfile。
class DynamicViewNode2RouterProfile(BaseModel):
    """动态视图 node2 的多路由配置，每条路由内部都允许声明 step1/step2。"""

    router1: DynamicViewNode2StepProfile | ModelProfile
    router2: DynamicViewNode2StepProfile | ModelProfile

    # 执行list profiles相关逻辑。
    def list_profiles(self) -> list[DynamicViewNode2StepProfile | ModelProfile]:
        """按优先级返回 node2 可执行的路由配置列表。"""
        return [
            self.router1,
            self.router2,
        ]


# 定义AppConfig。
class AppConfig(BaseModel):
    """Python 服务自身的固定运行配置。"""

    host: str
    port: int
    database_url: str
    environment: str = "production"
    trusted_proxy_ips: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])

    # 执行is development相关逻辑。
    @property
    def is_development(self) -> bool:
        """判断当前运行环境是否为开发环境。"""
        return self.environment.strip().lower() == "development"


# 定义MqttConfig。
class MqttConfig(BaseModel):
    """Python 直推 Flutter 时使用的 MQTT broker 配置。"""

    host: str
    port: int
    username: str
    password: str
    topic_prefix: str
    topic_secret: str
    client_id_prefix: str


# 定义UvicornConfig。
class UvicornConfig(BaseModel):
    """Uvicorn 进程与连接并发配置。"""

    workers: int = Field(default=4, ge=1)
    backlog: int = Field(default=2048, ge=1)
    limit_concurrency: int | None = Field(default=1000, ge=1)
    timeout_keep_alive: int = Field(default=15, ge=1)
    max_requests: int | None = Field(default=10000, ge=1)


# 定义DynamicViewScheduleConfig。
class DynamicViewScheduleConfig(BaseModel):
    """定时触发游戏动态视图生成链路时使用的固定调度配置。"""

    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=1)
    scene_count_min: int = Field(default=8, ge=6, le=12)


# 定义ZpayPlanConfig。
class ZpayPlanConfig(BaseModel):
    """ZPAY 支付套餐配置，金额和权益只以后端配置为准。"""

    name: str = Field(min_length=1, max_length=128)
    money: str = Field(pattern=r"^\d+(\.\d{1,2})?$")
    days: int = Field(default=30, ge=1, le=3660)
    credit_total: int = Field(default=0, ge=0)
    model_level: str = Field(default="basic", max_length=32)
    priority_level: int = Field(default=0, ge=0, le=100)


# 定义CreditBillingConfig。
class CreditBillingConfig(BaseModel):
    """Credit 计费配置，统一定义赠送额度和不同模型的消耗价格。"""

    free_daily_credits: int = Field(default=50, ge=0)
    model_costs: dict[str, int] = Field(
        default_factory=lambda: {
            "experience": 10,
            "basic": 20,
            "advanced": 50,
            "top": 100,
        }
    )


# 定义ZpayRateLimitConfig。
class ZpayRateLimitConfig(BaseModel):
    """支付接口限流配置，按 IP、用户和浏览器指纹做多维约束。"""

    order_ip_per_minute: int = Field(default=10, ge=1)
    order_identity_per_minute: int = Field(default=3, ge=1)
    notify_ip_per_minute: int = Field(default=240, ge=1)


# 定义ZpayConfig。
class ZpayConfig(BaseModel):
    """ZPAY 网关与商户配置。"""

    enabled: bool = True
    pid: str = Field(min_length=1, max_length=64)
    gateway: str = Field(default=PythonUrl.ZPAY_GATEWAY.value, min_length=1)
    public_api_base_url: str = Field(default=PythonUrl.ZPAY_CALLBACK_BASE_URL.value, max_length=500)
    notify_lock_timeout_seconds: int = Field(default=5, ge=1, le=30)
    order_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    plan_catalog: dict[str, ZpayPlanConfig] = Field(default_factory=dict)
    rate_limit: ZpayRateLimitConfig = Field(default_factory=ZpayRateLimitConfig)


# 定义EmailAuthRateLimitConfig。
class EmailAuthRateLimitConfig(BaseModel):
    """邮箱验证码认证限流配置。"""

    send_ip_per_minute: int = Field(default=5, ge=1)
    send_email_per_hour: int = Field(default=6, ge=1)
    verify_email_per_minute: int = Field(default=5, ge=1)


# 定义EmailAuthConfig。
class EmailAuthConfig(BaseModel):
    """邮箱验证码登录/注册配置。"""

    enabled: bool = True
    smtp_host: str = Field(default="smtp.exmail.qq.com", min_length=1, max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_ssl: bool = True
    username: str = Field(default="noreply@example.com", min_length=1, max_length=255)
    from_email: str = Field(default="noreply@example.com", min_length=1, max_length=255)
    from_name: str = Field(default="知搭 ZDA", max_length=64)
    code_ttl_minutes: int = Field(default=10, ge=1, le=60)
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    rate_limit: EmailAuthRateLimitConfig = Field(default_factory=EmailAuthRateLimitConfig)


# 定义LLMConfig。
class LLMConfig(BaseModel):
    """汇总所有 LLM 业务模块的配置。"""

    task_flow_version: int = Field(ge=1, le=2)

    # 执行resolve task flow version相关逻辑。
    def resolve_task_flow_version(self) -> int:
        """返回当前生效的动态视图任务流程版本号。"""
        return self.task_flow_version


# 定义Settings。
class Settings(BaseModel):
    """`config.yml` 解析后的类型化配置视图。"""

    app: AppConfig
    mqtt: MqttConfig
    uvicorn: UvicornConfig = Field(default_factory=UvicornConfig)
    dynamic_view_schedule: DynamicViewScheduleConfig
    zpay: ZpayConfig
    credit_billing: CreditBillingConfig = Field(default_factory=CreditBillingConfig)
    email_auth: EmailAuthConfig
    llm: LLMConfig


# 执行load settings相关逻辑。
def load_settings() -> Settings:
    """解析 `config.yml`。"""
    config_path = Path(__file__).resolve().parent.parent / "config.yml"
    with config_path.open("r", encoding="utf-8") as file:
        data: dict[str, Any] = yaml.safe_load(file)
    return Settings.model_validate(data)


settings = load_settings()


# 执行resolve development proxy url相关逻辑。
def resolve_development_proxy_url() -> str | None:
    """开发环境仅在显式配置代理地址时返回代理，否则直接连上游。"""
    if not settings.app.is_development:
        return None
    # 执行getenv相关逻辑。
    proxy_url = os.getenv(DEVELOPMENT_PROXY_ENV_NAME, "").strip()
    if not proxy_url:
        return None
    return proxy_url
