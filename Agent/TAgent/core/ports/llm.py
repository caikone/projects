# -*- coding: utf-8 -*-
"""LLM 端口（Port）：核心层对「大模型」这一外部能力的抽象契约。

核心服务只依赖这个接口，不关心底层是 OpenAI / DeepSeek / 通义 / 本地 vLLM，
也不关心用的是 requests / httpx / aiohttp。
新增一种后端 = 在 `infrastructure/llm/` 下新增一个适配器，业务代码零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..domain.messages import ChatMessage, ToolCall


@dataclass(slots=True)
class ToolCallDelta:
    """流式响应中工具调用的一个分片。

    OpenAI 兼容协议里 `tool_calls` 按 index 分片下发，
    适配器负责把它们累积成完整的 `ToolCall`。
    """

    index: int = 0
    id: str = ""
    name: str = ""
    arguments_fragment: str = ""


@dataclass(slots=True)
class LLMChunk:
    """流式响应的一个数据块。

    Attributes:
        text: 文本增量（可能为 None）。
        tool_call_deltas: 本块携带的工具调用分片。
        finish_reason: 结束原因（`stop` / `tool_calls` / `length` / None）。
        usage: token 用量（部分厂商在最后一块下发）。
    """

    text: str | None = None
    tool_call_deltas: list[ToolCallDelta] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """一次对话请求所需的最小输入。"""

    messages: list[ChatMessage]
    tool_specs: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:  # dataclass(frozen) 下仅做校验
        if not self.messages:
            raise ValueError("messages 不能为空")


@dataclass(frozen=True, slots=True)
class LLMTurn:
    """一轮流式响应的聚合结果。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMClient(ABC):
    """LLM 客户端端口。"""

    @abstractmethod
    def stream(self, request: LLMRequest) -> Iterator[LLMChunk]:
        """发起流式请求，逐块产出 `LLMChunk`。

        实现方必须把所有第三方异常转换成 `core.errors.LLMError` 子类。
        """
        raise NotImplementedError

    def complete_turn(self, request: LLMRequest) -> LLMTurn:
        """把流式响应聚合为一次完整结果（默认实现基于 `stream`）。

        需要「非流式」能力的 UI（例如批处理脚本）可直接调用此方法。
        """
        from .tool_codec import accumulate_chunks  # 局部导入避免循环依赖

        return accumulate_chunks(self.stream(request))

    def close(self) -> None:
        """释放底层资源（连接池等），默认无操作。"""
        return None
