# -*- coding: utf-8 -*-
"""本地命令行领域值对象。

与 `domain/webpage.py` 同样的思路：
适配器的产出是**结构化对象**（退出码 / stdout / stderr / 耗时 / 截断标记），
由 `CommandService.render()` 负责渲染成给模型看的文本。
将来 Web / Desktop 界面可以直接消费结构化字段（例如按退出码着色），
而不必反解析字符串。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandResult:
    """一次本地命令执行的结果。

    Attributes:
        command:    程序名（模型传进来的那个，原样回传）。
        args:       参数列表（已归一化成字符串元组，便于哈希 / 比较）。
        stdout:     标准输出（可能已被截断）。
        stderr:     标准错误（可能已被截断）。
        exit_code:  退出码；被超时终止时为 `None`（拿不到退出码）。
        elapsed_ms: 执行耗时（毫秒）。
        timed_out:  是否因超过超时上限被终止。
        stdout_truncated / stderr_truncated: 对应输出是否被截断。
        stdout_bytes / stderr_bytes: 输出原始字节数（截断前）。
    """

    command: str = ""
    args: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    elapsed_ms: int = 0
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0

    # ------------------------------------------------------------------
    @property
    def argv(self) -> list[str]:
        """完整的参数列表（程序名 + 参数），即传给 `subprocess` 的那个列表。"""
        return [self.command, *self.args]

    @property
    def command_line(self) -> str:
        """可回显的命令行（仅用于展示，执行时**从不**拼成字符串）。"""
        return shlex.join(self.argv)

    @property
    def succeeded(self) -> bool:
        """是否「正常结束且退出码为 0」。"""
        return self.exit_code == 0 and not self.timed_out

    @property
    def has_output(self) -> bool:
        return bool(self.stdout.strip() or self.stderr.strip())

    @property
    def exit_code_text(self) -> str:
        """退出码的可读表达（超时时没有退出码）。"""
        if self.timed_out:
            return "无（执行超时被终止）"
        return str(self.exit_code)


@dataclass(frozen=True)
class CommandRun:
    """交给模型 / UI 的最终交付物：渲染好的文本 + 结构化结果。

    多这一层是为了让 `CommandService` 同时服务「文本回复」与
    「未来界面的结构化卡片」两种消费者（与 `FetchedPage` 同理）。
    """

    result: CommandResult = field(default_factory=CommandResult)
    text: str = ""

    def __str__(self) -> str:  # pragma: no cover - 便捷输出
        return self.text


__all__ = ["CommandResult", "CommandRun"]
