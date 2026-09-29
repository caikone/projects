"""共享 pytest fixtures。

测试不访问真实 Go 后端；所有后端请求由 httpx.MockTransport 拦截。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

import httpx
import pytest

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.server import create_mcp


def _envelope(code: int = 200, message: str = "ok", data: Any = None) -> dict[str, Any]:
    """构造 Go 后端统一响应信封 {code,message,data,time}。"""
    return {"code": code, "message": message, "data": data, "time": "2026-09-16T00:00:00Z"}


def _pet(pet_id: str = "p1", **overrides: Any) -> dict[str, Any]:
    """构造一条满足 Pet 模型的最小宠物档案。"""
    base = {
        "id": pet_id,
        "name": "小黑",
        "species": "犬",
        "breed": "拉布拉多",
        "gender": "公",
        "ageMonths": 24,
        "color": "黑色",
        "chipNo": "CN-001",
        "ownerName": "张三",
        "ownerPhone": "13800000000",
        "ownerAddr": "北京市海淀区",
        "doctor": "李医生",
        "disease": "健康",
        "status": "已康复",
        "allergy": "",
        "note": "",
        "records": None,
        "charges": None,
        "totalCost": 0.0,
        "visitCount": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
    }
    base.update(overrides)
    return base


def _result(items: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    """构造 Go Result: {items,total,page,pageSize,totalPages,totalCost}。"""
    items = items if items is not None else [_pet()]
    total = kw.get("total", len(items))
    page = kw.get("page", 1)
    page_size = kw.get("pageSize", 20)
    total_pages = kw.get("totalPages", 1)
    total_cost = kw.get("totalCost", 0.0)
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
        "totalCost": total_cost,
    }


@pytest.fixture
def envelope() -> Callable[..., dict[str, Any]]:
    return _envelope


@pytest.fixture
def make_pet() -> Callable[..., dict[str, Any]]:
    return _pet


@pytest.fixture
def make_result() -> Callable[..., dict[str, Any]]:
    return _result


@pytest.fixture
def settings() -> Settings:
    """固定 Settings，避免依赖环境变量。"""
    return Settings(
        mcp_host="127.0.0.1",
        mcp_port=8000,
        mcp_endpoint_path="/mcp",
        pet_hospital_base_url="http://test-backend.example",
        pet_hospital_timeout=1.0,
        pet_hospital_retries=0,  # 测试关闭重试，便于断言
        log_level="WARNING",
    )


@pytest.fixture
def captured_request() -> dict[str, Any]:
    """记录最后一次后端请求的 url + params + headers，供参数转发断言使用。"""
    return {}


def make_mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.fixture
def mock_backend_factory():
    """返回工厂，按指定 handler 构造 PetHospitalClient + MCPServer。"""
    def _build(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        settings: Settings | None = None,
        retries: int = 0,
    ) -> tuple[PetHospitalClient, "Any"]:
        from pet_hospital_mcp.server import create_mcp

        s = settings or Settings(
            mcp_host="127.0.0.1",
            mcp_port=8000,
            mcp_endpoint_path="/mcp",
            pet_hospital_base_url="http://test-backend.example",
            pet_hospital_timeout=1.0,
            pet_hospital_retries=retries,
            log_level="WARNING",
        )
        transport = httpx.MockTransport(handler)
        client = PetHospitalClient(
            base_url=s.pet_hospital_base_url,
            timeout=s.pet_hospital_timeout,
            retries=s.pet_hospital_retries,
            transport=transport,
        )
        mcp = create_mcp(client=client, settings=s)
        return client, mcp

    return _build


@pytest.fixture
def jsonrpc_ok_handler():
    """默认后端处理器：返回成功 Result 含 1 条宠物。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = _envelope(data=_result())
        return httpx.Response(200, json=body)
    return handler


def parse_call_tool_result_text(content: Any) -> dict[str, Any]:
    """从 CallToolResult.content[0].text 解析 JSON。

    工具失败时 _raise_tool_error 抛 ToolError(payload_json)，SDK 把 JSON 字符串
    放进 content[0].text；工具成功时 content[0].text 也是 JSON。

    SDK 2.x 在 ToolError 路径会把消息包装为
    ``"Error executing tool <name>: <payload>"``；本函数自动定位到首个 ``{``
    起始处再解析，从而同时兼容「带前缀」与「纯 JSON」两种形式。
    """
    # content 可能是 list[TextContent]
    if isinstance(content, list) and content:
        text = getattr(content[0], "text", None) or content[0].get("text")
    elif isinstance(content, str):
        text = content
    else:
        text = None
    assert text is not None, f"无法从 content 提取 text: {content!r}"
    # 自动剥离 SDK 2.x 的 "Error executing tool <name>: " 前缀
    brace = text.find("{")
    if brace > 0:
        candidate = text[brace:]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    return json.loads(text)
