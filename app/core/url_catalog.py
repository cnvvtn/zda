# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\core\url_catalog.py。"""

from __future__ import annotations

from enum import Enum


# 定义PythonUrl。
class PythonUrl(str, Enum):
    """Python 后端统一 URL 枚举，集中收口接口路径与外部网关地址。"""

    ZPAY_GATEWAY = "https://zpayz.cn/"
    ZPAY_MAPI_PATH = "mapi.php"
    ZPAY_QUERY_API_PATH = "api.php"
    ZPAY_CALLBACK_BASE_URL = "https://example.com"
    ZPAY_PAYMENT_PREFIX = "/api/payments"
    ZPAY_ORDER_ROUTE = "/zpay/orders"
    ZPAY_NOTIFY_ROUTE = "/zpay/notify"
    ZPAY_NOTIFY_PATH = "/api/payments/zpay/notify"
    PAYMENT_MEMBERSHIP_ROUTE = "/memberships"
    PAYMENT_CREDIT_USAGE_ROUTE = "/credits/usage"
    PAYMENT_CREDIT_REDEEM_ROUTE = "/credits/redeem"
    PAYMENT_RESULT_PATH = "/payment-result.html"
    API_PREFIX = "/api/"
    ADMIN_API_PREFIX = "/api/admin"
    AUTH_API_PREFIX = "/api/auth"
    CHAT_API_PREFIX = "/api/chat"
    DYNAMIC_VIEW_API_PREFIX = "/api/dynamic-view"
    DYNAMIC_VIEW_KNOWLEDGE_API_PREFIX = "/api/dynamic-view/knowledge-view"
    WEBSITE_API_PREFIX = "/api/website"
    DYNAMIC_VIEW_HTML_TEMPLATE = "/api/dynamic-view/{archive_id}/html"
    DYNAMIC_VIEW_AUDIO_TEMPLATE = "/api/dynamic-view/{archive_id}/audio"
    DYNAMIC_VIEW_KNOWLEDGE_HTML_TEMPLATE = "/api/dynamic-view/knowledge-view/{archive_id}/html"
    DYNAMIC_VIEW_KNOWLEDGE_AUDIO_TEMPLATE = "/api/dynamic-view/knowledge-view/{archive_id}/audio"
    DYNAMIC_VIEW_KNOWLEDGE_SUBTITLE_AUDIO_TEMPLATE = "/api/dynamic-view/knowledge-view/{archive_id}/subtitle-audio"
    LEMONAPI_BASE_URL = "https://example.com/v1"

    # 执行join base相关逻辑。
    def join_base(self, path: "PythonUrl | str") -> str:
        """把当前枚举值作为 base URL，与路径安全拼接。"""
        normalized_path = path.value if isinstance(path, PythonUrl) else str(path)
        return f"{self.value.rstrip('/')}/{normalized_path.lstrip('/')}"

    # 执行format path相关逻辑。
    def format_path(self, **kwargs: object) -> str:
        """按枚举中的 URL 模板生成最终路径。"""
        return self.value.format(**kwargs)
