# -*- coding: utf-8 -*-
"""
LLM 多轮连续对话 + Function Call（工具调用）练习 —— 本地文件操作版

在 Practice02/chat_leap.py 基础上：
    1. 保留 getDate 工具（调用本地 date 命令）
    2. 保留 isLeapYear 工具（纯 Python 判断闰年）
    3. 新增一组本地文件操作工具（全部用纯 Python 实现，不调用外部命令）：
        - getCurrentDirectory   获取当前工作目录
        - listDirectory         列出指定目录下所有文件和子目录
        - getFileType           获取指定文件的文件类型
        - readFile              获取指定文件的内容（仅支持纯文本文件）
        - createFile            根据指定的目录和文件名创建新文件
        - editFile              编辑指定文件的内容（仅支持纯文本文件）
        - createDirectory       创建新目录（支持递归创建多级目录）
        - deleteDirectory       删除目录（支持删除非空目录，不可恢复！）
        - deleteFile            删除文件
        - renameItem            重命名文件或目录

用法：
    python chat_files.py              # 进入连续对话，输入问题后回车发送
    python chat_files.py "你的问题"    # 先发送该问题，之后继续交互输入

    试试问它："当前工作目录是什么？"、"列出 D:\\temp 下的文件"、
    "在 D:\\temp 下创建一个 hello.txt，内容是你好"、"把 hello.txt 改名为 hi.txt"

退出：
    按 Ctrl+C 强制终止程序

依赖：
    pip install requests
"""

import sys
import os
import json
import codecs
import shutil
import mimetypes
import subprocess
import configparser

import requests

# 配置文件与当前脚本放在同一目录
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 60

# 单轮对话中允许的最大工具调用轮次（防止模型无限循环调用工具）
# 文件操作类任务常常需要多步连续调用（建目录 → 建文件 → 编辑 → 验证），因此放宽到 10
MAX_TOOL_ROUNDS = 10

# 读取纯文本文件的最大字节数（1MB），超过部分截断，避免把超大文件塞进上下文
MAX_READ_BYTES = 1024 * 1024

# 工具结果在控制台打印时的最大显示长度（完整结果仍会发给模型）
MAX_PRINT_LEN = 300


# ===== Function Call 工具定义（OpenAI 兼容格式） =====
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间。当用户询问现在的时间、日期、星期几等问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断输入的年份是否为闰年。当用户询问某一年是不是闰年时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    },
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCurrentDirectory",
            "description": "获取当前工作目录的绝对路径。当用户询问当前目录、工作目录在哪里时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDirectory",
            "description": "列出指定目录下的所有文件和子目录（仅一层，不递归）。当用户想看某个目录里有什么内容时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径，可以是绝对路径或相对路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的文件类型（根据扩展名推断 MIME 类型和类别）。当用户询问某个文件是什么类型时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要查看类型的文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "读取指定文件的内容。仅支持纯文本文件（如 .txt、.py、.json、.md 等），不支持二进制文件（如图片、压缩包、可执行程序）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "在指定目录下创建一个新文件并写入初始内容（UTF-8 编码）。如果目录不存在会自动创建；如果同名文件已存在则不会覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "新文件所在的目录路径",
                    },
                    "filename": {
                        "type": "string",
                        "description": "新文件的文件名（含扩展名），例如 notes.txt",
                    },
                    "content": {
                        "type": "string",
                        "description": "写入文件的初始内容，可以为空字符串",
                    },
                },
                "required": ["directory", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFile",
            "description": (
                "编辑指定纯文本文件的内容。"
                "如果提供了 old_text，则把文件中所有出现的 old_text 替换为 new_text；"
                "如果没有提供 old_text（或为空字符串），则用 new_text 整体覆盖原文件内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要编辑的文件路径",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "要被替换的原文本；省略或为空时表示整体覆盖文件",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本（覆盖模式下即为新的完整内容）",
                    },
                },
                "required": ["path", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDirectory",
            "description": "创建一个新的目录，支持一次性创建多级不存在的父目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要创建的目录路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDirectory",
            "description": (
                "删除指定目录及其内部的所有文件和子目录（支持删除非空目录）。"
                "此操作不可恢复，调用前必须确认路径正确，切勿删除系统目录或用户未明确指定的目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的目录路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除指定的文件。此操作不可恢复，调用前必须确认路径正确。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renameItem",
            "description": "重命名指定的文件或目录，也可用于把文件/目录移动到另一个位置。目标路径已存在时会失败，不会覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {
                        "type": "string",
                        "description": "原文件或目录的路径",
                    },
                    "new_path": {
                        "type": "string",
                        "description": "新的文件或目录路径",
                    },
                },
                "required": ["old_path", "new_path"],
            },
        },
    },
]


