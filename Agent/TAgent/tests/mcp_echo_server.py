# -*- coding: utf-8 -*-
"""测试用的最小 MCP Server（stdio 传输）。

被 `tests/test_mcp.py` 的真实集成测试通过 `python mcp_echo_server.py` 拉起。
不参与 pytest 收集（文件名不以 test_ 开头）。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("echo-server", version="1.0.0")


@server.tool(description="回显给定文本")
def echo(text: str) -> str:
    return "echo: " + text


@server.tool(description="两数相加，返回结构化结果")
def add(a: int, b: int) -> dict:
    return {"sum": a + b, "a": a, "b": b}


@server.tool(description="总是失败，用于验证错误处理")
def boom() -> str:
    raise ValueError("这是被故意触发的失败")


if __name__ == "__main__":
    server.run("stdio")
