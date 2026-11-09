# -*- coding: utf-8 -*-
"""配置模板：缺配置自动生成 + 新分段解析。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config.loader import load_settings  # noqa: E402
from core.config.template import (  # noqa: E402
    MOCK_API_KEY,
    MOCK_BASE_URL,
    ensure_local_config,
    render_template,
)
from core.errors import ConfigError  # noqa: E402


class TemplateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_complete_file(self) -> None:
        path, created = ensure_local_config(self.tmp.name)
        self.assertTrue(created)
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        for section in ("[llm]", "[agent]", "[ui]", "[sessions]",
                        "[agent_docs]", "[mcp]", "[context]", "[cli]", "[webfetch]"):
            self.assertIn(section, content)
        self.assertIn(MOCK_API_KEY, content)
        self.assertIn(MOCK_BASE_URL, content)

    def test_does_not_overwrite(self) -> None:
        path, _ = ensure_local_config(self.tmp.name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[llm]\napi_key = mine\n")
        path2, created = ensure_local_config(self.tmp.name)
        self.assertFalse(created)
        self.assertEqual(path, path2)
        with open(path2, encoding="utf-8") as handle:
            self.assertIn("mine", handle.read())

    def test_generated_file_loads(self) -> None:
        path, _ = ensure_local_config(self.tmp.name)
        settings, used = load_settings(path)
        self.assertEqual(used, path)
        self.assertEqual(settings.llm.api_key, MOCK_API_KEY)
        self.assertEqual(settings.llm.model, "mock-model")
        # 默认值应与代码默认值一致（模板只是把它们写出来）
        self.assertEqual(settings.ui.sidebar_width, 30)
        self.assertTrue(settings.sessions.autosave)
        self.assertEqual(settings.agents_doc.filename, "AGENTS.md")
        self.assertTrue(settings.mcp_enabled)
        self.assertEqual(settings.context_summary_tokens, 5000)
        self.assertEqual(settings.cli_timeout, 30.0)

    def test_render_template_is_complete_ini(self) -> None:
        text = render_template()
        self.assertIn("api_key = mock-api-key", text)
        self.assertNotIn("{", text)


class SectionParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, extra: str) -> str:
        path = os.path.join(self.tmp.name, "config.ini")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "[llm]\nbase_url = https://example.com/v1\n"
                "api_key = k\nmodel = m\n\n" + extra)
        return path

    def test_sections_override_defaults(self) -> None:
        path = self._write(
            "[ui]\nevent_drain_interval_ms = 10\nsidebar_width = 44\n"
            "tool_panel_width = 50\ncommand_panel_rows = 5\ninput_height = 9\n"
            "tool_preview_chars = 120\nspinner_interval_ms = 40\n\n"
            "[sessions]\ndir = ./sess\nautosave = false\nmax_listed = 3\n\n"
            "[agent_docs]\nenabled = false\nfilename = CLAUDE.md\nmax_bytes = 1024\n\n"
            "[mcp]\nenabled = false\ntimeout = 5\nconnect_timeout = 2\n"
            "max_result_chars = 1000\ntool_description_chars = 200\n\n"
            "[context]\nwindow = 32768\nthreshold = 0.5\nsummary_tokens = 1000\n"
            "recall_max_chars = 2000\ndir = ./ctx\n\n"
            "[cli]\ntimeout = 9\nmax_stdout_chars = 100\nmax_stderr_chars = 50\n\n"
            "[webfetch]\ntimeout = 12\nconnect_timeout = 4\nmax_html_bytes = 2048\n"
        )
        settings, _ = load_settings(path)
        self.assertEqual(settings.ui.event_drain_interval_ms, 10)
        self.assertEqual(settings.ui.sidebar_width, 44)
        self.assertEqual(settings.ui.command_panel_rows, 5)
        self.assertEqual(settings.ui.input_height, 9)
        self.assertAlmostEqual(settings.ui.spinner_interval, 0.04)
        self.assertEqual(settings.sessions.directory, "./sess")
        self.assertFalse(settings.sessions.autosave)
        self.assertEqual(settings.sessions.max_listed, 3)
        self.assertFalse(settings.agents_doc.enabled)
        self.assertEqual(settings.agents_doc.filename, "CLAUDE.md")
        self.assertEqual(settings.agents_doc.max_bytes, 1024)
        self.assertFalse(settings.mcp_enabled)
        self.assertEqual(settings.mcp_timeout, 5.0)
        self.assertEqual(settings.mcp_max_result_chars, 1000)
        self.assertEqual(settings.mcp_tool_description_chars, 200)
        self.assertEqual(settings.context_window, 32768)
        self.assertEqual(settings.compact_threshold, 0.5)
        self.assertEqual(settings.context_summary_tokens, 1000)
        self.assertEqual(settings.context_recall_max_chars, 2000)
        self.assertEqual(settings.cli_timeout, 9.0)
        self.assertEqual(settings.cli_max_stdout_chars, 100)
        self.assertEqual(settings.webfetch_timeout, 12.0)
        self.assertEqual(settings.webfetch_max_html_bytes, 2048)

    def test_dedicated_section_wins_over_agent_section(self) -> None:
        path = self._write("[agent]\nmcp_timeout = 99\n\n[mcp]\ntimeout = 7\n")
        settings, _ = load_settings(path)
        self.assertEqual(settings.mcp_timeout, 7.0)

    def test_invalid_int_raises(self) -> None:
        path = self._write("[ui]\nsidebar_width = abc\n")
        with self.assertRaises(ConfigError):
            load_settings(path)

    def test_unknown_keys_are_ignored(self) -> None:
        path = self._write("[ui]\nnot_a_setting = 1\n")
        settings, _ = load_settings(path)
        self.assertEqual(settings.ui.sidebar_width, 30)


if __name__ == "__main__":
    unittest.main()
