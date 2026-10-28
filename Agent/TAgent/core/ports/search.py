# -*- coding: utf-8 -*-
"""联网搜索端口（Port）：搜索客户端契约。

核心服务只依赖该抽象，不感知具体搜索厂商与 HTTP 细节；
测试中可注入假客户端，未来更换搜索厂商也只需新增适配器。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.search import SearchReport


class SearchClient(ABC):
    """搜索客户端契约：输入查询词，输出结构化搜索报告。"""

    @abstractmethod
    def search(self, query: str) -> SearchReport:
        """执行一次搜索。

        Raises:
            SearchError: 网络失败、API 返回错误、响应格式异常等。
        """
        raise NotImplementedError


__all__ = ["SearchClient"]
