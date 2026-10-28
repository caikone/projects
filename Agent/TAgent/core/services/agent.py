# -*- coding: utf-8 -*-
"""Agent 应用服务：整条业务流水线的编排者。

这是 core 层**唯一**的对外门面（Facade）。它满足三条硬约束：

    1. 不含任何 `print` / `input` / 终端相关的调用——UI 无关；
    2. 不 import `requests` / `textual`——只依赖 `ports` 中的抽象；
    3. 通过**生成器 yield 事件**向外推送进度——天然支持流式，
       也让 CLI / TUI / Web / Desktop 能共用同一条流水线。

因此本文件可以脱离终端做纯单元测试。
"""

from __future__ import annotations

import threading
from typing import Iterator

from ..config.models import AgentSettings
from ..domain.events import (
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
from ..domain.messages import ChatMessage, ToolCall
from ..errors import LLMError
from ..ports.llm import LLMClient, LLMRequest
from ..ports.tools import ToolRegistry, ToolSpec
from .session import Session
from .tool_executor import ToolExecutor


class AgentService:
    """对话编排服务。

    Args:
        llm:      LLM 端口实现（如 `OpenAICompatibleClient`）。
        tools:    工具注册表。
        settings: 运行时配置。
    """

    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: AgentSettings) -> None:
        self._llm = llm
        self._tools = tools
        self._settings = settings
        self._executor = ToolExecutor(tools)
        self._cancel = threading.Event()
        self._specs_cache: list[dict] | None = None

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------
    @property
    def settings(self) -> AgentSettings:
        return self._settings

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    def available_tools(self) -> list[ToolSpec]:
        """当前可用工具的声明列表（UI 侧栏展示用）。"""
        return [tool.spec for tool in self._tools.all()]

    def tool_specs(self) -> list[dict]:
        """缓存后的 OpenAI 格式 tools 数组。"""
        if self._specs_cache is None:
            self._specs_cache = self._tools.specs()
        return self._specs_cache

    # ------------------------------------------------------------------
    # 取消控制（供 TUI 的 Ctrl+C / 停止按钮调用）
    # ------------------------------------------------------------------
    def request_cancel(self) -> None:
        """请求中断当前轮次（协作式，在下一次流式分片处生效）。"""
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def _begin_turn(self) -> None:
        self._cancel.clear()

    # ------------------------------------------------------------------
    # 核心：流式执行一轮对话
    # ------------------------------------------------------------------
    def stream_turn(self, session: Session, user_text: str,
                    turn_id: int | None = None) -> Iterator[AgentEvent]:
        """执行一轮完整对话，逐步 yield 事件。

        流程：

            user 消息入会话
              → 循环（最多 max_tool_rounds 次）：
                    流式请求模型 → yield 文本增量
                    → 若返回 tool_calls：yield 调用事件、执行工具、结果回灌
                    → 否则结束
              → 最终 assistant 消息入会话

        任何 `LLMError` 都被捕获并转成 `ErrorOccurred` 事件，
        保证 UI 不会因为一次网络抖动而崩掉。
        """
        self._begin_turn()
        turn_id = turn_id if turn_id is not None else session.next_turn_id

        session.add_user(user_text)
        yield TurnStarted(turn_id=turn_id, user_text=user_text)

        messages = session.messages  # 直接操作会话列表，保证上下文实时同步
        answer_parts: list[str] = []
        tool_call_total = 0
        rounds = 0
        cancelled = False

        for round_index in range(1, self._settings.max_tool_rounds + 1):
            rounds = round_index
            try:
                text, tool_calls = yield from self._run_round(
                    turn_id=turn_id, round_index=round_index, messages=messages)
            except LLMError as exc:
                yield ErrorOccurred(turn_id=turn_id, message=exc.message, error_type=exc.kind)
                yield TurnCompleted(
                    turn_id=turn_id,
                    answer="".join(answer_parts),
                    rounds=rounds,
                    tool_calls=tool_call_total,
                )
                return

            if text:
                answer_parts.append(text)
            yield AssistantMessageCompleted(
                turn_id=turn_id, content=text, round_index=round_index)

            if self._cancel.is_set():
                cancelled = True
                yield RoundFinished(turn_id=turn_id, round_index=round_index,
                                    has_tool_calls=False)
                break

            if not tool_calls:
                yield RoundFinished(turn_id=turn_id, round_index=round_index,
                                    has_tool_calls=False)
                break

            # 达到轮次上限：不再回灌工具结果，直接收尾
            if round_index >= self._settings.max_tool_rounds:
                yield RoundFinished(turn_id=turn_id, round_index=round_index,
                                    has_tool_calls=True, reached_limit=True)
                break

            tool_call_total += len(tool_calls)
            session.add(ChatMessage.assistant(text or None, tool_calls))

            for call in tool_calls:
                yield ToolCallStarted(
                    turn_id=turn_id,
                    call_id=call.id,
                    name=call.name,
                    arguments=call.parsed_arguments,
                    arguments_json=call.arguments_json,
                    round_index=round_index,
                )
                if self._cancel.is_set():
                    cancelled = True
                    break
                result = self._executor.execute(call)
                yield ToolCallCompleted(turn_id=turn_id, result=result, round_index=round_index)
                session.add(ChatMessage.tool(result))

            if cancelled:
                break

            yield RoundFinished(turn_id=turn_id, round_index=round_index, has_tool_calls=True)

        answer = "".join(answer_parts)
        if answer or not cancelled:
            session.add_assistant(answer)
        yield TurnCompleted(
            turn_id=turn_id,
            answer=answer,
            rounds=rounds,
            tool_calls=tool_call_total,
            cancelled=cancelled,
        )

    # ------------------------------------------------------------------
    def _run_round(self, turn_id: int, round_index: int,
                   messages: list[ChatMessage]) -> Iterator[AgentEvent]:
        """发起一次流式请求，yield 文本增量，结束时返回 (文本, 工具调用列表)。"""
        request = LLMRequest(
            messages=list(messages),
            tool_specs=self.tool_specs(),
            temperature=self._settings.llm.temperature,
            max_tokens=self._settings.llm.max_tokens,
        )

        text_parts: list[str] = []
        slots: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None

        for chunk in self._llm.stream(request):
            if self._cancel.is_set():
                break
            if chunk.text:
                text_parts.append(chunk.text)
                yield AssistantTextDelta(turn_id=turn_id, text=chunk.text)
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason

            for delta in chunk.tool_call_deltas:
                slot = slots.setdefault(
                    delta.index, {"id": "", "name": "", "arguments": ""})
                if delta.id:
                    slot["id"] = delta.id
                if delta.name:
                    slot["name"] += delta.name
                if delta.arguments_fragment:
                    slot["arguments"] += delta.arguments_fragment

        if finish_reason == "length" and not slots:
            # 输出达到 max_tokens 上限被截断：明确告知，而不是无声消失
            yield ErrorOccurred(
                turn_id=turn_id,
                message="回复因达到单次输出上限（max_tokens）被截断，"
                        "可让模型「接着上次继续」或调大 config.ini 里的 max_tokens。",
                error_type="notice",
            )

        tool_calls = [
            ToolCall(
                id=slots[i]["id"],
                name=slots[i]["name"],
                arguments_json=slots[i]["arguments"] or "{}",
            )
            for i in sorted(slots)
        ]
        return "".join(text_parts), tool_calls

    # ------------------------------------------------------------------
    def close(self) -> None:
        """释放底层资源。"""
        self._llm.close()


__all__ = ["AgentService"]
