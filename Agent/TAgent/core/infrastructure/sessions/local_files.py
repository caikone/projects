# -*- coding: utf-8 -*-
"""会话存档的本地文件实现：`<root>/<session_id>.json` + `<session_id>.jsonl`。

目录约定（唯一知道这个布局的地方）::

    .agents/sessions/
        a1b2c3d4e5f6.json      ← 会话快照（原子覆盖写）
        a1b2c3d4e5f6.jsonl     ← 事件日志（只追加，逐行 JSON）

为什么要两份：
    * 快照负责「快速恢复」——一次读取就能重建整段对话；
    * 事件日志负责「可追溯」——记录创建、注入 AGENTS.md、压缩、保存等动作，
      只追加的格式即使程序崩溃也不会损坏历史记录。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Mapping

from ...domain.sessions import (
    SESSION_FORMAT_VERSION,
    SessionRecord,
    SessionSummary,
    derive_title,
    summarize,
)
from ...errors import SessionError
from ...ports.sessions import SessionRepository

#: 会话文件名只允许这些字符（id 由程序生成，这里再兜一层防路径穿越）
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")


class LocalSessionRepository(SessionRepository):
    """把会话写到项目本地目录。"""

    def __init__(self, root: str | None = None, project_root: str = "") -> None:
        base = project_root or os.getcwd()
        self._root = os.path.abspath(root or os.path.join(base, ".agents", "sessions"))

    # ------------------------------------------------------------------
    @property
    def root(self) -> str:
        return self._root

    def path_of(self, session_id: str) -> str:
        return os.path.join(self._root, "%s.json" % _safe(session_id))

    def event_log_path(self, session_id: str) -> str:
        return os.path.join(self._root, "%s.jsonl" % _safe(session_id))

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def save(self, record: SessionRecord) -> SessionSummary:
        """原子写入快照，并追加一条 `snapshot` 事件。"""
        self._ensure_dir()
        path = self.path_of(record.id)
        payload = record.to_dict()
        payload["version"] = SESSION_FORMAT_VERSION
        _atomic_write_json(path, payload)

        summary = summarize(
            record.id,
            record.title or derive_title(record.messages),
            record.messages,
            record.updated_at,
            path,
        )
        self.append_event(record.id, "snapshot", {
            "title": summary.title,
            "messages": summary.message_count,
        })
        return summary

    def append_event(self, session_id: str, kind: str,
                     payload: Mapping[str, Any] | None = None) -> str:
        """追加一行事件日志（`flush` + `fsync`，崩溃也不丢）。"""
        self._ensure_dir()
        path = self.event_log_path(session_id)
        line = json.dumps({
            "ts": round(time.time(), 3),
            "session": session_id,
            "event": kind,
            **({"data": dict(payload)} if payload else {}),
        }, ensure_ascii=False)
        try:
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SessionError("写入会话事件日志失败：%s（%s）" % (path, exc))
        return path

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def load(self, session_id: str) -> SessionRecord | None:
        path = self.path_of(session_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        record = SessionRecord.from_dict(data, path=path)
        record.id = record.id or session_id
        return record

    def list_summaries(self) -> list[SessionSummary]:
        """扫描目录，按最后更新时间倒序返回。损坏文件直接跳过。"""
        if not os.path.isdir(self._root):
            return []
        summaries: list[SessionSummary] = []
        for name in os.listdir(self._root):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self._root, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            record = SessionRecord.from_dict(data, path=path)
            session_id = record.id or name[:-5]
            summaries.append(summarize(
                session_id,
                record.title or derive_title(record.messages),
                record.messages,
                record.updated_at,
                path,
            ))
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries

    # ------------------------------------------------------------------
    def delete(self, session_id: str) -> bool:
        removed = False
        for path in (self.path_of(session_id), self.event_log_path(session_id)):
            try:
                os.remove(path)
                removed = True
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SessionError("删除会话文件失败：%s（%s）" % (path, exc))
        return removed

    # ------------------------------------------------------------------
    def _ensure_dir(self) -> None:
        try:
            os.makedirs(self._root, exist_ok=True)
        except OSError as exc:
            raise SessionError("无法创建会话目录：%s（%s）" % (self._root, exc))


# ======================================================================
# 辅助
# ======================================================================
def _safe(session_id: str) -> str:
    cleaned = _SAFE_NAME.sub("", session_id or "").strip("._")
    return cleaned or "session"


def _atomic_write_json(path: str, payload: Mapping[str, Any]) -> None:
    """临时文件 + fsync + rename：任何时刻磁盘上都是一份完整文件。"""
    directory = os.path.dirname(path) or "."
    temp_path = os.path.join(directory, ".%s.tmp" % os.path.basename(path))
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise SessionError("写入会话文件失败：%s（%s）" % (path, exc))


__all__ = ["LocalSessionRepository"]
