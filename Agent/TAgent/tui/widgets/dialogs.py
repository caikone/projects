# -*- coding: utf-8 -*-
"""模态对话框：历史会话列表、首次生成配置的提示。

放在独立文件里，App 只负责「弹出哪个对话框 / 拿到什么结果」，
不掺进布局与渲染细节。
"""

from __future__ import annotations

from core.domain.sessions import SessionSummary

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option


class ResumeScreen(ModalScreen[str | None]):
    """历史会话列表面板。

    返回被选中的 session id；用户按 Esc 或点「取消」时返回 None。
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("q", "cancel", "取消", show=False),
    ]

    def __init__(self, summaries: list[SessionSummary],
                 current_id: str = "") -> None:
        super().__init__()
        self._summaries = list(summaries)
        self._current_id = current_id

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Container(classes="dialog"):
            yield Static("恢复历史会话", classes="dialog-title")
            yield Static("↑↓ 选择 · Enter 恢复 · Esc 取消", classes="dialog-hint")
            if self._summaries:
                options = [self._option(summary) for summary in self._summaries]
                yield OptionList(*options, id="session-list")
            else:
                yield Static("还没有保存过会话。先聊几句，然后 /save 或直接 /new 试试。",
                             classes="empty-hint")
            with Horizontal(classes="dialog-buttons"):
                yield Button("取消", id="cancel", variant="default")

    def on_mount(self) -> None:
        if not self._summaries:
            self.query_one("#cancel", Button).focus()
            return
        listing = self.query_one("#session-list", OptionList)
        listing.focus()
        for index, summary in enumerate(self._summaries):
            if summary.id == self._current_id:
                listing.highlighted = index
                break

    # ------------------------------------------------------------------
    def _option(self, summary: SessionSummary) -> Option:
        marker = "▶ " if summary.id == self._current_id else "  "
        text = Text(marker)
        text.append(summary.updated_text, style="bold cyan")
        text.append("  ")
        text.append(summary.display_title, style="white")
        text.append("  (%d 条)" % summary.message_count, style="dim")
        return Option(text, id=summary.id)

    # ------------------------------------------------------------------
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id if event.option else None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfigNoticeScreen(ModalScreen[str]):
    """首次自动生成 config.ini 后的提示框。

    返回 "quit"（退出以便编辑配置）或 "keep"（本次先看看）。
    """

    BINDINGS = [
        Binding("escape", "keep", "先看看"),
        Binding("ctrl+q", "quit_app", "退出"),
    ]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self._path = config_path

    def compose(self) -> ComposeResult:
        with Container(classes="dialog dialog-wide"):
            yield Static("已生成配置文件，需要修改后才能正常使用", classes="dialog-title")
            yield Static(
                "当前目录没有 config.ini，已为你生成一份完整模板：\n\n"
                "  %s\n\n"
                "[llm] 段里是占位（mock）信息，直接对话会连接失败。\n"
                "请把 base_url / api_key / model 改成你自己的服务，然后重新启动 TAgent。\n\n"
                "其它段（界面、会话、MCP、上下文、命令执行、网页抓取）都已经写好默认值，"
                "需要时直接改即可。" % self._path,
                classes="dialog-body", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("退出并修改（推荐）", id="quit", variant="primary")
                yield Button("先看看", id="keep", variant="default")

    def on_mount(self) -> None:
        self.query_one("#quit", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss("quit" if event.button.id == "quit" else "keep")

    def action_keep(self) -> None:
        self.dismiss("keep")

    def action_quit_app(self) -> None:
        self.dismiss("quit")


__all__ = ["ConfigNoticeScreen", "ResumeScreen"]
