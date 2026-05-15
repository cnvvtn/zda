# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\clients\http_request_policy.py。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


HTTP_MAX_RETRIES = 3
HTTP_RETRY_BACKOFF_SECONDS = 0.5
HTTP_CLOSE_HEADERS = {"Connection": "close"}
HTTP_RETRY_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
HTTPX_NO_KEEPALIVE_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=0,
    keepalive_expiry=0.0,
)


# 定义RetryAsyncTransport。
class RetryAsyncTransport(httpx.AsyncBaseTransport):
    """为 httpx.AsyncClient 提供 3 次指数退避重试的 transport 包装层。"""

    # 执行init相关逻辑。
    def __init__(self, *, proxy_url: str | None) -> None:
        """初始化禁用 keep-alive 的底层异步 HTTP transport。"""
        self._transport = httpx.AsyncHTTPTransport(
            limits=HTTPX_NO_KEEPALIVE_LIMITS,
            proxy=proxy_url,
            retries=0,
            trust_env=False,
        )

    # 执行handle async request相关逻辑。
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """执行单次异步请求，并对临时网络错误和临时状态码做指数退避重试。"""
        request_content = await request.aread()
        last_error: Exception | None = None
        for retry_index in range(HTTP_MAX_RETRIES + 1):
            retry_request = httpx.Request(
                request.method,
                request.url,
                headers=request.headers,
                content=request_content,
                extensions=request.extensions,
            )
            try:
                response = await self._transport.handle_async_request(retry_request)
            except httpx.RequestError as error:
                if retry_index >= HTTP_MAX_RETRIES:
                    raise
                last_error = error
                await asyncio.sleep(HTTP_RETRY_BACKOFF_SECONDS * (2**retry_index))
                continue
            if response.status_code not in HTTP_RETRY_STATUS_CODES:
                return response
            if retry_index >= HTTP_MAX_RETRIES:
                return response
            await response.aclose()
            await asyncio.sleep(HTTP_RETRY_BACKOFF_SECONDS * (2**retry_index))
        raise last_error if last_error is not None else RuntimeError("HTTP request failed")

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """关闭底层异步 HTTP transport。"""
        await self._transport.aclose()


# 执行build httpx async client kwargs相关逻辑。
def build_httpx_async_client_kwargs(
    *,
    timeout: Any,
    proxy_url: str | None,
) -> dict[str, Any]:
    """构造禁用连接复用的 httpx.AsyncClient 参数。"""
    client_kwargs: dict[str, Any] = {
        "headers": HTTP_CLOSE_HEADERS,
        "timeout": timeout,
        "transport": RetryAsyncTransport(proxy_url=proxy_url),
        "trust_env": False,
    }
    return client_kwargs
