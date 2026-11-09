# -*- coding: utf-8 -*-
"""会话存档端口。

服务层只依赖这个抽象：本地实现写 `.agents/sessions/`，
将来的 Web/Desktop 版可以换成数据库或对象存储，业务代码零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from ..domain.sessions import SessionRecord, SessionSummary


class SessionRepository(ABC):
    """会话的读写与事件日志。"""

    @abstractmethod
    def save(self, record: SessionRecord) -> SessionSummary:
        """写入（覆盖）一份会话快照，并记一条 `snapshot` 事件。"""

    @abstractmethod
    def load(self, session_id: str) -> SessionRecord | None:
        """按 id 读取会话；不存在或损坏时返回 None。"""

    @abstractmethod
    def list_summaries(self) -> list[SessionSummary]:
        """按最后更新时间倒序列出全部会话。"""

    @abstractmethod
    def append_event(self, session_id: str, kind: str,
                     payload: Mapping[str, Any] | None = None) -> str:
        """向会话事件日志追加一行（JSONL）。返回日志文件路径。"""

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        """删除一份会话（快照 + 事件日志）。"""

    @abstractmethod
    def path_of(self, session_id: str) -> str:
        """快照文件路径（UI 展示用）。"""

    @property
    @abstractmethod
    def root(self) -> str:
        """会话目录（用于向用户解释文件存在哪）。"""


__all__ = ["SessionRepository"]
