# -*- coding: utf-8 -*-
"""内置本地文件操作工具（10 个）。

本模块同样是**适配器**，只做三件事：
    1. 声明 function schema（给模型看）；
    2. 从参数字典里取值并做类型归一；
    3. 调用 `FileSystemService` 并把结构化结果 `render()` 成文本。

所有校验、异常语义、编码探测都在 `services/filesystem.py`。
"""

from __future__ import annotations

from ..services.filesystem import FileSystemService
from .base import ToolBuilder, text_param


def build_file_tools(service: FileSystemService | None = None) -> list:
    """构造文件操作类工具列表。"""
    fs = service or FileSystemService()
    builder = ToolBuilder(namespace="filesystem")

    # ------------------------------------------------------------------
    # 查询类
    # ------------------------------------------------------------------
    @builder.tool(
        name="getCurrentDirectory",
        description="获取当前工作目录的绝对路径。当用户询问当前目录、工作目录在哪里时调用。",
    )
    def _current_directory(args: dict) -> str:
        return "当前工作目录：%s" % fs.current_directory()

    @builder.tool(
        name="listDirectory",
        description="列出指定目录下的所有文件和子目录（仅一层，不递归）。当用户想看某个目录里有什么内容时调用。",
        params={"path": text_param("要列出的目录路径，可以是绝对路径或相对路径")},
        required=["path"],
    )
    def _list_directory(args: dict) -> str:
        return fs.list_directory(args.get("path") or "").render()

    @builder.tool(
        name="getFileType",
        description="获取指定文件的文件类型（根据扩展名推断 MIME 类型和类别）。当用户询问某个文件是什么类型时调用。",
        params={"path": text_param("要查看类型的文件路径")},
        required=["path"],
    )
    def _get_file_type(args: dict) -> str:
        return fs.describe_file(args.get("path") or "").render()

    @builder.tool(
        name="readFile",
        description=(
            "读取指定文件的内容。仅支持纯文本文件（如 .txt、.py、.json、.md 等），"
            "不支持二进制文件（如图片、压缩包、可执行程序）。"
        ),
        params={"path": text_param("要读取的文件路径")},
        required=["path"],
    )
    def _read_file(args: dict) -> str:
        return fs.read_text_file(args.get("path") or "").render()

    # ------------------------------------------------------------------
    # 变更类
    # ------------------------------------------------------------------
    @builder.tool(
        name="createFile",
        description=(
            "在指定目录下创建一个新文件并写入初始内容（UTF-8 编码）。"
            "如果目录不存在会自动创建；如果同名文件已存在则不会覆盖。"
        ),
        params={
            "directory": text_param("新文件所在的目录路径"),
            "filename": text_param("新文件的文件名（含扩展名），例如 notes.txt"),
            "content": text_param("写入文件的初始内容，可以为空字符串"),
        },
        required=["directory", "filename"],
    )
    def _create_file(args: dict) -> str:
        return fs.create_file(
            args.get("directory") or "",
            args.get("filename") or "",
            args.get("content") or "",
        ).render()

    @builder.tool(
        name="editFile",
        description=(
            "编辑指定纯文本文件的内容。"
            "如果提供了 old_text，则把文件中所有出现的 old_text 替换为 new_text；"
            "如果没有提供 old_text（或为空字符串），则用 new_text 整体覆盖原文件内容。"
        ),
        params={
            "path": text_param("要编辑的文件路径"),
            "old_text": text_param("要被替换的原文本；省略或为空时表示整体覆盖文件"),
            "new_text": text_param("替换后的新文本（覆盖模式下即为新的完整内容）"),
        },
        required=["path", "new_text"],
    )
    def _edit_file(args: dict) -> str:
        return fs.edit_file(
            args.get("path") or "",
            args.get("new_text") if args.get("new_text") is not None else "",
            args.get("old_text") or None,
        ).render()

    @builder.tool(
        name="createDirectory",
        description="创建一个新的目录，支持一次性创建多级不存在的父目录。",
        params={"path": text_param("要创建的目录路径")},
        required=["path"],
    )
    def _create_directory(args: dict) -> str:
        return fs.create_directory(args.get("path") or "").render()

    @builder.tool(
        name="deleteDirectory",
        description=(
            "删除指定目录及其内部的所有文件和子目录（支持删除非空目录）。"
            "此操作不可恢复，调用前必须确认路径正确，切勿删除系统目录或用户未明确指定的目录。"
        ),
        params={"path": text_param("要删除的目录路径")},
        required=["path"],
        destructive=True,
    )
    def _delete_directory(args: dict) -> str:
        return fs.delete_directory(args.get("path") or "").render()

    @builder.tool(
        name="deleteFile",
        description="删除指定的文件。此操作不可恢复，调用前必须确认路径正确。",
        params={"path": text_param("要删除的文件路径")},
        required=["path"],
        destructive=True,
    )
    def _delete_file(args: dict) -> str:
        return fs.delete_file(args.get("path") or "").render()

    @builder.tool(
        name="renameItem",
        description="重命名指定的文件或目录，也可用于把文件/目录移动到另一个位置。目标路径已存在时会失败，不会覆盖。",
        params={
            "old_path": text_param("原文件或目录的路径"),
            "new_path": text_param("新的文件或目录路径"),
        },
        required=["old_path", "new_path"],
    )
    def _rename_item(args: dict) -> str:
        return fs.rename(args.get("old_path") or "", args.get("new_path") or "").render()

    return builder.build()


__all__ = ["build_file_tools"]
