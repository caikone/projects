# -*- coding: utf-8 -*-
"""内置联网搜索工具：searchwdb。

本模块是**适配器**：把 `services/websearch.py` 的业务能力
翻译成模型可调用的 function schema + 参数解析。
搜索与结果整理逻辑一律在 WebSearchService 中，不在这里重复实现。
"""

from __future__ import annotations

from ..ports.tools import ToolOutcome
from ..services.websearch import WebSearchService
from .base import ToolBuilder, text_param


def build_search_tools(websearch: WebSearchService) -> list:
    """构造联网搜索类工具列表。"""
    builder = ToolBuilder(namespace="search")

    @builder.tool(
        name="searchwdb",
        description=(
            "联网搜索：调用百度千帆智能搜索获取互联网上的最新信息。"
            "当用户询问实时新闻、最新资讯、天气、价格等本地知识无法回答的问题时调用。"
        ),
        params={"query": text_param("搜索关键词或问题")},
        required=["query"],
    )
    def _search(args: dict):
        query = args.get("query")
        if query is None or not str(query).strip():
            return ToolOutcome.failure("缺少必需参数 query（搜索关键词或问题）")
        return websearch.search_text(str(query))

    return builder.build()


__all__ = ["build_search_tools"]
