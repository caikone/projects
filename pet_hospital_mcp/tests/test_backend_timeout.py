"""测试场景 4：超时与连接异常归一化。

httpx.TimeoutException → BACKEND_TIMEOUT；
httpx.ConnectError / NetworkError / TransportError → BACKEND_UNAVAILABLE。
"""

from __future__ import annotations

import httpx
import pytest
from mcp import Client

from pet_hospital_mcp.errors import BACKEND_TIMEOUT, BACKEND_UNAVAILABLE
from .conftest import parse_call_tool_result_text


@pytest.mark.asyncio
async def test_backend_timeout_normalized(mock_backend_factory):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("模拟后端读超时", request=_request)

    client, mcp = mock_backend_factory(handler, retries=0)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    assert result.is_error is True
    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_TIMEOUT
    assert "超时" in parsed["error"]["message"]
    assert "url" in parsed["error"]["details"]


@pytest.mark.asyncio
async def test_backend_timeout_retries_then_fails(mock_backend_factory):
    """retries=2 时应总共发起 3 次请求，每次都超时，最终归一化。"""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("连接超时", request=_request)

    client, mcp = mock_backend_factory(handler, retries=2)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    assert calls["n"] == 3, f"期望重试 3 次（retries=2），实际 {calls['n']}"
    assert result.is_error is True
    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_TIMEOUT


@pytest.mark.asyncio
async def test_backend_connect_error_normalized(mock_backend_factory):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连接被拒绝", request=_request)

    client, mcp = mock_backend_factory(handler, retries=0)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_UNAVAILABLE
    assert "连接" in parsed["error"]["message"]


@pytest.mark.asyncio
async def test_backend_network_error_normalized(mock_backend_factory):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.NetworkError("网络中断", request=_request)

    client, mcp = mock_backend_factory(handler, retries=0)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_UNAVAILABLE


@pytest.mark.asyncio
async def test_backend_read_error_normalized(mock_backend_factory):
    """httpx.TransportError 子类之一：ReadError。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("读取响应失败", request=_request)

    client, mcp = mock_backend_factory(handler, retries=0)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_UNAVAILABLE


@pytest.mark.asyncio
async def test_backend_retries_recovers(mock_backend_factory):
    """前 N-1 次失败、第 N 次成功时不应触发错误（重试机制有效）。"""
    state = {"attempt": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        state["attempt"] += 1
        if state["attempt"] < 3:
            raise httpx.ConnectTimeout("临时超时", request=_request)
        # 第 3 次返回成功
        from .conftest import _envelope, _result
        return httpx.Response(200, json=_envelope(data=_result()))

    client, mcp = mock_backend_factory(handler, retries=2)
    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    assert state["attempt"] == 3
    assert result.is_error is False
