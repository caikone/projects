# -*- coding: utf-8 -*-
"""本地命令执行领域服务：校验参数、驱动执行、渲染成给模型看的文本。

业务规则（参数合法性、超时、输出长度上限、渲染格式）只在这里实现一次；
`tools/builtin_cli.py` 仅是把它翻译成 function schema 的适配器。
"""

from __future__ import annotations

from typing import Sequence

from ..domain.cli import CommandResult, CommandRun
from ..errors import CliArgumentError
from ..ports.cli import CommandRunner


class CommandService:
    """命令执行服务：拿到「程序名 + 参数」，产出「结构化结果 + 可回传文本」。"""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    # ------------------------------------------------------------------
    def run(self, command: str, args: Sequence[str] = ()) -> CommandRun:
        """执行命令并返回结构化结果 + 渲染文本。

        Raises:
            CliArgumentError: 命令名为空，或 args 不是字符串数组。
            CliNotFoundError / CliExecutionError: 见 `core.errors`。
        """
        command, args = self.validate(command, args)
        result = self._runner.run(command, args)
        return CommandRun(result=result, text=self.render(result))

    # ------------------------------------------------------------------
    @staticmethod
    def validate(command: str, args: Sequence[str] = ()) -> tuple[str, list[str]]:
        """校验并归一化「程序名 + 参数列表」。

        Raises:
            CliArgumentError: 命令名为空，或 args 类型不对。
        """
        command = (command or "").strip()
        if not command:
            raise CliArgumentError("缺少必需参数 command（要执行的程序名）")

        if args is None:
            args = []
        if isinstance(args, str):
            # 刻意在服务层就拦住：如果这里偷偷 shlex.split，等于把「字符串」
            # 又变成 shell 语义，与「参数列表执行」的教学目标相悖。
            raise CliArgumentError(
                "args 必须是字符串数组，例如 [\"status\", \"--short\"]；"
                "收到的是字符串 %r。请改用数组。" % args)
        if not isinstance(args, (list, tuple)):
            raise CliArgumentError("args 必须是字符串数组，收到的是 %s。" % type(args).__name__)

        normalized: list[str] = []
        for item in args:
            if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                normalized.append(str(item))
            else:
                raise CliArgumentError(
                    "args 里的每一项都必须是字符串，收到的是 %s。" % type(item).__name__)
        return command, normalized

    # ------------------------------------------------------------------
    @staticmethod
    def render(result: CommandResult) -> str:
        """把执行结果渲染成给模型的文本。

        结构：命令行 → 退出码/耗时 → stdout → stderr → 截断与超时说明。
        关键点：**退出码非 0 不是工具失败**，必须原样呈现，
        让模型结合 stderr 判断原因并决定下一步（改参数重试 / 换命令 / 向用户说明）。
        """
        parts: list[str] = [
            "$ %s" % result.command_line,
            "退出码：%s（耗时 %d ms）" % (result.exit_code_text, result.elapsed_ms),
        ]

        if result.stdout.strip():
            parts.append("【stdout】\n%s" % result.stdout.strip())
        if result.stderr.strip():
            parts.append("【stderr】\n%s" % result.stderr.strip())
        if not result.has_output:
            parts.append("（该命令没有产生任何输出）")

        notes: list[str] = []
        if result.timed_out:
            notes.append("执行超时已被终止，以上为终止前产生的部分输出。")
        if result.stdout_truncated:
            notes.append("stdout 共 %d 字节，过长已截断。" % result.stdout_bytes)
        if result.stderr_truncated:
            notes.append("stderr 共 %d 字节，过长已截断。" % result.stderr_bytes)
        if notes:
            parts.append("（%s）" % " ".join(notes))

        return "\n\n".join(parts)


__all__ = ["CommandService"]
