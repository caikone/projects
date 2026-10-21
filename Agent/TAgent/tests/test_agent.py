# -*- coding: utf-8 -*-
"""Agent 服务的端到端测试：用脚本化 LLM 客户端跑通整条流水线。

这里验证的是「core 能不能在没有界面、没有网络的情况下独立工作」——
TUI / Web / Desktop 都只是这条流水线的不同渲染器。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config.models import AgentSettings, LLMSettings  # noqa: E402
from core.container import AppContainer  # noqa: E402
from core.domain.events import (  # noqa: E402
    AssistantTextDelta,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnStarted,
)
from core.infrastructure.llm.mock import ScriptedLLMClient  # noqa: E402
from core.services.session import Session  # noqa: E402
from core.tools.registry import InMemoryToolRegistry  # noqa: E402
from core.tools.builtin_system import build_system_tools  # noqa: E402
from core.services.agent import AgentService  # noqa: E402


def make_settings() -> AgentSettings:
    return AgentSettings(llm=LLMSettings(base_url="http://test", api_key="k", model="m"))


class AgentServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = make_settings()
        self.registry = InMemoryToolRegistry()
        self.registry.register_many(build_system_tools())
        self.session = Session(system_prompt="你是测试助手。")

    def _service(self, script) -> AgentService:
        return AgentService(ScriptedLLMClient(script), self.registry, self.settings)

    # ------------------------------------------------------------------
    def test_plain_answer_no_tools(self) -> None:
        agent = self._service([{"text": "你好，我是 TAgent。"}])
        events = list(agent.stream_turn(self.session, "你是谁"))

        self.assertIsInstance(events[0], TurnStarted)
        texts = "".join(e.text for e in events if isinstance(e, AssistantTextDelta))
        self.assertEqual(texts, "你好，我是 TAgent。")
        last = events[-1]
        self.assertIsInstance(last, TurnCompleted)
        self.assertEqual(last.answer, "你好，我是 TAgent。")

    def test_tool_call_round_trip(self) -> None:
        script = [
            {"tool_calls": [{"id": "1", "name": "isLeapYear", "arguments": '{"year": 2024}'}]},
            {"text": "2024 年是闰年。"},
        ]
        agent = self._service(script)
        events = list(agent.stream_turn(self.session, "2024 是闰年吗"))

        started = [e for e in events if isinstance(e, ToolCallStarted)]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].name, "isLeapYear")
        self.assertEqual(started[0].arguments["year"], 2024)

        finished = [e for e in events if isinstance(e, ToolCallCompleted)]
        self.assertEqual(len(finished), 1)
        self.assertTrue(finished[0].result.ok)
        self.assertIn("闰年", finished[0].result.content)

        self.assertEqual(events[-1].answer, "2024 年是闰年。")
        # 上下文里应包含：user / assistant(tool_calls) / tool / assistant
        roles = [m.role.value for m in self.session.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "assistant"])

    def test_unknown_tool_is_reported_to_model(self) -> None:
        script = [
            {"tool_calls": [{"id": "1", "name": "noSuchTool", "arguments": "{}"}]},
            {"text": "抱歉。"},
        ]
        agent = self._service(script)
        events = list(agent.stream_turn(self.session, "调用一个不存在的工具"))
        finished = [e for e in events if isinstance(e, ToolCallCompleted)]
        self.assertFalse(finished[0].result.ok)
        self.assertIn("未知工具", finished[0].result.content)

    def test_max_tool_rounds_guard(self) -> None:
        # 模型每轮都要调工具 → 必须在 max_tool_rounds 处停下
        self.settings = self.settings.with_overrides(max_tool_rounds=3)
        script = [{"tool_calls": [{"id": str(i), "name": "isLeapYear",
                                   "arguments": '{"year": 2000}'}]} for i in range(10)]
        agent = self._service(script)
        events = list(agent.stream_turn(self.session, "无限调用"))
        finished = [e for e in events if isinstance(e, ToolCallCompleted)]
        self.assertLessEqual(len(finished), 3)
        self.assertIsInstance(events[-1], TurnCompleted)

    def test_core_never_prints(self) -> None:
        """核心层不得产生任何终端输出（UI 无关性的回归测试）。"""
        import io
        from contextlib import redirect_stdout

        agent = self._service([{"text": "安静"}])
        buf = io.StringIO()
        with redirect_stdout(buf):
            list(agent.stream_turn(self.session, "hi"))
        self.assertEqual(buf.getvalue(), "")


class ContainerTest(unittest.TestCase):
    def test_container_builds_all_defaults(self) -> None:
        container = AppContainer.build(
            llm=ScriptedLLMClient([{"text": "ok"}]),
            search_paths=[os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "config.ini")],
        )
        self.assertEqual(len(container.tools.all()), 12)
        ctx = container.build_context(initial_prompt="hi")
        self.assertIsNotNone(ctx.session)
        self.assertIsNotNone(ctx.new_session)
        self.assertEqual(ctx.initial_prompt, "hi")


if __name__ == "__main__":
    unittest.main()
