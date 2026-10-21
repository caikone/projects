# -*- coding: utf-8 -*-
"""文件系统领域值对象。

`services/filesystem.py` 返回的都是这里定义的结构化对象，
由 `tools/builtin_files.py` 负责格式化成给模型看的字符串。
这样「业务结果」与「给 LLM 看的文本」彻底解耦——
将来接入 Web 界面时可以直接渲染结构化数据，而不用解析文本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """目录下的一个条目。"""

    name: str
    is_dir: bool

    @property
    def tag(self) -> str:
        return "[目录]" if self.is_dir else "[文件]"


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    """目录列举结果。"""

    path: str
    entries: list[DirectoryEntry] = field(default_factory=list)

    @property
    def dir_count(self) -> int:
        return sum(1 for e in self.entries if e.is_dir)

    @property
    def file_count(self) -> int:
        return sum(1 for e in self.entries if not e.is_dir)

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def render(self) -> str:
        """渲染成给模型 / 用户看的文本。"""
        if self.is_empty:
            return "目录 %s 是空的" % self.path
        lines = ["目录 %s 下共有 %d 个子目录、%d 个文件："
                 % (self.path, self.dir_count, self.file_count)]
        for entry in self.entries:
            lines.append("%s %s" % (entry.tag, entry.name))
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class FileTypeInfo:
    """文件类型推断结果。"""

    path: str
    extension: str
    mime_type: str | None
    category: str
    size: int | None

    def render(self) -> str:
        size_text = "%d 字节" % self.size if self.size is not None else "未知"
        return (
            "文件：%s\n"
            "扩展名：%s\n"
            "MIME 类型：%s\n"
            "类别：%s\n"
            "大小：%s"
            % (self.path,
               self.extension or "（无扩展名）",
               self.mime_type or "未知",
               self.category,
               size_text)
        )


@dataclass(frozen=True, slots=True)
class TextFileContent:
    """纯文本文件内容读取结果。"""

    path: str
    text: str
    encoding: str
    truncated: bool = False
    truncated_at: int = 0

    def render(self) -> str:
        header = "文件 %s 的内容（编码：%s）：" % (self.path, self.encoding)
        if self.truncated:
            header += "\n[提示] 文件超过 %d 字节，仅显示开头部分。" % self.truncated_at
        return header + "\n" + self.text


@dataclass(frozen=True, slots=True)
class EditSummary:
    """文件编辑结果。"""

    path: str
    encoding: str
    replaced: int = 0        # >0 表示替换模式；0 表示整体覆盖
    old_text: str | None = None
    new_text: str = ""

    @property
    def mode(self) -> str:
        return "replace" if self.replaced else "overwrite"

    def render(self) -> str:
        if self.replaced:
            action = "已把 %d 处 %r 替换为 %r" % (self.replaced, self.old_text, self.new_text)
        else:
            action = "已整体覆盖文件内容"
        return "%s：%s（保存编码：%s）" % (action, self.path, self.encoding)


@dataclass(frozen=True, slots=True)
class MutationResult:
    """创建 / 删除 / 重命名等变更类操作的统一结果。"""

    action: str
    path: str
    detail: str = ""
    affected: Iterable[str] = ()

    def render(self) -> str:
        base = "已%s：%s" % (self.action, self.path)
        return base + ("（%s）" % self.detail if self.detail else "")
