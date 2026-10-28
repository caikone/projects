# -*- coding: utf-8 -*-
"""工具装配基础设施：声明式定义 + 统一异常护栏。

提供两样东西：
    1. `ToolBuilder`：用装饰器声明式地定义工具，把「给模型看的 schema」
       与「真正干活的 Python 函数」写在一起，保证二者永不脱节；
    2. `_guard`：把业务异常（如 `FileSystemError`）统一转成
       `ToolOutcome.failure`，让「工具失败」成为模型可以消化的上下文，
       而不是让整轮会话崩掉。
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from ..errors import TAgentError
from ..ports.tools import FunctionTool, Handler, ParamSpec, ToolOutcome, ToolSpec


def guard(handler: Handler) -> Callable[[dict[str, Any]], ToolOutcome]:
    """给处理函数套上异常护栏。"""

    @wraps(handler)
    def wrapper(arguments: dict[str, Any]) -> ToolOutcome:
        try:
            result = handler(arguments)
        except TAgentError as exc:
            return ToolOutcome.failure(exc.message)
        except Exception as exc:  # 兜底：任何未预期异常都不能中断会话
            return ToolOutcome.failure("工具执行异常：%s" % exc)
        if isinstance(result, ToolOutcome):
            return result
        return ToolOutcome.success(result if isinstance(result, str) else str(result))

    return wrapper


class ToolBuilder:
    """收集一组工具定义的构建器。

    用法::

        builder = ToolBuilder()
        @builder.tool("getDate", "获取当前日期时间")
        def _get_date(args):
            return calendar.current_datetime_text()
        tools = builder.build()
    """

    def __init__(self, namespace: str = "") -> None:
        self.namespace = namespace
        self._tools: list[FunctionTool] = []

    # ------------------------------------------------------------------
    def tool(self,
             name: str,
             description: str,
             params: dict[str, ParamSpec] | None = None,
             required: list[str] | None = None,
             destructive: bool = False) -> Callable[[Handler], Handler]:
        """声明一个工具（装饰器）。

        Args:
            name: 工具名（需与模型侧一致，建议驼峰）。
            description: 给模型看的说明，写清楚何时该调用。
            params: 参数声明；`required` 中的键必须都在 `params` 里。
            required: 必填参数名列表。
            destructive: 是否为破坏性操作（UI 会高亮警示）。
        """
        spec = ToolSpec(
            name=name,
            description=description,
            params=params or {},
            required=required or [],
            destructive=destructive,
        )

        def decorator(handler: Handler) -> Handler:
            self._tools.append(FunctionTool(spec, guard(handler)))
            return handler

        return decorator

    # ------------------------------------------------------------------
    def add(self, tool: FunctionTool) -> None:
        """直接追加一个已构造好的工具。"""
        self._tools.append(tool)

    def extend(self, tools: list[FunctionTool]) -> None:
        self._tools.extend(tools)

    def build(self) -> list[FunctionTool]:
        return list(self._tools)


def text_param(description: str) -> ParamSpec:
    """快捷构造字符串参数声明。"""
    return ParamSpec(type="string", description=description)


def int_param(description: str) -> ParamSpec:
    """快捷构造整数参数声明。"""
    return ParamSpec(type="integer", description=description)


def string_array_param(description: str) -> ParamSpec:
    """快捷构造字符串数组参数声明（如命令的参数列表）。"""
    return ParamSpec(type="array", description=description, items={"type": "string"})


__all__ = ["ToolBuilder", "guard", "text_param", "int_param", "string_array_param"]