class LLMStreamError(Exception):
    """流式响应内部返回错误对象时抛出（用于避免把半截回复存入聊天记录）"""
    pass


# ===== 工具实现 =====

def tool_get_date(args):
    """调用本地 date 命令获取当前日期和时间（不使用 Python 的 datetime）"""
    try:
        if os.name == "nt":
            # Windows：直接运行 date 会进入交互模式，必须加 /t 参数；
            # date 和 time 是 cmd 内部命令，需要 shell=True
            date_part = subprocess.run(
                "date /t", shell=True, capture_output=True, text=True, timeout=10)
            time_part = subprocess.run(
                "time /t", shell=True, capture_output=True, text=True, timeout=10)
            output = (date_part.stdout.strip() + " " + time_part.stdout.strip()).strip()
        else:
            # Linux / macOS：date 命令直接输出日期和时间
            result = subprocess.run(
                ["date"], capture_output=True, text=True, timeout=10)
            output = result.stdout.strip()
        return output if output else "date 命令没有返回内容"
    except Exception as e:
        return "调用 date 命令失败：%s" % e


def tool_is_leap_year(args):
    """判断年份是否为闰年（纯 Python 代码计算，不调用任何系统命令）

    格里高利历闰年规则：
        - 能被 4 整除但不能被 100 整除的年份是闰年（如 2024）
        - 能被 400 整除的年份也是闰年（如 2000）
        - 其余年份不是闰年（如 1900、2023）
    """
    year = args.get("year")
    if year is None:
        return "缺少必需参数 year"

    try:
        year = int(year)
    except (TypeError, ValueError):
        return "参数 year 必须是整数，实际收到：%r" % (year,)

    is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    return "%d 年%s闰年" % (year, "是" if is_leap else "不是")


# ----- 本地文件操作工具 -----

def _read_text_file(path):
    """读取纯文本文件，返回 (文本内容, 编码名, 是否被截断)。

    依次尝试 UTF-8（含 BOM）和 GBK 解码；含有空字节或无法解码时
    抛出 ValueError，表示该文件不是受支持的纯文本文件。
    """
    with open(path, "rb") as f:
        raw = f.read(MAX_READ_BYTES + 1)
    truncated = len(raw) > MAX_READ_BYTES
    raw = raw[:MAX_READ_BYTES]

    # 空字节是二进制文件的典型特征（图片、压缩包、可执行文件等）
    if b"\x00" in raw:
        raise ValueError("文件内容含有空字节，疑似二进制文件，不支持读取")

    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig", truncated
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), enc, truncated
        except UnicodeDecodeError:
            continue
    raise ValueError("无法按 UTF-8 / GBK 解码，该文件可能不是纯文本文件")


def tool_get_current_directory(args):
    """获取当前工作目录"""
    return "当前工作目录：%s" % os.getcwd()


