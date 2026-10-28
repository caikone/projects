# -*- coding: utf-8 -*-
"""文件系统领域服务的单元测试（不依赖网络、不依赖界面）。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.errors import FileSystemError
from core.services.filesystem import FileSystemService



class FileSystemServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent_fs_")
        self.fs = FileSystemService(max_read_bytes=4096)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    def test_current_directory(self) -> None:
        self.assertTrue(os.path.isdir(self.fs.current_directory()))

    def test_create_and_list(self) -> None:
        target = os.path.join(self.tmp, "a", "b")
        self.fs.create_directory(target)
        self.fs.create_file(target, "hello.txt", "你好")
        listing = self.fs.list_directory(target)
        self.assertEqual(listing.file_count, 1)
        self.assertEqual(listing.dir_count, 0)
        self.assertEqual(listing.entries[0].name, "hello.txt")
        self.assertIn("[文件] hello.txt", listing.render())

    def test_create_file_refuses_overwrite(self) -> None:
        self.fs.create_file(self.tmp, "x.txt", "1")
        with self.assertRaises(FileSystemError) as ctx:
            self.fs.create_file(self.tmp, "x.txt", "2")
        self.assertIn("不会覆盖", str(ctx.exception))

    def test_read_text_file(self) -> None:
        self.fs.create_file(self.tmp, "r.txt", "hello 世界")
        content = self.fs.read_text_file(os.path.join(self.tmp, "r.txt"))
        self.assertEqual(content.text, "hello 世界")
        self.assertFalse(content.truncated)

    def test_read_binary_rejected(self) -> None:
        bin_path = os.path.join(self.tmp, "bin.dat")
        with open(bin_path, "wb") as f:
            f.write(b"\x00\x01\x02")
        with self.assertRaises(FileSystemError) as ctx:
            self.fs.read_text_file(bin_path)
        self.assertIn("二进制", str(ctx.exception))

    def test_edit_replace_and_overwrite(self) -> None:
        path = os.path.join(self.tmp, "e.txt")
        self.fs.create_file(self.tmp, "e.txt", "a-b-a")
        summary = self.fs.edit_file(path, "X", old_text="a")
        self.assertEqual(summary.replaced, 2)
        self.assertEqual(self.fs.read_text_file(path).text, "X-b-X")

        self.fs.edit_file(path, "全部替换")
        self.assertEqual(self.fs.read_text_file(path).text, "全部替换")

    def test_edit_missing_old_text(self) -> None:
        path = os.path.join(self.tmp, "m.txt")
        self.fs.create_file(self.tmp, "m.txt", "abc")
        with self.assertRaises(FileSystemError):
            self.fs.edit_file(path, "x", old_text="不存在")

    def test_edit_chinese_not_escaped(self) -> None:
        """中文参数在结果/错误信息里必须原样显示，不能变成 \\uXXXX。"""
        path = os.path.join(self.tmp, "cn.txt")
        self.fs.create_file(self.tmp, "cn.txt", "今天天气很好")

        summary = self.fs.edit_file(path, "今天天气真好", old_text="很好")
        rendered = summary.render()
        self.assertIn("很好", rendered)
        self.assertIn("真好", rendered)
        # 「很好」若被转义会变成 \u5f88\u597d
        self.assertNotIn("\\u5f88", rendered)

        with self.assertRaises(FileSystemError) as ctx:
            self.fs.edit_file(path, "x", old_text="找不到的中文")
        message = str(ctx.exception)
        self.assertIn("找不到的中文", message)
        # 「找」若被转义会变成 \u627e
        self.assertNotIn("\\u627e", message)

    def test_delete_file_and_directory(self) -> None:
        d = os.path.join(self.tmp, "del")
        self.fs.create_directory(d)
        self.fs.create_file(d, "f.txt", "x")
        self.fs.delete_directory(d)
        self.assertFalse(os.path.exists(d))

    def test_delete_directory_rejects_file(self) -> None:
        path = os.path.join(self.tmp, "only.txt")
        self.fs.create_file(self.tmp, "only.txt", "x")
        with self.assertRaises(FileSystemError) as ctx:
            self.fs.delete_directory(path)
        self.assertIn("deleteFile", str(ctx.exception))

    def test_rename(self) -> None:
        src = os.path.join(self.tmp, "old.txt")
        self.fs.create_file(self.tmp, "old.txt", "x")
        dst = os.path.join(self.tmp, "new.txt")
        self.fs.rename(src, dst)
        self.assertTrue(os.path.exists(dst))
        self.assertFalse(os.path.exists(src))

    def test_describe_file(self) -> None:
        self.fs.create_file(self.tmp, "t.py", "print(1)")
        info = self.fs.describe_file(os.path.join(self.tmp, "t.py"))
        self.assertEqual(info.mime_type, "text/x-python")
        self.assertEqual(info.category, "文本文件")

    def test_missing_path_errors(self) -> None:
        with self.assertRaises(FileSystemError):
            self.fs.list_directory(os.path.join(self.tmp, "nope"))
        with self.assertRaises(FileSystemError):
            self.fs.read_text_file("")


if __name__ == "__main__":
    unittest.main()
