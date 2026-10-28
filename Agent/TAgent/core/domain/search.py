# -*- coding: utf-8 -*-
"""联网搜索领域模型：搜索结果的结构化表达。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchSource:
    """一条参考来源。"""

    #: 来源标题（缺失时由适配器用 URL 兜底）
    title: str = ""
    #: 来源链接
    url: str = ""


@dataclass(frozen=True)
class SearchReport:
    """一次搜索的完整结果。

    Attributes:
        summary: 搜索摘要（智能搜索生成版返回的答案正文，已去除引用角标）。
        sources: 参考来源列表（标题 + URL）。
    """

    summary: str = ""
    sources: list[SearchSource] = field(default_factory=list)


__all__ = ["SearchSource", "SearchReport"]
