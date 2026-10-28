# -*- coding: utf-8 -*-
"""网页抓取端口（Port）：「拿到 URL，返回结构化正文」的契约。

核心服务只依赖这个抽象，因此：
    - 用 curl + trafilatura、用 requests + readability、甚至用无头浏览器，
      都只是换一个适配器，业务层零改动；
    - 测试里可以注入假客户端，完全离线跑通工具链路。

适配器的职责边界很明确：
    **把外部世界的失败翻译成 `core.errors.WebFetchError` 子类**，
    绝不让 `subprocess` / `trafilatura` 的原始异常流到服务层。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.webpage import WebDocument


class WebPageFetcher(ABC):
    """网页抓取客户端契约。"""

    @abstractmethod
    def fetch(self, url: str) -> WebDocument:
        """抓取一个 URL 并提取正文。

        Args:
            url: 绝对 URL（调用方已保证是 http/https）。

        Returns:
            `WebDocument`，其中 `markdown` 为正文的 Markdown 表达。

        Raises:
            WebFetchError: 抓取超时、主机不可达、HTTP 错误、解析失败、
                           或未能提取出正文（子类会给出更具体的类型）。
        """
        raise NotImplementedError


__all__ = ["WebPageFetcher"]
