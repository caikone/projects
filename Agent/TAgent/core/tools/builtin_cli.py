# -*- coding: utf-8 -*-
"""内置命令行工具：RunCli。

本模块是**适配器**：把 `services/cli.py` 的业务能力
翻译成模型可调用的 function schema + 参数解析。
执行、校验、输出渲染逻辑一律在 CommandService，不在这里重复实现。
"""

from __future__ import annotations

from ..ports.tools import ToolOutcome
from ..services.cli import CommandService
from .base import ToolBuilder, string_array_param, text_param


def build_cli_tools(cli: CommandService) -> list:
    """构造本地命令行类工具列表。"""
    builder = ToolBuilder(namespace="cli")

    @builder.tool(
        name="RunCli",
        description=(
            "在本机执行已安装的命令行程序（CLI），返回标准输出、标准错误与退出码。"
            "参数：command 为程序名（或完整路径），args 为参数**数组**，"
            "例如 command=\"git\"、args=[\"status\", \"--short\"]。"
            "执行方式：程序名与参数拼成列表后以 shell=False 运行，"
            "不会经过 shell 解释，因此 & | > 等字符会被当作普通字符，不存在命令注入。"
            "读结果：exit code 为 0 表示命令自身执行成功；非 0 不代表工具故障，"
            "请结合 stderr 判断原因，再决定修正参数重试、换一条命令或向用户说明。"
            "输出过长会被截断并标注（保留开头与结尾）。"
            "注意：本工具以运行 TAgent 的当前用户权限直接执行，"
            "请勿执行删除、格式化等破坏性或不可逆的命令，除非用户明确要求。"
        ),
        params={
            "command": text_param(
                "要执行的程序名（如 git / python / curl / ipconfig）或可执行文件的完整路径"),
            "args": string_array_param(
                "参数数组，逐项作为独立参数传入；没有参数时省略或传 []"),
        },
        required=["command"],
    )
    def _run_cli(args: dict):
        command = args.get("command")
        if command is None or not str(command).strip():
            return ToolOutcome.failure("缺少必需参数 command（要执行的程序名）")
        return cli.run(str(command), args.get("args") or []).text

    return builder.build()


__all__ = ["build_cli_tools"]
