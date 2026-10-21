# -*- coding: utf-8 -*-
"""TAgent TUI 主应用（Textual App）。

职责边界非常明确：
    - 只做「布局 + 事件分发 + 键盘绑定」；
    - 所有业务能力来自 `core`（通过 `FrontendContext` 注入）；
    - 真正的对话在**后台工作线程**里跑，事件经由线程安全队列回流到 UI 线程，
      因此流式输出再密集也不会卡住界面。
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Deque

from core.domain.events import TurnCompleted
from core.ports.frontend import FrontendContext
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from .bridge import AgentEventBridge
from .widgets import ChatInput, ChatView, StatusBar, ToolPanel

#: 事件回流到 UI 线程的排空间隔（秒）
DRAIN_INTERVAL = 1 / 30


class TAgentApp(App[None]):
    """TAgent 终端界面。"""

    CSS_PATH = "styles.tcss"
    TITLE = "TAgent"
    SUB_TITLE = "终端智能助手"

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+n", "new_session", "新会话"),
        Binding("ctrl+x", "cancel_turn", "中止本轮"),
        Binding("ctrl+t", "toggle_tools", "工具面板"),
        Binding("ctrl+k", "clear_tools", "清空工具面板"),
    ]

    def __init__(self, ctx: FrontendContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._session = ctx.session
        self._agent = ctx.agent
        self._busy = False
        self._queue: Deque[object] = deque()
        self._queue_lock = threading.Lock()
        self._bridge: AgentEventBridge | None = None
        # 供界面展示的元信息
        self.config_source = str(ctx.metadata.get("config_source", ""))

    # ==================================================================
    # 布局
    # ==================================================================
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(
            model=self.ctx.settings.llm.model,
            tool_count=len(self.ctx.tools.all()),
            config_source=self.config_source,
            id="status",
        )
        with Horizontal(id="body"):
            yield ChatView(id="chat")
            yield ToolPanel(id="tools")
        yield ChatInput(id="input")
        yield Footer()

    async def on_mount(self) -> None:
        chat = self.query_one(ChatView)
        tools = self.query_one(ToolPanel)
        status = self.query_one(StatusBar)
        self._bridge = AgentEventBridge(chat=chat, tools=tools, status=status)

        # 事件回流泵：把工作线程产出的事件搬到 UI 线程
        self.set_interval(DRAIN_INTERVAL, self._drain_events)

        await chat.clear_messages()
        status.set_state("空闲", "idle")
        self.query_one(ChatInput).focus()

        prompt = self.ctx.initial_prompt
        if prompt:
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
        if self._busy:
            await self.query_one(ChatView).add_notice(
                "上一轮还没结束，按 Ctrl+X 可中止，或稍后再发送。")
            return

        self._busy = True
        self.query_one(ChatInput).lock_input()
        self.query_one(StatusBar).set_state("已发送，等待模型…", "thinking")
        self._run_turn(text)

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
            await self._bridge.handle(event)
            if isinstance(event, TurnCompleted):
                self._finish_turn()

    def _finish_turn(self) -> None:
        self._busy = False
        self.query_one(ChatInput).unlock_input()

    # ==================================================================
    # 动作
    # ==================================================================
    def action_new_session(self) -> None:
        """开一个新会话（清空上下文与界面）。"""
        if self._busy:
            return
        factory = self.ctx.new_session
        if factory is None:
            return
        self._session = factory()
        self._busy = False

        chat = self.query_one(ChatView)
        tools = self.query_one(ToolPanel)
        status = self.query_one(StatusBar)
        self.run_worker(chat.clear_messages(), exclusive=False)
        self.run_worker(tools.reset(), exclusive=False)
        status.set_turns(0, 0)
        status.set_state("新会话", "idle")
        self.query_one(ChatInput).unlock_input()

    def action_cancel_turn(self) -> None:
        """协作式中止当前轮次。"""
        if not self._busy:
            return
        self._agent.request_cancel()
        self.query_one(StatusBar).set_state("正在中止…", "error")

    def action_toggle_tools(self) -> None:
        """显示 / 隐藏工具面板。"""
        self.query_one(ToolPanel).toggle_class("-hidden")

    def action_clear_tools(self) -> None:
        """清空工具面板内容。"""
        self.run_worker(self.query_one(ToolPanel).reset(), exclusive=False)

    def action_quit(self) -> None:
        self.exit(0)
