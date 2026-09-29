"""测试场景 7：无状态 Streamable HTTP 流程。

覆盖：
- `GET /health` 返回 200 + 必需字段（stateless=True、protocolVersion=2026-07-28、sdkVersion、mcpEndpoint、backend）；
- 通过 SDK `Client(streamable_http_client(...))` 经 HTTP 调用 list_tools / call_tool 成功；
- `client.protocol_version == "2026-07-28"`；
- 响应头不含 `mcp-session-id`（无状态：无 initialize、无 Mcp-Session-Id）；
- 教学场景已关闭 DNS-rebinding 保护：直接以任意 Host 访问不被 421。
"""

from __future__ import annotations

from typing import Any

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from pet_hospital_mcp.errors import BACKEND_API_ERROR
from pet_hospital_mcp.tools.list_pets import ListPetsOutput
from .conftest import _envelope, _pet, _result, parse_call_tool_result_text


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_returns_expected_payload(mock_backend_factory):
    """GET /health 返回 200 + 必需字段。"""
    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(data=_result()))

    _client, mcp = mock_backend_factory(backend)
    app = mcp.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        # 教学场景关闭 Host/Origin 校验
        transport_security=_make_teaching_security(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["service"] == "pet-hospital-mcp"
    assert body["mcpEndpoint"] == "/mcp"
    assert body["protocolVersion"] == "2026-07-28"
    assert body["sdkVersion"] == "2.0.0"
    assert body["backend"] == "http://test-backend.example"
    assert body["stateless"] is True


@pytest.mark.asyncio
async def test_health_endpoint_works_with_arbitrary_host(mock_backend_factory):
    """关闭 DNS-rebinding 后，任意 Host（含公网域名）都能命中 /health。"""
    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(data=_result()))

    _client, mcp = mock_backend_factory(backend)
    app = mcp.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=_make_teaching_security(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/health", headers={"Host": "pet.example.com"})

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 经 HTTP 的 MCP 协议流程
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stateless_http_tools_list_and_call(mock_backend_factory, make_pet):
    """经 Streamable HTTP：tools/list + tools/call 全程成功。"""
    pet = make_pet(id="p1", name="小黑")
    backend_calls = {"n": 0}

    def backend(_request: httpx.Request) -> httpx.Response:
        backend_calls["n"] += 1
        return httpx.Response(200, json=_envelope(data=_result([pet], total=1)))

    _client, mcp = mock_backend_factory(backend)
    app = mcp.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=_make_teaching_security(),
    )

    http_client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
    try:
        # httpx2.ASGITransport 不会触发 ASGI lifespan，而
        # mcp.session_manager 的后台任务组在 lifespan 里启动；
        # 这里手动 enter 它，使 /mcp 可被处理。
        async with mcp.session_manager.run():
            # SDK 文档：Client 接受任何 "async with ... as (read, write)" 的对象，
            # 即 streamable_http_client(...) 本身（一个异步上下文管理器），
            # 不是它 yield 出的 (read, write) 元组。
            async with Client(
                streamable_http_client("http://test/mcp", http_client=http_client)
            ) as c:
                assert c.protocol_version == "2026-07-28"

                listing = await c.list_tools()
                assert any(t.name == "list_pets" for t in listing.tools)

                result = await c.call_tool("list_pets", {"filters": {"species": "犬"}})
    finally:
        await http_client.aclose()

    assert result.is_error is False
    parsed = ListPetsOutput.model_validate(result.structured_content)
    assert parsed.items[0].id == "p1"
    # 后端被命中过
    assert backend_calls["n"] == 1


@pytest.mark.asyncio
async def test_stateless_http_no_session_id_in_response(mock_backend_factory):
    """无状态：响应头不包含 mcp-session-id。"""
    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(data=_result()))

    _client, mcp = mock_backend_factory(backend)
    app = mcp.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=_make_teaching_security(),
    )

    # httpx.ASGITransport 不会触发 ASGI lifespan，而 mcp.session_manager 的
    # 后台任务组在 lifespan 里启动；这里手动 enter 它，使 /mcp 可被处理。
    transport = httpx.ASGITransport(app=app)
    async with mcp.session_manager.run():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )

    # 无状态模式下：无论 RPC 是否成功，响应头都不应包含 mcp-session-id
    lower_headers = {k.lower() for k in resp.headers.keys()}
    assert "mcp-session-id" not in lower_headers, (
        f"无状态模式下响应不应包含 mcp-session-id，实际头部: {lower_headers}"
    )


@pytest.mark.asyncio
async def test_stateless_http_error_propagates(mock_backend_factory):
    """经 HTTP 调用，后端错误仍以统一错误结构返回（is_error=True）。"""
    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 500, "message": "boom", "data": None})

    _client, mcp = mock_backend_factory(backend)
    app = mcp.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=_make_teaching_security(),
    )

    http_client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
    try:
        async with mcp.session_manager.run():
            async with Client(
                streamable_http_client("http://test/mcp", http_client=http_client)
            ) as c:
                result = await c.call_tool("list_pets", {"filters": {}})
    finally:
        await http_client.aclose()

    assert result.is_error is True
    parsed = parse_call_tool_result_text(result.content)
    assert parsed["error"]["code"] == BACKEND_API_ERROR


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_teaching_security():
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(enable_dns_rebinding_protection=False)
