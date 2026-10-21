# -*- coding: utf-8 -*-
"""对话视图：消息列表容器，负责流式渲染节流与自动滚动。"""

from __future__ import annotations

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

    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self._timer = self.set_interval(FLUSH_INTERVAL, self.flush)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

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
        """结束流式消息（强制渲染一次）。"""
        if self._streaming is None:
            return
        self._streaming.flush(force=True)
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
        """定时器回调：把脏卡片统一渲染一次并滚到底部。"""
        changed = False
        for card in self.query(MessageCard):
            if card.flush():
                changed = True
        if changed:
            self.scroll_end(animate=False)

    def _after_change(self) -> None:
        self.scroll_end(animate=False)
