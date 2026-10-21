# -*- coding: utf-8 -*-
"""事件桥接层（防腐层）：把 core 的领域事件翻译成界面动作。

存在的意义：
    `core.domain.events` 是纯粹的领域概念，界面不应该认识它们之外的东西；
    反过来，界面组件也不该认识领域事件。
    这一层把两边的耦合收敛到一个文件里——
    将来写 Web 版时，只需再写一个 `web/bridge.py` 把同样的事件转成 SSE 消息即可。
"""

from __future__ import annotations

from core.domain.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    ErrorOccurred,
    RoundFinished,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnStarted,
)


class AgentEventBridge:
    """把 Agent 事件流驱动到 TUI 的三个视图组件上。

    Args:
        chat:   对话视图。
        tools:  工具活动面板。
        status: 状态栏。
    """

    def __init__(self, chat, tools, status) -> None:
        self.chat = chat
        self.tools = tools
        self.status = status
        self._turns = 0
        self._tool_calls = 0

    # ------------------------------------------------------------------
    async def handle(self, event) -> None:
        """按事件类型分发（只做界面动作，不含业务判断）。"""
        if isinstance(event, TurnStarted):
            await self._on_turn_started(event)
        elif isinstance(event, AssistantTextDelta):
            await self._on_text_delta(event)
        elif isinstance(event, ToolCallStarted):
            await self._on_tool_started(event)
        elif isinstance(event, ToolCallCompleted):
            self._on_tool_completed(event)
        elif isinstance(event, RoundFinished):
            self._on_round_finished(event)
        elif isinstance(event, AssistantMessageCompleted):
            self._on_message_completed(event)
        elif isinstance(event, ErrorOccurred):
            await self._on_error(event)
        elif isinstance(event, TurnCompleted):
            await self._on_turn_completed(event)

    # ------------------------------------------------------------------
    async def _on_turn_started(self, event: TurnStarted) -> None:
        await self.chat.add_message("你", event.user_text, kind="user")
        self.status.set_state("等待响应…", "thinking")

    async def _on_text_delta(self, event: AssistantTextDelta) -> None:
        self.status.set_state("生成中…", "thinking")
        await self.chat.stream_text(event.text)

    async def _on_tool_started(self, event: ToolCallStarted) -> None:
        self.status.set_state("调用工具 %s" % event.name, "tool")
        await self.tools.add_call(
            call_id=event.call_id,
            name=event.name,
            arguments_json=event.arguments_json,
            arguments=event.arguments,
            round_index=event.round_index,
        )
        self._tool_calls += 1
        self.status.set_turns(self._turns, self._tool_calls)

    def _on_tool_completed(self, event: ToolCallCompleted) -> None:
        result = event.result
        if result is None:
            return
        self.tools.complete_call(result.call_id, result.ok, result.content, result.elapsed_ms)

    def _on_round_finished(self, event: RoundFinished) -> None:
        if event.reached_limit:
            self.status.set_state("已达工具轮次上限", "error")

    def _on_message_completed(self, event: AssistantMessageCompleted) -> None:
        # 中间轮次的文本已完成，但整轮可能还要继续调工具，这里不收尾
        return None

    async def _on_error(self, event: ErrorOccurred) -> None:
        self.status.set_state("出错：%s" % event.message[:40], "error")
        await self.chat.add_error(event.message)

    async def _on_turn_completed(self, event: TurnCompleted) -> None:
        self._turns += 1
        self.status.set_turns(self._turns, self._tool_calls)
        await self.chat.end_stream()
        if event.cancelled:
            self.status.set_state("已中止", "idle")
        else:
            self.status.set_state("空闲", "idle")
