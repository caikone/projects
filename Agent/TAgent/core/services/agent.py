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
    ContextUsageUpdated,
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
from .context import ContextService
from .session import Session
from .tool_executor import ToolExecutor


class AgentService:
    """对话编排服务。

    Args:
        llm:      LLM 端口实现（如 `OpenAICompatibleClient`）。
        tools:    工具注册表。
        settings: 运行时配置。
        context:  上下文压缩服务（可选）；提供后每次响应结束都会上报用量。
    """

    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: AgentSettings,
                 context: ContextService | None = None) -> None:
        self._llm = llm
        self._tools = tools
        self._settings = settings
        self._context = context
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

    @property
    def context(self) -> ContextService | None:
        """上下文压缩服务（未装配时为 None）。"""
        return self._context

    def available_tools(self) -> list[ToolSpec]:
        """当前可用工具的声明列表（UI 侧栏展示用）。"""
        return [tool.spec for tool in self._tools.all()]

    def tool_specs(self) -> list[dict]:
        """缓存后的 OpenAI 格式 tools 数组。"""
        if self._specs_cache is None:
            self._specs_cache = self._tools.specs()
        return self._specs_cache

    def refresh_tools(self) -> int:
        """工具集合发生变化后调用（如 MCP 在后台加载完成）。

        返回当前工具数量，方便 UI 顺手刷新状态栏。
        """
        self._specs_cache = None
        return len(self._tools.all())

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
                text, tool_calls, api_usage = yield from self._run_round(
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

            # 每次模型响应后上报上下文用量（优先用 API 的精确值）
            usage_event = self._usage_event(session, api_usage)
            if usage_event is not None:
                yield usage_event

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
    def _usage_event(self, session: Session,
                     api_usage: dict | None) -> ContextUsageUpdated | None:
        """把当前上下文用量打包成事件（未装配压缩服务时返回 None）。"""
        if self._context is None:
            return None
        snapshot = self._context.usage(session, self.tool_specs(), api_usage=api_usage)
        return ContextUsageUpdated(
            used_tokens=snapshot.used_tokens,
            window_tokens=snapshot.window_tokens,
            percent=snapshot.percent,
            window_is_default=snapshot.window_is_default,
            warning=self._context.should_warn(snapshot),
            from_api=snapshot.from_api,
        )

    def _run_round(self, turn_id: int, round_index: int,
                   messages: list[ChatMessage]) -> Iterator[AgentEvent]:
        """发起一次流式请求，yield 文本增量，结束时返回 (文本, 工具调用, usage)。"""
        request = LLMRequest(
            messages=list(messages),
            tool_specs=self.tool_specs(),
            temperature=self._settings.llm.temperature,
            max_tokens=self._settings.llm.max_tokens,
        )

        text_parts: list[str] = []
        slots: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        api_usage: dict | None = None

        for chunk in self._llm.stream(request):
            if self._cancel.is_set():
                break
            if chunk.text:
                text_parts.append(chunk.text)
                yield AssistantTextDelta(turn_id=turn_id, text=chunk.text)
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage:
                # 部分厂商在最后一帧单独下发 usage，取最后一个非空值
                api_usage = dict(chunk.usage)

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
        return "".join(text_parts), tool_calls, api_usage

    # ------------------------------------------------------------------
    def close(self) -> None:
        """释放底层资源。"""
        self._llm.close()


__all__ = ["AgentService"]
