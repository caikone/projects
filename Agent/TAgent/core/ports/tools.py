# -*- coding: utf-8 -*-
"""工具端口（Port）：工具契约 + 注册表契约。

工具被建模为「声明（spec）+ 行为（invoke）」两部分：
    - `ToolSpec` 是给模型看的声明，可序列化成 OpenAI function schema；
    - `Tool` 是可执行的行为，返回结构化的 `ToolOutcome`（而不是裸字符串），
      让 UI 层能区分成功/失败并做差异化展示。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from ..domain.messages import ToolCall, ToolResult
from ..errors import ToolArgumentError


@dataclass(frozen=True)
class ParamSpec:
    """单个参数的 JSON Schema 声明。"""

    type: str = "string"
    description: str = ""
    enum: list[Any] | None = None
    default: Any = None
    #: 数组元素的类型（type="array" 时必填，如 {"type": "string"}）
    items: dict[str, Any] | None = None

    def to_json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        if self.items is not None:
            schema["items"] = self.items
        return schema


@dataclass(frozen=True)
class ToolSpec:
    """工具的完整声明（对应 OpenAI `tools[].function`）。"""

    name: str
    description: str
    params: dict[str, ParamSpec] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    #: 危险操作（删除类）会被 UI 高亮提示
    destructive: bool = False

    def to_json_schema(self) -> dict[str, Any]:
        """参数对象的 JSON Schema。"""
        return {
            "type": "object",
            "properties": {k: v.to_json_schema() for k, v in self.params.items()},
            "required": list(self.required),
        }

    def to_openai_tool(self) -> dict[str, Any]:
        """转换成 OpenAI tools 数组中的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.to_json_schema(),
            },
        }


@dataclass(slots=True)
class ToolOutcome:
    """工具执行结果（结构化，非裸字符串）。

    Attributes:
        content: 回传给模型的文本（成功时为结果摘要，失败时为错误说明）。
        ok: 是否执行成功。
        display: 给 UI 展示的短文本；默认等于 `content`（超长时由 UI 自行截断）。
    """

    content: str = ""
    ok: bool = True
    display: str | None = None

    def shown(self) -> str:
        return self.display if self.display is not None else self.content

    @classmethod
    def success(cls, content: str, display: str | None = None) -> "ToolOutcome":
        return cls(content=content, ok=True, display=display)

    @classmethod
    def failure(cls, content: str, display: str | None = None) -> "ToolOutcome":
        return cls(content=content, ok=False, display=display)


class Tool(ABC):
    """工具抽象基类。"""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """工具声明。"""
        raise NotImplementedError

    @abstractmethod
    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        """执行工具。实现方应捕获自身业务异常并转成 `ToolOutcome.failure`。"""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.spec.name


Handler = Callable[[dict[str, Any]], "ToolOutcome | str | Any"]


class FunctionTool(Tool):
    """把一个普通函数包装成 `Tool`。

    处理函数返回值约定：
        - `ToolOutcome` ：原样返回；
        - `str`         ：视为成功结果；
        - 其它对象      ：`str()` 后作为成功结果。
    """

    def __init__(self, spec: ToolSpec, handler: Handler) -> None:
        self._spec = spec
        self._handler = handler

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        result = self._handler(arguments)
        if isinstance(result, ToolOutcome):
            return result
        return ToolOutcome.success(result if isinstance(result, str) else str(result))

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "<FunctionTool %s>" % self._spec.name


class ToolRegistry(ABC):
    """工具注册表端口：注册、查询、声明导出、分发执行。"""

    @abstractmethod
    def register(self, tool: Tool) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, name: str) -> Tool | None:
        raise NotImplementedError

    @abstractmethod
    def all(self) -> list[Tool]:
        raise NotImplementedError

    @abstractmethod
    def specs(self) -> list[dict[str, Any]]:
        """导出 OpenAI 格式的 tools 数组。"""
        raise NotImplementedError

    @abstractmethod
    def invoke(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用，含参数解析与异常兜底，永不抛出业务异常。"""
        raise NotImplementedError

    @abstractmethod
    def invoke_by_arguments(self, name: str, arguments: dict[str, Any],
                            call_id: str = "") -> ToolOutcome:
        """在参数已解析的前提下执行工具。

        未知工具时抛出 `ToolNotFoundError`；工具内部异常由工具自己转成
        `ToolOutcome.failure`（`FunctionTool` 之外的实现需自行保证）。
        """
        raise NotImplementedError


def parse_tool_arguments(call: ToolCall) -> dict[str, Any]:
    """解析工具参数为字典；非法时抛出 `ToolArgumentError`。"""
    raw = (call.arguments_json or "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        raise ToolArgumentError(call.name, raw)
    if not isinstance(loaded, dict):
        raise ToolArgumentError(call.name, raw)
    return loaded
