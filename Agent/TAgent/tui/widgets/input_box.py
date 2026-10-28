# -*- coding: utf-8 -*-
"""输入区：多行文本输入。

========================================================================
按键策略（为什么不用单纯的 Shift+Enter）
========================================================================
Textual 在 Windows 上通过 `ReadConsoleInputW` 读键（见 `textual/drivers/win32.py`），
**它只取 `uChar.UnicodeChar`，完全丢弃 `dwControlKeyState` 里的 Shift 状态**，
所以 Shift+Enter 到达应用时就是一个普通的 Enter —— 在这类终端里
单纯依赖 Shift+Enter 换行是做不到的（`<u` 的 kitty 协议只作用于输出/VT 解析，救不了这条路径）。

因此这里采用「三重保障」：

    Enter          智能回车（不依赖任何修饰键）：
                   单行            → 发送
                   多行且末行非空  → 换行
                   末行为空行      → 发送（再敲一次 Enter 即可送出）
                   光标不在末尾    → 换行
    Ctrl+J         强制换行（任何终端都能识别）
    Ctrl+S         强制发送
    Shift+Enter    换行（终端能上报修饰键时，如 Linux/macOS + kitty 协议）

粘贴的多行文本由 TextArea 的 Paste 通道直接插入，同样不会触发提交。
"""

from __future__ import annotations

from textual.binding import Binding
from textual.events import Key, Paste
from textual.message import Message
from textual.widgets import TextArea

from ..text_utils import (
    normalize_newlines,
    repair_escaped_text,
    strip_trailing_blank_lines,
)

#: 视为「插入换行」的按键
NEWLINE_KEYS = frozenset({"shift+enter", "ctrl+j"})

#: 强制发送的按键
SUBMIT_KEYS = frozenset({"ctrl+s"})

#: 输入框为空时的占位提示
PLACEHOLDER = "输入消息，Enter 发送 · Ctrl+J 换行 · Ctrl+S 立即发送"


class ChatInput(TextArea):
    """聊天输入框。"""

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
        kwargs.setdefault("placeholder", PLACEHOLDER)
        super().__init__(**kwargs)
        self._locked = False
        self._repairing = False

    # ==================================================================
    # 锁定状态（等待模型回复时不可提交）
    # ==================================================================
    @property
    def locked(self) -> bool:
        return self._locked

    def lock_input(self) -> None:
        """注意：不能叫 `lock`——`MessagePump.lock` 是 Textual 内部属性。"""
        self._locked = True
        self.add_class("input-locked")

    def unlock_input(self) -> None:
        self._locked = False
        self.remove_class("input-locked")
        self.focus()

    # ==================================================================
    # 键盘
    # ==================================================================
    async def _on_key(self, event: Key) -> None:
        key = event.key

        if key in SUBMIT_KEYS:
            event.stop()
            event.prevent_default()
            self._submit()
            return

        if key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self._insert_newline()
            return

        if key == "enter":
            event.stop()
            event.prevent_default()
            if self.should_submit_on_enter():
                self._submit()
            else:
                self._insert_newline()
            return

        await super()._on_key(event)
        # 某些终端把中文送来时是字面转义，这里统一还原（逐字符输入也能修）
        self._repair_buffer()

    async def _on_paste(self, event: Paste) -> None:
        """粘贴：归一化换行 + 还原被转义的中文。

        注意：**这里绝不能调用 `super()._on_paste(event)`**。
        Textual 8 除了 `_on_paste` 这个方法名之外，还把基类同名处理器单独注册了一遍，
        （见 `_text_area.py` 中的 `paste` 绑定链），由消息泵在**本方法之后**再调用一次。
        子类再 `super()` 一次就会把同一段文本插入两遍。

        正确做法是**原地改写 `event.text`**：本方法作为第一步先把文本洗干净，
        紧随其后的基类处理器会用洗好的文本完成唯一一次插入。
        """
        original = event.text or ""
        cleaned = normalize_newlines(repair_escaped_text(original))
        if cleaned != original:
            event.text = cleaned

    # ------------------------------------------------------------------
    @property
    def is_multiline(self) -> bool:
        """当前内容是否为多行。"""
        return "\n" in self.text

    def should_submit_on_enter(self) -> bool:
        """判断这次 Enter 应该是「发送」还是「换行」。

        规则（全部不依赖修饰键，因此在任何终端里行为一致）：
            1. 内容为空             → 发送（`_submit` 内部会忽略空内容）
            2. 光标不在文本末尾     → 换行（用户想在中间拆行）
            3. 末尾是空行           → 发送（多行时按两次 Enter 送出）
            4. 已是多行且末行非空   → 换行
            5. 单行                → 发送
        """
        text = self.text
        if not text.strip():
            return True
        if not self._cursor_at_end():
            return False
        if text.endswith("\n"):
            return True
        return not self.is_multiline

    def _cursor_at_end(self) -> bool:
        """光标是否位于文档末尾。"""
        try:
            return tuple(self.cursor_location) >= tuple(self.document.end)
        except Exception:  # pragma: no cover - 极端情况下的属性差异
            return True

    def _insert_newline(self) -> None:
        self.insert("\n")

    # ==================================================================
    # 文本清洗
    # ==================================================================
    def _repair_buffer(self) -> None:
        """把缓冲区里的字面 unicode 转义还原成真实字符（幂等）。"""
        if self._repairing:
            return
        repaired = repair_escaped_text(self.text)
        if repaired == self.text:
            return
        self._repairing = True
        try:
            self.load_text(repaired)
            try:
                self.move_cursor(self.document.end)
            except Exception:  # pragma: no cover
                pass
        finally:
            self._repairing = False

    # ==================================================================
    # 提交
    # ==================================================================
    def _submit(self) -> None:
        if self._locked:
            return
        text = strip_trailing_blank_lines(repair_escaped_text(self.text)).strip()
        if not text:
            return
        self.post_message(self.Submitted(self, text))
        self.clear()


__all__ = ["ChatInput", "NEWLINE_KEYS", "PLACEHOLDER", "SUBMIT_KEYS"]
