# -*- coding: utf-8 -*-
"""会话存档服务：把 `Session` 与存储实现连接起来。

服务层职责：
    * 会话 ↔ 存档记录的相互转换（`Session` 有业务方法，`SessionRecord` 是纯数据）；
    * 「自动保存」策略（新建会话 / 退出 / 恢复前）集中实现一次；
    * 向事件日志追加可读事件（创建、注入 AGENTS.md、压缩、恢复）。

存储细节全在 `ports.SessionRepository` 的实现里，本文件不出现任何路径拼接。
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from ..config.models import SessionSettings
from ..domain.messages import Role
from ..domain.sessions import SessionRecord, SessionSummary, derive_title
from ..errors import SessionError, SessionNotFoundError
from ..ports.sessions import SessionRepository
from .session import Session


class SessionService:
    """会话保存 / 列表 / 恢复的统一入口。"""

    def __init__(self, repository: SessionRepository | None,
                 settings: SessionSettings | None = None) -> None:
        self._repo = repository
        self._settings = settings or SessionSettings()

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._repo is not None

    @property
    def autosave(self) -> bool:
        return self._settings.autosave

    @property
    def directory(self) -> str:
        return self._repo.root if self._repo is not None else ""

    def path_of(self, session_id: str) -> str:
        return self._repo.path_of(session_id) if self._repo is not None else ""

    # ------------------------------------------------------------------
    # 创建 / 转换
    # ------------------------------------------------------------------
    def create(self, system_prompt: str | None = None,
               history_limit: int = 0) -> Session:
        """创建一个内存会话（尚未落盘）。"""
        return Session(system_prompt=system_prompt, history_limit=history_limit)

    @staticmethod
    def to_record(session: Session) -> SessionRecord:
        return SessionRecord(
            id=session.id,
            title=session.title,
            system_prompt=session.system_prompt,
            history_limit=session.history_limit,
            messages=[m for m in session.messages if m.role is not Role.SYSTEM],
            created_at=session.created_at,
            updated_at=session.updated_at,
            agents_doc=session.agents_doc,
        )

    @staticmethod
    def from_record(record: SessionRecord) -> Session:
        """把存档还原成可用的会话（System Prompt 会重新插到最前面）。"""
        session = Session(
            id=record.id,
            title=record.title,
            messages=[m for m in record.messages if m.role is not Role.SYSTEM],
            system_prompt=record.system_prompt,
            history_limit=record.history_limit,
            created_at=record.created_at,
            updated_at=record.updated_at,
            agents_doc=record.agents_doc,
        )
        if not session.title:
            session.title = derive_title(session.messages)
        return session

    # ------------------------------------------------------------------
    # 保存 / 恢复 / 列表
    # ------------------------------------------------------------------
    def save(self, session: Session) -> SessionSummary:
        """显式保存（`/save`、退出前、新建会话前调用）。"""
        if self._repo is None:
            raise SessionError("当前未启用会话存档（[sessions] 段不可用）。")
        if not session.title:
            session.title = derive_title(session.messages)
        return self._repo.save(self.to_record(session))

    def autosave_session(self, session: Session) -> SessionSummary | None:
        """按配置自动保存：关闭自动保存、未启用存档、空会话时直接跳过。"""
        if self._repo is None or not self._settings.autosave:
            return None
        if not any(m.role is not Role.SYSTEM for m in session.messages):
            return None
        try:
            return self.save(session)
        except SessionError:
            # 自动保存失败不应挡住用户操作（例如 /new 或退出）
            return None

    def load(self, session_id: str) -> Session:
        """读取并还原会话。"""
        if self._repo is None:
            raise SessionError("当前未启用会话存档，无法恢复历史会话。")
        record = self._repo.load(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        return self.from_record(record)

    def list_summaries(self, limit: int | None = None) -> list[SessionSummary]:
        """历史会话列表（按最近更新倒序）。"""
        if self._repo is None:
            return []
        summaries = self._repo.list_summaries()
        cap = self._settings.max_listed if limit is None else limit
        return summaries[:cap] if cap and cap > 0 else summaries

    def delete(self, session_id: str) -> bool:
        if self._repo is None:
            return False
        return self._repo.delete(session_id)

    # ------------------------------------------------------------------
    def record_event(self, session_id: str, kind: str,
                     payload: Mapping[str, Any] | None = None) -> None:
        """向事件日志追加一条事件（失败只忽略，不影响主流程）。"""
        if self._repo is None:
            return
        try:
            self._repo.append_event(session_id, kind, payload)
        except SessionError:
            pass

    def describe(self) -> str:
        if self._repo is None:
            return "未启用会话存档"
        return "会话目录：%s" % self._repo.root

    def ensure_directory(self) -> None:
        """提前建好目录（UI 展示路径前调用，避免用户找不到）。"""
        if self._repo is None:
            return
        try:
            os.makedirs(self._repo.root, exist_ok=True)
        except OSError as exc:
            raise SessionError("无法创建会话目录：%s（%s）" % (self._repo.root, exc))


__all__ = ["SessionService"]
