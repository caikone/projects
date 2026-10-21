# -*- coding: utf-8 -*-
"""流式分片 → 领域对象的累积器（协议编解码工具）。

放在 ports 层而不是基础设施层，是因为它是**协议中立**的通用逻辑：
任何遵从 OpenAI 流式分片语义的适配器都能复用。
"""

from __future__ import annotations

from typing import Iterable

from ..domain.messages import ToolCall
from .llm import LLMChunk, LLMTurn


def accumulate_chunks(chunks: Iterable[LLMChunk]) -> LLMTurn:
    """把一串 `LLMChunk` 累积成一整轮结果。

    - `text` 顺序拼接；
    - `tool_calls` 按 index 分组，累积 id / name / arguments；
    - 以最后一个非 None 的 `finish_reason` / `usage` 为准。
    """
    text_parts: list[str] = []
    slots: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    usage: dict | None = None

    for chunk in chunks:
        if chunk.text:
            text_parts.append(chunk.text)

        for delta in chunk.tool_call_deltas:
            slot = slots.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
            if delta.id:
                slot["id"] = delta.id
            if delta.name:
                slot["name"] += delta.name
            if delta.arguments_fragment:
                slot["arguments"] += delta.arguments_fragment

        if chunk.finish_reason:
            finish_reason = chunk.finish_reason
        if chunk.usage:
            usage = chunk.usage

    tool_calls = [
        ToolCall(
            id=slots[i]["id"],
            name=slots[i]["name"],
            arguments_json=slots[i]["arguments"] or "{}",
        )
        for i in sorted(slots)
    ]
    return LLMTurn(
        text="".join(text_parts),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
    )
