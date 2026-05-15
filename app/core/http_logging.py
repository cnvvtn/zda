# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\core\http_logging.py。"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Message


logger = logging.getLogger(__name__)


# 执行log http exchange相关逻辑。
async def log_http_exchange(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """统一记录所有 HTTP 请求与响应，确保控制台能直接看到真实请求体和响应体。"""
    # 执行perf counter相关逻辑。
    started_at = time.perf_counter()
    # 执行body相关逻辑。
    request_body_bytes = await request.body()
    logged_request = Request(request.scope, _build_receive(request_body_bytes))
    # 执行info相关逻辑。
    logger.info(
        "HTTP Request | method=%s | path=%s | query=%s | body=%s",
        logged_request.method,
        logged_request.url.path,
        logged_request.url.query,
        # 执行decode body bytes相关逻辑。
        _decode_body_bytes(request_body_bytes),
    )
    try:
        # 执行call next相关逻辑。
        response = await call_next(logged_request)
    except Exception:
        # 执行exception相关逻辑。
        logger.exception(
            "HTTP Response | method=%s | path=%s | status=500 | durationMs=%s | body=%s",
            logged_request.method,
            logged_request.url.path,
            # 执行build duration ms相关逻辑。
            _build_duration_ms(started_at),
            "Internal Server Error",
        )
        raise
    return _build_logged_response(
        request=logged_request,
        response=response,
        started_at=started_at,
    )


# 执行build receive相关逻辑。
def _build_receive(request_body_bytes: bytes) -> Callable[[], Awaitable[Message]]:
    """把已经读取过的请求体重新封装成 receive，避免下游路由读不到 body。"""
    # 执行receive相关逻辑。
    async def receive() -> Message:
        """向 Starlette 复用缓存后的请求体，保证中间件记录后业务层还能继续读取。"""
        return {
            "type": "http.request",
            "body": request_body_bytes,
            "more_body": False,
        }

    return receive


# 执行build logged response相关逻辑。
def _build_logged_response(
    *,
    request: Request,
    response: Response,
    started_at: float,
) -> Response:
    """根据响应类型选择普通响应或流式响应的日志包装方式。"""
    if _is_streaming_response(response):
        return _build_logged_streaming_response(
            request=request,
            response=response,
            started_at=started_at,
        )
    # 执行read response body bytes相关逻辑。
    response_body_bytes = _read_response_body_bytes(response)
    # 执行info相关逻辑。
    logger.info(
        "HTTP Response | method=%s | path=%s | status=%s | durationMs=%s | body=%s",
        request.method,
        request.url.path,
        response.status_code,
        # 执行build duration ms相关逻辑。
        _build_duration_ms(started_at),
        # 执行decode body bytes相关逻辑。
        _decode_body_bytes(response_body_bytes),
    )
    return response


# 执行build logged streaming response相关逻辑。
def _build_logged_streaming_response(
    *,
    request: Request,
    response: Response,
    started_at: float,
) -> StreamingResponse:
    """对流式响应做透传包装，并在流结束后把完整响应体打印到控制台。"""
    # 执行getattr相关逻辑。
    original_iterator = getattr(response, "body_iterator")

    # 执行logged body iterator相关逻辑。
    async def logged_body_iterator() -> AsyncIterator[bytes]:
        """边向客户端透传流式分片，边在内存里累计响应体供控制台输出。"""
        response_chunks: list[bytes] = []
        try:
            async for chunk in original_iterator:
                # 执行normalize response chunk相关逻辑。
                chunk_bytes = _normalize_response_chunk(chunk)
                # 执行append相关逻辑。
                response_chunks.append(chunk_bytes)
                yield chunk_bytes
        finally:
            response_body_bytes = b"".join(response_chunks)
            # 执行info相关逻辑。
            logger.info(
                "HTTP Response | method=%s | path=%s | status=%s | durationMs=%s | body=%s",
                request.method,
                request.url.path,
                response.status_code,
                # 执行build duration ms相关逻辑。
                _build_duration_ms(started_at),
                # 执行summarize stream body相关逻辑。
                _summarize_stream_body(response_chunks, response_body_bytes),
            )

    return StreamingResponse(
        # 执行logged body iterator相关逻辑。
        logged_body_iterator(),
        status_code=response.status_code,
        # 执行build response headers相关逻辑。
        headers=_build_response_headers(response),
        media_type=response.media_type,
        background=response.background,
    )


# 执行is streaming response相关逻辑。
def _is_streaming_response(response: Response) -> bool:
    """兼容 Starlette 内部包装响应类型，只要存在 body_iterator 就视为流式响应。"""
    return getattr(response, "body_iterator", None) is not None


# 执行read response body bytes相关逻辑。
def _read_response_body_bytes(response: Response) -> bytes:
    """统一读取普通响应体字节，兼容 Response.body 为空的兜底场景。"""
    # 执行getattr相关逻辑。
    response_body = getattr(response, "body", b"")
    if isinstance(response_body, bytes):
        return response_body
    if isinstance(response_body, str):
        return response_body.encode("utf-8")
    return str(response_body).encode("utf-8")


# 执行build response headers相关逻辑。
def _build_response_headers(response: Response) -> dict[str, str]:
    """复制响应头时剔除由新响应重新计算的长度头，避免内容长度失真。"""
    filtered_headers: dict[str, str] = {}
    for header_key, header_value in response.headers.items():
        if header_key.lower() == "content-length":
            continue
        filtered_headers[header_key] = header_value
    return filtered_headers


# 执行normalize response chunk相关逻辑。
def _normalize_response_chunk(chunk: object) -> bytes:
    """把流式响应分片统一转成字节，避免不同响应类型混入字符串对象。"""
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, str):
        return chunk.encode("utf-8")
    return str(chunk).encode("utf-8")


# 执行decode body bytes相关逻辑。
def _decode_body_bytes(body_bytes: bytes) -> str:
    """把请求体或响应体统一解码成可打印文本，空体时返回固定占位。"""
    if not body_bytes:
        return "null"
    return body_bytes.decode("utf-8", errors="replace")


# 执行summarize stream body相关逻辑。
def _summarize_stream_body(response_chunks: list[bytes], response_body_bytes: bytes) -> str:
    """流式响应只打印摘要，避免 NDJSON 全量刷屏污染控制台。"""
    if not response_body_bytes:
        return "stream<empty>"
    # 执行decode body bytes相关逻辑。
    response_text = _decode_body_bytes(response_body_bytes)
    # 执行count相关逻辑。
    line_count = response_text.count("\n")
    preview_text = response_text[:160].replace("\r", " ").replace("\n", " ")
    if len(response_text) > 160:
        preview_text += "...[truncated]"
    return (
        f"stream<chunks={len(response_chunks)}, bytes={len(response_body_bytes)}, "
        f"lines={line_count}, preview={preview_text}>"
    )


# 执行build duration ms相关逻辑。
def _build_duration_ms(started_at: float) -> int:
    """把请求耗时统一折算成毫秒整数，方便控制台快速比对慢请求。"""
    return int((time.perf_counter() - started_at) * 1000)
