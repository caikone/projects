# -*- coding: utf-8 -*-
"""终端文本清洗工具的单元测试（不依赖 Textual，纯函数测试）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tui.text_utils import (  # noqa: E402
    has_non_ascii,
    normalize_newlines,
    repair_escaped_text,
    strip_trailing_blank_lines,
)


class RepairEscapedTextTest(unittest.TestCase):
    """终端把多字节字符送来是字面 \\uXXXX 时，必须还原成真实字符。"""

    def test_escaped_chinese(self) -> None:
        self.assertEqual(repair_escaped_text("\\u4f60\\u597d"), "你好")
        self.assertEqual(repair_escaped_text("\\u4f60\\u597d\\uff0c\\u4e16\\u754c"),
                         "你好，世界")

    def test_big_form(self) -> None:
        self.assertEqual(repair_escaped_text("\\U0001F600"), "\U0001F600")

    def test_hex_bytes_are_left_alone(self) -> None:
        """按字节转义的 UTF-8 太有歧义（可能是 latin-1 文本），不做猜测。"""
        self.assertEqual(repair_escaped_text("\\xe4\\xbd\\xa0"), "\\xe4\\xbd\\xa0")

    def test_mixed_with_ascii_text(self) -> None:
        self.assertEqual(repair_escaped_text("hello \\u4e16\\u754c"), "hello 世界")

    def test_plain_text_untouched(self) -> None:
        for text in ("hello", "你好", "", "a\\u1234b"[:1]):
            self.assertEqual(repair_escaped_text(text), text)

    def test_real_chinese_is_never_touched(self) -> None:
        """已经有真实中文 → 说明通道正常，绝不篡改用户写的字面量。"""
        text = "说明：\\u4f60 是 U+4F60"
        self.assertEqual(repair_escaped_text(text), text)

    def test_ascii_escapes_are_left_alone(self) -> None:
        """ASCII 转义不构成「被破坏的输入」特征，保持原样。"""
        self.assertEqual(repair_escaped_text("\\u0041\\u0042"), "\\u0041\\u0042")

    def test_surrogates_rejected(self) -> None:
        self.assertEqual(repair_escaped_text("\\ud83d"), "\\ud83d")

    def test_has_non_ascii(self) -> None:
        self.assertTrue(has_non_ascii("你好"))
        self.assertFalse(has_non_ascii("hello \\u4f60"))


class NewlineTest(unittest.TestCase):
    def test_normalize_newlines(self) -> None:
        self.assertEqual(normalize_newlines("a\r\nb"), "a\nb")
        self.assertEqual(normalize_newlines("a\rb"), "a\nb")
        self.assertEqual(normalize_newlines("a\nb"), "a\nb")

    def test_strip_trailing_blank_lines(self) -> None:
        self.assertEqual(strip_trailing_blank_lines("第一行\n第二行\n"), "第一行\n第二行")
        self.assertEqual(strip_trailing_blank_lines("单行"), "单行")
        # 有意保留的多行内容里的空行不动（只处理尾部）
        self.assertEqual(strip_trailing_blank_lines("a\n\nb"), "a\n\nb")


if __name__ == "__main__":
    unittest.main(verbosity=2)
