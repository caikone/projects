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
        # 注意：传 classes= 会**替换** DEFAULT_CLASSES，必须把 "card" 一起写上，
        # 否则 `.card { height: auto }` 等基础规则全部失配——卡片退回容器的
        # `height: 1fr`，长回答会被裁掉且外层滚动容器测不到溢出（无法上下滑动）。
        super().__init__(classes="card card-%s" % kind)
        self.sender = sender
        self.kind = kind
        self._text = text
        self._dirty = True
        self._flushing = False
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

    @property
    def flushing(self) -> bool:
        """是否有一轮渲染正在进行（收尾时需等待它结束）。"""
        return self._flushing

    async def flush(self, force: bool = False) -> bool:
        """把缓冲区渲染到界面。返回是否真的发生了更新。

        **必须 await `Markdown.update()` 的返回值**：
        它返回 `AwaitComplete`，文档写明「await 它以确保所有子块都已挂载」。
        不 await 的话，标题之后的段落 / 表格 / 列表会在异步挂载过程中
        被下一次 update 冲掉——表现为「消息只显示到第一个标题」。

        注意脏标记的处理：`await update()` 期间可能又有新的流式增量到达
        （`append()` 会把 `_dirty` 置 True）。因此渲染完成后**不能无条件**
        清 `_dirty`，而要比较快照与当前缓冲——否则新增量会被误标成已渲染，
        界面永久冻结在旧快照上（表现为「回复只显示到第一个标题就没了」）。
        """
        if self._flushing or self._body is None:
            return False
        if not (self._dirty or force):
            return False
        self._flushing = True
        try:
            content = self._text
            if isinstance(self._body, Markdown):
                await self._body.update(content or " ")
                # Markdown 增高后，`height: auto` 的卡片/滚动容器不会自动重算，
                # 表现为「内容被卡片裁掉、外层没有滚动条、无法上下滑动」。
                # refresh(layout=True) 会把布局失效向上冒泡，重新测量虚拟高度。
                self._body.refresh(layout=True)
                self.refresh(layout=True)
            else:
                self._body.update(content)
                self.refresh(layout=True)
            # 只清掉「已确认渲染」的部分；等待期间新到的增量保持脏标记
            self._dirty = self._text != content
            return True
        finally:
            self._flushing = False

    def mark_error(self) -> None:
        """把卡片切换为错误样式。"""
        self.kind = "error"
        self.add_class("card-error")
        self.remove_class("card-assistant")
