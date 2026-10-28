# -*- coding: utf-8 -*-
"""终端输入文本的清洗工具。

背景：某些 Windows 终端（尤其是 legacy console host）交付非 ASCII 字符时，
走的不是「粘贴 / 可打印键」路径，而是把字符序列化成字面的
`\\u4f60\\u597d` 这样的转义序列塞进缓冲区，于是输入框里看到的是编码而不是中文。

这一层负责把这类**字面转义序列**还原成真实字符。
"""

from __future__ import annotations

import re
import unicodedata

#: 匹配 \uXXXX / \UXXXXXXXX 两种字面的 unicode 转义。
#: 刻意**不支持** `\xNN`：按字节转义的 UTF-8（`\xe4\xbd\xa0`）无法与
#: 「用户真的在输入 latin-1 文本」区分开，猜错就是乱码。
_ESCAPE_RE = re.compile(
    r"\\u([0-9a-fA-F]{4})"
    r"|\\U([0-9a-fA-F]{8})"
)

#: 认为可以安全还原的字符类别（字母 / 数字 / 标点 / 符号 / 空白）
_SAFE_CATEGORIES = frozenset({"L", "M", "N", "P", "S", "Z"})


def has_non_ascii(text: str) -> bool:
    """文本中是否已经含真实非 ASCII 字符。"""
    return any(ord(char) > 0x7F for char in text)


def _safe_char(code: int) -> str:
    """把码点转成字符；只接受「多字节且可打印」的字符。

    ASCII 范围内的转义（`\\x41`、`\\u0041`）一律不动——
    用户可能是真的在输入字面量，而我们只关心中文这类多字节字符被转义的场景。
    """
    if code <= 0x7F:
        return ""
    if 0xD800 <= code <= 0xDFFF:  # 代理区，无法单独成字符
        return ""
    try:
        char = chr(code)
    except ValueError:
        return ""
    if unicodedata.category(char)[0] not in _SAFE_CATEGORIES:
        return ""
    return char


def repair_escaped_text(text: str) -> str:
    """把文本里的字面 unicode 转义（`\\uXXXX`）还原为真实字符。

    只在「整段文本里没有任何真实非 ASCII 字符」时才动手——
    一旦已经有正常中文，说明传输通道没问题，用户就是在写字面量，绝不篡改。

    Args:
        text: 原始文本。

    Returns:
        还原后的文本；无需改动时原样返回。
    """
    if "\\" not in text or not _ESCAPE_RE.search(text):
        return text
    if has_non_ascii(text):
        return text

    changed = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        for group in match.groups():
            if group is None:
                continue
            char = _safe_char(int(group, 16))
            if char:
                changed = True
                return char
        return match.group(0)

    repaired = _ESCAPE_RE.sub(_replace, text)
    return repaired if changed else text


def normalize_newlines(text: str) -> str:
    """把 Windows 剪贴板风格的换行统一换成 `\\n`。"""
    if "\r\n" in text:
        return text.replace("\r\n", "\n")
    if "\r" in text:
        return text.replace("\r", "\n")
    return text


def strip_trailing_blank_lines(text: str) -> str:
    """去掉尾部空行（多行输入里，`Enter` 发送前总会多出一个换行）。"""
    stripped = text.rstrip("\n")
    return stripped if stripped != text else text


__all__ = [
    "has_non_ascii",
    "normalize_newlines",
    "repair_escaped_text",
    "strip_trailing_blank_lines",
]
