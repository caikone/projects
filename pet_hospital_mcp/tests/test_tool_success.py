"""测试场景 1：正常调用 + 参数转发验证。

覆盖：
- 工具成功返回结构化结果（items/total/page/pageSize/totalPages/totalCost）；
- 调用者提供的过滤参数被原样转发到 Go 后端 `GET /api/v1/pets`；
- 未提供字段不转发（避免向后端泄漏默认值）。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp import Client

from pet_hospital_mcp.tools.list_pets import ListPetsOutput
from .conftest import _envelope, _result


@pytest.mark.asyncio
async def test_list_pets_success_returns_full_result(mock_backend_factory, make_pet):
    """成功路径：结构化输出字段齐全。"""
    pet = make_pet(id="p1", name="小黑", totalCost=128.5)
    handler = lambda req: httpx.Response(200, json=_envelope(data=_result([pet], total=1)))
    client, mcp = mock_backend_factory(handler)

    async with Client(mcp) as c:
        result = await c.call_tool("list_pets", {"filters": {}})

    assert result.is_error is False
    parsed = ListPetsOutput.model_validate(result.structured_content)
    assert parsed.total == 1
    assert parsed.page == 1
    assert parsed.pageSize == 20
    assert parsed.totalPages == 1
    assert parsed.totalCost == 0.0  # result() 默认 totalCost=0.0
    assert parsed.items[0].id == "p1"
    assert parsed.items[0].name == "小黑"


@pytest.mark.asyncio
async def test_list_pets_forwards_filters_to_backend(mock_backend_factory):
    """调用者提供的过滤参数必须原样转发；未提供字段不应出现。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # 解析 query 参数
        q = request.url.params
        captured["params"] = {k: v for k, v in q.items()}
        captured["path"] = request.url.path
        return httpx.Response(200, json=_envelope(data=_result()))

    client, mcp = mock_backend_factory(handler)

    filters = {
        "q": "咳嗽",
        "species": "犬",
        "status": "就诊中",
        "doctor": "李医生",
        "min": 100.0,
        "max": 1000.0,
        "sortBy": "totalCost",
        "order": "desc",
        "page": 2,
        "pageSize": 15,
    }
    async with Client(mcp) as c:
        await c.call_tool("list_pets", {"filters": filters})

    assert captured["path"] == "/api/v1/pets"
    forwarded = captured["params"]
    for k, v in filters.items():
        assert k in forwarded, f"期望 {k} 被转发，实际转发: {list(forwarded.keys())}"
        assert forwarded[k] == str(v), f"{k}: 期望 {v!r}, 实际 {forwarded[k]!r}"

    # 未提供字段不应转发
    assert "name" not in forwarded
    assert "ownerName" not in forwarded
    assert "ownerPhone" not in forwarded
    assert "disease" not in forwarded


@pytest.mark.asyncio
async def test_list_pets_empty_filters_sends_only_defaults(mock_backend_factory):
    """空 filters 仍发请求，仅带默认业务查询参数（不附加 MCP 默认值）。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_envelope(data=_result()))

    client, mcp = mock_backend_factory(handler)
    async with Client(mcp) as c:
        await c.call_tool("list_pets", {"filters": {}})

    # 空 filters 不应附加任何业务参数
    assert captured["params"] == {} or all(k not in captured["params"] for k in [
        "q", "name", "species", "status", "doctor", "disease",
        "min", "max", "sortBy", "order", "page", "pageSize",
    ])
