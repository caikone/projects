# -*- coding: utf-8 -*-
"""上下文归档存储端口。

`ContextService`（服务层）通过这个抽象读写归档，
不关心文件在哪里、用什么格式存——
Web 版将来可以换成数据库实现，服务层零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.context import ArchiveRecord, SearchHit


class ContextArchiveStore(ABC):
    """原始上下文归档的存取契约（`.agents/context/*.jsonl`）。"""

    @abstractmethod
    def save(self, archive_id: str, records: list[ArchiveRecord],
             meta: dict | None = None) -> str:
        """保存一份归档，返回实际写入的文件路径。

        编号已存在时自动追加 `-1`、`-2` 序号，绝不覆盖旧归档。
        写入失败时抛出 `ContextArchiveError`。
        """
        raise NotImplementedError

    @abstractmethod
    def list_ids(self) -> list[str]:
        """已有的归档编号（按文件名排序 = 按时间排序）。"""
        raise NotImplementedError

    @abstractmethod
    def search(self, keywords: list[str],
               archive_id: str | None = None) -> list[SearchHit]:
        """在归档中检索。

        Args:
            keywords: 小写化的关键词列表（AND 语义）。
            archive_id: 指定编号时只搜这一份；None 时搜全部归档。
        """
        raise NotImplementedError

    @abstractmethod
    def path_of(self, archive_id: str) -> str:
        """归档编号对应的文件路径（用于提示与展示）。"""
        raise NotImplementedError


__all__ = ["ContextArchiveStore"]
