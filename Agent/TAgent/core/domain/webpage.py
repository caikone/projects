# -*- coding: utf-8 -*-
"""网页抓取领域值对象。

与 `domain/filesystem.py` 同样的思路：
抓取适配器的产出是**结构化对象**，由 `WebFetchService.render_document()`
负责渲染成给模型看的文本。将来 Web / Desktop 界面可以直接消费结构化字段
（标题、正文 Markdown、耗时、是否被截断），而不必反解析字符串。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WebDocument:
    """一次网页抓取的完整结果。

    Attributes:
        url:        原始请求的 URL（模型传进来的那个，原样回传）。
        final_url:  跟随重定向之后的实际 URL（无重定向时等于 url）。
        status_code: HTTP 状态码（未知为 0）。
        title:      从正文中提取的页面标题（可能为空）。
        markdown:   正文的 Markdown 表达（已去除导航、广告、页脚等噪音）。
        html_bytes: 原始 HTML 字节数（与 markdown 长度差反映「压缩比」）。
        elapsed_ms: 抓取 + 提取总耗时。
        truncated:  正文是否因超过上限被截断。
        link_count: 正文中的链接数量（0 表示本次未包含链接）。
    """

    url: str = ""
    final_url: str = ""
    status_code: int = 0
    title: str = ""
    markdown: str = ""
    html_bytes: int = 0
    elapsed_ms: int = 0
    truncated: bool = False
    link_count: int = 0

    # ------------------------------------------------------------------
    @property
    def char_count(self) -> int:
        """正文 Markdown 的字符数。"""
        return len(self.markdown)

    @property
    def is_usable(self) -> bool:
        """是否有可交付的正文。"""
        return bool(self.markdown.strip())

    @property
    def compression(self) -> str:
        """压缩比描述：把「省了多少上下文」量化出来，便于判断抓取策略是否有效。"""
        if not self.html_bytes:
            return "未知"
        ratio = self.char_count / max(self.html_bytes, 1)
        return "HTML %d 字节 → 正文 %d 字符（%.1f%%）" % (
            self.html_bytes, self.char_count, ratio * 100)


@dataclass(frozen=True)
class FetchedPage:
    """交给模型 / UI 的最终交付物：渲染好的文本 + 结构化元数据。

    单独多一层是为了让 `WebFetchService` 能同时服务「文本回复」
    与「未来界面的结构化卡片」两种消费者。
    """

    document: WebDocument = field(default_factory=WebDocument)
    text: str = ""

    def __str__(self) -> str:  # pragma: no cover - 便捷输出
        return self.text


__all__ = ["WebDocument", "FetchedPage"]