def tool_list_directory(args):
    """列出指定目录下的所有文件和子目录（仅一层）"""
    path = args.get("path")
    if not path:
        return "缺少必需参数 path"

    if not os.path.exists(path):
        return "目录不存在：%s" % path
    if not os.path.isdir(path):
        return "指定的路径不是目录：%s" % path

    try:
        dirs, files = [], []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        dirs.append(entry.name)
                    else:
                        files.append(entry.name)
                except OSError:
                    files.append(entry.name)  # 无法判断类型的按文件处理
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)

        if not dirs and not files:
            return "目录 %s 是空的" % os.path.abspath(path)

        lines = ["目录 %s 下共有 %d 个子目录、%d 个文件："
                 % (os.path.abspath(path), len(dirs), len(files))]
        for name in dirs:
            lines.append("[目录] %s" % name)
        for name in files:
            lines.append("[文件] %s" % name)
        return "\n".join(lines)
    except OSError as e:
        return "列出目录失败：%s" % e


# 常见扩展名 -> MIME 类型的补充映射（mimetypes 模块在部分系统上不全）
_EXTRA_MIME_TYPES = {
    ".py": "text/x-python",
    ".js": "application/javascript",
    ".ts": "application/typescript",
    ".json": "application/json",
    ".md": "text/markdown",
    ".ini": "text/plain",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".go": "text/x-go",
    ".java": "text/x-java",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
}


def tool_get_file_type(args):
    """根据扩展名推断指定文件的类型"""
    path = args.get("path")
    if not path:
        return "缺少必需参数 path"

    if not os.path.exists(path):
        return "文件不存在：%s" % path
    if os.path.isdir(path):
        return "指定的路径是一个目录，不是文件：%s" % path

    ext = os.path.splitext(path)[1].lower()
    mime = _EXTRA_MIME_TYPES.get(ext)
    if mime is None:
        mime, _ = mimetypes.guess_type(path)

    if mime is None:
        category = "未知类型"
    elif mime.startswith("text/"):
        category = "文本文件"
    elif mime.startswith("image/"):
        category = "图片文件"
    elif mime.startswith("audio/"):
        category = "音频文件"
    elif mime.startswith("video/"):
        category = "视频文件"
    elif mime in ("application/zip", "application/x-rar-compressed",
                  "application/x-7z-compressed", "application/gzip",
                  "application/x-tar", "application/x-bzip2"):
        category = "压缩文件"
    elif mime.startswith("application/"):
        category = "应用/数据文件"
    else:
        category = "其他类型"

    try:
        size = os.path.getsize(path)
        size_text = "%d 字节" % size
    except OSError:
        size_text = "未知"

    return "文件：%s\n扩展名：%s\nMIME 类型：%s\n类别：%s\n大小：%s" % (
        os.path.abspath(path),
        ext if ext else "（无扩展名）",
        mime if mime else "未知",
        category,
        size_text,
    )


def tool_read_file(args):
    """读取指定纯文本文件的内容"""
    path = args.get("path")
    if not path:
        return "缺少必需参数 path"

    if not os.path.exists(path):
        return "文件不存在：%s" % path
    if os.path.isdir(path):
        return "指定的路径是一个目录，不是文件：%s" % path

    try:
        text, encoding, truncated = _read_text_file(path)
    except ValueError as e:
        return "无法读取：%s（readFile 仅支持纯文本文件）" % e
    except OSError as e:
        return "读取文件失败：%s" % e

    header = "文件 %s 的内容（编码：%s）：" % (os.path.abspath(path), encoding)
    if truncated:
        header += "\n[提示] 文件超过 %d 字节，仅显示开头部分。" % MAX_READ_BYTES
    return header + "\n" + text


