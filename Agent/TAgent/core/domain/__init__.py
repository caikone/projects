# -*- coding: utf-8 -*-
"""领域层：与任何协议、任何界面都无关的数据结构。"""

from .events import (
    AgentEvent,
    AssistantMessageCompleted,
    AssistantTextDelta,
    ErrorOccurred,
    RoundFinished,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnStarted,
)
from .messages import ChatMessage, Role, ToolCall, ToolResult, to_openai_payload

__all__ = [
    "AgentEvent",
    "AssistantMessageCompleted",
    "AssistantTextDelta",
    "ErrorOccurred",
    "RoundFinished",
    "ToolCallCompleted",
    "ToolCallStarted",
    "TurnCompleted",
    "TurnStarted",
    "ChatMessage",
    "Role",
    "ToolCall",
    "ToolResult",
    "to_openai_payload",
]
