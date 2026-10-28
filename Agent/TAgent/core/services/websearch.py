# -*- coding: utf-8 -*-
"""联网搜索领域服务：校验输入与配置，把搜索报告整理成给模型看的文本。

业务规则只在这里实现一次；`tools/builtin_search.py` 仅是把它翻译成
function schema 的适配器，不重复任何逻辑。
"""

from __future__ import annotations

from ..config.models import SearchSettings
from ..domain.search import SearchReport
from ..errors import SearchError
from ..ports.search import SearchClient


class WebSearchService:
    """联网搜索服务。"""

    def __init__(self, settings: SearchSettings, client: SearchClient) -> None:
        self._settings = settings
        self._client = client

    # ------------------------------------------------------------------
    def search_text(self, query: str) -> str:
        """执行搜索并返回整理好的文本（摘要 + 参考来源）。

        Raises:
            SearchError: 关键词为空、缺少配置，或客户端报告的各类搜索错误。
        """
        query = (query or "").strip()
        if not query:
            raise SearchError("搜索关键词不能为空")
        if not self._settings.configured:
            raise SearchError(
                "缺少搜索配置：请在 config.ini 的 [search] 段配置 baidu_qianfan_api_key")

        report = self._client.search(query)
        return self.format_report(report)

    # ------------------------------------------------------------------
    @staticmethod
    def format_report(report: SearchReport) -> str:
        """把搜索报告整理为结构清晰的文本，供模型引用作答。"""
        lines: list[str] = []
        if report.summary:
            lines.append("【搜索摘要】")
            lines.append(report.summary)
        if report.sources:
            if lines:
                lines.append("")
            lines.append("【参考来源】")
            for index, source in enumerate(report.sources, 1):
                lines.append("%d. %s" % (index, source.title))
                if source.url:
                    lines.append("   %s" % source.url)
        return "\n".join(lines)


__all__ = ["WebSearchService"]
