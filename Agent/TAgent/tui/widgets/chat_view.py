# -*- coding: utf-8 -*-
"""对话视图：消息列表容器，负责流式渲染节流与自动滚动。"""

from __future__ import annotations

import asyncio

from textual.containers import VerticalScroll
from textual.widgets import Static

from .message_card import MessageCard

#: 流式渲染刷新间隔（秒）——约 20 FPS，兼顾流畅度与 CPU 占用
FLUSH_INTERVAL = 1 / 20


class ChatView(VerticalScroll):
    """对话消息列表。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._streaming: MessageCard | None = None
        self._timer = None
        #: 是否自动跟随到底部（用户往上翻看历史时暂停跟随，避免被强行拽回）
        self._follow = True

    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self._timer = self.set_interval(FLUSH_INTERVAL, self.flush)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """滚动位置变化时更新「是否跟随底部」。

        必须先调 `super()`：`ScrollView.watch_scroll_y` 负责刷滚动条位置并
        重绘视口，覆盖掉它会导致「scroll_y 变了但屏幕不动」。
        """
        super().watch_scroll_y(old_value, new_value)
        self._follow = self.is_vertical_scroll_end

    # ------------------------------------------------------------------
    # 消息写入
    # ------------------------------------------------------------------
    async def add_message(self, sender: str, text: str, kind: str = "assistant") -> MessageCard:
        """追加一条完整消息（非流式）。返回卡片以便后续修改。"""
        card = MessageCard(sender=sender, kind=kind, text=text)
        await self.mount(card)
        self._after_change()
        return card

    async def start_stream(self, sender: str = "助手") -> MessageCard:
        """开启一条流式消息；若上一条未结束则复用它（多轮工具调用场景）。"""
        if self._streaming is not None:
            return self._streaming
        card = MessageCard(sender=sender, kind="assistant")
        await self.mount(card)
        self._streaming = card
        self._after_change()
        return card

    async def stream_text(self, delta: str) -> None:
        """向当前流式消息追加文本。"""
        if self._streaming is None:
            await self.start_stream()
        self._streaming.append(delta)  # type: ignore[union-attr]

    async def end_stream(self) -> None:
        """结束流式消息（确保最终全文渲染完成）。"""
        card = self._streaming
        if card is None:
            return
        # 定时器可能正在渲染同一张卡片（`_flushing=True` 时 flush 会立即返回）。
        # 这里必须「等到真的渲染完」——否则最后一帧会丢掉收尾前到达的增量，
        # 表现为回复停在某个标题后面、下面的内容永远不出现。
        for _ in range(100):
            await card.flush(force=True)
            if not card.dirty and not card.flushing:
                break
            await asyncio.sleep(0.02)
        self._streaming = None
        self._after_change()

    async def add_error(self, text: str) -> None:
        """追加一条错误提示。"""
        await self.end_stream()
        await self.add_message("错误", text, kind="error")

    async def add_notice(self, text: str) -> None:
        """追加一条系统提示。"""
        await self.add_message("系统", text, kind="system")

    async def clear_messages(self) -> None:
        """清空对话区。"""
        self._streaming = None
        await self.remove_children()
        await self.mount(Static("新会话已开始，输入内容开始对话。", classes="empty-hint"))
        self.scroll_end(animate=False)

    # ------------------------------------------------------------------
    # 渲染节流
    # ------------------------------------------------------------------
    def flush(self) -> None:
        """定时器回调：把脏卡片统一渲染一次并滚到底部。

        `MessageCard.flush` 是协程（Markdown.update 的挂载必须被 await），
        Textual 的 `set_interval` 支持异步回调，多个 tick 重入由卡片内的
        `_flushing` 标志防抖。
        """
        return self._flush_all()

    async def _flush_all(self) -> None:
        changed = False
        for card in self.query(MessageCard):
            if await card.flush():
                changed = True
        if changed:
            self._maybe_follow()

    def _after_change(self) -> None:
        self._maybe_follow()

    def _maybe_follow(self) -> None:
        """内容增长时跟随到底部；用户正在往上翻看时不打扰。"""
        if self._follow:
            self.scroll_end(animate=False)

    def stick_to_bottom(self) -> None:
        """强制恢复跟随（用户按 Ctrl+End 或发送新消息时）。"""
        self._follow = True
        self.scroll_end(animate=False)
