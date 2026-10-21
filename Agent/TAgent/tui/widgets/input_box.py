# -*- coding: utf-8 -*-
"""输入区：多行文本输入（Enter 发送 / Shift+Enter 换行）。"""

from __future__ import annotations

from textual.binding import Binding
from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea


class ChatInput(TextArea):
    """聊天输入框。

    TextArea 自己接管了 Enter（插入换行），因此这里重写 `_on_key`：
        - Enter         → 提交
        - Shift+Enter   → 插入换行
        - 其余按键      → 交回 TextArea 默认处理
    """

    BINDINGS = [
        Binding("ctrl+s", "submit", "发送", show=False),
    ]

    class Submitted(Message):
        """用户提交了一段文本。"""

        def __init__(self, sender: "ChatInput", text: str) -> None:
            super().__init__()
            self.text = text
            self.input = sender

        @property
        def control(self) -> "ChatInput":
            return self.input

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("classes", "chat-input")
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("tab_behavior", "indent")
        super().__init__(**kwargs)
        self._locked = False

    # ------------------------------------------------------------------
    @property
    def locked(self) -> bool:
        """是否处于「等待模型回复」的锁定状态。"""
        return self._locked

    def lock_input(self) -> None:
        """注意：不能叫 `lock`——`MessagePump.lock` 是 Textual 内部属性。"""
        self._locked = True
        self.add_class("input-locked")

    def unlock_input(self) -> None:
        self._locked = False
        self.remove_class("input-locked")
        self.focus()

    # ------------------------------------------------------------------
    async def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self._submit()
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    # ------------------------------------------------------------------
    def _submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        if self._locked:
            return
        self.post_message(self.Submitted(self, text))
        self.clear()
