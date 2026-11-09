# -*- coding: utf-8 -*-
"""斜杠命令的**定义与解析**（纯数据，不依赖任何 UI 组件）。

放在 TUI 层而不是 core：这些命令是「界面操作」（新建会话、退出、打开面板），
不是业务能力；Web 版可以有自己的一套命令。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    """一条斜杠命令。"""

    #: 主名称，含前导斜杠，如 `/new`
    name: str
    #: 一句话说明（补全面板与 /help 共用）
    description: str
    #: 其它写法（都会被解析到同一条命令）
    aliases: tuple[str, ...] = ()
    #: 是否需要用户在命令后补充参数（补全时只填入不执行）
    takes_args: bool = False

    @property
    def usage(self) -> str:
        return self.name + (" …" if self.takes_args else "")


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "显示全部命令与快捷键", aliases=("/?", "/h")),
    SlashCommand("/new", "新建会话（先自动保存当前会话）"),
    SlashCommand("/resume", "从历史会话列表中选择并恢复"),
    SlashCommand("/save", "保存当前会话"),
    SlashCommand("/sessions", "列出历史会话（不切换）", aliases=("/ls",)),
    SlashCommand("/compact", "压缩上下文（归档后生成摘要）"),
    SlashCommand("/clear", "清空当前对话历史（保留会话与 AGENTS.md）", aliases=("/reset",)),
    SlashCommand("/context", "查看上下文用量与归档状态", aliases=("/usage",)),
    SlashCommand("/tools", "查看当前可用工具"),
    SlashCommand("/mcp", "查看 MCP 服务器与工具状态"),
    SlashCommand("/skills", "查看已发现的 Skill"),
    SlashCommand("/model", "查看当前模型与接口地址"),
    SlashCommand("/config", "查看生效的配置文件与关键参数"),
    SlashCommand("/quit", "退出 TAgent", aliases=("/exit", "/q")),
)

#: 名称 / 别名 → 命令
_BY_NAME: dict[str, SlashCommand] = {}
for _command in COMMANDS:
    _BY_NAME[_command.name] = _command
    for _alias in _command.aliases:
        _BY_NAME[_alias] = _command


def resolve(name: str) -> SlashCommand | None:
    """把用户输入的命令名（含 `/`）解析成命令对象。"""
    return _BY_NAME.get((name or "").strip().lower())


def split_command(text: str) -> tuple[str, str]:
    """把 `/resume 3` 拆成 (`/resume`, `3`)。"""
    stripped = (text or "").strip()
    if not stripped:
        return "", ""
    parts = stripped.split(maxsplit=1)
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


def filter_commands(text: str) -> list[SlashCommand]:
    """按输入前缀过滤命令（用于补全面板）。

    规则：`/` 显示全部；`/re` 显示名称或别名以 `/re` 开头的命令；
    已经输入空格（开始写参数）时不再提示。
    """
    stripped = (text or "").strip()
    if not stripped.startswith("/") or " " in stripped or "\n" in stripped:
        return []
    prefix = stripped.lower()
    matched = [
        command for command in COMMANDS
        if command.name.startswith(prefix)
        or any(alias.startswith(prefix) for alias in command.aliases)
    ]
    # 完全匹配的排在最前：用户已经打全了，回车就是执行它
    matched.sort(key=lambda item: (item.name != prefix, item.name))
    return matched


def help_text() -> str:
    """`/help` 的正文。"""
    width = max(len(command.usage) for command in COMMANDS)
    lines = ["可用命令（输入 / 会弹出补全面板，↑↓ 选择 · Enter 执行 · Tab 补全）："]
    for command in COMMANDS:
        lines.append("  %-*s  %s" % (width, command.usage, command.description))
    lines += [
        "",
        "快捷键：",
        "  Ctrl+N 新会话 · Ctrl+R 历史会话 · Ctrl+S 保存 · Ctrl+X 中止本轮",
        "  Ctrl+T 工具面板 · Ctrl+B 边栏 · Ctrl+K 清空工具面板 · Ctrl+Q 退出",
    ]
    return "\n".join(lines)


__all__ = [
    "COMMANDS",
    "SlashCommand",
    "filter_commands",
    "help_text",
    "resolve",
    "split_command",
]
