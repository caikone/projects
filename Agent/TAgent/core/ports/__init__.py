# -*- coding: utf-8 -*-
"""端口层：核心对外依赖的抽象契约（依赖倒置）。"""

from .frontend import Frontend, FrontendContext
from .llm import LLMChunk, LLMClient, LLMRequest, LLMTurn, ToolCallDelta
from .tools import (
    FunctionTool,
    ParamSpec,
    Tool,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    parse_tool_arguments,
)

__all__ = [
    "Frontend",
    "FrontendContext",
    "LLMChunk",
    "LLMClient",
    "LLMRequest",
    "LLMTurn",
    "ToolCallDelta",
    "FunctionTool",
    "ParamSpec",
    "Tool",
    "ToolOutcome",
    "ToolRegistry",
    "ToolSpec",
    "parse_tool_arguments",
]
