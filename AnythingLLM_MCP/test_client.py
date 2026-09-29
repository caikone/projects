"""端到端测试：连接 MCP Server，列出工具并调用 ask_first_workspace"""
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    async with streamable_http_client("http://127.0.0.1:8000/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.discover()
            print("协议版本:", session.protocol_version)
            tools = await session.list_tools()
            print("工具:", [t.name for t in tools.tools])
            result = await session.call_tool("ask_first_workspace", {"question": "这个工作区里有什么内容？请简要介绍。"})
            print("回答:", result.content[0].text[:500])


asyncio.run(main())
