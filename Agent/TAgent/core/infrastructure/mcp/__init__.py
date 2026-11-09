# -*- coding: utf-8 -*-
"""MCP 基础设施适配器集合。

    - `json_config.JsonMcpConfigLoader`：读 `.mcp.json`
    - `sdk_client.SdkMcpClient`        ：官方 SDK 的同步封装
"""

from .json_config import DEFAULT_FILENAME, JsonMcpConfigLoader
from .sdk_client import SdkMcpClient, SdkMcpClientFactory, sdk_available

__all__ = [
    "DEFAULT_FILENAME",
    "JsonMcpConfigLoader",
    "SdkMcpClient",
    "SdkMcpClientFactory",
    "sdk_available",
]
