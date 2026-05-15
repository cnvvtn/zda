# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\api\response.py。"""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder


HTTP_OK_CODE = 200
HTTP_ERROR_CODE = 500


# 执行to response data相关逻辑。
def to_response_data(data: Any) -> Any:
    """把 Pydantic、datetime、列表等对象转成可 JSON 序列化的数据。"""
    return jsonable_encoder(data, by_alias=True, exclude_none=False)


# 执行ajax success相关逻辑。
def ajax_success(data: Any = None, msg: str = "操作成功") -> dict[str, Any]:
    """返回 HoopGo AjaxResult 成功结构。"""
    result: dict[str, Any] = {"code": HTTP_OK_CODE, "msg": msg}
    if data is not None:
        result["data"] = to_response_data(data)
    return result


# 执行ajax error相关逻辑。
def ajax_error(msg: str = "操作失敗", code: int = HTTP_ERROR_CODE) -> dict[str, Any]:
    """返回 HoopGo AjaxResult 错误结构。"""
    return {"code": code, "msg": msg}


# 执行table data相关逻辑。
def table_data(rows: list[Any], total: int | None = None, msg: str = "操作成功") -> dict[str, Any]:
    """返回 HoopGo TableDataInfo 结构。"""
    encoded_rows = to_response_data(rows)
    return {
        "total": len(encoded_rows) if total is None else total,
        "rows": encoded_rows,
        "code": HTTP_OK_CODE,
        "msg": msg,
    }
