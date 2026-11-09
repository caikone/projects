# -*- coding: utf-8 -*-
"""Web 界面入口：`python run.py --ui web`。

职责只有三件：算静态资源目录、拉起 uvicorn、必要时打开浏览器。
所有业务都在 `web/server.py` 里转成 HTTP/SSE，本文件不碰会话与工具。
"""

from __future__ import annotations

import os
import socket
import threading
import webbrowser

from core.errors import FrontendError
from core.ports.frontend import Frontend, FrontendContext

#: 项目根目录（web/ 的上一级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 前端构建产物的默认位置
DEFAULT_ASSETS = os.path.join(PROJECT_ROOT, "frontend", "dist")


def resolve_assets(configured: str) -> str:
    """确定静态资源目录：配置优先，否则用 frontend/dist。"""
    if configured:
        return configured if os.path.isabs(configured) else os.path.join(PROJECT_ROOT, configured)
    return DEFAULT_ASSETS


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _open_later(url: str, delay: float = 1.2) -> None:
    timer = threading.Timer(delay, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


class WebFrontend(Frontend):
    """浏览器界面（React + SSE）。"""

    name = "web"
    description = "Web 界面（React + Slate + SSE，现代风格）"
    requires_tty = False
    #: 先进界面再连 MCP：浏览器不用等几秒白屏
    defer_mcp = True

    def run(self, ctx: FrontendContext) -> int:
        try:
            import uvicorn  # noqa: F401  （延迟导入：没装时给出可读错误）
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            raise FrontendError(
                "Web 界面需要 starlette 与 uvicorn：pip install starlette uvicorn（%s）" % exc)

        from .server import create_app

        settings = ctx.settings.web
        host, port = settings.host or "127.0.0.1", int(settings.port or 8760)
        assets = resolve_assets(settings.assets_dir)

        if not _port_available(host, port):
            raise FrontendError(
                "端口 %d 已被占用。请关闭占用该端口的程序，"
                "或修改 config.ini 的 [web] port。" % port)

        url = "http://%s:%d" % ("127.0.0.1" if host in ("0.0.0.0", "::") else host, port)
        has_assets = os.path.isfile(os.path.join(assets, "index.html"))

        print("[Web] 反诈助手已启动：%s" % url, flush=True)
        if has_assets:
            print("[Web] 静态资源：%s" % assets, flush=True)
        else:
            print("[Web] 前端尚未构建：cd frontend && npm install && npm run build"
                  "（首页会给出同样提示）", flush=True)
        if ctx.metadata.get("config_created"):
            print("[Web] 当前是自动生成的占位配置，请改好 [llm] 段后重启。", flush=True)
        print("[Web] 按 Ctrl+C 停止。", flush=True)

        if settings.open_browser:
            _open_later(url)

        app = create_app(ctx, assets_dir=assets if has_assets else "")
        try:
            uvicorn.run(app, host=host, port=port, log_level="warning",
                        access_log=False)
        except KeyboardInterrupt:  # pragma: no cover - 终端里 Ctrl+C
            pass
        return 0


__all__ = ["WebFrontend", "resolve_assets"]
