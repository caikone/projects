# -*- coding: utf-8 -*-
"""会话管理：多轮上下文的容器。

职责边界：
    - 只负责「消息怎么存、怎么裁剪」，不负责「怎么问模型」；
    - 与 UI 无关，因此 Web 端可以把它放进 Redis/数据库，TUI 端放内存即可。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from ..domain.messages import ChatMessage, Role


@dataclass
class Session:
    """一次会话（等价于一个对话窗口）。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    system_prompt: str | None = None
    #: 历史上限（0 = 不限制）；超出时从最早的非 system 消息开始丢弃
    history_limit: int = 0
    #: 创建 / 最后更新时间（Unix 秒），供 /resume 列表排序展示
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    #: 已注入的 AGENTS.md 路径（非空表示注入过了，避免重复追加）
    agents_doc: str = ""

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.system_prompt and not any(m.role == Role.SYSTEM for m in self.messages):
            self.messages.insert(0, ChatMessage.system(self.system_prompt))

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    @property
    def next_turn_id(self) -> int:
        """下一个轮次序号（从 1 开始，UI 用它给事件分组）。"""
        return sum(1 for m in self.messages if m.role == Role.USER) + 1

    def add(self, message: ChatMessage) -> None:
        self.messages.append(message)
        self.updated_at = time.time()
        self._trim()

    def add_user(self, content: str) -> ChatMessage:
        message = ChatMessage.user(content)
        if not self.title:
            self.title = content[:24]
        self.add(message)
        return message

    def add_assistant(self, content: str) -> ChatMessage:
        message = ChatMessage.assistant(content)
        self.add(message)
        return message

    def insert_after_system(self, message: ChatMessage) -> int:
        """把消息插到 System Prompt 之后（AGENTS.md 等长期约定用）。

        放在历史最前面有两个好处：模型每轮都能看到；位置固定，前缀稳定，
        服务端的 Prompt 缓存可以持续命中。
        """
        index = 1 if self.messages and self.messages[0].role == Role.SYSTEM else 0
        self.messages.insert(index, message)
        self.updated_at = time.time()
        return index

    def snapshot(self) -> list[ChatMessage]:
        """返回当前完整上下文（供请求使用）。"""
        return list(self.messages)

    def clear(self) -> None:
        """清空对话，但保留系统提示词。"""
        self.messages = ([ChatMessage.system(self.system_prompt)]
                         if self.system_prompt else [])
        self.title = ""
        self.updated_at = time.time()

    def replace_history(self, messages: list[ChatMessage]) -> None:
        """整体替换非 system 历史（上下文压缩后使用）。

        系统提示词始终保留：压缩只改变「聊过什么」，
        不改变 Agent 的能力定义。
        """
        system = [m for m in self.messages if m.role == Role.SYSTEM]
        self.messages = system + list(messages)
        self.updated_at = time.time()

    def stats(self) -> dict[str, int]:
        """会话统计信息（UI 状态栏用）。"""
        counts = {role: 0 for role in Role}
        for m in self.messages:
            counts[m.role] += 1
        return {
            "total": len(self.messages),
            "user": counts[Role.USER],
            "assistant": counts[Role.ASSISTANT],
            "tool": counts[Role.TOOL],
            "system": counts[Role.SYSTEM],
        }

    # ------------------------------------------------------------------
    def _trim(self) -> None:
        if not self.history_limit or len(self.messages) <= self.history_limit:
            return
        # 保留 system 消息，从最早的消息开始丢弃
        kept = [m for m in self.messages if m.role == Role.SYSTEM]
        rest = [m for m in self.messages if m.role != Role.SYSTEM]
        overflow = len(self.messages) - self.history_limit
        kept.extend(rest[overflow:])
        self.messages = kept


class SessionStore:
    """多会话容器（为将来的 Web/Desktop 多标签页预留）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, **kwargs: object) -> Session:
        session = Session(**kwargs)  # type: ignore[arg-type]
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
