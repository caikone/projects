# -*- coding: utf-8 -*-
"""AGENTS.md 注入：一次注入、全程复用。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config.models import AgentsDocSettings  # noqa: E402
from core.container import AppContainer  # noqa: E402
from core.domain.agents import looks_like_agents_message  # noqa: E402
from core.domain.messages import Role  # noqa: E402
from core.infrastructure.llm.mock import ScriptedLLMClient  # noqa: E402
from core.services.agents_docs import AgentsDocsService  # noqa: E402
from core.services.session import Session  # noqa: E402


def write_doc(root: str, text: str, name: str = "AGENTS.md") -> str:
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class AgentsDocsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_file(self) -> None:
        service = AgentsDocsService(self.root, AgentsDocSettings())
        self.assertIsNone(service.find())
        session = Session(system_prompt="系统")
        self.assertIsNone(service.apply(session))

    def test_injected_after_system_once(self) -> None:
        write_doc(self.root, "# 约定\n永远用中文回答。\n")
        service = AgentsDocsService(self.root, AgentsDocSettings())
        session = Session(system_prompt="系统")

        doc = service.apply(session)
        self.assertIsNotNone(doc)
        self.assertEqual([m.role for m in session.messages], [Role.SYSTEM, Role.USER])
        injected = session.messages[1].content or ""
        self.assertTrue(looks_like_agents_message(injected))
        self.assertIn("永远用中文回答", injected)
        self.assertEqual(session.agents_doc, os.path.join(self.root, "AGENTS.md"))

        # 再次调用不应重复注入
        self.assertIsNone(service.apply(session))
        self.assertEqual(len(session.messages), 2)

    def test_truncated(self) -> None:
        write_doc(self.root, "x" * 500)
        service = AgentsDocsService(self.root, AgentsDocSettings(max_bytes=100))
        doc = service.find()
        self.assertIsNotNone(doc)
        self.assertTrue(doc.truncated)
        self.assertEqual(len(doc.text), 100)

    def test_disabled(self) -> None:
        write_doc(self.root, "约定")
        service = AgentsDocsService(self.root, AgentsDocSettings(enabled=False))
        self.assertIsNone(service.find())

    def test_empty_file_ignored(self) -> None:
        write_doc(self.root, "   \n")
        service = AgentsDocsService(self.root, AgentsDocSettings())
        self.assertIsNone(service.find())

    def test_custom_filename(self) -> None:
        write_doc(self.root, "约定", name="CLAUDE.md")
        service = AgentsDocsService(self.root, AgentsDocSettings(filename="CLAUDE.md"))
        self.assertIsNotNone(service.find())


class ContainerInjectionTest(unittest.TestCase):
    """端到端：容器新建会话时注入，并写进会话事件日志。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_session_injects_and_logs(self) -> None:
        write_doc(self.root, "# 项目约定\n先读 README。\n")
        container = AppContainer.build(
            llm=ScriptedLLMClient([{"text": "ok"}]),
            overrides={"mcp_enabled": False},
            project_root=self.root,
            ensure_config=False,
        )
        session = container.build_context().session
        self.assertTrue(session.agents_doc)
        self.assertTrue(any(looks_like_agents_message(m.content or "")
                            for m in session.messages))

        log_path = os.path.join(container.sessions.directory, session.id + ".jsonl")
        self.assertTrue(os.path.isfile(log_path))
        with open(log_path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("agents_doc_injected", content)

        # 第二轮对话里 AGENTS.md 仍然只有一份（复用，不重复注入）
        list(container.agent.stream_turn(session, "开始工作"))
        injected = [m for m in session.messages
                    if m.role is Role.USER and looks_like_agents_message(m.content or "")]
        self.assertEqual(len(injected), 1)


if __name__ == "__main__":
    unittest.main()
