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
    ContextCompacted,
    ContextCompactFailed,
    ContextCompactStarted,
    ContextUsageUpdated,
    ErrorOccurred,
    RoundFinished,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnStarted,
)


class AgentEventBridge:
    """把 Agent 事件流驱动到 TUI 的四个视图组件上。

    Args:
        chat:     对话视图。
        tools:    工具活动面板。
        status:   顶部状态栏。
        thinking: 输入框前方的「思考中」提示条（可选，便于单组件测试）。
    """

    def __init__(self, chat, tools, status, thinking=None) -> None:
        self.chat = chat
        self.tools = tools
        self.status = status
        self.thinking = thinking
        self._turns = 0
        self._tool_calls = 0
        #: 阈值告警只提示一次，用量回落后自动复位
        self._usage_warned = False

    def reset_counters(self) -> None:
        """切换会话时重置轮次统计与告警状态（不算业务，只是界面计数）。"""
        self._turns = 0
        self._tool_calls = 0
        self._usage_warned = False

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
        elif isinstance(event, ContextUsageUpdated):
            await self._on_context_usage(event)
        elif isinstance(event, ContextCompactStarted):
            self._on_compact_started(event)
        elif isinstance(event, ContextCompacted):
            await self._on_compacted(event)
        elif isinstance(event, ContextCompactFailed):
            await self._on_compact_failed(event)
        elif isinstance(event, TurnCompleted):
            await self._on_turn_completed(event)

    # ------------------------------------------------------------------
    async def _on_turn_started(self, event: TurnStarted) -> None:
        await self.chat.add_message("你", event.user_text, kind="user")
        self.status.set_state("等待响应…", "thinking")
        if self.thinking is not None:
            self.thinking.set_busy("等待响应…")

    async def _on_text_delta(self, event: AssistantTextDelta) -> None:
        self.status.set_state("生成中…", "thinking")
        if self.thinking is not None:
            self.thinking.set_busy("生成中…")
        await self.chat.stream_text(event.text)

    async def _on_tool_started(self, event: ToolCallStarted) -> None:
        self.status.set_state("调用工具 %s" % event.name, "tool")
        if self.thinking is not None:
            self.thinking.set_busy("调用工具 %s…" % event.name)
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
        if self.thinking is not None:
            self.thinking.set_label("出错：%s" % event.message[:24])
        await self.chat.add_error(event.message)

    async def _on_turn_completed(self, event: TurnCompleted) -> None:
        self._turns += 1
        self.status.set_turns(self._turns, self._tool_calls)
        await self.chat.end_stream()
        if event.cancelled:
            self.status.set_state("已中止", "idle")
        else:
            self.status.set_state("空闲", "idle")
        if self.thinking is not None:
            self.thinking.set_label("收尾中…")

    # ------------------------------------------------------------------
    # 上下文用量与压缩
    # ------------------------------------------------------------------
    async def _on_context_usage(self, event: ContextUsageUpdated) -> None:
        self.status.set_usage(event.used_tokens, event.window_tokens,
                              event.percent, event.window_is_default)
        if event.warning and not self._usage_warned:
            self._usage_warned = True
            await self.chat.add_notice(
                "⚠ 上下文已使用 %d%%（%s / %s%s）。已达到压缩阈值，"
                "普通输入已暂停，请执行 /compact 压缩上下文后继续。"
                % (event.percent,
                   _k(event.used_tokens), _k(event.window_tokens),
                   "，默认窗口" if event.window_is_default else ""))
        elif not event.warning:
            # 压缩后用量回落：允许下一次再提示
            self._usage_warned = False

    def _on_compact_started(self, event: ContextCompactStarted) -> None:
        self.status.set_state("正在压缩上下文…", "thinking")
        if self.thinking is not None:
            self.thinking.set_busy("正在压缩上下文…")

    async def _on_compacted(self, event: ContextCompacted) -> None:
        self._usage_warned = False
        note = ("上下文压缩完成：%d 条历史 → 摘要，约 %s → %s。\n"
                "归档编号 %s（%s）。需要细节时可用 recallContext 检索原始记录。"
                % (event.message_count, _k(event.tokens_before),
                   _k(event.tokens_after), event.archive_id, event.archive_path))
        if event.summary_truncated:
            note += "\n（摘要超出约 5,000 Tokens 上限，已本地截断。）"
        await self.chat.add_notice(note)
        self.status.set_state("压缩完成", "idle")
        if self.thinking is not None:
            self.thinking.set_label("压缩完成")

    async def _on_compact_failed(self, event: ContextCompactFailed) -> None:
        self.status.set_state("压缩失败", "error")
        if self.thinking is not None:
            self.thinking.set_label("压缩失败")
        await self.chat.add_error(event.message)


def _k(tokens: int) -> str:
    """Token 数的紧凑显示（与状态栏保持一致）。"""
    if tokens >= 1024:
        return "%.1fk" % (tokens / 1024)
    return str(tokens)
