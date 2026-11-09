# -*- coding: utf-8 -*-
"""AGENTS.md 项目约定的渲染规则（纯函数，无 I/O）。

「一次注入、全程复用」的落点：
    注入的是一条**普通 user 消息**，位于 System Prompt 之后、对话之前。
    位置固定 + 内容不变 ⇒ 每次请求的前缀稳定 ⇒ 服务端 Prompt 缓存可以持续命中。
"""

from __future__ import annotations

#: 注入消息的标题行（也是「是否已注入」的识别标记之一）
AGENTS_HEADER = "【项目约定 · {filename}】"

#: 标题行之后的一句说明，明确告诉模型这段内容的效力
AGENTS_INSTRUCTION = (
    "以下是项目根目录 {filename} 的完整内容，是本项目的长期约定与规范。"
    "请在本次会话的后续所有回答与工具调用中始终遵守；"
    "如与用户当次指令冲突，以用户当次指令为准。"
)


def render_agents_message(filename: str, text: str, truncated: bool = False,
                          size: int = 0) -> str:
    """把 AGENTS.md 正文渲染成一条注入消息。

    Args:
        filename: 文件名（`AGENTS.md`）。
        text: 文件正文（已按上限截断）。
        truncated: 是否发生过截断。
        size: 文件原始字节数（仅用于截断提示）。
    """
    body = (text or "").strip()
    lines = [
        AGENTS_HEADER.format(filename=filename),
        AGENTS_INSTRUCTION.format(filename=filename),
        "",
        body,
    ]
    if truncated:
        lines += ["", "（原文共 %d 字节，超出单次注入上限，已截断；"
                     "需要细节请用 readFile 读取完整文件。）" % size]
    return "\n".join(lines).strip()


def looks_like_agents_message(content: str, filename: str = "AGENTS.md") -> bool:
    """判断一条消息是否就是注入的 AGENTS.md（用于防重复注入）。"""
    header = AGENTS_HEADER.format(filename=filename)
    return (content or "").lstrip().startswith(header)


__all__ = [
    "AGENTS_HEADER",
    "AGENTS_INSTRUCTION",
    "looks_like_agents_message",
    "render_agents_message",
]
