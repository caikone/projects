# -*- coding: utf-8 -*-
"""斜杠命令补全面板。

交互（与常见 IDE 一致）：
    输入 `/`        → 自动弹出，列出全部命令
    继续输入        → 实时过滤
    ↑ / ↓           → 移动高亮
    Enter           → 执行选中的命令
    Tab             → 只把命令名补进输入框（留出写参数的位置）
    Esc             → 关闭面板（输入内容保留）
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..commands import SlashCommand, filter_commands

HINT_ACTIVE = "命令补全  ↑↓ 选择 · Enter 执行 · Tab 补全 · Esc 关闭"
HINT_EMPTY = "没有匹配的命令，Esc 关闭"


class CommandPalette(Vertical):
    """输入框上方的命令候选列表。"""

    DEFAULT_CLASSES = "palette"

    def __init__(self, max_rows: int = 8, **kwargs) -> None:
        kwargs.setdefault("id", "palette")
        super().__init__(**kwargs)
        self._max_rows = max(3, int(max_rows or 8))
        self._matches: list[SlashCommand] = []
        self._index = 0
        self._rows: list[Static] = []
        self._hint: Static | None = None
        self.add_class("-hidden")

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        self._hint = Static(HINT_ACTIVE, classes="palette-hint", markup=False)
        yield self._hint
        for _ in range(self._max_rows):
            row = Static("", classes="palette-item", markup=False)
            self._rows.append(row)
            yield row

    # ------------------------------------------------------------------
    @property
    def visible(self) -> bool:
        return not self.has_class("-hidden")

    @property
    def matches(self) -> list[SlashCommand]:
        return list(self._matches)

    @property
    def selected(self) -> SlashCommand | None:
        if not self._matches:
            return None
        if 0 <= self._index < len(self._matches):
            return self._matches[self._index]
        return self._matches[0]

    @property
    def highlight_index(self) -> int:
        return self._index

    # ------------------------------------------------------------------
    def update_query(self, text: str) -> bool:
        """按当前输入刷新候选；返回面板是否应该显示。"""
        matches = filter_commands(text)
        self._matches = matches
        self._index = 0
        self._render_rows()
        if matches:
            self.remove_class("-hidden")
        else:
            self.add_class("-hidden")
        return bool(matches)

    def move(self, delta: int) -> None:
        if not self._matches:
            return
        self._index = (self._index + delta) % len(self._matches)
        self._render_rows()

    def hide(self) -> None:
        self.add_class("-hidden")

    def show(self, text: str) -> bool:
        return self.update_query(text)

    # ------------------------------------------------------------------
    def _render_rows(self) -> None:
        if self._hint is not None:
            self._hint.update(HINT_ACTIVE if self._matches else HINT_EMPTY)
        visible = self._matches[: self._max_rows]
        for index, row in enumerate(self._rows):
            if index >= len(visible):
                row.update("")
                row.set_class(False, "-active")
                row.styles.display = "none"
                continue
            command = visible[index]
            row.styles.display = "block"
            row.update("  %-14s %s" % (command.usage, command.description))
            row.set_class(index == self._index, "-active")


__all__ = ["CommandPalette"]
