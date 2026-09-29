"""测试场景 3：后端 4xx / 5xx 错误归一化。

后端 HTTP 4xx/5xx 必须返回 is_error=True 且统一错误结构
{error:{code:BACKEND_API_ERROR, message, details:{status_code,...}}}。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp import Client

from pet_hospital_mcp.errors import BACKEND_API_ERROR
from .conftest import _envelope, parse_call_tool_result_text


@pytest.mark.parametrize(
    "status, body",
    [
        (400, {"code": 400, "message": "请求参数错误", "data": None}),
        (401, {"code": 401, "message": "未授权", "data": None}),
        (403, {"code": 403, "message": "禁止访问", "data": None}),
        (404, {"code": 404, "message": "资源不存在", "data": None}),
        (422, {"code": 422, "message": "参数校验失败", "data": None}),
        (500, {"code": 500, "message": "内部错误", "data": None}),
        (502, {"code": 502, "message": "网关错误", "data": None}),
        (503, {"code": 503, "message": "服务不可用", "data": None}),
        (504, {"code": 504, "message": "网关超时", "data": None}),
    ],
)
@pytest.mark.asyncio
async def test_backend_http_error_normalized(
    mock_backend_factory, status: int, body: dict[str, Any]
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    assert result.is_error is True, f"HTTP {status} 应触发 is_error=True"
    parsed = parse_call_tool_result_text(result.content)
    err = parsed["error"]
    assert err["code"] == BACKEND_API_ERROR
    assert str(status) in err["message"], f"错误消息应包含状态码 {status}"
    assert err["details"]["status_code"] == status
    # body 摘要应被记录
    assert "body" in err["details"]


@pytest.mark.asyncio
async def test_backend_business_code_non_200_normalized(mock_backend_factory):
    """HTTP 200 但信封 code != 200：仍归一化为 BACKEND_API_ERROR。"""
    body = {"code": 5001, "message": "数据库查询失败", "data": None, "time": "x"}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    assert result.is_error is True
    parsed = parse_call_tool_result_text(result.content)
    err = parsed["error"]
    assert err["code"] == BACKEND_API_ERROR
    assert err["message"] == "数据库查询失败"
    assert err["details"]["code"] == 5001


@pytest.mark.asyncio
async def test_backend_error_message_when_envelope_missing_message(mock_backend_factory):
    """HTTP 200，code != 200，但 message 字段缺失：回退到默认消息。"""
    body = {"code": 999, "data": None}  # 没有 message

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_API_ERROR
    assert "后端业务错误" in parsed["error"]["message"]
