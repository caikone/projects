# -*- coding: utf-8 -*-
"""内置网页抓取工具：fetchWeb。

本模块是**适配器**：把 `services/webfetch.py` 的业务能力
翻译成模型可调用的 function schema + 参数解析。
抓取、校验、正文渲染逻辑一律在 WebFetchService，不在这里重复实现。
"""

from __future__ import annotations

from ..ports.tools import ToolOutcome
from ..services.webfetch import WebFetchService
from .base import ToolBuilder, text_param


def build_webfetch_tools(webfetch: WebFetchService) -> list:
    """构造网页抓取类工具列表。"""
    builder = ToolBuilder(namespace="webfetch")

    @builder.tool(
        name="fetchWeb",
        description=(
            "获取指定网页的完整正文（原文）并提取为 Markdown。"
            "当用户要求查看、获取、提取某个网页/文章的具体内容、原文或全文时调用。"
            "返回的 Markdown 就是该网页的正文原文（含标题、段落、列表、表格与链接），"
            "已去除导航、广告、页脚等噪音。"
            "如果用户要的是原文/全文/完整内容，请把返回的 Markdown 正文原样复述给用户，"
            "不要自行概括或压缩；只有在用户明确要「总结/概括」时才做归纳。"
        ),
        params={"url": text_param("目标网页的完整 URL，必须以 http:// 或 https:// 开头")},
        required=["url"],
    )
    def _fetch_web(args: dict):
        url = args.get("url")
        if url is None or not str(url).strip():
            return ToolOutcome.failure("缺少必需参数 url（目标网页地址）")
        return webfetch.fetch(str(url)).text

    return builder.build()


__all__ = ["build_webfetch_tools"]
