# -*- coding: utf-8 -*-
"""消息卡片：对话区里的一条消息（支持流式增量渲染）。"""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Markdown, Static


class MessageCard(Vertical):
    """一条消息。

    - `user` / `system`：纯文本（`Static`，不做 Markdown 解析，避免用户输入被误渲染）
    - `assistant` / `error`：Markdown 渲染（代码块高亮）

    流式更新采用「缓冲 + 节流刷新」：
    高频的文本增量只写入缓冲区，由 `flush()`（定时调用）统一渲染，
    避免每个 token 都触发一次重排。
    """

    DEFAULT_CLASSES = "card"

    def __init__(self, sender: str, kind: str = "assistant", text: str = "") -> None:
        super().__init__(classes="card-%s" % kind)
        self.sender = sender
        self.kind = kind
        self._text = text
        self._dirty = True
        self._body: Markdown | Static | None = None
        self.created_at = time.time()

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(self.sender, classes="card-sender")
        if self.kind in ("assistant", "error", "system"):
            self._body = Markdown(self._text or " ", classes="card-body")
        else:
            self._body = Static(self._text, classes="card-body", markup=False)
        yield self._body

    # ------------------------------------------------------------------
    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._dirty = True

    def append(self, delta: str) -> None:
        """追加一段流式文本（不立即渲染）。"""
        self._text += delta
        self._dirty = True

    # ------------------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self._dirty

    def flush(self, force: bool = False) -> bool:
        """把缓冲区渲染到界面。返回是否真的发生了更新。"""
        if not (self._dirty or force) or self._body is None:
            return False
        self._dirty = False
        content = self._text
        if isinstance(self._body, Markdown):
            self._body.update(content or " ")
        else:
            self._body.update(content)
        return True

    def mark_error(self) -> None:
        """把卡片切换为错误样式。"""
        self.kind = "error"
        self.add_class("card-error")
        self.remove_class("card-assistant")
