#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TAgent 统一启动入口。

    python run.py                       # 启动默认界面（TUI）
    python run.py --ui tui              # 显式指定界面
    python run.py --ui list             # 列出所有已注册界面
    python run.py "你的问题"             # 带一个首问启动
    python run.py --config ./my.ini      # 指定配置文件

新增界面时**不需要改动本文件**：只要在项目根目录新建与 `core/` 平级的包，
在里面实现 `Frontend` 子类，`core.frontend_registry` 会自动发现。
"""

from __future__ import annotations

import argparse
import os
import sys

# 保证无论从哪个目录启动，都能 import 到 core / tui 等顶层包
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.errors import ConfigError, FrontendError, TAgentError  # noqa: E402
from core.frontend_registry import (  # noqa: E402
    available_frontends,
    create_frontend,
    discover_frontends,
    load_errors,
)
from core.ports.frontend import Frontend  # noqa: E402

DEFAULT_UI = "tui"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="TAgent —— core 与界面分离的终端智能助手",
    )
    parser.add_argument("prompt", nargs="*", help="启动后立即发送的第一条提问")
    parser.add_argument("--ui", default=DEFAULT_UI, help="选择界面（如 tui）；--ui list 查看全部")
    parser.add_argument("--config", default=None, help="指定 config.ini 路径")
    parser.add_argument("--max-rounds", type=int, default=None,
                        help="覆盖单轮最大工具循环次数")
    parser.add_argument("--verbose", action="store_true", help="输出前端发现过程的诊断信息")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 1) 发现所有界面实现
    discover_frontends(verbose=args.verbose)

    if args.ui == "list":
        return _print_frontends()

    # 2) 先解析界面（纯本地动作，失败得快，不必等配置与网络）
    try:
        frontend = create_frontend(args.ui)
    except FrontendError as exc:
        print("[界面错误] %s" % exc, file=sys.stderr)
        return 1
    if not isinstance(frontend, Frontend):  # pragma: no cover
        print("[界面错误] %s 不是合法的 Frontend 实现" % args.ui, file=sys.stderr)
        return 1

    # 3) 组装核心（配置 → LLM 客户端 → 工具 → Agent 服务）
    try:
        from core.container import AppContainer

        overrides = {"max_tool_rounds": args.max_rounds} if args.max_rounds else None
        container = AppContainer.build(config_path=args.config, overrides=overrides)
    except ConfigError as exc:
        print("[配置错误] %s" % exc, file=sys.stderr)
        return 1

    # 4) 交给界面
    initial_prompt = " ".join(args.prompt).strip() or None
    ctx = container.build_context(initial_prompt=initial_prompt)

    try:
        return frontend.run(ctx)
    except TAgentError as exc:
        print("[运行错误] %s" % exc, file=sys.stderr)
        return 1
    finally:
        container.close()


def _print_frontends() -> int:
    """`--ui list`：列出可用界面，并诊断未能加载的界面包。"""
    frontends = available_frontends()
    broken = load_errors()

    if not frontends and not broken:
        print("（尚未发现任何界面实现）")
        return 1

    print("已注册的界面：")
    for name, desc in sorted(frontends.items()):
        print("  %-10s %s" % (name, desc))
    if not frontends:
        print("（空）")

    if broken:
        print("\n未能加载的界面包：")
        for package, reason in sorted(broken.items()):
            print("  %-10s %s" % (package, reason))
        print("\n用 --verbose 可查看发现过程的完整诊断。")
    return 0 if frontends else 1


if __name__ == "__main__":
    sys.exit(main())
