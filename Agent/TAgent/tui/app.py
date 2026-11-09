# -*- coding: utf-8 -*-
"""TAgent TUI 主应用（Textual App）。

职责边界非常明确：
    - 只做「布局 + 事件分发 + 键盘绑定 + 斜杠命令」；
    - 所有业务能力来自 `core`（通过 `FrontendContext` 注入）；
    - 真正的对话在**后台工作线程**里跑，事件经由线程安全队列回流到 UI 线程，
      因此流式输出再密集也不会卡住界面。
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque

from core.domain.agents import looks_like_agents_message
from core.domain.context import format_tokens
from core.domain.events import (
    ContextCompacted,
    ContextCompactFailed,
    ContextCompactStarted,
    ContextUsageUpdated,
    TurnCompleted,
)
from core.domain.mcp import McpSnapshot
from core.domain.messages import Role
from core.errors import TAgentError
from core.ports.frontend import FrontendContext
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from . import commands as slash
from .bridge import AgentEventBridge
from .widgets import (
    ChatInput,
    ChatView,
    CommandPalette,
    Sidebar,
    StatusBar,
    ThinkingBar,
    ToolPanel,
)
from .widgets.dialogs import ConfigNoticeScreen, ResumeScreen


@dataclass
class McpLoaded:
    """后台 MCP 连接完成（只在本层流转，不进 core 事件体系）。"""

    snapshot: McpSnapshot


class TAgentApp(App[None]):
    """TAgent 终端界面。"""

    CSS_PATH = "styles.tcss"
    TITLE = "TAgent"
    SUB_TITLE = "终端智能助手"
    #: 关掉 Textual 自带的 Ctrl+P 命令面板：我们有更贴合业务的斜杠命令补全
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+n", "new_session", "新会话"),
        Binding("ctrl+r", "resume_session", "历史会话"),
        Binding("ctrl+x", "cancel_turn", "中止本轮"),
        Binding("ctrl+t", "toggle_tools", "工具面板"),
        Binding("ctrl+b", "toggle_sidebar", "边栏"),
        Binding("ctrl+k", "clear_tools", "清空工具面板"),
        # 对话区翻页：输入框有焦点时也要生效，所以用 priority
        Binding("pageup", "scroll_chat_up", "上翻", priority=True, show=False),
        Binding("pagedown", "scroll_chat_down", "下翻", priority=True, show=False),
        Binding("ctrl+end", "scroll_chat_bottom", "跳到最新", priority=True, show=False),
    ]

    def __init__(self, ctx: FrontendContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._session = ctx.session
        self._agent = ctx.agent
        self._sessions = ctx.sessions
        self._ui = ctx.settings.ui
        self._busy = False
        self._queue: Deque[object] = deque()
        self._queue_lock = threading.Lock()
        self._bridge: AgentEventBridge | None = None
        self._multiline_input = False
        #: 正在执行 /compact（期间禁止普通输入与工具调用）
        self._compacting = False
        #: 上下文达到阈值后暂停普通输入，直到压缩成功
        self._context_blocked = False
        #: 填充命令名时不要重新弹出补全面板
        self._suppress_palette = False
        #: 后台 MCP 是否还在加载
        self.mcp_loading = bool(ctx.metadata.get("mcp_loading"))
        # 供界面展示的元信息
        self.config_source = str(ctx.metadata.get("config_source", ""))
        self.config_created = bool(ctx.metadata.get("config_created"))
        self.skills_summary = str(ctx.metadata.get("skills", ""))
        self.agents_doc_summary = str(ctx.metadata.get("agents_doc", ""))
        self.mcp_summary = str(ctx.metadata.get("mcp", ""))
        raw_snapshot = ctx.metadata.get("mcp_snapshot")
        self.mcp_snapshot: McpSnapshot | None = (
            raw_snapshot if isinstance(raw_snapshot, McpSnapshot) else None)
        #: MCP 连接失败等需要提醒用户的信息（启动时在对话区展示）
        raw_notices = ctx.metadata.get("mcp_notices") or []
        self.mcp_notices = [str(item) for item in raw_notices if str(item).strip()]

    # ==================================================================
    # 布局
    # ==================================================================
    def compose(self) -> ComposeResult:
        context = self.ctx.context
        yield Header(show_clock=True)
        yield StatusBar(
            model=self.ctx.settings.llm.model,
            tool_count=len(self.ctx.tools.all()),
            config_source=self.config_source,
            skills=self.skills_summary,
            mcp=self.mcp_summary or ("MCP 加载中…" if self.mcp_loading else ""),
            context_window=context.window_tokens if context else 0,
            context_is_default=context.window_is_default if context else True,
            id="status",
        )
        with Horizontal(id="body"):
            yield Sidebar(tool_limit=self._ui.sidebar_tool_limit, id="sidebar")
            yield ChatView(id="chat",
                           flush_interval=self._ui.stream_flush_interval)
            yield ToolPanel(preview_chars=self._ui.tool_preview_chars, id="tools")
        yield ThinkingBar(spinner_interval=self._ui.spinner_interval, id="thinking")
        yield CommandPalette(max_rows=self._ui.command_panel_rows, id="palette")
        yield ChatInput(id="input")
        yield Footer()

    async def on_mount(self) -> None:
        chat = self.query_one(ChatView)
        tools = self.query_one(ToolPanel)
        status = self.query_one(StatusBar)
        thinking = self.query_one(ThinkingBar)
        sidebar = self.query_one(Sidebar)
        self._bridge = AgentEventBridge(chat=chat, tools=tools, status=status,
                                        thinking=thinking)

        # 宽度来自 [ui] 段，不同终端尺寸都能调到顺手
        sidebar.styles.width = max(16, self._ui.sidebar_width)
        tools.styles.width = max(24, self._ui.tool_panel_width)
        self.query_one(ChatInput).styles.height = max(3, self._ui.input_height)

        # 事件回流泵：把工作线程产出的事件搬到 UI 线程
        self.set_interval(self._ui.event_drain_interval, self._drain_events)

        # 命令补全的按键拦截器（面板不可见时直接放行）
        self.query_one(ChatInput).set_navigator(self._handle_palette_key)

        await chat.clear_messages()
        status.set_state("空闲", "idle")
        thinking.set_idle(multiline=False)
        self._refresh_session_view()

        # MCP 出问题时立刻让用户看到原因，而不是等到调用工具才失败
        for notice in self.mcp_notices:
            await chat.add_notice(notice)
        if self._session.agents_doc:
            await chat.add_notice(
                "已把 %s 作为项目约定注入会话（%s）。"
                % (self.ctx.settings.agents_doc.filename,
                   "后续轮次直接复用，不再重复消耗上下文"))
        if self._session.messages:
            await self._render_session_history()

        # MCP：先出界面，再后台连接
        if self.mcp_loading:
            await sidebar.set_loading()
            self._load_mcp()
        elif self.mcp_snapshot is not None:
            await sidebar.update_mcp(self.mcp_snapshot)

        if self.config_created:
            self.set_timer(0.4, self._show_config_notice)

        self.query_one(ChatInput).focus()

        prompt = self.ctx.initial_prompt
        # 配置刚生成时 [llm] 还是占位值，先让用户改配置，不要拿它去发请求
        if prompt and not self.config_created:
            async def kick_off() -> None:
                await self._submit(prompt)

            self.set_timer(0.3, kick_off)

    # ==================================================================
    # 用户输入
    # ==================================================================
    async def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        await self._submit(message.text)

    async def _submit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        # 以 / 开头的是本地命令，不发给模型
        if text.startswith("/"):
            self.query_one(CommandPalette).hide()
            await self._handle_command(text)
            return

        chat_view = self.query_one(ChatView)
        if self._compacting:
            await chat_view.add_notice("正在压缩上下文，请稍候（压缩期间不接受新的任务）。")
            return
        if self._context_blocked:
            await chat_view.add_notice(
                "上下文已达到压缩阈值，普通输入已暂停。请先执行 /compact 压缩上下文。")
            return
        if self._busy:
            await chat_view.add_notice(
                "上一轮还没结束，按 Ctrl+X 可中止，或稍后再发送。")
            return

        self._busy = True
        chat_input = self.query_one(ChatInput)
        chat_input.lock_input()
        chat_view.stick_to_bottom()
        self.query_one(StatusBar).set_state("已发送，等待模型…", "thinking")
        self.query_one(ThinkingBar).set_busy("已发送，等待模型…")
        self._multiline_input = False
        self._run_turn(text)

    async def on_text_area_changed(self, event: object) -> None:
        """输入框内容变化：更新按键提示 + 刷新命令补全。"""
        chat_input = self.query_one(ChatInput)
        source = getattr(event, "text_area", None) or getattr(event, "input", None)
        if source is not None and source is not chat_input:
            return

        multiline = chat_input.is_multiline
        if multiline != self._multiline_input:
            self._multiline_input = multiline
            self.query_one(ThinkingBar).set_hint(multiline)

        if self._suppress_palette:
            # 只吞掉「刚填充命令名」引发的那一次变化
            self._suppress_palette = False
            palette = self.query_one(CommandPalette)
            palette.hide()
            return
        palette = self.query_one(CommandPalette)
        if chat_input.text.startswith("/") and not chat_input.locked:
            palette.update_query(chat_input.text)
        else:
            palette.hide()

    # ==================================================================
    # 命令补全面板
    # ==================================================================
    async def _handle_palette_key(self, key: str) -> bool:
        """返回 True 表示这个按键被补全面板消费了。"""
        palette = self.query_one(CommandPalette)
        if not palette.visible:
            return False

        if key == "up":
            palette.move(-1)
            return True
        if key == "down":
            palette.move(1)
            return True
        if key == "escape":
            palette.hide()
            return True
        if key == "tab":
            command = palette.selected
            if command is not None:
                self._fill_command(command, submit=False)
            return True
        if key == "enter":
            command = palette.selected
            if command is not None:
                self._fill_command(command, submit=not command.takes_args)
            return True
        return False

    def _fill_command(self, command: slash.SlashCommand, submit: bool) -> None:
        """把命令名填进输入框；无参数命令直接执行。"""
        palette = self.query_one(CommandPalette)
        palette.hide()
        chat_input = self.query_one(ChatInput)
        if submit:
            chat_input.clear()
            self.run_worker(self._submit(command.name), exclusive=False)
            return

        text = command.name + (" " if command.takes_args else "")
        self._suppress_palette = text != chat_input.text
        chat_input.load_text(text)
        chat_input.move_cursor(chat_input.document.end)
        chat_input.focus()

    # ==================================================================
    # 本地命令
    # ==================================================================
    async def _handle_command(self, text: str) -> None:
        """处理以 `/` 开头的本地命令（不进入模型上下文）。"""
        name, args = slash.split_command(text)
        command = slash.resolve(name)
        chat = self.query_one(ChatView)
        if command is None:
            await chat.add_notice("未知命令「%s」。输入 / 查看命令列表。" % name)
            return

        handlers = {
            "/help": self._cmd_help,
            "/new": self._cmd_new,
            "/resume": self._cmd_resume,
            "/save": self._cmd_save,
            "/sessions": self._cmd_sessions,
            "/compact": self._cmd_compact,
            "/clear": self._cmd_clear,
            "/context": self._cmd_context,
            "/tools": self._cmd_tools,
            "/mcp": self._cmd_mcp,
            "/skills": self._cmd_skills,
            "/model": self._cmd_model,
            "/config": self._cmd_config,
            "/quit": self._cmd_quit,
        }
        await handlers[command.name](args)

    # ---------------- 各命令实现 ----------------
    async def _cmd_help(self, _args: str) -> None:
        await self.query_one(ChatView).add_notice(slash.help_text())

    async def _cmd_new(self, _args: str) -> None:
        await self._new_session()

    async def _cmd_resume(self, _args: str) -> None:
        self._open_resume()

    async def _cmd_save(self, _args: str) -> None:
        chat = self.query_one(ChatView)
        if self._sessions is None or not self._sessions.enabled:
            await chat.add_notice("当前未启用会话存档，无法保存。")
            return
        if not any(m.role is not Role.SYSTEM for m in self._session.messages):
            await chat.add_notice("当前会话还没有内容，先聊几句再保存吧。")
            return
        try:
            summary = self._sessions.save(self._session)
        except TAgentError as exc:
            await chat.add_error(exc.message)
            return
        self._refresh_session_view()
        await chat.add_notice(
            "会话已保存：%s\n文件：%s" % (summary.display_title, summary.path))

    async def _cmd_sessions(self, _args: str) -> None:
        chat = self.query_one(ChatView)
        if self._sessions is None or not self._sessions.enabled:
            await chat.add_notice("当前未启用会话存档。")
            return
        summaries = self._sessions.list_summaries()
        if not summaries:
            await chat.add_notice("还没有保存过会话。可用 /save 保存当前会话。")
            return
        lines = ["历史会话（用 /resume 打开列表并恢复）："]
        for index, item in enumerate(summaries, start=1):
            marker = "▶" if item.id == self._session.id else " "
            lines.append("%s %2d. %s  %s  (%d 条)" % (
                marker, index, item.updated_text, item.display_title,
                item.message_count))
        await chat.add_notice("\n".join(lines))

    async def _cmd_compact(self, _args: str) -> None:
        await self._start_compact()

    async def _cmd_clear(self, _args: str) -> None:
        await self._clear_history()

    async def _cmd_context(self, _args: str) -> None:
        chat = self.query_one(ChatView)
        context = self.ctx.context
        if context is None:
            await chat.add_notice("当前未启用上下文压缩功能。")
            return
        snapshot = context.usage(self._session, self._agent.tool_specs())
        window = format_tokens(snapshot.window_tokens) + (
            "（默认）" if snapshot.window_is_default else "")
        store = getattr(context, "store", None)
        store_text = ""
        if store is not None and hasattr(store, "describe"):
            try:
                store_text = "\n归档：%s" % store.describe()
            except TAgentError:
                store_text = ""
        await chat.add_notice(
            "上下文：%s / %s · %d%%（%s）\n阈值：%d%%%s" % (
                format_tokens(snapshot.used_tokens), window, snapshot.percent,
                "API 精确值" if snapshot.from_api else "本地估算",
                round(context.threshold * 100), store_text))

    async def _cmd_tools(self, _args: str) -> None:
        tools = self.ctx.tools.all()
        lines = ["当前可用工具（%d 个）：" % len(tools)]
        for tool in sorted(tools, key=lambda item: item.spec.name):
            description = " ".join((tool.spec.description or "").split())
            if len(description) > 60:
                description = description[:59] + "…"
            lines.append("  %-28s %s" % (tool.spec.name, description))
        await self.query_one(ChatView).add_notice("\n".join(lines))

    async def _cmd_mcp(self, _args: str) -> None:
        chat = self.query_one(ChatView)
        snapshot = self._collect_mcp_snapshot()
        if snapshot is None:
            await chat.add_notice("当前未启用 MCP。")
            return
        if self.mcp_loading:
            await chat.add_notice("MCP 仍在后台连接中，稍后会自动刷新边栏。")
            return
        if snapshot.config_error:
            await chat.add_error(snapshot.config_error)
            return
        if not snapshot.servers:
            await chat.add_notice("没有配置任何 MCP 服务器（.mcp.json）。")
            return
        lines = ["MCP 服务器（%d/%d 已连接 · %d 个工具）：" % (
            snapshot.connected_count, snapshot.total_count, snapshot.total_tools)]
        for server in snapshot.servers:
            lines.append("  %s  [%s]" % (server.name, server.status_text))
            if server.tools:
                for name in server.tools:
                    lines.append("      · %s" % name)
            elif server.error:
                lines.append("      %s" % server.error)
        await chat.add_notice("\n".join(lines))

    async def _cmd_skills(self, _args: str) -> None:
        chat = self.query_one(ChatView)
        skills = self.ctx.skills
        if skills is None or not hasattr(skills, "catalog"):
            await chat.add_notice("当前未启用 Skill。")
            return
        catalog = skills.catalog
        if catalog.empty:
            await chat.add_notice("未发现 Skill（目录：%s）。" % skills.root)
            return
        lines = ["已发现 %d 个 Skill（%s）：" % (len(catalog.skills), skills.root)]
        for skill in catalog.skills:
            lines.append("  %-24s %s" % (skill.name, skill.description))
        await chat.add_notice("\n".join(lines))

    async def _cmd_model(self, _args: str) -> None:
        llm = self.ctx.settings.llm
        await self.query_one(ChatView).add_notice(
            "模型：%s\n接口：%s\n密钥：%s\n超时：%.0fs · max_tokens：%s · temperature：%s"
            % (llm.model, llm.chat_completions_url, llm.masked_api_key,
               llm.timeout, llm.max_tokens or "默认", llm.temperature))

    async def _cmd_config(self, _args: str) -> None:
        settings = self.ctx.settings
        await self.query_one(ChatView).add_notice(
            "配置文件：%s%s\n"
            "界面：事件回流 %dms · 流式重绘 %dms · 边栏 %d 列\n"
            "会话：%s（自动保存：%s）\n"
            "AGENTS.md：%s\n"
            "MCP：%s\n"
            "上下文：窗口 %s · 阈值 %d%%"
            % (self.config_source,
               "（本次自动生成，需修改后重启）" if self.config_created else "",
               settings.ui.event_drain_interval_ms,
               settings.ui.stream_flush_interval_ms,
               settings.ui.sidebar_width,
               self._sessions.directory if self._sessions else "未启用",
               "开" if (self._sessions and self._sessions.autosave) else "关",
               self.agents_doc_summary or "未启用",
               self.mcp_summary or ("加载中…" if self.mcp_loading else "未配置"),
               format_tokens(settings.context_window) if settings.context_window else "默认 128k",
               round(settings.compact_threshold * 100)))

    async def _cmd_quit(self, _args: str) -> None:
        self._save_session(silent=True)
        self.exit(0)

    # ==================================================================
    # 会话：新建 / 保存 / 恢复
    # ==================================================================
    def _save_session(self, silent: bool = False):
        """保存当前会话；失败时（非静默）在对话区提示。"""
        if self._sessions is None or not self._sessions.enabled:
            return None
        try:
            return self._sessions.autosave_session(self._session)
        except TAgentError as exc:  # 理论上 autosave 已吞异常，这里兜底
            if not silent:
                self.run_worker(self.query_one(ChatView).add_error(exc.message),
                                exclusive=False)
            return None

    def _refresh_session_view(self) -> None:
        """刷新状态栏 / 边栏里的会话信息。"""
        sidebar = self.query_one(Sidebar)
        sidebar.update_session(
            self._session.id,
            self._session.title,
            len([m for m in self._session.messages if m.role is not Role.SYSTEM]),
        )

    async def _new_session(self) -> None:
        """新建会话：先自动保存当前会话，再切换到空白会话。"""
        chat = self.query_one(ChatView)
        if self._busy or self._compacting:
            await chat.add_notice("请等待当前任务结束（或按 Ctrl+X 中止）后再新建会话。")
            return

        summary = None
        if self._sessions is not None and self._sessions.enabled:
            summary = self._save_session(silent=True)
            if summary is not None:
                self._sessions.record_event(self._session.id, "superseded_by_new_session", {})

        factory = self.ctx.new_session
        if factory is None:  # pragma: no cover - 容器总会提供
            return
        self._session = factory()
        self._busy = False
        self._context_blocked = False

        tools = self.query_one(ToolPanel)
        status = self.query_one(StatusBar)
        await chat.clear_messages()
        await tools.reset()
        status.set_turns(0, 0)
        if self._bridge is not None:
            self._bridge.reset_counters()
        context = self.ctx.context
        if context is not None:
            status.set_usage(0, context.window_tokens, 0, context.window_is_default)
        status.set_state("新会话", "idle")
        chat_input = self.query_one(ChatInput)
        chat_input.unlock_input()
        self._multiline_input = False
        self.query_one(ThinkingBar).set_idle(multiline=chat_input.is_multiline)
        self._refresh_session_view()

        if summary is not None:
            await chat.add_notice("上一段会话已保存：%s" % summary.path)
        if self._session.agents_doc:
            await chat.add_notice("已在新会话中注入项目约定 %s。"
                                  % self.ctx.settings.agents_doc.filename)

    def _open_resume(self) -> None:
        chat = self.query_one(ChatView)
        if self._busy or self._compacting:
            self.run_worker(
                chat.add_notice("请等待当前任务结束（或按 Ctrl+X 中止）后再恢复会话。"),
                exclusive=False)
            return
        if self._sessions is None or not self._sessions.enabled:
            self.run_worker(chat.add_notice("当前未启用会话存档。"), exclusive=False)
            return
        summaries = self._sessions.list_summaries()
        self.push_screen(
            ResumeScreen(summaries, current_id=self._session.id),
            self._on_resume_selected,
        )

    def _on_resume_selected(self, session_id: str | None) -> None:
        if not session_id:
            return
        self.run_worker(self._restore_session(session_id), exclusive=False)

    async def _restore_session(self, session_id: str) -> None:
        """恢复历史会话并把它渲染回对话区。"""
        chat = self.query_one(ChatView)
        if self._sessions is None:  # pragma: no cover - 调用方已检查
            return
        try:
            session = self._sessions.load(session_id)
        except TAgentError as exc:
            await chat.add_error(exc.message)
            return

        # 切换前保存当前会话（避免误操作丢历史）
        self._save_session(silent=True)

        prepare = self.ctx.prepare_session
        if prepare is not None:
            session = prepare(session)
        self._session = session
        self._busy = False
        self._context_blocked = False

        await chat.clear_messages()
        await self.query_one(ToolPanel).reset()
        await self._render_session_history()
        status = self.query_one(StatusBar)
        status.set_turns(0, 0)
        if self._bridge is not None:
            self._bridge.reset_counters()
        status.set_state("已恢复会话", "idle")
        context = self.ctx.context
        if context is not None:
            snapshot = context.usage(self._session, self._agent.tool_specs())
            status.set_usage(snapshot.used_tokens, snapshot.window_tokens,
                             snapshot.percent, snapshot.window_is_default)
        chat_input = self.query_one(ChatInput)
        chat_input.unlock_input()
        self.query_one(ThinkingBar).set_idle(multiline=chat_input.is_multiline)
        self._refresh_session_view()
        self._sessions.record_event(session.id, "resumed", {})
        await chat.add_notice(
            "已恢复会话：%s（%d 条消息）" % (
                session.title or session.id, len(session.messages)))

    async def _render_session_history(self) -> None:
        """把会话历史渲染成消息卡片（工具内部消息不展示）。"""
        chat = self.query_one(ChatView)
        filename = self.ctx.settings.agents_doc.filename
        for message in self._session.messages:
            if message.role is Role.SYSTEM:
                continue
            if message.role is Role.TOOL:
                continue
            text = message.content or ""
            if not text.strip():
                continue
            if message.role is Role.USER:
                if looks_like_agents_message(text, filename):
                    await chat.add_notice("（已恢复项目约定 %s 的注入内容）" % filename)
                    continue
                await chat.add_message("你", text, kind="user")
            else:
                await chat.add_message("助手", text, kind="assistant")

    # ==================================================================
    # 压缩
    # ==================================================================
    async def _start_compact(self) -> None:
        """启动一次上下文压缩（在后台线程执行，期间禁止其它输入）。"""
        context = self.ctx.context
        if context is None:
            await self.query_one(ChatView).add_notice("当前未启用上下文压缩功能。")
            return
        if self._compacting:
            await self.query_one(ChatView).add_notice("压缩已在进行中，请稍候。")
            return
        if self._busy:
            await self.query_one(ChatView).add_notice(
                "请等待当前回复结束（或按 Ctrl+X 中止）后再压缩上下文。")
            return

        self._compacting = True
        chat = self.query_one(ChatView)
        self.query_one(ChatInput).lock_input()
        self.query_one(StatusBar).set_state("正在压缩上下文…", "thinking")
        self.query_one(ThinkingBar).set_busy("正在压缩上下文…")
        await chat.add_notice(
            "开始压缩上下文：先把原始记录归档到本地，再由模型生成摘要（可能需要一会儿）。")
        self._run_compact()

    async def _clear_history(self) -> None:
        """清空对话历史，但保留会话本身与项目约定。"""
        chat = self.query_one(ChatView)
        if self._busy or self._compacting:
            await chat.add_notice("请等待当前任务结束（或按 Ctrl+X 中止）后再清空历史。")
            return
        self._session.messages = [m for m in self._session.messages
                                  if m.role is Role.SYSTEM]
        self._session.title = ""
        self._session.agents_doc = ""
        prepare = self.ctx.prepare_session
        if prepare is not None:
            prepare(self._session)
        await chat.clear_messages()
        await self.query_one(ToolPanel).reset()
        self.query_one(StatusBar).set_turns(0, 0)
        if self._bridge is not None:
            self._bridge.reset_counters()
        context = self.ctx.context
        if context is not None:
            snapshot = context.usage(self._session, self._agent.tool_specs())
            self.query_one(StatusBar).set_usage(
                snapshot.used_tokens, snapshot.window_tokens,
                snapshot.percent, snapshot.window_is_default)
        self._context_blocked = False
        self._refresh_session_view()
        await chat.add_notice("已清空对话历史（会话 ID 与项目约定保留）。")

    # ==================================================================
    # 后台执行（工作线程）
    # ==================================================================
    @work(thread=True, exclusive=True, group="agent")
    def _run_turn(self, text: str) -> None:
        """在后台线程驱动核心对话，事件入队（绝不在此触碰任何控件）。"""
        try:
            for event in self._agent.stream_turn(self._session, text):
                self._enqueue(event)
        except Exception as exc:  # 兜底：任何异常都要让界面恢复可交互
            from core.domain.events import ErrorOccurred

            self._enqueue(ErrorOccurred(message="对话过程异常：%s" % exc, error_type="error"))
            self._enqueue(TurnCompleted(answer="", rounds=0, tool_calls=0, cancelled=True))

    def _enqueue(self, event: object) -> None:
        with self._queue_lock:
            self._queue.append(event)

    @work(thread=True, exclusive=True, group="compact")
    def _run_compact(self) -> None:
        """在后台线程执行压缩（压缩期间不会有任何工具调用）。"""
        context = self.ctx.context
        if context is None:  # pragma: no cover - 调用方已检查
            return
        session = self._session
        specs = self._agent.tool_specs()

        self._enqueue(ContextCompactStarted())
        try:
            result = context.compact(session, specs)
        except TAgentError as exc:
            self._enqueue(ContextCompactFailed(message=exc.message))
        except Exception as exc:  # 兜底：压缩失败也要让界面恢复可交互
            self._enqueue(ContextCompactFailed(message="压缩过程异常：%s" % exc))
        else:
            self._enqueue(ContextCompacted(
                archive_id=result.archive_id,
                archive_path=result.archive_path,
                message_count=result.message_count,
                tokens_before=result.tokens_before,
                tokens_after=result.tokens_after,
                summary_truncated=result.summary_truncated,
            ))
            # 压缩后立刻刷新用量（此时 warning 必然为 False，解除输入限制）
            snapshot = context.usage(session, specs)
            self._enqueue(ContextUsageUpdated(
                used_tokens=snapshot.used_tokens,
                window_tokens=snapshot.window_tokens,
                percent=snapshot.percent,
                window_is_default=snapshot.window_is_default,
                warning=context.should_warn(snapshot),
                from_api=snapshot.from_api,
            ))

    @work(thread=True, exclusive=True, group="mcp")
    def _load_mcp(self) -> None:
        """后台连接 MCP：界面先可用，连接结果回来后再刷新。"""
        loader = self.ctx.load_mcp
        if loader is None:  # pragma: no cover - 容器总会提供
            return
        try:
            snapshot = loader()
        except Exception as exc:  # 极端情况下也要让界面退出「加载中」
            self._enqueue(McpLoaded(McpSnapshot(
                notices=("MCP 加载失败：%s" % exc,), loaded=True)))
            return
        if isinstance(snapshot, McpSnapshot):
            self._enqueue(McpLoaded(snapshot))

    async def _drain_events(self) -> None:
        """UI 线程：批量取出事件交给桥接层渲染。"""
        with self._queue_lock:
            if not self._queue:
                return
            batch = list(self._queue)
            self._queue.clear()

        if self._bridge is None:
            return

        for event in batch:
            if isinstance(event, McpLoaded):
                await self._apply_mcp(event.snapshot)
                continue
            await self._bridge.handle(event)
            if isinstance(event, ContextUsageUpdated):
                # 达到阈值即暂停普通输入；压缩成功后（用量回落）自动解除
                self._context_blocked = event.warning
            elif isinstance(event, (ContextCompacted, ContextCompactFailed)):
                self._finish_compact()
            elif isinstance(event, TurnCompleted):
                self._finish_turn()

    async def _apply_mcp(self, snapshot: McpSnapshot) -> None:
        """MCP 后台加载完成：刷新边栏、状态栏与工具数。"""
        self.mcp_loading = False
        self.mcp_snapshot = snapshot
        self.mcp_summary = snapshot.summary
        self.query_one(StatusBar).set_mcp(snapshot.summary or "MCP 未配置")
        self.query_one(StatusBar).set_tool_count(len(self.ctx.tools.all()))
        await self.query_one(Sidebar).update_mcp(snapshot)
        chat = self.query_one(ChatView)
        for notice in snapshot.notices:
            await chat.add_notice(notice)
        if snapshot.servers and snapshot.connected_count:
            await chat.add_notice(
                "MCP 已就绪：%d/%d 台服务器，%d 个工具可用。" % (
                    snapshot.connected_count, snapshot.total_count,
                    snapshot.total_tools))

    def _collect_mcp_snapshot(self) -> McpSnapshot | None:
        """当前最新的 MCP 快照（加载完成前为启动时的空快照）。"""
        return self.mcp_snapshot

    def _finish_turn(self) -> None:
        self._busy = False
        chat_input = self.query_one(ChatInput)
        chat_input.unlock_input()
        self.query_one(ThinkingBar).set_idle(multiline=chat_input.is_multiline)
        # 每轮结束保存一次：意外关窗口也不会丢掉整段对话
        self._save_session(silent=True)
        self._refresh_session_view()

    def _finish_compact(self) -> None:
        """压缩结束后恢复输入（成功失败都要解锁，否则界面卡死）。"""
        self._compacting = False
        chat_input = self.query_one(ChatInput)
        chat_input.unlock_input()
        self.query_one(ThinkingBar).set_idle(multiline=chat_input.is_multiline)

    # ==================================================================
    # 首次生成配置的提示
    # ==================================================================
    def _show_config_notice(self) -> None:
        self.push_screen(ConfigNoticeScreen(self.config_source), self._on_config_notice)

    def _on_config_notice(self, result: str | None) -> None:
        if result == "quit":
            self.exit(0)

    # ==================================================================
    # 动作
    # ==================================================================
    def action_new_session(self) -> None:
        """开一个新会话（清空上下文与界面）。"""
        self.run_worker(self._new_session(), exclusive=False)

    def action_resume_session(self) -> None:
        self._open_resume()

    def action_cancel_turn(self) -> None:
        """协作式中止当前轮次。"""
        if not self._busy:
            return
        self._agent.request_cancel()
        self.query_one(StatusBar).set_state("正在中止…", "error")
        self.query_one(ThinkingBar).set_busy("正在中止…")

    def action_toggle_tools(self) -> None:
        """显示 / 隐藏工具面板。"""
        self.query_one(ToolPanel).toggle_class("-hidden")

    def action_toggle_sidebar(self) -> None:
        """显示 / 隐藏左侧边栏（MCP 与当前会话信息）。"""
        self.query_one(Sidebar).toggle_class("-hidden")

    def action_clear_tools(self) -> None:
        """清空工具面板内容。"""
        self.run_worker(self.query_one(ToolPanel).reset(), exclusive=False)

    # ---------------- 对话区滚动 ----------------
    def action_scroll_chat_up(self) -> None:
        """向上翻一页（查看更早的内容）。"""
        self.query_one(ChatView).scroll_page_up(animate=False)

    def action_scroll_chat_down(self) -> None:
        """向下翻一页。"""
        self.query_one(ChatView).scroll_page_down(animate=False)

    def action_scroll_chat_bottom(self) -> None:
        """跳回最新内容并恢复自动跟随。"""
        self.query_one(ChatView).stick_to_bottom()

    def action_quit(self) -> None:
        """退出前保存会话（自动保存关闭时也不丢这一段对话）。"""
        self._save_session(silent=True)
        self.exit(0)


__all__ = ["TAgentApp", "McpLoaded"]
