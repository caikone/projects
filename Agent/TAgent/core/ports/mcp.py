# -*- coding: utf-8 -*-
"""MCP 客户端端口。

这里只定义「TAgent 需要 MCP 做什么」，不涉及 SDK、asyncio 或线程：

    - `connect()`   建立连接并拿到服务器信息与工具清单（阻塞式，同步语义）；
    - `call_tool()` 调用一个工具并拿回纯数据结果；
    - `close()`     释放连接（含子进程、HTTP 会话）。

同步语义是刻意的：TAgent 的 Agent 循环是同步生成器，端口层若暴露
async，会把 asyncio 传染到 services / tools / tui 各处。异步与线程的
复杂性全部收敛在 `infrastructure/mcp/sdk_client.py` 内部。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..domain.mcp import McpCallResult, McpServerSpec, McpToolInfo


@dataclass(frozen=True)
class McpHandshake:
    """连接成功后的握手信息。"""

    #: 服务器自报的名字与版本（展示用）
    server_name: str = ""
    server_version: str = ""
    protocol_version: str = ""
    tools: list[McpToolInfo] = field(default_factory=list)

    def summary(self) -> str:
        if self.server_name and self.server_version:
            return "%s %s" % (self.server_name, self.server_version)
        return self.server_name or ""


class McpClient(ABC):
    """一个 MCP Server 的连接（生命周期由 `McpService` 管理）。"""

    @property
    @abstractmethod
    def spec(self) -> McpServerSpec:
        """对应的服务器规格。"""
        raise NotImplementedError

    @property
    def resolved_endpoint(self) -> str:
        """实际连通的地址。

        默认与配置一致；实现方若对地址做了补全（例如给漏写 `/mcp` 的
        URL 补上后缀），应返回补全后的地址，便于上层提示用户。
        """
        return self.spec.endpoint

    @abstractmethod
    def connect(self) -> McpHandshake:
        """建立连接、完成 initialize 并拉取工具清单。

        Raises:
            McpConnectionError: 无法连接 / 握手失败 / initialize 超时。
        """
        raise NotImplementedError

    @abstractmethod
    def call_tool(self, tool_name: str, arguments: dict[str, Any],
                  timeout: float | None = None) -> McpCallResult:
        """调用工具。

        Args:
            tool_name: **服务器内部**的原始工具名（不是加前缀的暴露名）。
            arguments: 参数字典。
            timeout: 覆盖默认超时（秒）。

        Raises:
            McpToolCallError: 未连接、超时、协议错误、服务器返回 JSON-RPC 错误。
        """
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """关闭连接；必须可重复调用且不抛异常。"""
        raise NotImplementedError


class McpClientFactory(ABC):
    """按规格创建客户端的工厂（便于测试替换成内存实现）。"""

    @abstractmethod
    def create(self, spec: McpServerSpec) -> McpClient:
        raise NotImplementedError


__all__ = ["McpHandshake", "McpClient", "McpClientFactory"]
