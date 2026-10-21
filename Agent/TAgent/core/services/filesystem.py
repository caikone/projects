# -*- coding: utf-8 -*-
"""本地文件系统领域服务。

这里只做**业务**，不碰三件事：
    1. 不生成给 LLM 看的 JSON Schema（那是 `tools/builtin_files.py` 的职责）；
    2. 不打印任何东西（那是 UI 的职责）；
    3. 不吞异常——失败时抛 `FileSystemError`，由调用方决定如何降级。

因此它可以被文件工具、Web API、批处理脚本、单元测试任意复用。
"""

from __future__ import annotations

import mimetypes
import os
import shutil

from ..domain.filesystem import (
    DirectoryEntry,
    DirectoryListing,
    EditSummary,
    FileTypeInfo,
    MutationResult,
    TextFileContent,
)
from ..errors import FileSystemError

#: mimetypes 模块在部分系统上覆盖不全，这里补充常见开发文件类型
EXTRA_MIME_TYPES: dict[str, str] = {
    ".py": "text/x-python",
    ".js": "application/javascript",
    ".ts": "application/typescript",
    ".tsx": "text/tsx",
    ".jsx": "text/jsx",
    ".json": "application/json",
    ".md": "text/markdown",
    ".ini": "text/plain",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".toml": "text/plain",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".go": "text/x-go",
    ".java": "text/x-java",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".rs": "text/x-rust",
    ".sh": "text/x-sh",
    ".bat": "text/plain",
    ".ps1": "text/x-powershell",
}

ARCHIVE_MIME_TYPES = frozenset({
    "application/zip",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
    "application/gzip",
    "application/x-tar",
    "application/x-bzip2",
})

#: 允许写入/编辑的文本编码探测顺序
DECODE_ORDER = ("utf-8-sig", "utf-8", "gbk")


def categorize(mime: str | None) -> str:
    """把 MIME 类型归类为人类可读的中文类别。"""
    if not mime:
        return "未知类型"
    if mime.startswith("text/"):
        return "文本文件"
    if mime.startswith("image/"):
        return "图片文件"
    if mime.startswith("audio/"):
        return "音频文件"
    if mime.startswith("video/"):
        return "视频文件"
    if mime in ARCHIVE_MIME_TYPES:
        return "压缩文件"
    if mime.startswith("application/"):
        return "应用/数据文件"
    return "其他类型"


