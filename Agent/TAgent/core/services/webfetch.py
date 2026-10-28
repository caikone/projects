# -*- coding: utf-8 -*-
"""网页抓取领域服务：校验 URL、驱动抓取、渲染成给模型看的文本。

业务规则（URL 合法性、正文长度上限、文本渲染格式）只在这里实现一次；
`tools/builtin_webfetch.py` 仅是把它翻译成 function schema 的适配器。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..domain.webpage import FetchedPage, WebDocument
from ..errors import InvalidURLError
from ..ports.webpage import WebPageFetcher

#: 正文 Markdown 的最大字符数（超出截断，控制上下文 Token 消耗）
_DEFAULT_MAX_CHARS = 20000

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

#: 正文末尾追加来源与压缩比信息所用的标题
_SOURCE_HEADER = "【来源】"


class WebFetchService:
    """网页抓取服务：拿到 URL，产出「Markdown 正文 + 结构化文档」。"""

    def __init__(self, fetcher: WebPageFetcher, max_chars: int = _DEFAULT_MAX_CHARS) -> None:
        self._fetcher = fetcher
        self._max_chars = max_chars

    # ------------------------------------------------------------------
    def fetch(self, url: str) -> FetchedPage:
        """抓取网页并返回结构化结果。

        Args:
            url: 目标网页 URL。

        Raises:
            InvalidURLError: URL 非法或不是 http/https。
            WebFetchError: 抓取 / 解析 / 无正文等（见 `core.errors`）。
        """
        url = self._validate_url(url)
        document = self._fetcher.fetch(url)
        text = self.render_document(document)
        return FetchedPage(document=document, text=text)

    # ------------------------------------------------------------------
    def render_document(self, document: WebDocument) -> str:
        """把抓取结果渲染成给模型的 Markdown 文本。

        结构：标题 + 正文 + 来源脚注。正文超长时截断并明确标注，
        让模型知道「这是被截断的」，避免把片段当全貌。
        """
        markdown = document.markdown
        truncated = document.truncated
        if len(markdown) > self._max_chars:
            markdown = markdown[: self._max_chars]
            truncated = True

        parts: list[str] = []
        if document.title:
            parts.append("# %s" % document.title)
        parts.append(markdown)
        parts.append("")
        parts.append("%s %s" % (_SOURCE_HEADER, document.final_url or document.url))
        parts.append("（%s）" % document.compression)
        if truncated:
            parts.append("（正文已截断至 %d 字符，如需更多请访问原文）" % self._max_chars)

        return "\n\n".join(parts).strip()

    # ------------------------------------------------------------------
    @staticmethod
    def validate_url(url: str) -> str:
        """校验并规范化 URL。

        Raises:
            InvalidURLError: 不是 http/https 或结构非法。
        """
        url = (url or "").strip()
        if not url:
            raise InvalidURLError(url, "为空")
        if not _SCHEME_RE.match(url):
            raise InvalidURLError(url, "只支持 http/https 协议")
        parsed = urlparse(url)
        if not parsed.netloc:
            raise InvalidURLError(url, "缺少主机名")
        return url

    def _validate_url(self, url: str) -> str:
        return self.validate_url(url)


__all__ = ["WebFetchService"]
