# -*- coding: utf-8 -*-
"""会话存档的领域模型与纯函数。

这一层只管「一条会话在磁盘上长什么样、列表里怎么显示」，
不碰文件 I/O（那是 infrastructure 的事），也不碰 UI。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .messages import ChatMessage, Role

#: 存档格式版本（将来改结构时用来做兼容迁移）
SESSION_FORMAT_VERSION = 1

#: 列表里的标题最大长度
TITLE_MAX_CHARS = 40
#: 列表里的摘要最大长度
PREVIEW_MAX_CHARS = 60


@dataclass
class SessionRecord:
    """会话的完整存档内容（与 `services.Session` 同构，但不含业务方法）。

    基础设施层只认识这个纯数据对象，因此可以独立替换存储实现，
    也不会为了「读个文件」而反向依赖服务层。
    """

    id: str
    title: str = ""
    system_prompt: str | None = None
    history_limit: int = 0
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    agents_doc: str = ""
    #: 快照文件路径（读取时由存储实现填上，写入时忽略）
    path: str = ""
    version: int = SESSION_FORMAT_VERSION

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """转换成写入磁盘的字典。

        只保存非 system 消息：System Prompt 单独存一份，
        避免「系统提示词 + 消息列表里的 system」重复注入。
        """
        return {
            "version": self.version,
            "id": self.id,
            "title": self.title,
            "created_at": round(self.created_at, 3),
            "updated_at": round(self.updated_at, 3),
            "system_prompt": self.system_prompt or "",
            "history_limit": self.history_limit,
            "agents_doc": self.agents_doc,
            "messages": [m.to_dict() for m in self.messages
                         if m.role is not Role.SYSTEM],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], path: str = "") -> "SessionRecord":
        """从磁盘字典还原；字段缺失时给出安全默认值。"""
        messages = [
            ChatMessage.from_dict(item)
            for item in data.get("messages") or []
            if isinstance(item, dict)
        ]
        now = time.time()
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            system_prompt=(str(data.get("system_prompt") or "") or None),
            history_limit=_as_int(data.get("history_limit")),
            messages=messages,
            created_at=_as_float(data.get("created_at"), now),
            updated_at=_as_float(data.get("updated_at"), now),
            agents_doc=str(data.get("agents_doc") or ""),
            path=path,
            version=_as_int(data.get("version"), SESSION_FORMAT_VERSION),
        )

    @property
    def message_count(self) -> int:
        return len(self.messages)


@dataclass(frozen=True)
class SessionSummary:
    """`/resume` 列表里的一行。"""

    id: str
    title: str
    updated_at: float
    message_count: int = 0
    preview: str = ""
    path: str = ""

    @property
    def display_title(self) -> str:
        title = (self.title or "").strip()
        return title or "（未命名会话）"

    @property
    def updated_text(self) -> str:
        return format_relative(self.updated_at)

    @property
    def meta_text(self) -> str:
        """「11-04 11:20 · 12 条消息」这样的一行副标题。"""
        return "%s · %d 条消息" % (format_timestamp(self.updated_at), self.message_count)


@dataclass(frozen=True)
class SessionEvent:
    """会话事件日志的一行（JSONL）。"""

    session_id: str
    kind: str
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": round(self.ts, 3),
            "session": self.session_id,
            "event": self.kind,
            **({"data": self.payload} if self.payload else {}),
        }


# ======================================================================
# 纯函数
# ======================================================================
def summarize(session_id: str, title: str, messages: Sequence[ChatMessage],
              updated_at: float, path: str = "") -> SessionSummary:
    """从会话内容生成列表摘要（不依赖 Session 类，便于单测）。"""
    return SessionSummary(
        id=session_id,
        title=title,
        updated_at=updated_at,
        message_count=len(messages),
        preview=preview_of(messages),
        path=path,
    )


def preview_of(messages: Iterable[ChatMessage], limit: int = PREVIEW_MAX_CHARS) -> str:
    """取「最后一条用户或助手消息」的前若干字符，作为列表摘要。"""
    for message in reversed(list(messages)):
        if message.role not in (Role.USER, Role.ASSISTANT):
            continue
        text = " ".join((message.content or "").split())
        if not text:
            continue
        return text[:limit] + ("…" if len(text) > limit else "")
    return ""


def derive_title(messages: Sequence[ChatMessage]) -> str:
    """没有 title 时用第一条用户消息兜底。"""
    for message in messages:
        if message.role is Role.USER and (message.content or "").strip():
            text = " ".join((message.content or "").split())
            return text[:TITLE_MAX_CHARS] + ("…" if len(text) > TITLE_MAX_CHARS else "")
    return ""


def format_timestamp(ts: float | None) -> str:
    """`2026-11-04 11:20`（跨年时自动带上年份）。"""
    if not ts:
        return "未知时间"
    moment = time.localtime(ts)
    if time.localtime().tm_year != moment.tm_year:
        return time.strftime("%Y-%m-%d %H:%M", moment)
    return time.strftime("%m-%d %H:%M", moment)


def format_relative(ts: float | None, now: float | None = None) -> str:
    """人类友好的相对时间：刚刚 / 3 分钟前 / 昨天 / 5 天前。"""
    if not ts:
        return "未知"
    current = now if now is not None else time.time()
    delta = current - ts
    if delta < 0:
        return "刚刚"
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return "%d 分钟前" % int(delta // 60)
    if delta < 86400:
        return "%d 小时前" % int(delta // 3600)
    if delta < 86400 * 2:
        return "昨天"
    if delta < 86400 * 30:
        return "%d 天前" % int(delta // 86400)
    return time.strftime("%Y-%m-%d", time.localtime(ts))


__all__ = [
    "PREVIEW_MAX_CHARS",
    "SESSION_FORMAT_VERSION",
    "TITLE_MAX_CHARS",
    "SessionEvent",
    "SessionRecord",
    "SessionSummary",
    "derive_title",
    "format_relative",
    "format_timestamp",
    "preview_of",
    "summarize",
]


def _as_int(value: Any, default: int = 0) -> int:
    """宽容的整数转换：存档文件被手改坏时也不应让整个会话打不开。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
