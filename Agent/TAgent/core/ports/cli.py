# -*- coding: utf-8 -*-
"""本地命令执行端口。

服务层只依赖这个抽象，不感知 `subprocess` 的存在，
因此测试里可以换成假实现，将来也能替换为「远程执行 / 容器内执行」等后端。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..domain.cli import CommandResult


class CommandRunner(ABC):
    """命令执行器抽象。"""

    @abstractmethod
    def run(self, command: str, args: Sequence[str] = ()) -> CommandResult:
        """执行一条命令并返回结构化结果。

        Args:
            command: 程序名（或完整路径）。
            args: 参数列表，逐项作为独立参数传递（不经过 shell）。

        Raises:
            CliNotFoundError: PATH 里找不到该程序。
            CliExecutionError: 进程无法启动（权限不足 / 不是可执行文件等）。
        """
        raise NotImplementedError


__all__ = ["CommandRunner"]
