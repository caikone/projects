# -*- coding: utf-8 -*-
"""TUI 前端实现：core 认识它的唯一入口。

`run.py` 通过 `core.frontend_registry` 发现并实例化这个类，
再把 `FrontendContext` 交给它——它对外部世界的全部依赖都在 ctx 里。
"""

from __future__ import annotations

import os
import sys

from core.ports.frontend import Frontend, FrontendContext

from .app import TAgentApp
from .console_utf8 import restore_factory


class TuiFrontend(Frontend):
    """基于 Textual 的终端界面。"""

    name = "tui"
    description = "终端 TUI 界面（Textual，默认）"
    requires_tty = True

    def run(self, ctx: FrontendContext) -> int:
        if requires_headless_fallback():
            # 没有可用终端时给出明确提示，而不是抛一堆 Textual 内部错误
            print("当前环境没有可用的交互式终端，无法启动 TUI。", file=sys.stderr)
            return 2

        # Windows 上把控制台切成 UTF-8，避免中文被代码页吞成 `?` 或字面转义；
        # 退出时必定恢复用户的原始代码页。
        restore = restore_factory()
        try:
            app = TAgentApp(ctx)
            app.run()
        finally:
            if restore is not None:
                restore()
        return 0

    def shutdown(self) -> None:
        return None


def requires_headless_fallback() -> bool:
    """判断当前进程是否运行在可用终端中。"""
    if os.environ.get("TAGENT_HEADLESS"):
        return True
    try:
        return not sys.stdin.isatty()
    except Exception:  # pragma: no cover - 极端环境下 isatty 可能失败
        return False


__all__ = ["TuiFrontend", "requires_headless_fallback"]
