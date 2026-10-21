# -*- coding: utf-8 -*-
"""状态栏：模型 / 工具数 / 运行状态 / 会话统计。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

STATE_STYLE = {
    "idle": "state-idle",
    "thinking": "state-thinking",
    "tool": "state-tool",
    "error": "state-error",
    "done": "state-idle",
}


class StatusBar(Horizontal):
    """顶部状态条。"""

    def __init__(self, model: str = "", tool_count: int = 0,
                 config_source: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._tool_count = tool_count
        self._config_source = config_source
        self._state_text = "空闲"
        self._state_key = "idle"
        self._turns = 0
        self._tool_calls = 0
        self._widgets_ready = False
        self._model_label: Static | None = None
        self._state_label: Static | None = None
        self._stat_label: Static | None = None

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        self._model_label = Static(self._model_text(), classes="status-model")
        self._state_label = Static(self._state_text, classes="status-state state-idle")
        self._stat_label = Static(self._stat_text(), classes="status-stat")
        yield self._model_label
        yield self._state_label
        yield self._stat_label
        self._widgets_ready = True

    # ------------------------------------------------------------------
    def _model_text(self) -> str:
        text = "模型 %s" % self._model if self._model else "模型 未配置"
        if self._tool_count:
            text += "  ·  %d 个工具" % self._tool_count
        return text

    def _stat_text(self) -> str:
        return "对话 %d 轮  ·  工具调用 %d 次" % (self._turns, self._tool_calls)

    # ------------------------------------------------------------------
    def set_state(self, text: str, key: str = "idle") -> None:
        """更新运行状态。"""
        self._state_text = text
        old_class = STATE_STYLE.get(self._state_key, "state-idle")
        new_class = STATE_STYLE.get(key, "state-idle")
        self._state_key = key
        if self._widgets_ready and self._state_label is not None:
            self._state_label.update(text)
            self._state_label.remove_class(old_class)
            self._state_label.add_class(new_class)

    def set_turns(self, turns: int, tool_calls: int) -> None:
        self._turns = turns
        self._tool_calls = tool_calls
        if self._widgets_ready and self._stat_label is not None:
            self._stat_label.update(self._stat_text())

    def bump_tool_calls(self, count: int = 1) -> None:
        self.set_turns(self._turns, self._tool_calls + count)
