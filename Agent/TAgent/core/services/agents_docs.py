# -*- coding: utf-8 -*-
"""AGENTS.md 服务：发现项目约定文件，并在会话首次加载时注入一次。

为什么只注入一次：
    AGENTS.md 是项目的长期约定，内容在会话内不会变。把它作为**一条普通消息**
    放在历史最前面，后续每一轮请求都直接复用这份历史即可——
    既不用每轮重复拼接（省 token），也保证请求前缀稳定（缓存有效）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config.models import AgentsDocSettings
from ..domain.agents import looks_like_agents_message, render_agents_message
from ..domain.messages import ChatMessage, Role
from .session import Session

#: 读取时的兜底编码（老文件可能不是 UTF-8）
_FALLBACK_ENCODINGS = ("utf-8-sig", "gb18030")


@dataclass(frozen=True)
class AgentsDoc:
    """一次读取的结果。"""

    path: str
    filename: str
    text: str
    size: int
    truncated: bool

    @property
    def chars(self) -> int:
        return len(self.text)


class AgentsDocsService:
    """读取注入 AGENTS.md 的服务。

    Args:
        project_root: 会话启动目录（AGENTS.md 在此查找）。
        settings:     `[agent_docs]` 段配置。
    """

    def __init__(self, project_root: str, settings: AgentsDocSettings) -> None:
        self._root = project_root or os.getcwd()
        self._settings = settings

    # ------------------------------------------------------------------
    @property
    def filename(self) -> str:
        return self._settings.filename or "AGENTS.md"

    @property
    def path(self) -> str:
        return os.path.join(self._root, self.filename)

    @property
    def enabled(self) -> bool:
        return bool(self._settings.enabled and self.filename)

    # ------------------------------------------------------------------
    def find(self) -> AgentsDoc | None:
        """读取 AGENTS.md；文件不存在、不可读或为空时返回 None。"""
        if not self.enabled:
            return None
        path = self.path
        if not os.path.isfile(path):
            return None
        try:
            size = os.path.getsize(path)
            limit = max(0, int(self._settings.max_bytes or 0))
            with open(path, "rb") as handle:
                raw = handle.read(limit + 1) if limit else handle.read()
        except OSError:
            return None

        truncated = bool(limit) and len(raw) > limit
        if truncated:
            raw = raw[:limit]
        text = _decode(raw)
        if not text.strip():
            return None
        return AgentsDoc(path=path, filename=self.filename, text=text,
                         size=size, truncated=truncated)

    # ------------------------------------------------------------------
    def apply(self, session: Session) -> AgentsDoc | None:
        """把 AGENTS.md 注入会话（幂等）。

        Returns:
            本次实际注入的内容；已经注入过 / 没有文件时返回 None。
        """
        if session.agents_doc:
            return None
        if any(looks_like_agents_message(m.content or "", self.filename)
               for m in session.messages if m.role is Role.USER):
            # 从磁盘恢复的老会话：消息已在历史里，只补标记
            session.agents_doc = self.path
            return None

        doc = self.find()
        if doc is None:
            return None

        message = ChatMessage.user(render_agents_message(
            doc.filename, doc.text, doc.truncated, doc.size))
        session.insert_after_system(message)
        session.agents_doc = doc.path
        return doc

    def describe(self) -> str:
        if not self.enabled:
            return "AGENTS.md 注入已关闭"
        doc = self.find()
        if doc is None:
            return "未找到 %s（%s）" % (self.filename, self.path)
        return "%s 已就绪（%d 字符%s）" % (
            self.filename, doc.chars, "，已截断" if doc.truncated else "")


def _decode(raw: bytes) -> str:
    for encoding in _FALLBACK_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


__all__ = ["AgentsDoc", "AgentsDocsService"]
