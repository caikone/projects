"""`python -m pet_hospital_mcp` 入口：以无状态 Streamable HTTP 启动 MCP 服务。"""

from __future__ import annotations

from .config import load_settings
from .server import SERVER_NAME, mcp, settings


def main() -> None:
    """以 streamable-http 传输启动 MCP 服务（无状态、JSON 响应）。"""
    print(f"{SERVER_NAME}: MCP {settings.mcp_url} (stateless, 2026-07-28)")
    print(f"  backend: {settings.pet_hospital_base_url}")
    print(f"  health : http://{settings.mcp_host}:{settings.mcp_port}/health")
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path=settings.mcp_endpoint_path,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
