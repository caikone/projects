# -*- coding: utf-8 -*-
"""工具执行器：把「模型请求的工具调用」翻译成「结构化执行结果」。

它是工具体系与会话之间的胶水层：
    - 负责参数解析、异常兜底、耗时统计；
    - **不做任何输出**，结果以 `ToolResult` 返回，由调用方决定如何呈现。
"""

from __future__ import annotations

import time

from ..domain.messages import ToolCall, ToolResult
from ..errors import TAgentError, ToolError
from ..ports.tools import ToolRegistry, parse_tool_arguments


class ToolExecutor:
    """按注册表分发并执行工具调用。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def execute(self, call: ToolCall) -> ToolResult:
        """执行单个工具调用，永不抛出（错误转化为 `ok=False` 的结果文本）。

        这样设计的原因：工具失败也是「上下文」的一部分——
        模型需要看到失败原因才能自我修正，而不是让整轮对话崩掉。
        """
        started = time.perf_counter()
        try:
            arguments = parse_tool_arguments(call)
            outcome = self._registry.invoke_by_arguments(call.name, arguments, call.id)
            content, ok = outcome.content, outcome.ok
        except TAgentError as exc:
            content, ok = exc.message, False
        except Exception as exc:  # 兜底：任何未预期异常都不能中断会话
            content, ok = "工具 %s 执行异常：%s" % (call.name, exc), False

        elapsed = int((time.perf_counter() - started) * 1000)
        return ToolResult(
            call_id=call.id, name=call.name, content=content, ok=ok, elapsed_ms=elapsed)

    def execute_all(self, calls: list[ToolCall]) -> list[ToolResult]:
        """顺序执行一批工具调用（保持调用顺序，便于与消息一一对应）。"""
        return [self.execute(call) for call in calls]


__all__ = ["ToolExecutor", "ToolError"]
