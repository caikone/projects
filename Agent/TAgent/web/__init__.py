# -*- coding: utf-8 -*-
"""Web 界面：把 core 的事件流转成 HTTP + SSE，交给浏览器里的 React 应用。

    web/frontend.py   Frontend 实现（`run.py --ui web` 的入口）
    web/server.py     Starlette 应用：API + SSE + 静态资源托管

本包是**唯一**允许依赖 starlette / uvicorn 的地方；core 与 tui 完全不感知。
"""

from __future__ import annotations

__all__ = ["WebFrontend"]


def __getattr__(name: str):  # 懒加载：没装 starlette 时 `--ui list` 仍能正常工作
    if name == "WebFrontend":
        from .frontend import WebFrontend

        return WebFrontend
    raise AttributeError(name)
