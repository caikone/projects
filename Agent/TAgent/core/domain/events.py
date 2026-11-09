# -*- coding: utf-8 -*-
"""Agent 流式事件模型（Core → UI 的唯一通信契约）。

核心层**从不**直接调用 UI，只向外 `yield` 事件；UI 层订阅并自行渲染。
这样 TUI / CLI / Web / Desktop 可以共享完全相同的一条业务流水线，
且核心层可以脱离终端做单元测试。

事件流时序（一轮对话）：:

    TurnStarted
      └─ AssistantTextDelta *      （可能 0~n 次，打字机流）
      └─ ToolCallStarted */ ToolCallCompleted *   （每轮工具调用）
      └─ ... 循环若干轮 ...
      └─ AssistantMessageCompleted
    TurnCompleted
    （异常时）ErrorOccurred
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .messages import ToolResult


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class AgentEvent:
    """所有 Agent 事件的基类。

    Attributes:
        turn_id: 所属轮次序号（从 1 开始），UI 可据此分组。
        ts: 事件产生的时间戳（毫秒）。
    """

    turn_id: int = 0
    ts: int = field(default_factory=_now_ms)


@dataclass(slots=True)
class TurnStarted(AgentEvent):
    """一轮对话开始。"""

    user_text: str = ""


@dataclass(slots=True)
class AssistantTextDelta(AgentEvent):
    """模型文本增量（打字机流）。"""

    text: str = ""


@dataclass(slots=True)
class ToolCallStarted(AgentEvent):
    """即将执行一个工具调用。"""

    call_id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    arguments_json: str = "{}"
    round_index: int = 1


@dataclass(slots=True)
class ToolCallCompleted(AgentEvent):
    """工具调用执行结束（成功或失败都发此事件）。"""

    result: ToolResult | None = None
    round_index: int = 1


@dataclass(slots=True)
class RoundFinished(AgentEvent):
    """一次「模型请求 → 工具执行」循环结束。"""

    round_index: int = 1
    has_tool_calls: bool = False
    reached_limit: bool = False


@dataclass(slots=True)
class AssistantMessageCompleted(AgentEvent):
    """模型本轮流式输出结束（可能只是中间产物，后面还要调工具）。"""

    content: str = ""
    round_index: int = 1


@dataclass(slots=True)
class TurnCompleted(AgentEvent):
    """本轮对话彻底结束，`answer` 为最终回复全文。"""

    answer: str = ""
    rounds: int = 1
    tool_calls: int = 0
    cancelled: bool = False


@dataclass(slots=True)
class ErrorOccurred(AgentEvent):
    """发生可恢复错误（网络 / 接口 / 解析等），交给 UI 决定如何提示。"""

    message: str = ""
    error_type: str = "error"
    recoverable: bool = True


@dataclass(slots=True)
class ContextUsageUpdated(AgentEvent):
    """上下文用量刷新（每次模型响应后发出，供状态栏与阈值告警使用）。"""

    used_tokens: int = 0
    window_tokens: int = 0
    percent: int = 0
    #: 窗口大小是否为「拿不到真实值」时的默认假设（UI 显示「默认」）
    window_is_default: bool = False
    #: 是否已达到压缩阈值（达到后 UI 应提示并暂停普通输入）
    warning: bool = False
    #: 用量是否来自模型 API（False = 本地估算）
    from_api: bool = False


@dataclass(slots=True)
class ContextCompactStarted(AgentEvent):
    """开始压缩上下文（压缩期间禁止工具调用与普通输入）。"""

    archive_id: str = ""


@dataclass(slots=True)
class ContextCompacted(AgentEvent):
    """上下文压缩成功：历史已被摘要替换。"""

    archive_id: str = ""
    archive_path: str = ""
    message_count: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    summary_truncated: bool = False


@dataclass(slots=True)
class ContextCompactFailed(AgentEvent):
    """上下文压缩失败：原始上下文保持不变，可重试。"""

    message: str = ""
