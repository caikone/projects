"""MCP 服务装配。

- 使用 SDK 2.x 的 MCPServer（不使用、不导入 mcp.server.fastmcp.FastMCP）
- 无状态 Streamable HTTP（stateless_http=True，json_response=True）
- 不实现旧协议的 initialize / Mcp-Session-Id / 会话存储
- 提供 /health 健康检查端点（@mcp.custom_route，不经 MCP）
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import Settings, load_settings
from .rest_client import PetHospitalClient
from .tools import register_all

# 教学场景：不实现认证、权限、CORS 或 Origin 校验，
# 关闭 DNS-rebinding 保护（Host/Origin 头校验），仅在 127.0.0.1 监听。
_TEACHING_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)

# 协议版本与 SDK 版本（来自 mcp 包，避免硬编码猜测）
try:
    from mcp.types.version import LATEST_PROTOCOL_VERSION as _PROTOCOL_VERSION
except Exception:  # pragma: no cover - 极端兜底
    _PROTOCOL_VERSION = "2026-07-28"

try:
    from importlib.metadata import version as _pkg_version

    _SDK_VERSION = _pkg_version("mcp")
except Exception:  # pragma: no cover
    _SDK_VERSION = "2.0.0"

SERVER_NAME = "pet-hospital-mcp"
SERVER_INSTRUCTIONS = (
    "宠物医院 MCP 服务。将 Go 宠物医院 REST API 暴露给 AI Agent。"
    "当前阶段提供工具：list_pets（查询宠物档案列表：过滤+排序+分页）。"
    "无状态 Streamable HTTP，协议版本 2026-07-28。"
)


def create_mcp(
    client: PetHospitalClient | None = None,
    settings: Settings | None = None,
) -> MCPServer:
    """构造一个 MCPServer 实例，注册 list_pets 工具与 /health 路由。

    client 为空时，按 settings 构造指向真实 Go 后端的 PetHospitalClient；
    测试时可传入注入了 httpx.MockTransport 的 client。
    """
    settings = settings or load_settings()
    if client is None:
        client = PetHospitalClient(
            base_url=settings.pet_hospital_base_url,
            timeout=settings.pet_hospital_timeout,
            retries=settings.pet_hospital_retries,
        )

    mcp = MCPServer(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_all(mcp, client)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "service": SERVER_NAME,
                "mcpEndpoint": settings.mcp_endpoint_path,
                "protocolVersion": _PROTOCOL_VERSION,
                "sdkVersion": _SDK_VERSION,
                "backend": settings.pet_hospital_base_url,
                "stateless": True,
            }
        )

    return mcp


def create_app(
    transport: Any | None = None,
    settings: Settings | None = None,
) -> Any:
    """构造 Starlette ASGI 应用（带 /mcp 与 /health）。

    transport 用于测试注入 httpx.MockTransport 给后端客户端；生产留空。
    返回 (app, mcp, client) 三元组。
    """
    settings = settings or load_settings()
    if transport is not None:
        client = PetHospitalClient(
            base_url=settings.pet_hospital_base_url,
            timeout=settings.pet_hospital_timeout,
            retries=settings.pet_hospital_retries,
            transport=transport,
        )
        mcp = create_mcp(client=client, settings=settings)
    else:
        mcp = create_mcp(settings=settings)
        client = None  # 真实 client 由 create_mcp 内部持有
    app = mcp.streamable_http_app(
        host=settings.mcp_host,
        streamable_http_path=settings.mcp_endpoint_path,
        stateless_http=True,
        json_response=True,
        transport_security=_TEACHING_SECURITY,
    )
    return app, mcp, client


# 模块级单例：供 `python -m pet_hospital_mcp`、`uvicorn pet_hospital_mcp.server:app` 与
# 需要默认实例的测试使用。后端客户端仅在工具被调用时才真正发请求。
settings = load_settings()
mcp = create_mcp(settings=settings)
app = mcp.streamable_http_app(
    host=settings.mcp_host,
    streamable_http_path=settings.mcp_endpoint_path,
    stateless_http=True,
    json_response=True,
    transport_security=_TEACHING_SECURITY,
)
