# -*- coding: utf-8 -*-
"""会话存档：领域模型、本地存储、服务层。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config.models import SessionSettings  # noqa: E402
from core.domain.messages import ChatMessage, Role, ToolCall  # noqa: E402
from core.domain.sessions import (  # noqa: E402
    SessionRecord,
    derive_title,
    format_relative,
    format_timestamp,
    preview_of,
)
from core.errors import SessionError, SessionNotFoundError  # noqa: E402
from core.infrastructure.sessions.local_files import LocalSessionRepository  # noqa: E402
from core.services.session import Session  # noqa: E402
from core.services.sessions import SessionService  # noqa: E402


def make_session(title: str = "测试会话") -> Session:
    session = Session(system_prompt="你是助手", history_limit=0)
    session.add_user("你好")
    session.add_assistant("你好，有什么可以帮你？")
    session.title = title
    return session


class MessageSerializationTest(unittest.TestCase):
    def test_roundtrip_with_tool_calls(self) -> None:
        message = ChatMessage.assistant(
            None, [ToolCall(id="c1", name="readFile", arguments_json='{"path":"a"}')])
        restored = ChatMessage.from_dict(message.to_dict())
        self.assertIs(restored.role, Role.ASSISTANT)
        self.assertEqual(len(restored.tool_calls), 1)
        self.assertEqual(restored.tool_calls[0].name, "readFile")
        self.assertEqual(restored.tool_calls[0].arguments_json, '{"path":"a"}')

    def test_tool_message_roundtrip(self) -> None:
        from core.domain.messages import ToolResult

        message = ChatMessage.tool(ToolResult(call_id="c1", name="readFile", content="内容"))
        restored = ChatMessage.from_dict(message.to_dict())
        self.assertIs(restored.role, Role.TOOL)
        self.assertEqual(restored.tool_call_id, "c1")
        self.assertEqual(restored.content, "内容")

    def test_unknown_role_falls_back_to_user(self) -> None:
        restored = ChatMessage.from_dict({"role": "weird", "content": "x"})
        self.assertIs(restored.role, Role.USER)


class SessionRecordTest(unittest.TestCase):
    def test_to_dict_excludes_system_message(self) -> None:
        session = make_session()
        record = SessionService.to_record(session)
        data = record.to_dict()
        self.assertEqual(len(data["messages"]), 2)  # system 不入列表
        self.assertEqual(data["system_prompt"], "你是助手")

    def test_from_dict_restores_system_prompt_once(self) -> None:
        session = make_session()
        record = SessionRecord.from_dict(SessionService.to_record(session).to_dict())
        restored = SessionService.from_record(record)
        roles = [m.role for m in restored.messages]
        self.assertEqual(roles[0], Role.SYSTEM)
        self.assertEqual(roles.count(Role.SYSTEM), 1)
        self.assertEqual(restored.title, "测试会话")

    def test_preview_and_title(self) -> None:
        messages = [ChatMessage.user("第一个问题"), ChatMessage.assistant("回答")]
        self.assertEqual(derive_title(messages), "第一个问题")
        self.assertEqual(preview_of(messages), "回答")

    def test_time_formatting(self) -> None:
        self.assertEqual(format_relative(1000, now=1010), "刚刚")
        self.assertTrue(format_relative(1000, now=1000 + 7200).endswith("小时前"))
        self.assertTrue(format_timestamp(0) != "")


class LocalRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalSessionRepository(root=self.tmp.name, project_root=self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_load_list_delete(self) -> None:
        session = make_session("存档测试")
        summary = self.repo.save(SessionService.to_record(session))
        self.assertEqual(summary.message_count, 2)
        self.assertTrue(os.path.isfile(summary.path))

        loaded = self.repo.load(session.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "存档测试")
        self.assertEqual(len(loaded.messages), 2)

        summaries = self.repo.list_summaries()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].id, session.id)

        self.assertTrue(self.repo.delete(session.id))
        self.assertIsNone(self.repo.load(session.id))
        self.assertEqual(self.repo.list_summaries(), [])

    def test_event_log_is_jsonl(self) -> None:
        session = make_session()
        path = self.repo.append_event(session.id, "agents_doc_injected", {"chars": 10})
        self.repo.save(SessionService.to_record(session))
        with open(path, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(lines[0]["event"], "agents_doc_injected")
        self.assertEqual(lines[0]["data"]["chars"], 10)
        self.assertEqual(lines[-1]["event"], "snapshot")

    def test_corrupt_file_is_skipped(self) -> None:
        os.makedirs(self.tmp.name, exist_ok=True)
        with open(os.path.join(self.tmp.name, "broken.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(self.repo.list_summaries(), [])
        self.assertIsNone(self.repo.load("broken"))

    def test_path_is_sanitized(self) -> None:
        path = self.repo.path_of("../../etc/passwd")
        self.assertTrue(path.startswith(self.tmp.name))


class SessionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalSessionRepository(root=self.tmp.name, project_root=self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_autosave_respects_switch(self) -> None:
        service = SessionService(self.repo, SessionSettings(autosave=False))
        self.assertIsNone(service.autosave_session(make_session()))

        service = SessionService(self.repo, SessionSettings(autosave=True))
        summary = service.autosave_session(make_session())
        self.assertIsNotNone(summary)
        self.assertEqual(len(service.list_summaries()), 1)

    def test_autosave_skips_empty_session(self) -> None:
        service = SessionService(self.repo, SessionSettings(autosave=True))
        empty = Session(system_prompt="系统")
        self.assertIsNone(service.autosave_session(empty))

    def test_load_missing_raises(self) -> None:
        service = SessionService(self.repo, SessionSettings())
        with self.assertRaises(SessionNotFoundError):
            service.load("nope")

    def test_save_without_repository_raises(self) -> None:
        service = SessionService(None, SessionSettings())
        self.assertFalse(service.enabled)
        with self.assertRaises(SessionError):
            service.save(make_session())

    def test_list_limit(self) -> None:
        service = SessionService(self.repo, SessionSettings(max_listed=1))
        for index in range(3):
            session = make_session("会话%d" % index)
            session.updated_at = 1000 + index
            service.save(session)
        self.assertEqual(len(service.list_summaries()), 1)


if __name__ == "__main__":
    unittest.main()