def tool_create_file(args):
    """在指定目录下创建新文件（目录不存在时自动创建，文件已存在时不覆盖）"""
    directory = args.get("directory")
    filename = args.get("filename")
    if not directory:
        return "缺少必需参数 directory"
    if not filename:
        return "缺少必需参数 filename"
    content = args.get("content") or ""

    full_path = os.path.join(directory, filename)
    if os.path.exists(full_path):
        return "创建失败：目标已存在 %s（不会覆盖已有文件）" % os.path.abspath(full_path)

    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(full_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
    except OSError as e:
        return "创建文件失败：%s" % e

    return "已创建文件：%s（写入 %d 个字符）" % (os.path.abspath(full_path), len(content))


def tool_edit_file(args):
    """编辑指定纯文本文件：提供 old_text 时做替换，否则整体覆盖"""
    path = args.get("path")
    if not path:
        return "缺少必需参数 path"
    new_text = args.get("new_text")
    if new_text is None:
        return "缺少必需参数 new_text"
    old_text = args.get("old_text")

    if not os.path.exists(path):
        return "文件不存在：%s" % path
    if os.path.isdir(path):
        return "指定的路径是一个目录，不是文件：%s" % path

    try:
        text, encoding, _ = _read_text_file(path)
    except ValueError as e:
        return "无法编辑：%s（editFile 仅支持纯文本文件）" % e
    except OSError as e:
        return "读取文件失败：%s" % e

    if old_text:
        count = text.count(old_text)
        if count == 0:
            return "编辑失败：文件中没有找到要替换的文本：%r" % old_text
        text = text.replace(old_text, new_text)
        action = "已把 %d 处 %r 替换为 %r" % (count, old_text, new_text)
    else:
        text = new_text
        action = "已整体覆盖文件内容"

    try:
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(text)
    except OSError as e:
        return "写入文件失败：%s" % e

    return "%s：%s（保存编码：%s）" % (action, os.path.abspath(path), encoding)


def tool_create_directory(args):
    """创建新目录（支持递归创建多级父目录）"""
    path = args.get("path")
    if not path:
        return "缺少必需参数 path"

    if os.path.exists(path):
        if os.path.isdir(path):
            return "目录已存在：%s" % os.path.abspath(path)
        return "创建失败：同名文件已存在 %s" % os.path.abspath(path)

    try:
        os.makedirs(path)
    except OSError as e:
        return "创建目录失败：%s" % e
    return "已创建目录：%s" % os.path.abspath(path)


def tool_delete_directory(args):
    """删除指定目录及其全部内容（支持非空目录，不可恢复）"""
    path = args.get("path")
    if not path:
        return "缺少必需参数 path"

    if not os.path.exists(path):
        return "目录不存在：%s" % path
    if not os.path.isdir(path):
        return "指定的路径不是目录（删除文件请使用 deleteFile）：%s" % path

    try:
        shutil.rmtree(path)
    except OSError as e:
        return "删除目录失败：%s" % e
    return "已删除目录及其全部内容：%s" % os.path.abspath(path)


def tool_delete_file(args):
    """删除指定文件"""
    path = args.get("path")
    if not path:
        return "缺少必需参数 path"

    if not os.path.exists(path):
        return "文件不存在：%s" % path
    if os.path.isdir(path):
        return "指定的路径是一个目录（删除目录请使用 deleteDirectory）：%s" % path

    try:
        os.remove(path)
    except OSError as e:
        return "删除文件失败：%s" % e
    return "已删除文件：%s" % os.path.abspath(path)


def tool_rename_item(args):
    """重命名文件或目录（目标已存在时不覆盖，直接报错）"""
    old_path = args.get("old_path")
    new_path = args.get("new_path")
    if not old_path:
        return "缺少必需参数 old_path"
    if not new_path:
        return "缺少必需参数 new_path"

    if not os.path.exists(old_path):
        return "原路径不存在：%s" % old_path
    if os.path.exists(new_path):
        return "重命名失败：目标已存在 %s（不会覆盖）" % os.path.abspath(new_path)

    kind = "目录" if os.path.isdir(old_path) else "文件"
    try:
        os.rename(old_path, new_path)
    except OSError as e:
        return "重命名失败：%s" % e
    return "已将%s %s 重命名为 %s" % (
        kind, os.path.abspath(old_path), os.path.abspath(new_path))


# 工具名 -> 实现函数 的映射（实现函数统一接收解析后的参数字典）
TOOL_IMPLS = {
    "getDate": tool_get_date,
    "isLeapYear": tool_is_leap_year,
    "getCurrentDirectory": tool_get_current_directory,
    "listDirectory": tool_list_directory,
    "getFileType": tool_get_file_type,
    "readFile": tool_read_file,
    "createFile": tool_create_file,
    "editFile": tool_edit_file,
    "createDirectory": tool_create_directory,
    "deleteDirectory": tool_delete_directory,
    "deleteFile": tool_delete_file,
    "renameItem": tool_rename_item,
}


def load_config(path=CONFIG_FILE):
    """读取 config.ini，返回 (base_url, api_key, model, timeout)"""
    if not os.path.exists(path):
        raise FileNotFoundError("找不到配置文件：%s" % path)

    parser = configparser.ConfigParser()
    # 保留 key 的大小写（默认会转小写，这里保持原样更直观）
    parser.optionxform = str
    parser.read(path, encoding="utf-8")

    if not parser.has_section("llm"):
        raise KeyError("配置文件里缺少 [llm] 段")

    def get(key, default=None):
        value = parser.get("llm", key, fallback=default)
        return value.strip() if isinstance(value, str) else value

    base_url = get("base_url")
    api_key = get("api_key")
    model = get("model")
    timeout = get("timeout", str(DEFAULT_TIMEOUT))

    if not base_url:
        raise KeyError("[llm] 缺少 base_url 配置")
    if not api_key:
        raise KeyError("[llm] 缺少 api_key 配置")
    if not model:
        raise KeyError("[llm] 缺少 model 配置")

    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return base_url.rstrip("/"), api_key, model, timeout


def chat_stream(messages, base_url, api_key, model, timeout=DEFAULT_TIMEOUT):
    """向 LLM 接口发送【流式】请求（带 tools），逐段 yield 模型返回的 delta 对象

    delta 中可能包含：
        - content：文本增量
        - tool_calls：工具调用增量（按 index 分片，需要客户端累积拼接）
    """
    url = base_url + "/chat/completions"

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        # 完整的历史对话 + 当前用户消息
        "messages": messages,
        # 关键：开启流式
        "stream": True,
        # 声明可用工具，让模型在需要时发起 function call
        "tools": TOOLS,
    }

    # stream=True：不一次性读完响应，而是边收边读
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout, stream=True)
    # 非 2xx 直接抛 HTTPError
    resp.raise_for_status()

    # 逐行读取 SSE 分片：每行形如 "data: {...}"，以 "data: [DONE]" 结束
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line.startswith("data:"):
            continue

        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break

        try:
            obj = json.loads(data)
        except ValueError:
            continue  # 跳过无法解析的行

        # 服务端可能在流中返回错误对象
        if isinstance(obj, dict) and obj.get("error"):
            err = obj["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise LLMStreamError(msg)

        choices = obj.get("choices") or []
        if not choices:
            continue

        delta = choices[0].get("delta") or {}
        if delta:
            yield delta


def stream_one_round(messages, base_url, api_key, model, timeout):
    """执行一轮流式请求：边收边打印文本增量（打字机效果）

    返回 (完整文本, 合并后的 tool_calls 列表)。
    tool_calls 中每项形如 {"id": ..., "name": ..., "arguments": ...}
    """
    content_parts = []
    # 流式中 tool_calls 按 index 分片到达，这里按 index 累积拼接
    tool_slots = {}

    for delta in chat_stream(messages, base_url, api_key, model, timeout):
        # 1) 文本增量：打字机输出
        content = delta.get("content")
        if content:
            print(content, end="", flush=True)  # flush 保证逐段立即输出
            content_parts.append(content)

        # 2) 工具调用增量：按 index 累积 id / name / arguments
        for tc in delta.get("tool_calls") or []:
            index = tc.get("index") or 0
            slot = tool_slots.setdefault(
                index, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]

    tool_calls = [tool_slots[i] for i in sorted(tool_slots)]
    return "".join(content_parts), tool_calls


def run_tools(tool_calls):
    """执行模型请求的工具调用，返回对应的 tool 消息列表"""
    tool_messages = []
    for tc in tool_calls:
        name = tc["name"]
        impl = TOOL_IMPLS.get(name)
        if impl is None:
            result = "未知工具：%s" % name
        else:
            # 模型传来的 arguments 是 JSON 字符串，先解析成字典再交给工具实现
            raw_args = (tc.get("arguments") or "").strip()
            try:
                args = json.loads(raw_args) if raw_args else {}
            except ValueError:
                args = None
            if not isinstance(args, dict):
                result = "工具参数不是合法的 JSON 对象：%s" % raw_args
            else:
                result = impl(args)
        result = str(result)
        # 控制台只展示摘要（如 readFile 的结果可能很长），完整结果仍会发给模型
        if len(result) > MAX_PRINT_LEN:
            shown = result[:MAX_PRINT_LEN] + "...（共 %d 字符，已截断显示）" % len(result)
        else:
            shown = result
        print("[工具调用] %s(%s) -> %s" % (name, tc.get("arguments") or "", shown))
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })
    return tool_messages


