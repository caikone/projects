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
from textual import events  # noqa: E402
from textual.widgets import Markdown  # noqa: E402
from tui.app import TAgentApp  # noqa: E402
from tui.text_utils import has_non_ascii  # noqa: E402
from tui.widgets import ChatInput, ChatView, StatusBar, ThinkingBar, ToolPanel  # noqa: E402
from tui.widgets.message_card import MessageCard  # noqa: E402
from tui.widgets.thinking_bar import Spinner  # noqa: E402
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
                for widget_type in (StatusBar, ChatView, ToolPanel, ChatInput, ThinkingBar):
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

    # ------------------------------------------------------------------
    # 多行输入
    # ------------------------------------------------------------------
    def test_shift_enter_and_ctrl_j_insert_newline(self) -> None:
        """Shift+Enter / Ctrl+J 只换行、不提交。"""

        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                box = app.query_one(ChatInput)
                box.focus()

                await pilot.press("a")
                await pilot.press("shift+enter")
                await pilot.press("b")
                await pilot.press("ctrl+j")
                await pilot.press("c")
                await pilot.pause()

                self.assertEqual(box.text, "a\nb\nc")
                self.assertFalse(app._busy)  # 没有触发提交

        asyncio.run(main())

    def test_enter_submits_multiline_text(self) -> None:
        """多行文本：Enter 先换行，末行空行再 Enter 才发送。

        Windows 上 Shift 修饰符会被 Textual 的 win32 驱动丢弃，
        所以必须保证「不依赖修饰键也能输入多行」。
        """

        async def main() -> None:
            app = build_app([{"text": "收到"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                box = app.query_one(ChatInput)
                box.focus()

                box.insert("第一行")
                await pilot.press("shift+enter")
                box.insert("第二行")
                await pilot.pause()
                self.assertEqual(box.text, "第一行\n第二行")

                # 多行且末行非空 → Enter 继续换行
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(box.text, "第一行\n第二行\n")
                self.assertFalse(app._busy)

                # 末行已经是空行 → 这次 Enter 才发送，且不带尾随空行
                await pilot.press("enter")
                await _wait_done(app, pilot)

                self.assertEqual(box.text, "")
                texts = _card_texts(app.query_one(ChatView))
                self.assertTrue(any("第一行\n第二行" in t for t in texts), texts)

        asyncio.run(main())

    def test_single_line_enter_still_submits(self) -> None:
        """单行时 Enter 仍然是「发送」，不能被多行策略拖慢。"""

        async def main() -> None:
            app = build_app([{"text": "好的"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                box = app.query_one(ChatInput)
                box.focus()

                for char in "你好":
                    box.insert(char)
                await pilot.pause()
                await pilot.press("enter")
                await _wait_done(app, pilot)

                self.assertEqual(box.text, "")
                self.assertTrue(any("你好" in t for t in _card_texts(app.query_one(ChatView))))

        asyncio.run(main())

    def test_ctrl_s_forces_submit(self) -> None:
        """Ctrl+S 无论单行多行都立即发送。"""

        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                box = app.query_one(ChatInput)
                box.focus()

                box.insert("甲\n乙")
                await pilot.pause()
                await pilot.press("ctrl+s")
                await _wait_done(app, pilot)

                self.assertEqual(box.text, "")
                self.assertTrue(any("甲\n乙" in t for t in _card_texts(app.query_one(ChatView))))

        asyncio.run(main())

    def test_paste_multiline_text(self) -> None:
        """粘贴的多行文本直接进输入框，不会被拆开或触发提交。"""

        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                box = app.query_one(ChatInput)
                box.focus()

                box._forward_event(events.Paste("甲乙丙\r\n丁戊己"))
                await pilot.pause()

                # Windows 剪贴板的 \r\n 必须归一化成 \n，且只能插入一次
                self.assertEqual(box.text, "甲乙丙\n丁戊己")
                self.assertFalse(app._busy)

        asyncio.run(main())

    def test_streamed_markdown_renders_all_blocks(self) -> None:
        """流式 Markdown 的所有块（标题/表格/列表）都必须渲染出来。

        回归背景：`Markdown.update()` 返回 `AwaitComplete`，不 await 的话
        子块挂载被跳过/被打断——实测连续 4 次不 await 的 update 只挂载出
        5/7 块，表现为「消息只显示到第一个标题」，用户看到拦腰截断的回复。

        测试策略：分多次追加文本，每次 append 后立即 `await flush()`，
        在**不做任何额外等待**的前提下断言所有块已挂载——
        旧代码（同步 update）在这里必然挂载不全。
        """

        chunks = [
            "## 成都东软学院：最受欢迎教师名单\n\n",
            "该页面是学校发布的评选结果公告，共 28 位教师。\n\n",
            "### 名单\n\n| 学院 | 教师 |\n|---|---|\n| 计算机与软件学院 | 黄波 |\n\n",
            "### 简介\n\n- **黄波**：主讲《HTML5应用开发》。\n\n",
            "如需整理成文件请告诉我路径。",
        ]
        full_md = "".join(chunks)

        async def main() -> None:
            app = build_app([{"text": full_md}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatView)
                card = await chat.start_stream()
                for chunk in chunks:
                    card.append(chunk)
                    await card.flush()   # 旧代码这里不等挂载就返回
                await chat.end_stream()
                await pilot.pause()

                self.assertEqual(card.text, full_md)
                markdown = card.query_one(Markdown)
                child_types = [type(child).__name__ for child in markdown.children]
                # H2 / 段落 / H3 / 表格 / H3 / 列表 / 尾部段落 = 7 块，一块都不能少
                self.assertEqual(
                    child_types,
                    ["MarkdownH2", "MarkdownParagraph", "MarkdownH3", "MarkdownTable",
                     "MarkdownH3", "MarkdownBulletList", "MarkdownParagraph"],
                    "块序列不符，尾部内容丢了：%s" % child_types)

        asyncio.run(main())

    def test_stream_append_during_render_keeps_final_content(self) -> None:
        """渲染进行中到达的增量，收尾时必须全部渲染出来（不能被脏标记吞掉）。

        回归背景（真实故障）：20FPS 定时器 flush 取快照 `content = self._text`
        后 `await Markdown.update(content)`；等待期间新 token 到达会置 `_dirty`，
        旧代码 update 返回后**无条件** `_dirty = False` 把这些增量标成已渲染。
        收尾时 `end_stream` 的 `flush(force=True)` 若正撞上上一轮 flush 未结束
        （`_flushing=True` 直接返回 False），最终全文就永远不渲染——
        用户看到的是「回复停在第一个标题，下面的内容消失」。

        测试策略：把 `Markdown.update` 人为放慢，在渲染进行中追加剩余内容，
        然后 `end_stream()`，断言渲染结果包含最后一段。
        """

        answer = "# 标题\n\n" + "\n\n".join(
            "第%d段：%s" % (i, "内容" * 60) for i in range(1, 25))

        async def main() -> None:
            app = build_app([{"text": answer}])
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatView)
                card = await chat.start_stream()

                original_update = Markdown.update
                slow = {"on": True}

                async def slow_update(self, markdown):
                    result = original_update(self, markdown)
                    if slow["on"]:
                        await asyncio.sleep(0.15)  # 放大竞态窗口
                    await result
                    return result

                Markdown.update = slow_update
                try:
                    await chat.stream_text(answer[:200])
                    await asyncio.sleep(0.06)        # 让定时器抓到这一轮 flush
                    await chat.stream_text(answer[200:])
                finally:
                    slow["on"] = False
                    Markdown.update = original_update

                await chat.end_stream()
                await pilot.pause()

                rendered = card.query_one(Markdown).source or ""
                self.assertEqual(card.text, answer)
                self.assertIn("第24段", rendered, "尾部内容被吞掉：只渲染了 %d/%d 字符"
                              % (len(rendered), len(answer)))

        asyncio.run(main())

    def test_long_answer_can_scroll(self) -> None:
        """长回答必须能上下滚动查看（曾经无法滚动，内容被卡片裁掉）。

        回归背景：
        1) `MessageCard.__init__` 传 `classes="card-assistant"` 会**替换**
           `DEFAULT_CLASSES`，导致 `.card { height: auto }` 失配，卡片退回容器的
           `height: 1fr`，长内容被裁；ChatView 测不到溢出 → `max_scroll_y == 0`；
        2) 滚动位置与自动跟随要区分：用户往上翻看时，新内容到达不能把人拽回底部。
        """

        long_text = "# 标题\n\n" + "\n\n".join(
            "第%d段：%s" % (i, "这是一段比较长的正文内容，" * 6) for i in range(1, 20))

        async def main() -> None:
            app = build_app([{"text": long_text}])
            async with app.run_test(size=(110, 30)) as pilot:
                await pilot.pause()
                await app._submit("看长文")
                await _wait_done(app, pilot)
                await pilot.pause(0.2)

                chat = app.query_one(ChatView)
                card = [c for c in chat.query(MessageCard)][-1]
                # 卡片必须同时带 "card" 基础类，否则 height:auto 不生效
                self.assertIn("card", card.classes)
                self.assertGreater(chat.max_scroll_y, 0,
                                   "长内容不可滚动：虚拟高度没有随内容增长")

                # PageUp 能往上翻
                await pilot.press("pageup")
                await pilot.pause(0.1)
                self.assertLess(chat.scroll_y, chat.max_scroll_y)

                # 滚动必须真的重绘（重写 watch_scroll_y 时漏掉 super() 的回归）
                card_top_before = card.region.y
                chat.scroll_home(animate=False)
                await pilot.pause(0.15)
                self.assertNotEqual(card.region.y, card_top_before,
                                    "scroll_y 变了但视口没有重绘")

                # 往上翻看时，新内容到达不应把视图拽回底部
                chat.scroll_to(y=chat.max_scroll_y / 2, animate=False)
                await pilot.pause(0.1)
                middle = chat.scroll_y
                card.append("\n\n追加的一段。")
                await card.flush()
                await pilot.pause(0.1)
                self.assertAlmostEqual(chat.scroll_y, middle, delta=1.0)

                # Ctrl+End 回到最新并恢复跟随
                await pilot.press("ctrl+end")
                await pilot.pause(0.1)
                self.assertEqual(chat.scroll_y, chat.max_scroll_y)
                self.assertTrue(chat._follow)

        asyncio.run(main())

    def test_pasted_escaped_unicode_is_repaired(self) -> None:
        """终端把中文送来是字面 `\\uXXXX` 时，要还原成真实字符。"""

        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                box = app.query_one(ChatInput)
                box.focus()

                box._forward_event(events.Paste("\\u4f60\\u597d\\uff0c\\u4e16\\u754c"))
                await pilot.pause()

                self.assertEqual(box.text, "你好，世界")
                self.assertFalse(has_non_ascii("\\u4f60\\u597d"))

        asyncio.run(main())

    # ------------------------------------------------------------------
    # 输入框前方的「思考中」提示条
    # ------------------------------------------------------------------
    def test_thinking_bar_shows_spinner_while_busy(self) -> None:
        """等待模型回复时输入框上方出现转圈动画，结束后回到空闲提示。"""

        async def main() -> None:
            app = build_app([{"text": "答完了"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                bar = app.query_one(ThinkingBar)
                self.assertFalse(bar.busy)

                # 关键：`_submit` 内部没有 await，事件循环不会被让出，
                # 所以紧接着的同步断言是确定性的（一 pause 脚本化模型就跑完了）
                await app._submit("问个问题")
                self.assertTrue(bar.busy)
                self.assertTrue(app.query_one(Spinner).running)
                self.assertFalse(bar.has_class("-idle"))

                frames = {app.query_one(Spinner).advance() for _ in range(4)}
                self.assertTrue(frames, "转圈动画必须能推进")

                await _wait_done(app, pilot)
                await pilot.pause()
                self.assertFalse(bar.busy)
                self.assertFalse(app.query_one(Spinner).running)
                self.assertTrue(bar.has_class("-idle"))

        asyncio.run(main())

    def test_thinking_bar_hint_switches_with_multiline(self) -> None:
        """单行 / 多行来回切换时，提示条文案跟着变。"""

        async def main() -> None:
            app = build_app([{"text": "x"}])
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                box = app.query_one(ChatInput)
                bar = app.query_one(ThinkingBar)
                box.focus()

                self.assertIn("Ctrl+J", bar._text.content)

                box.insert("甲")
                await pilot.press("ctrl+j")
                box.insert("乙")
                await pilot.pause()

                self.assertIn("多行输入", bar._text.content)

        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
