# -*- coding: utf-8 -*-
"""输入框上方的「思考中」提示条：转圈动画 + 当前在做的事情。

它是一个**界面层的纯表现组件**：不知道 LLM、不知道 core 事件，
只暴露 `set_busy()` / `set_idle()` / `set_hint()` 三个动作，
真正的驱动来自 `tui/bridge.py`（core 事件 → 界面动作）。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

#: 点字字符组成的帧序列（比 -\|/ 更平滑，且单宽好对齐）
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

#: 动画间隔（秒）
SPINNER_INTERVAL = 1 / 12

#: 单行输入时的按键提示
HINT_SINGLE_LINE = "Enter 发送 · Ctrl+J 换行 · Ctrl+S 立即发送"

#: 多行输入时的按键提示
HINT_MULTILINE = "多行输入：Enter 换行，末行空行 Enter 发送 · Ctrl+S 立即发送"

#: 忙碌时的默认文案
BUSY_LABEL = "思考中…"


class Spinner(Static):
    """逐帧旋转的提示符号。

    独立成组件是为了可测试：测试里直接调用 `advance()` 推进一帧，
    无需等待真实定时器。
    """

    DEFAULT_CLASSES = "spinner"

    def __init__(self, frames: tuple[str, ...] = SPINNER_FRAMES, **kwargs) -> None:
        super().__init__(frames[0], **kwargs)
        self._frames = frames
        self._index = 0
        self._timer = None

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._timer is not None

    def start(self) -> None:
        """开始动画（幂等）。"""
        if self._timer is not None:
            return
        self.remove_class("-stopped")
        self._timer = self.set_interval(SPINNER_INTERVAL, self.advance)

    def stop(self) -> None:
        """停止动画并复位。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._index = 0
        self.add_class("-stopped")
        self.update(self._frames[0])

    def advance(self) -> str:
        """推进一帧，返回当前帧字符。"""
        self._index = (self._index + 1) % len(self._frames)
        frame = self._frames[self._index]
        self.update(frame)
        return frame

    @property
    def current_frame(self) -> str:
        return self._frames[self._index]


class ThinkingBar(Horizontal):
    """输入框前方的状态提示条。

    三种状态：
        - 忙碌：转圈动画 + 「思考中… / 调用工具 xxx / 正在中止…」
        - 空闲多行：换行提示（因为 Windows 终端送不出 Shift+Enter，必须提示替代键）
        - 空闲单行：简短按键提示
    """

    DEFAULT_CLASSES = "thinking-bar"

    IDLE_HINT = HINT_SINGLE_LINE

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._busy = False
        self._label = ""
        self._hint = HINT_SINGLE_LINE
        self._spinner: Spinner | None = None
        self._text: Static | None = None

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        self._spinner = Spinner(id="think-spinner")
        self._text = Static(self._hint, id="think-text", classes="hint-idle")
        yield self._spinner
        yield self._text

    def on_mount(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
        self._refresh_text()

    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, label: str = BUSY_LABEL) -> None:
        """进入忙碌状态并开始转圈。"""
        self._busy = True
        self._label = label
        if self._spinner is not None:
            self._spinner.start()
        self.remove_class("-idle")
        self._refresh_text()

    def set_idle(self, multiline: bool = False) -> None:
        """回到空闲状态（停转并显示按键提示）。"""
        self._busy = False
        self._label = ""
        if self._spinner is not None:
            self._spinner.stop()
        self.add_class("-idle")
        self._hint = HINT_MULTILINE if multiline else HINT_SINGLE_LINE
        self._refresh_text()

    def set_label(self, label: str) -> None:
        """更新忙碌文案（例如切换到「调用工具 xxx」）。"""
        if not self._busy:
            return
        self._label = label
        self._refresh_text()

    def set_hint(self, multiline: bool) -> None:
        """空闲时根据输入框是否多行切换提示文案。"""
        if self._busy:
            return
        self._hint = HINT_MULTILINE if multiline else HINT_SINGLE_LINE
        self._refresh_text()

    # ------------------------------------------------------------------
    def _refresh_text(self) -> None:
        """刷新文案与样式。

        注意：**绝对不要**把这个方法命名成 `_render()`——
        `Widget._render()` 是 Textual 内部的渲染钩子，一旦被同名覆盖返回 None，
        容器渲染时会直接抛 `'NoneType' object has no attribute 'render_strips'`。
        """
        if self._text is None:
            return
        text = self._label if self._busy else self._hint
        self._text.update(text)
        self._text.set_class(not self._busy, "hint-idle")
        self._text.set_class(self._busy, "hint-busy")


__all__ = [
    "BUSY_LABEL",
    "HINT_MULTILINE",
    "HINT_SINGLE_LINE",
    "Spinner",
    "ThinkingBar",
]
