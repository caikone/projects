# -*- coding: utf-8 -*-
"""本地文件实现的上下文归档存储。

唯一知道「归档长在磁盘哪里」的地方：``<项目根>/.agents/context/<编号>.jsonl``。

文件格式：**每行一个 JSON 对象**

    第 1 行（可选）：{"type": "meta", "archive_id": ..., "archived_at": ..., ...}
    其后每行：      {"seq": 1, "role": "user", "content": "..."}

选择 jsonl 而不是单个大 JSON，是为了：

    * 追加安全（内容先写临时文件再原子替换，不会留下半个文件）；
    * 逐行检索（`recallContext` 不需要把整份归档读进内存）；
    * 人能直接 `grep` 查（教学场景很重要）。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Iterable

from ...domain.context import (
    ArchiveRecord,
    SearchHit,
    search_records,
    with_collision_suffix,
)
from ...errors import ContextArchiveError
from ...ports.context import ContextArchiveStore

DEFAULT_DIR_NAME = os.path.join(".agents", "context")
FILE_SUFFIX = ".jsonl"


class LocalContextArchiveStore(ContextArchiveStore):
    """把归档写到项目本地的 `.agents/context/` 目录。"""

    def __init__(self, root: str | None = None, *, project_root: str | None = None) -> None:
        base = project_root or os.getcwd()
        self._root = os.path.abspath(root) if root else os.path.join(
            base, DEFAULT_DIR_NAME)

    # ------------------------------------------------------------------
    @property
    def root(self) -> str:
        return self._root

    def path_of(self, archive_id: str) -> str:
        return os.path.join(self._root, "%s%s" % (archive_id, FILE_SUFFIX))

    def exists(self, archive_id: str) -> bool:
        return os.path.isfile(self.path_of(archive_id))

    # ------------------------------------------------------------------
    def save(self, archive_id: str, records: list[ArchiveRecord],
             meta: dict | None = None) -> str:
        if not records:
            raise ContextArchiveError("没有需要归档的上下文内容")

        try:
            os.makedirs(self._root, exist_ok=True)
        except OSError as exc:
            raise ContextArchiveError("无法创建归档目录 %s：%s" % (self._root, exc))

        # 同秒重复压缩时追加序号，绝不覆盖已有归档
        archive_id = with_collision_suffix(archive_id, self.exists)
        target = self.path_of(archive_id)

        header = {"type": "meta", "archive_id": archive_id,
                  "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "message_count": len(records)}
        if meta:
            header.update({k: v for k, v in meta.items() if k != "type"})

        payload: list[str] = [json.dumps(header, ensure_ascii=False)]
        payload.extend(json.dumps(record.to_json(), ensure_ascii=False)
                       for record in records)

        # 先写临时文件再 rename：中途崩溃也不会留下半份归档
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n",
                    dir=self._root, prefix=".tmp-", suffix=FILE_SUFFIX,
                    delete=False) as stream:
                temp_path = stream.name
                stream.write("\n".join(payload))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, target)
        except OSError as exc:
            self._cleanup_temp(temp_path)
            raise ContextArchiveError("写入归档文件失败（%s）：%s" % (target, exc))

        return target

    @staticmethod
    def _cleanup_temp(path: str) -> None:
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:  # pragma: no cover - 清理失败不影响主流程
                pass

    # ------------------------------------------------------------------
    def list_ids(self) -> list[str]:
        try:
            names = os.listdir(self._root)
        except FileNotFoundError:
            return []
        except OSError as exc:  # pragma: no cover - 权限等异常
            raise ContextArchiveError("无法读取归档目录 %s：%s" % (self._root, exc))

        ids = [name[:-len(FILE_SUFFIX)] for name in names
               if name.endswith(FILE_SUFFIX) and not name.startswith(".")]
        # 编号形如 20261022-153000，字典序即时间序；倒序 = 最新在前
        return sorted(ids, reverse=True)

    def load(self, archive_id: str) -> list[ArchiveRecord]:
        """读取一份归档的全部记录（跳过 meta 行）。"""
        path = self.path_of(archive_id)
        try:
            with open(path, "r", encoding="utf-8") as stream:
                return self._parse_lines(stream, archive_id)
        except FileNotFoundError:
            raise ContextArchiveError("找不到编号为 %s 的上下文归档" % archive_id)
        except OSError as exc:
            raise ContextArchiveError("读取归档失败（%s）：%s" % (path, exc))

    def _parse_lines(self, stream: Iterable[str], archive_id: str) -> list[ArchiveRecord]:
        records: list[ArchiveRecord] = []
        for raw in stream:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                continue  # 单行损坏不应让整份归档不可读
            if not isinstance(data, dict) or data.get("type") == "meta":
                continue
            try:
                records.append(ArchiveRecord.from_json(data))
            except (TypeError, ValueError):  # pragma: no cover - 字段异常
                continue
        return records

    # ------------------------------------------------------------------
    def search(self, keywords: list[str],
               archive_id: str | None = None) -> list[SearchHit]:
        if not keywords:
            return []
        if archive_id:
            return search_records(self.load(archive_id), keywords, archive_id)

        hits: list[SearchHit] = []
        # list_ids 已是「最新在前」，检索结果天然优先给最近的信息
        for current in self.list_ids():
            try:
                records = self.load(current)
            except ContextArchiveError:  # 单份归档坏了不影响其它归档
                continue
            hits.extend(search_records(records, keywords, current))
        return hits

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """一句话描述归档目录（供提示信息展示）。"""
        count = len(self.list_ids())
        return "%s（%d 份归档）" % (self._root, count)


__all__ = ["LocalContextArchiveStore", "DEFAULT_DIR_NAME"]
