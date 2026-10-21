# -*- coding: utf-8 -*-
"""工具活动面板：实时展示模型调用了哪些工具、参数与结果。"""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

#: 面板内结果预览的最大长度
PREVIEW_MAX = 400


def _preview(text: str, limit: int = PREVIEW_MAX) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + "\n…（共 %d 字符，已截断）" % len(text)
    return text or "（无输出）"


def _format_arguments(arguments_json: str, arguments: dict) -> str:
    """把参数渲染成可读的多行文本。"""
    source = arguments if arguments else None
    if source is None:
        try:
            loaded = json.loads(arguments_json or "{}")
            source = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            source = {}
    if not source:
        return "（无参数）"
    return "\n".join("  %s = %s" % (k, v) for k, v in source.items())


class ToolCallCard(Collapsible):
    """一次工具调用的卡片（可展开查看参数与结果）。"""

    def __init__(self, name: str) -> None:
        self._detail = Static("", classes="tool-detail", markup=False)
        super().__init__(self._detail, title="%s" % name, collapsed=True)
        self.add_class("tool-card")
        self.tool_name = name
        #: 最近一次渲染的详情文本（便于测试与调试）
        self.detail_text = ""

    def set_started(self, arguments_json: str, arguments: dict, round_index: int) -> None:
        self.title = "⟳ %s" % self.tool_name
        self.add_class("tool-running")
        self.detail_text = "第 %d 轮 · 执行中\n%s" % (
            round_index, _format_arguments(arguments_json, arguments))
        self._detail.update(self.detail_text)

    def set_finished(self, ok: bool, content: str, elapsed_ms: int) -> None:
        mark = "✓" if ok else "✗"
        self.title = "%s %s  (%d ms)" % (mark, self.tool_name, elapsed_ms)
        self.remove_class("tool-running")
        self.add_class("tool-ok" if ok else "tool-fail")
        self.detail_text = _preview(content)
        self._detail.update(self.detail_text)
        if not ok:
            self.collapsed = False


class ToolPanel(VerticalScroll):
    """工具活动侧栏。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cards: dict[str, ToolCallCard] = {}
        self._empty: Static | None = None

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("工具活动", classes="panel-title")
        self._empty = Static("本轮还没有工具调用。", classes="empty-hint")
        yield self._empty

    # ------------------------------------------------------------------
    async def add_call(self, call_id: str, name: str, arguments_json: str,
                       arguments: dict, round_index: int) -> None:
        if self._empty is not None:
            await self._empty.remove()
            self._empty = None
        card = ToolCallCard(name)
        card.set_started(arguments_json, arguments, round_index)
        await self.mount(card)
        self._cards[call_id] = card
        self.scroll_end(animate=False)

    def complete_call(self, call_id: str, ok: bool, content: str, elapsed_ms: int) -> None:
        card = self._cards.get(call_id)
        if card is None:
            return
        card.set_finished(ok, content, elapsed_ms)
        self.scroll_end(animate=False)

    async def reset(self) -> None:
        """清空面板（新会话时调用）。"""
        self._cards.clear()
        await self.remove_children()
        await self.mount(Static("工具活动", classes="panel-title"))
        self._empty = Static("本轮还没有工具调用。", classes="empty-hint")
        await self.mount(self._empty)
