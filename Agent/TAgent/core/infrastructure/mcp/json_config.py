# -*- coding: utf-8 -*-
"""`.mcp.json` 读取与校验。

按 MCP 生态的约定，配置形如::

    {
      "mcpServers": {
        "Bazi-MCP": {"type": "streamable_http", "url": "https://.../mcp"},
        "local-fs":  {"command": "npx", "args": ["-y", "@mcp/server-filesystem", "."]}
      }
    }

本模块只负责「文件 → 规格」：读盘、解析 JSON、调用领域层的纯函数校验。
连接与协议交互在 `sdk_client.py`。
"""

from __future__ import annotations

import json
import os

from ...domain.mcp import McpConfigIssue, McpServerSpec, parse_mcp_config
from ...errors import McpConfigError

#: 默认文件名（放在启动目录，即「当前项目」根）
DEFAULT_FILENAME = ".mcp.json"


class JsonMcpConfigLoader:
    """从 `.mcp.json` 读取 MCP Server 配置。"""

    def __init__(self, path: str | None = None, *,
                 project_root: str | None = None,
                 default_timeout: float = 60.0) -> None:
        self._path = path or ""
        self._project_root = project_root or os.getcwd()
        self._default_timeout = default_timeout

    # ------------------------------------------------------------------
    @property
    def path(self) -> str:
        """实际要读取的配置文件路径。"""
        if self._path:
            return self._path
        return os.path.join(self._project_root, DEFAULT_FILENAME)

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    # ------------------------------------------------------------------
    def load(self) -> tuple[list[McpServerSpec], list[McpConfigIssue], str]:
        """读取并解析配置。

        Returns:
            (规格列表, 配置问题列表, 实际读取的路径)。文件不存在时返回
            `([], [], "")`——没有 MCP 配置是正常状态，不是错误。

        Raises:
            McpConfigError: 文件存在但读不了 / JSON 非法 / 顶层结构不可用。
        """
        path = self.path
        if not os.path.isfile(path):
            if self._path:  # 显式指定却不存在，属于用户配置错误
                raise McpConfigError("找不到 MCP 配置文件：%s" % path)
            return [], [], ""

        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                raw = handle.read()
        except OSError as exc:
            raise McpConfigError("无法读取 MCP 配置文件 %s：%s" % (path, exc))

        if not raw.strip():
            return [], [], path

        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise McpConfigError("MCP 配置文件不是合法的 JSON：%s（%s）" % (path, exc))

        # 允许 JSON 里写注释（部分用户会加 // 说明）——这里不做兼容，
        # 出错时把原因说清楚即可，避免引入非标准解析器。
        try:
            specs, issues = parse_mcp_config(
                payload,
                default_timeout=self._default_timeout,
                base_dir=os.path.dirname(os.path.abspath(path)),
            )
        except ValueError as exc:
            raise McpConfigError("MCP 配置文件结构不正确：%s（%s）" % (path, exc))

        return specs, issues, path


__all__ = ["JsonMcpConfigLoader", "DEFAULT_FILENAME"]
