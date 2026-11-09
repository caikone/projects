# -*- coding: utf-8 -*-
"""状态栏：模型 / 工具数 / 运行状态 / 会话统计 / 上下文用量。"""

from __future__ import annotations

from core.domain.context import format_tokens
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
                 config_source: str = "", skills: str = "", mcp: str = "",
                 context_window: int = 0, context_is_default: bool = True,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._tool_count = tool_count
        self._config_source = config_source
        self._skills = skills
        self._mcp = mcp
        self._state_text = "空闲"
        self._state_key = "idle"
        self._turns = 0
        self._tool_calls = 0
        #: 上下文用量（used, window, percent, window_is_default）
        self._usage: tuple[int, int, int, bool] = (0, context_window, 0, context_is_default)
        self._widgets_ready = False
        self._model_label: Static | None = None
        self._state_label: Static | None = None
        self._stat_label: Static | None = None
        self._usage_label: Static | None = None

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        self._model_label = Static(self._model_text(), classes="status-model")
        self._usage_label = Static(self._usage_text(), classes="status-usage")
        self._state_label = Static(self._state_text, classes="status-state state-idle")
        self._stat_label = Static(self._stat_text(), classes="status-stat")
        yield self._model_label
        yield self._usage_label
        yield self._state_label
        yield self._stat_label
        self._widgets_ready = True

    # ------------------------------------------------------------------
    def _model_text(self) -> str:
        text = "模型 %s" % self._model if self._model else "模型 未配置"
        if self._tool_count:
            text += "  ·  %d 个工具" % self._tool_count
        if self._mcp:
            text += "  ·  %s" % self._mcp
        if self._skills:
            text += "  ·  %s" % self._skills
        return text

    def _stat_text(self) -> str:
        return "对话 %d 轮  ·  工具调用 %d 次" % (self._turns, self._tool_calls)

    def _usage_text(self) -> str:
        """上下文用量：`上下文 12k / 128k（默认） · 9%`。"""
        used, window, percent, is_default = self._usage
        if window <= 0:
            return "上下文 未知"
        window_text = format_tokens(window) + ("（默认）" if is_default else "")
        return "上下文 %s / %s · %d%%" % (format_tokens(used), window_text, percent)

    # ------------------------------------------------------------------
    def _refresh_model_label(self) -> None:
        if self._widgets_ready and self._model_label is not None:
            self._model_label.update(self._model_text())

    def set_mcp(self, text: str) -> None:
        """更新 MCP 摘要（异步连接完成后刷新）。"""
        self._mcp = text
        self._refresh_model_label()

    def set_skills(self, text: str) -> None:
        self._skills = text
        self._refresh_model_label()

    def set_tool_count(self, count: int) -> None:
        """工具数量变化（MCP 工具挂载后调用）。"""
        self._tool_count = max(0, count)
        self._refresh_model_label()

    def set_usage(self, used: int, window: int, percent: int,
                  window_is_default: bool = False) -> None:
        """更新上下文用量（状态栏中段单独一列，最显眼的位置）。"""
        self._usage = (max(0, used), max(0, window), max(0, min(100, percent)),
                       window_is_default)
        if self._widgets_ready and self._usage_label is not None:
            self._usage_label.update(self._usage_text())

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
