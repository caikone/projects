# -*- coding: utf-8 -*-
"""把「只写了 HTTP 启动方式」的 MCP Server 以 **stdio** 方式拉起来。

很多自己写的 MCP Server 源码长这样::

    mcp = MCPServer("anythingllm")

    @mcp.tool()
    def ask(...): ...

    if __name__ == "__main__":
        mcp.run(transport="streamable-http")     # ← 只支持 HTTP

TAgent 的 stdio 传输是**由它自己拉起并回收子进程**的，比让用户先手动
开一个 HTTP 服务再连要省事得多。这个桥接器就是为此存在：它不改对方源码，
只是在子进程里把项目目录加进 `sys.path`，取到 `MCPServer` 对象，然后以
stdio 方式运行。

用法（通常写在 `.mcp.json` 的 `args` 里）::

    python mcp_stdio_bridge.py <项目目录> <模块名> [变量名] [--env K=V ...]

    python .../mcp_stdio_bridge.py E:/projects/AnythingLLM_MCP server mcp
    python .../mcp_stdio_bridge.py E:/projects/pet_hospital_mcp pet_hospital_mcp.server mcp

约定：

    * **stdout 只允许出现 JSON-RPC 消息**——任何诊断信息都写 stderr，
      否则会破坏 stdio 协议（这也是 TAgent 默认丢弃 stderr 的原因，
      排查时设 `TAGENT_MCP_STDERR=1`）；
    * 模块导入失败 / 找不到对象时，打印可读错误后以非 0 退出，
      父进程会报「无法连接」，比静默的 `Connection closed` 好排查。
"""

from __future__ import annotations

import os
import sys


def _log(message: str) -> None:
    """诊断信息一律走 stderr，绝不污染 stdout 上的 JSON-RPC 流。"""
    sys.stderr.write("[mcp-stdio-bridge] %s\n" % message)
    sys.stderr.flush()


def _parse_args(argv: list[str]) -> tuple[str, str, str, dict[str, str]]:
    if len(argv) < 2:
        raise SystemExit(
            "用法：mcp_stdio_bridge.py <项目目录> <模块名> [变量名] [--env K=V ...]\n"
            "例如：mcp_stdio_bridge.py E:/projects/AnythingLLM_MCP server mcp")

    project_dir = argv[0]
    module_name = argv[1]
    # 变量名可省略，默认取约定俗成的 `mcp`
    attr = argv[2] if len(argv) > 2 and not argv[2].startswith("--") else "mcp"

    overrides: dict[str, str] = {}
    rest = argv[2:]
    index = 0
    while index < len(rest):
        item = rest[index]
        if item == "--env" and index + 1 < len(rest):
            key, _, value = rest[index + 1].partition("=")
            if key:
                overrides[key.strip()] = value
            index += 2
            continue
        if item == attr:  # 变量名本身，跳过
            index += 1
            continue
        _log("忽略无法识别的参数：%s" % item)
        index += 1

    return project_dir, module_name, attr, overrides


def main(argv: list[str]) -> int:
    project_dir, module_name, attr, overrides = _parse_args(argv)

    if overrides:
        os.environ.update(overrides)
    project_dir = os.path.abspath(os.path.expanduser(project_dir))
    if not os.path.isdir(project_dir):
        _log("项目目录不存在：%s" % project_dir)
        return 2

    # 项目目录优先，其次它的 src/ 布局（很多包用 src 布局）
    for path in (project_dir, os.path.join(project_dir, "src")):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)

    try:
        module = __import__(module_name, fromlist=["*"])
    except Exception as exc:
        _log("导入模块 %s 失败：%s: %s" % (module_name, type(exc).__name__, exc))
        return 3

    server = getattr(module, attr, None)
    if server is None:
        candidates = [name for name in dir(module)
                      if type(getattr(module, name)).__name__ == "MCPServer"]
        hint = "，可用的有：%s" % "、".join(candidates) if candidates else ""
        _log("模块 %s 里没有名为 %s 的对象%s" % (module_name, attr, hint))
        return 4
    if not hasattr(server, "run"):
        _log("%s.%s 不是 MCP Server（没有 run 方法）" % (module_name, attr))
        return 5

    _log("以 stdio 方式启动 %s.%s（项目目录 %s）" % (module_name, attr, project_dir))
    try:
        server.run("stdio")
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    except Exception as exc:
        _log("运行失败：%s: %s" % (type(exc).__name__, exc))
        return 6
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