def main():
    # 1) 读取配置
    try:
        base_url, api_key, model, timeout = load_config()
    except (FileNotFoundError, KeyError, configparser.Error) as e:
        print("[配置错误] %s" % e)
        return 1

    # 命令行参数作为“第一条”问题，之后改为交互输入
    argv_question = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else None

    # 聊天记录：保存全部历史对话，每次请求都完整带上
    history = []

    print("已进入连续对话模式（支持日期/闰年 + 本地文件操作共 %d 个工具调用），"
          "输入问题后回车发送；按 Ctrl+C 退出。\n" % len(TOOLS))

    # 2) 无限循环：持续接收输入 → 发起流式请求（可能带工具调用）→ 打印结果
    try:
        while True:
            # 2.1) 获取用户输入
            if argv_question is not None:
                question = argv_question
                argv_question = None  # 只消费一次
                print("你: %s" % question)
            else:
                try:
                    question = input("你: ").strip()
                except EOFError:
                    print("\n[输入已结束]")
                    break

            # 空输入：不退出，直接进入下一轮
            if not question:
                continue

            # 2.2) 组装消息：历史全部对话 + 当前用户消息
            messages = history + [{"role": "user", "content": question}]

            # 2.3) 流式请求 + 工具调用循环：
            #      模型返回 tool_calls → 执行工具 → 把结果喂回模型 → 直到模型给出最终文本
            print("===== 模型回复 =====")
            answer = ""
            try:
                for _ in range(MAX_TOOL_ROUNDS):
                    round_text, tool_calls = stream_one_round(
                        messages, base_url, api_key, model, timeout)
                    if round_text:
                        print()  # 文本输出后换行
                    answer += round_text

                    if not tool_calls:
                        break  # 模型没有调用工具，本轮结束

                    # 把模型的助手消息（含 tool_calls）追加进请求消息
                    messages.append({
                        "role": "assistant",
                        "content": round_text if round_text else None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            }
                            for tc in tool_calls
                        ],
                    })
                    # 执行工具并把结果以 tool 消息喂回模型
                    messages.extend(run_tools(tool_calls))
                else:
                    print("[提示] 工具调用轮次过多，已停止。")
            except requests.exceptions.Timeout:
                print("\n[请求超时] 请检查网络，或调大 config.ini 中的 timeout。")
            except requests.exceptions.ConnectionError:
                print("\n[连接失败] 无法访问接口地址，请检查 base_url 与网络。")
            except requests.exceptions.HTTPError as e:
                # 尽量把服务端返回的错误信息带出来
                body = ""
                if e.response is not None:
                    body = e.response.text[:500]
                print("\n[接口返回错误] %s %s" % (e, body))
            except LLMStreamError as e:
                print("\n[接口返回错误] %s" % e)
            except (KeyError, IndexError, ValueError):
                print("\n[解析失败] 接口返回格式不符合预期，请检查 base_url / model 是否正确。")
            except requests.exceptions.RequestException as e:
                print("\n[请求异常] %s" % e)
            else:
                # 2.4) 本轮正常完成后，把问答追加进聊天记录
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": answer})

            print()  # 每轮之间空一行
    except KeyboardInterrupt:
        # 唯一退出方式：Ctrl+C
        print("\n[已退出] 再见！")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
