# -*- coding: utf-8 -*-
"""脚本化 LLM 客户端（测试 / 离线演示用）。

它同样实现 `LLMClient` 端口，因此可以无缝顶替真实客户端：
核心服务、事件流、工具链路全部走真实代码路径，只有「模型」是假的。
这让整个 core 层可以脱离网络做确定性测试。
"""

from __future__ import annotations

from typing import Iterator

from ...domain.messages import ChatMessage, Role
from ...ports.llm import LLMChunk, LLMClient, LLMRequest, ToolCallDelta


class ScriptedLLMClient(LLMClient):
    """按脚本回放响应的假客户端。

    Args:
        script: 每轮响应的剧本。每项形如::

            {"text": "好的", "tool_calls": [{"id": "1", "name": "getDate",
                                             "arguments": "{}"}]}
    """

    def __init__(self, script: list[dict] | None = None) -> None:
        self.script = script or []
        self.requests: list[LLMRequest] = []
        self._cursor = 0

    # ------------------------------------------------------------------
    def stream(self, request: LLMRequest) -> Iterator[LLMChunk]:
        self.requests.append(request)
        item = self.script[self._cursor] if self._cursor < len(self.script) else {}
        self._cursor += 1

        text = item.get("text") or ""
        if text:
            for i in range(0, len(text), 8):  # 模拟分片到达
                yield LLMChunk(text=text[i:i + 8])

        for index, call in enumerate(item.get("tool_calls") or []):
            yield LLMChunk(tool_call_deltas=[
                ToolCallDelta(index=index, id=call.get("id", "call_%d" % index),
                              name=call.get("name", ""),
                              arguments_fragment=call.get("arguments", "{}"))
            ])

        yield LLMChunk(finish_reason="tool_calls" if item.get("tool_calls") else "stop")

    # ------------------------------------------------------------------
    @property
    def last_request(self) -> LLMRequest | None:
        return self.requests[-1] if self.requests else None

    def last_user_text(self) -> str:
        """取最近一条用户消息，便于断言。"""
        for message in reversed(self.last_request.messages if self.last_request else []):
            if isinstance(message, ChatMessage) and message.role == Role.USER:
                return message.content or ""
        return ""


class EchoLLMClient(LLMClient):
    """回声客户端：把用户最后一句话原样返回（无需任何脚本）。"""

    def stream(self, request: LLMRequest) -> Iterator[LLMChunk]:
        text = ""
        for message in reversed(request.messages):
            if message.role == Role.USER:
                text = message.content or ""
                break
        for i in range(0, len(text), 8):
            yield LLMChunk(text=text[i:i + 8])
        yield LLMChunk(finish_reason="stop")


__all__ = ["ScriptedLLMClient", "EchoLLMClient"]
