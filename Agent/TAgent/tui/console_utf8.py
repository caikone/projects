# -*- coding: utf-8 -*-
"""Windows 控制台输入输出代码页设置。

为什么需要它
------------------------------------------------------------------------
在 Windows 上，控制台（conhost / Windows Terminal）的**输入代码页**
决定了终端用什么编码把按键交给程序。代码页不是 UTF-8（65001）时，
中文这类字符在到达应用之前就可能被换成 `?`，甚至退化成字面转义序列，
表现为「输入框里出现 unicode 编码而不是中文」。

这里在启动 TUI 前把输入 / 输出代码页切成 UTF-8，退出时**原样恢复**——
进程结束不该改变用户终端的既有状态。
"""

from __future__ import annotations

import os
from typing import Callable

#: UTF-8 代码页
CP_UTF8 = 65001


def restore_factory() -> Callable[[], None] | None:
    """把控制台切换到 UTF-8，返回用于恢复原状的回调函数。

    Returns:
        `None` 表示当前平台不是 Windows（或没有可用的 console API），无需处理。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        old_input = int(kernel32.GetConsoleCP())
        old_output = int(kernel32.GetConsoleOutputCP())
        kernel32.SetConsoleCP(CP_UTF8)
        kernel32.SetConsoleOutputCP(CP_UTF8)
    except Exception:  # 非控制台环境 / 权限受限：静默放弃，不要打断启动
        return None

    def restore() -> None:
        try:
            kernel32.SetConsoleCP(old_input)
            kernel32.SetConsoleOutputCP(old_output)
        except Exception:  # pragma: no cover - 退出路径上的异常不该外泄
            pass

    return restore


__all__ = ["CP_UTF8", "restore_factory"]