class FileSystemService:
    """本地文件操作服务（纯 Python 实现，不调用任何外部命令）。"""

    def __init__(self, max_read_bytes: int = 1024 * 1024) -> None:
        self.max_read_bytes = max_read_bytes

    # ------------------------------------------------------------------
    # 查询类
    # ------------------------------------------------------------------
    def current_directory(self) -> str:
        """返回当前工作目录的绝对路径。"""
        return os.getcwd()

    def list_directory(self, path: str) -> DirectoryListing:
        """列出目录下的一层条目（不递归）。

        Raises:
            FileSystemError: 路径不存在、不是目录或无权限读取。
        """
        if not path:
            raise FileSystemError("缺少必需参数 path")
        if not os.path.exists(path):
            raise FileSystemError("目录不存在：%s" % path)
        if not os.path.isdir(path):
            raise FileSystemError("指定的路径不是目录：%s" % path)

        entries: list[DirectoryEntry] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        is_dir = False  # 无法判断类型时按文件处理
                    entries.append(DirectoryEntry(name=entry.name, is_dir=is_dir))
        except OSError as exc:
            raise FileSystemError("列出目录失败：%s" % exc)

        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return DirectoryListing(path=os.path.abspath(path), entries=entries)

    def describe_file(self, path: str) -> FileTypeInfo:
        """按扩展名推断文件类型。"""
        if not path:
            raise FileSystemError("缺少必需参数 path")
        if not os.path.exists(path):
            raise FileSystemError("文件不存在：%s" % path)
        if os.path.isdir(path):
            raise FileSystemError("指定的路径是一个目录，不是文件：%s" % path)

        ext = os.path.splitext(path)[1].lower()
        mime = EXTRA_MIME_TYPES.get(ext)
        if mime is None:
            mime, _ = mimetypes.guess_type(path)

        try:
            size: int | None = os.path.getsize(path)
        except OSError:
            size = None

        return FileTypeInfo(
            path=os.path.abspath(path),
            extension=ext,
            mime_type=mime,
            category=categorize(mime),
            size=size,
        )

    def read_text_file(self, path: str) -> TextFileContent:
        """读取纯文本文件内容（拒绝二进制文件）。

        Raises:
            FileSystemError: 文件不存在、是目录、疑似二进制或无法按已知编码解码。
        """
        if not path:
            raise FileSystemError("缺少必需参数 path")
        if not os.path.exists(path):
            raise FileSystemError("文件不存在：%s" % path)
        if os.path.isdir(path):
            raise FileSystemError("指定的路径是一个目录，不是文件：%s" % path)

        try:
            with open(path, "rb") as f:
                raw = f.read(self.max_read_bytes + 1)
        except OSError as exc:
            raise FileSystemError("读取文件失败：%s" % exc)

        truncated = len(raw) > self.max_read_bytes
        raw = raw[: self.max_read_bytes]

        # 空字节是二进制文件的典型特征（图片、压缩包、可执行程序等）
        if b"\x00" in raw:
            raise FileSystemError("文件内容含有空字节，疑似二进制文件，不支持读取")

        for encoding in DECODE_ORDER:
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            return TextFileContent(
                path=os.path.abspath(path),
                text=text,
                encoding=encoding,
                truncated=truncated,
                truncated_at=self.max_read_bytes,
            )
        raise FileSystemError("无法按 UTF-8 / GBK 解码，该文件可能不是纯文本文件")

    # ------------------------------------------------------------------
    # 变更类
    # ------------------------------------------------------------------
    def create_file(self, directory: str, filename: str, content: str = "") -> MutationResult:
        """在指定目录下创建新文件（目录缺失时自动创建，同名文件不覆盖）。"""
        if not directory:
            raise FileSystemError("缺少必需参数 directory")
        if not filename:
            raise FileSystemError("缺少必需参数 filename")

        full_path = os.path.join(directory, filename)
        if os.path.exists(full_path):
            raise FileSystemError(
                "创建失败：目标已存在 %s（不会覆盖已有文件）" % os.path.abspath(full_path))

        try:
            if not os.path.isdir(directory):
                os.makedirs(directory)
            with open(full_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except OSError as exc:
            raise FileSystemError("创建文件失败：%s" % exc)

        return MutationResult(
            action="创建文件",
            path=os.path.abspath(full_path),
            detail="写入 %d 个字符" % len(content),
        )

    def edit_file(self, path: str, new_text: str, old_text: str | None = None) -> EditSummary:
        """编辑纯文本文件：提供 old_text 做全量替换，否则整体覆盖。"""
        if not path:
            raise FileSystemError("缺少必需参数 path")
        if new_text is None:
            raise FileSystemError("缺少必需参数 new_text")

        content = self.read_text_file(path)
        encoding = content.encoding

        if old_text:
            count = content.text.count(old_text)
            if count == 0:
                raise FileSystemError("编辑失败：文件中没有找到要替换的文本：%r" % old_text)
            updated = content.text.replace(old_text, new_text)
            summary = EditSummary(
                path=os.path.abspath(path),
                encoding=encoding,
                replaced=count,
                old_text=old_text,
                new_text=new_text,
            )
        else:
            updated = new_text
            summary = EditSummary(
                path=os.path.abspath(path), encoding=encoding, replaced=0, new_text=new_text)

        try:
            with open(path, "w", encoding=encoding, newline="") as f:
                f.write(updated)
        except OSError as exc:
            raise FileSystemError("写入文件失败：%s" % exc)

        return summary

    def create_directory(self, path: str) -> MutationResult:
        """创建目录（支持递归创建多级父目录）。"""
        if not path:
            raise FileSystemError("缺少必需参数 path")
        if os.path.exists(path):
            if os.path.isdir(path):
                raise FileSystemError("目录已存在：%s" % os.path.abspath(path))
            raise FileSystemError("创建失败：同名文件已存在 %s" % os.path.abspath(path))

        try:
            os.makedirs(path)
        except OSError as exc:
            raise FileSystemError("创建目录失败：%s" % exc)
        return MutationResult(action="创建目录", path=os.path.abspath(path))

    def delete_directory(self, path: str) -> MutationResult:
        """删除目录及其全部内容（支持非空目录，不可恢复）。"""
        if not path:
            raise FileSystemError("缺少必需参数 path")
        if not os.path.exists(path):
            raise FileSystemError("目录不存在：%s" % path)
        if not os.path.isdir(path):
            raise FileSystemError("指定的路径不是目录（删除文件请使用 deleteFile）：%s" % path)

        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise FileSystemError("删除目录失败：%s" % exc)
        return MutationResult(action="删除目录及其全部内容", path=os.path.abspath(path))

    def delete_file(self, path: str) -> MutationResult:
        """删除文件。"""
        if not path:
            raise FileSystemError("缺少必需参数 path")
        if not os.path.exists(path):
            raise FileSystemError("文件不存在：%s" % path)
        if os.path.isdir(path):
            raise FileSystemError("指定的路径是一个目录（删除目录请使用 deleteDirectory）：%s" % path)

        try:
            os.remove(path)
        except OSError as exc:
            raise FileSystemError("删除文件失败：%s" % exc)
        return MutationResult(action="删除文件", path=os.path.abspath(path))

    def rename(self, old_path: str, new_path: str) -> MutationResult:
        """重命名 / 移动文件或目录（目标已存在时不覆盖）。"""
        if not old_path:
            raise FileSystemError("缺少必需参数 old_path")
        if not new_path:
            raise FileSystemError("缺少必需参数 new_path")
        if not os.path.exists(old_path):
            raise FileSystemError("原路径不存在：%s" % old_path)
        if os.path.exists(new_path):
            raise FileSystemError(
                "重命名失败：目标已存在 %s（不会覆盖）" % os.path.abspath(new_path))

        kind = "目录" if os.path.isdir(old_path) else "文件"
        try:
            os.rename(old_path, new_path)
        except OSError as exc:
            raise FileSystemError("重命名失败：%s" % exc)
        return MutationResult(
            action="将%s重命名" % kind,
            path="%s → %s" % (os.path.abspath(old_path), os.path.abspath(new_path)),
        )


def ensure_directory(path: str) -> str:
    """确保目录存在，返回绝对路径（供工具与外部脚本复用）。"""
    os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)
