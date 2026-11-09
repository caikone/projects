# -*- coding: utf-8 -*-
"""左侧边栏：MCP 服务器与它们的工具、当前会话信息。

为什么单独一块：
    MCP 是「外部能力」，连接是异步的（可能几秒），失败也是常态。
    用户需要一眼看到「到底连上了哪些服务器、能用哪些工具」，
    而不是等模型调错工具才发现某个服务器没连上。
"""

from __future__ import annotations

from core.domain.mcp import McpSnapshot

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

#: 单台服务器在边栏里最多列出多少个工具（其余折叠成一句统计）
TOOL_DISPLAY_LIMIT = 12


class Sidebar(VerticalScroll):
    """侧栏：MCP + 会话。"""

    def __init__(self, tool_limit: int = TOOL_DISPLAY_LIMIT, **kwargs) -> None:
        super().__init__(**kwargs)
        #: 单台服务器最多列出多少个工具（其余折叠成一句统计）
        self._tool_limit = max(1, int(tool_limit or TOOL_DISPLAY_LIMIT))
        self._mcp_box: Vertical | None = None
        self._session_label: Static | None = None
        self._loading_text = ""

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("MCP 服务器", classes="panel-title")
        self._mcp_box = Vertical(id="sidebar-mcp", classes="sidebar-section")
        yield self._mcp_box
        yield Static("当前会话", classes="panel-title")
        self._session_label = Static("（未开始）", classes="sidebar-body", markup=False)
        yield self._session_label

    def on_mount(self) -> None:
        if self._loading_text:
            self.run_worker(self.set_loading(self._loading_text), exclusive=False)

    # ------------------------------------------------------------------
    async def set_loading(self, text: str = "正在连接 MCP 服务器…") -> None:
        """显示「加载中」占位（TUI 已经可用，MCP 在后台连接）。"""
        self._loading_text = text
        if self._mcp_box is None:
            return
        await self._mcp_box.remove_children()
        await self._mcp_box.mount(Static(text, classes="sidebar-hint"))
        await self._mcp_box.mount(Static("（连接结果返回后会自动刷新）",
                                        classes="sidebar-hint"))

    async def update_mcp(self, snapshot: McpSnapshot) -> None:
        """按快照重建 MCP 区域。"""
        if self._mcp_box is None:
            return
        await self._mcp_box.remove_children()

        if snapshot.config_error:
            await self._mcp_box.mount(
                Static("✗ " + snapshot.config_error, classes="sidebar-error"))
            return

        if not snapshot.servers:
            text = ("未配置 MCP（无 .mcp.json）"
                    if snapshot.loaded else "正在连接 MCP 服务器…")
            await self._mcp_box.mount(Static(text, classes="sidebar-hint"))
            return

        for server in snapshot.servers:
            if server.disabled:
                mark, css = "○", "sidebar-muted"
            elif server.connected:
                mark, css = "●", "sidebar-ok"
            else:
                mark, css = "✗", "sidebar-error"
            title = "%s %s  (%d 个工具)" % (mark, server.name, server.tool_count)
            await self._mcp_box.mount(Static(title, classes="sidebar-server " + css,
                                             markup=False))
            detail = server.server_info or server.endpoint
            await self._mcp_box.mount(
                Static("    " + detail, classes="sidebar-meta", markup=False))

            if server.connected and server.tools:
                shown = server.tools[:self._tool_limit]
                for name in shown:
                    await self._mcp_box.mount(
                        Static("    · " + name, classes="sidebar-tool", markup=False))
                if len(server.tools) > len(shown):
                    await self._mcp_box.mount(Static(
                        "    … 另有 %d 个工具" % (len(server.tools) - len(shown)),
                        classes="sidebar-meta", markup=False))
            elif not server.connected and server.error:
                await self._mcp_box.mount(
                    Static("    " + _short(server.error, 200),
                           classes="sidebar-meta", markup=False))

        if snapshot.connected_count or snapshot.total_tools:
            await self._mcp_box.mount(Static(
                "共 %d/%d 台已连接 · %d 个工具" % (
                    snapshot.connected_count, snapshot.total_count, snapshot.total_tools),
                classes="sidebar-meta", markup=False))

    def update_session(self, session_id: str, title: str, message_count: int) -> None:
        if self._session_label is None:
            return
        text = "%s\n%d 条消息" % (session_id, message_count)
        if title:
            text = "%s\n%s" % (text, _short(title, 40))
        self._session_label.update(text)


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["Sidebar", "TOOL_DISPLAY_LIMIT"]
