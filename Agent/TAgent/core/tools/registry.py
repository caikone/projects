# -*- coding: utf-8 -*-
"""工具注册表实现（内存版）。

未来的 Web/Desktop 版本若需要「按用户动态启用工具」，
只需另写一个 `ToolRegistry` 实现（例如数据库版），业务代码无需改动。
"""

from __future__ import annotations

from typing import Any

from ..domain.messages import ToolCall, ToolResult
from ..errors import ToolArgumentError, ToolNotFoundError
from ..ports.tools import Tool, ToolOutcome, ToolRegistry, parse_tool_arguments


class InMemoryToolRegistry(ToolRegistry):
    """基于字典的内存注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------
    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError("工具重复注册：%s" % tool.spec.name)
        self._tools[tool.spec.name] = tool

    def register_many(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ------------------------------------------------------------------
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    # ------------------------------------------------------------------
    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec.to_openai_tool() for tool in self._tools.values()]

    # ------------------------------------------------------------------
    def invoke_by_arguments(self, name: str, arguments: dict[str, Any],
                            call_id: str = "") -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        try:
            return tool.invoke(arguments)
        except ToolArgumentError:
            raise
        except Exception as exc:  # 双保险：工具实现若漏了护栏，这里兜住
            return ToolOutcome.failure("工具 %s 执行失败：%s" % (name, exc))

    def invoke(self, call: ToolCall) -> ToolResult:
        """执行工具调用，异常一律转为 `ok=False` 的结果。"""
        try:
            arguments = parse_tool_arguments(call)
            outcome = self.invoke_by_arguments(call.name, arguments, call.id)
        except ToolNotFoundError as exc:
            return ToolResult(call_id=call.id, name=call.name, content=exc.message, ok=False)
        except ToolArgumentError as exc:
            return ToolResult(call_id=call.id, name=call.name, content=exc.message, ok=False)
        return ToolResult(call_id=call.id, name=call.name,
                          content=outcome.content, ok=outcome.ok)


__all__ = ["InMemoryToolRegistry"]
