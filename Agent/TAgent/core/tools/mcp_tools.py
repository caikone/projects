# -*- coding: utf-8 -*-
"""把 MCP 工具包装成 TAgent 的 `Tool`。

MCP 工具与本地工具在模型眼里没有区别：都是 `tools[]` 里的一项 function，
调用后都得到一段文本结果。差异全部收敛在这里与 `services/mcp.py`：

    - 暴露名带 `mcp__<server>__` 前缀（避免跨服务器重名、也便于模型分辨来源）；
    - 参数 schema 直接透传服务器给的 `inputSchema`（可能是嵌套结构，
      无法用扁平的 `ParamSpec` 表达，因此 `ToolSpec.raw_schema` 原样承载）；
    - 调用转发到对应服务器，参数用服务器内部的**原始工具名**。
"""

from __future__ import annotations

from typing import Any

from ..domain.mcp import McpToolDescriptor
from ..ports.tools import Tool, ToolOutcome, ToolSpec
from ..services.mcp import McpService

#: 服务器描述过长时截断，避免占用过多上下文
MAX_DESCRIPTION_CHARS = 1500


def normalize_input_schema(schema: Any) -> dict[str, Any]:
    """保证 schema 是一个合法的 object 结构（模型对空/畸形 schema 很敏感）。"""
    data = dict(schema) if isinstance(schema, dict) else {}
    if not data:
        return {"type": "object", "properties": {}}
    # 只有 anyOf / oneOf 这类联合 schema 不该被强行标成 object
    if "type" not in data and "anyOf" not in data and "oneOf" not in data:
        data["type"] = "object"
    if data.get("type") == "object" and not isinstance(data.get("properties"), dict):
        data["properties"] = {}
    return data


def describe_tool(descriptor: McpToolDescriptor,
                  max_description_chars: int = MAX_DESCRIPTION_CHARS) -> str:
    """拼给模型看的工具说明：保留服务器原文 + 标注来源与原始名。"""
    text = (descriptor.description or "").strip()
    if not text:
        text = "（服务器未提供描述，请根据参数 schema 判断用途）"
    limit = max(100, int(max_description_chars or MAX_DESCRIPTION_CHARS))
    if len(text) > limit:
        text = text[:limit] + "…（描述过长已截断）"
    return "%s\n\n[来源] MCP 服务器「%s」，原始工具名：%s" % (
        text, descriptor.server_name, descriptor.tool_name)


class McpTool(Tool):
    """单个 MCP 工具。每次调用都经过 `McpService` 找到对应服务器。"""

    def __init__(self, descriptor: McpToolDescriptor, service: McpService,
                 max_description_chars: int = MAX_DESCRIPTION_CHARS) -> None:
        self._descriptor = descriptor
        self._service = service
        self._spec = ToolSpec(
            name=descriptor.exposed_name,
            description=describe_tool(descriptor, max_description_chars),
            raw_schema=normalize_input_schema(descriptor.input_schema),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    @property
    def descriptor(self) -> McpToolDescriptor:
        return self._descriptor

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        return self._service.call(self._descriptor.exposed_name, arguments)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "<McpTool %s <- %s/%s>" % (
            self._descriptor.exposed_name,
            self._descriptor.server_name,
            self._descriptor.tool_name)


def build_mcp_tools(service: McpService,
                    max_description_chars: int = MAX_DESCRIPTION_CHARS) -> list[McpTool]:
    """按已发现的工具清单构造工具实例（由容器注册进 ToolRegistry）。"""
    return [McpTool(descriptor, service, max_description_chars)
            for descriptor in service.tools]


__all__ = ["McpTool", "build_mcp_tools", "normalize_input_schema", "describe_tool"]
