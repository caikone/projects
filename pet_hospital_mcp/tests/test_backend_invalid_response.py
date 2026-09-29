"""测试场景 5：后端非法 JSON / 数据模型不符。

- 非 JSON 文本（HTML 错误页等）→ BACKEND_INVALID_RESPONSE；
- JSON 但非对象（数组、字符串）→ BACKEND_INVALID_RESPONSE；
- 信封缺 data 字段（data=null）→ BACKEND_INVALID_RESPONSE；
- 信封 data 字段非对象 → BACKEND_INVALID_RESPONSE；
- 信封结构正确但 Pet 模型字段类型不符 → BACKEND_INVALID_RESPONSE
  （工具层捕获 pydantic.ValidationError 后归一化，不应回退到 INTERNAL_ERROR）。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp import Client

from pet_hospital_mcp.errors import BACKEND_INVALID_RESPONSE
from .conftest import _envelope, _pet, _result, parse_call_tool_result_text


@pytest.mark.asyncio
async def test_backend_non_json_body_normalized(mock_backend_factory):
    """后端返回 HTML 错误页（非 JSON）→ BACKEND_INVALID_RESPONSE。"""
    body = "<html><body>502 Bad Gateway</body></html>"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/html"},
        )

    client, mcp = mock_backend_factory(handler, retries=0)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_INVALID_RESPONSE
    assert "非 JSON" in parsed["error"]["message"] or "JSON" in parsed["error"]["message"]
    # body 摘要应被截断并记录
    assert "body" in parsed["error"]["details"]


@pytest.mark.asyncio
async def test_backend_json_array_normalized(mock_backend_factory):
    """后端返回 JSON 数组（不是对象信封）→ BACKEND_INVALID_RESPONSE。"""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_backend_json_string_normalized(mock_backend_factory):
    """后端返回 JSON 字符串 → BACKEND_INVALID_RESPONSE。"""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="hello")

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_backend_data_null_normalized(mock_backend_factory):
    """信封 code=200 但 data=null → BACKEND_INVALID_RESPONSE。"""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "message": "ok", "data": None})

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_INVALID_RESPONSE
    assert "data" in parsed["error"]["message"].lower() or "对象" in parsed["error"]["message"]


@pytest.mark.asyncio
async def test_backend_data_array_normalized(mock_backend_factory):
    """信封 data 是数组（应为对象）→ BACKEND_INVALID_RESPONSE。"""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 200, "message": "ok", "data": [1, 2, 3]},
        )

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_backend_schema_mismatch_normalized(mock_backend_factory, make_pet):
    """信封结构合法但 Pet.items 中字段类型与 Pet 模型不符 → BACKEND_INVALID_RESPONSE。

    例如 visitCount 必须是 int，传入字符串；ageMonths 传字符串等。
    工具层应捕获 pydantic.ValidationError 并归一化为
    BACKEND_INVALID_RESPONSE，而不是 INTERNAL_ERROR。
    """
    bad_pet = make_pet()
    bad_pet["visitCount"] = "not-an-int"  # type: ignore[assignment]
    bad_pet["ageMonths"] = "twenty"  # type: ignore[assignment]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(data=_result([bad_pet])))

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    assert result.is_error is True
    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_INVALID_RESPONSE, (
        "后端响应 schema 不匹配应归一化为 BACKEND_INVALID_RESPONSE，"
        f"实际: {parsed['error']['code']}"
    )
    assert "validation_errors" in parsed["error"]["details"]


@pytest.mark.asyncio
async def test_backend_missing_required_result_field(mock_backend_factory):
    """信封 data 缺少 ListPetsOutput 必填字段 total → BACKEND_INVALID_RESPONSE。"""
    incomplete_data = {"items": [], "page": 1, "pageSize": 20}  # 缺 total/totalPages/totalCost

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(data=incomplete_data))

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_INVALID_RESPONSE
