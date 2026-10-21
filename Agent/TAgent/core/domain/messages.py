# -*- coding: utf-8 -*-
"""对话领域模型。

这里是**协议中立**的领域对象：不依赖 OpenAI、不依赖任何 UI 框架。
与具体厂商协议的转换统一放在 `infrastructure` 适配器里
（见 `core.infrastructure.llm.openai_compat`）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    """对话角色。使用 str 混入，便于直接参与 JSON 序列化。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """模型发起的一次工具调用请求。

    Attributes:
        id: 本次调用的唯一标识，回传结果时必须原样带回。
        name: 工具名。
        arguments_json: 原始参数（JSON 字符串），用于回传给模型。
        arguments: 解析后的参数字典；解析失败时为 None。
    """

    id: str
    name: str
    arguments_json: str = "{}"
    arguments: dict[str, Any] | None = None

    @property
    def parsed_arguments(self) -> dict[str, Any]:
        """惰性解析参数；失败时返回空字典（错误由 ToolExecutor 统一处理）。"""
        if self.arguments is None:
            try:
                loaded = json.loads(self.arguments_json or "{}")
            except ValueError:
                self.arguments = {}
            else:
                self.arguments = loaded if isinstance(loaded, dict) else {}
        return self.arguments

    def to_openai(self) -> dict[str, Any]:
        """转换成 OpenAI function_call 结构。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_json},
        }


@dataclass
class ToolResult:
    """一次工具调用的执行结果。"""

    call_id: str
    name: str
    content: str
    ok: bool = True
    elapsed_ms: int = 0


@dataclass
class ChatMessage:
    """一条对话消息。

    同一个类承载四种角色：
        - user      ：`content` 为用户输入
        - assistant ：`content` 为文本回复，`tool_calls` 为并发起的调用
        - tool      ：`content` 为工具结果，`tool_call_id` 指向对应调用
        - system    ：`content` 为系统提示词
    """

    role: Role
    content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None

    # ------------------------------------------------------------------
    # 便捷构造
    # ------------------------------------------------------------------
    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: str | None = None,
                  tool_calls: list[ToolCall] | None = None) -> "ChatMessage":
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, result: ToolResult) -> "ChatMessage":
        return cls(role=Role.TOOL, content=result.content, tool_call_id=result.call_id)

    # ------------------------------------------------------------------
    # 协议转换（OpenAI 兼容格式）
    # ------------------------------------------------------------------
    def to_openai(self) -> dict[str, Any]:
        """序列化为 OpenAI `/chat/completions` 所需的 message 结构。"""
        payload: dict[str, Any] = {"role": self.role.value}
        if self.content is not None:
            payload["content"] = self.content
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
        return payload


def to_openai_payload(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """把领域消息列表转换成 OpenAI 兼容的请求体 messages。"""
    return [m.to_openai() for m in messages]
