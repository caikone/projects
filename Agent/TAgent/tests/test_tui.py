# -*- coding: utf-8 -*-
"""TUI 无头冒烟测试：验证界面能挂载、能发消息、能渲染流式结果。

用 Textual 的 `run_test()` 驱动，不需要真实终端。
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.container import AppContainer  # noqa: E402
from core.infrastructure.llm.mock import ScriptedLLMClient  # noqa: E402
from tui.app import TAgentApp  # noqa: E402
from tui.widgets import ChatInput, ChatView, StatusBar, ToolPanel  # noqa: E402
from tui.widgets.message_card import MessageCard  # noqa: E402
from tui.widgets.tool_panel import ToolCallCard  # noqa: E402


async def _wait_done(app, pilot, rounds: int = 80) -> None:
    for _ in range(rounds):
        await pilot.pause(0.05)
        if not app._busy:
            break


def _card_texts(view) -> list[str]:
    return [card.text for card in view.query(MessageCard)]


def build_app(script):
    container = AppContainer.build(llm=ScriptedLLMClient(script))
    ctx = container.build_context()
    return TAgentApp(ctx)


class TuiSmokeTest(unittest.TestCase):
    def test_app_composes(self) -> None:
        async def main() -> None:
            app = build_app([{"text": "你好"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.assertIsNotNone(app.query_one(ChatView))
                self.assertIsNotNone(app.query_one(ToolPanel))
                self.assertIsNotNone(app.query_one(StatusBar))
                self.assertEqual(len(app.screen.query("Header")), 1)

        asyncio.run(main())

    def test_streaming_answer_renders(self) -> None:
        async def main() -> None:
            app = build_app([{"text": "你好，我是 TAgent。"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app._submit("你是谁")
                await _wait_done(app, pilot)

                texts = _card_texts(app.query_one(ChatView))
                self.assertTrue(any("你是谁" in t for t in texts), texts)
                self.assertTrue(any("TAgent" in t for t in texts), texts)
                self.assertFalse(app._busy)

        asyncio.run(main())

    def test_tool_call_shows_in_panel(self) -> None:
        script = [
            {"tool_calls": [{"id": "1", "name": "isLeapYear", "arguments": '{"year": 2024}'}]},
            {"text": "2024 年是闰年。"},
        ]

        async def main() -> None:
            app = build_app(script)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app._submit("2024 是闰年吗")
                await _wait_done(app, pilot)

                panel = app.query_one(ToolPanel)
                cards = list(panel.query(ToolCallCard))
                self.assertEqual(len(cards), 1)
                self.assertIn("isLeapYear", cards[0].title)
                self.assertTrue(cards[0].has_class("tool-ok"))
                self.assertIn("闰年", cards[0].detail_text)

        asyncio.run(main())

    def test_toggle_tools_and_new_session(self) -> None:
        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(140, 36)) as pilot:
                await pilot.pause()

                full_width = app.query_one(ChatView).outer_size.width
                app.action_toggle_tools()
                await pilot.pause()
                self.assertEqual(app.query_one(ToolPanel).styles.display, "none")
                self.assertGreater(app.query_one(ChatView).outer_size.width, full_width)

                app.action_toggle_tools()
                await pilot.pause()
                self.assertEqual(app.query_one(ChatView).outer_size.width, full_width)

                app.action_new_session()
                await pilot.pause()
                self.assertEqual([m.role.value for m in app._session.messages], ["system"])

        asyncio.run(main())

    def test_layout_has_nonzero_status_and_input(self) -> None:
        """防止再次出现「边框吃掉高度导致状态栏塌成 0 行」这类布局回归。"""

        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(140, 36)) as pilot:
                await pilot.pause()
                await pilot.pause()
                for widget_type in (StatusBar, ChatView, ToolPanel, ChatInput):
                    size = app.query_one(widget_type).outer_size
                    self.assertGreater(size.height, 0)
                    self.assertGreater(size.width, 0)

        asyncio.run(main())

    def test_bindings_registered(self) -> None:
        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                keys = {b.key for b in app.BINDINGS}
                self.assertIn("ctrl+n", keys)
                self.assertIn("ctrl+x", keys)
                self.assertIn("ctrl+t", keys)

        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
