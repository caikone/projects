# -*- coding: utf-8 -*-
"""本地命令执行适配器：用 `subprocess` 运行已安装的命令行程序。

设计要点（遵循项目分层规范）：

    1. **第三方依赖只出现在本模块**：`subprocess` 不外泄到服务层，
       核心服务面对的是 `CommandRunner` 抽象。
    2. **程序名与参数拼成列表，`shell=False`**：绝不拼接 shell 命令字符串，
       因此不存在 shell 注入，路径含空格 / 特殊字符（如 `&`、`|`、`>`）
       也会被当作普通字符原样传给程序。
    3. **stdin 接到 DEVNULL**：避免程序等待交互输入而把 Agent 卡死。
    4. **超时即终止**：`subprocess.run(timeout=...)` 超时后杀掉进程，
       并把**已经产生的部分输出**保留下来——对排查「为什么卡住」很有价值。
    5. **输出截断保留头尾**：超长输出只留前段与后段（中间省略并明确标注），
       因为命令的关键信息常常在结尾（报错摘要、统计行）。

安全性说明（教学实验阶段）：
本适配器**不做**命令白名单 / 黑名单 / 人工审批，命令以运行 TAgent 的
当前用户权限直接执行——这是刻意的：先让学生看清「Agent 调用本地工具」的
完整链路，护栏属于下一阶段的内容。
"""

from __future__ import annotations

import subprocess
import time
from typing import Sequence

from ...domain.cli import CommandResult
from ...errors import CliExecutionError, CliNotFoundError
from ...ports.cli import CommandRunner

#: 默认执行超时（秒）
_DEFAULT_TIMEOUT = 30.0
#: stdout / stderr 各自保留的最大字符数（超出截断并标注）
_DEFAULT_MAX_STDOUT_CHARS = 8000
_DEFAULT_MAX_STDERR_CHARS = 4000
#: 截断时保留的尾部比例（其余给头部）
_TAIL_RATIO = 0.3

_ENCODINGS = ("utf-8", "gb18030", "latin-1")


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """把超长文本截成「头部 + 省略标记 + 尾部」。

    Args:
        text: 原始文本。
        limit: 保留的字符数上限。

    Returns:
        (截断后的文本, 是否发生截断)。
    """
    if limit <= 0 or len(text) <= limit:
        return text, False
    tail = max(int(limit * _TAIL_RATIO), 1)
    head = max(limit - tail, 1)
    omitted = len(text) - head - tail
    return (
        "%s\n…（已省略 %d 字符）…\n%s" % (text[:head], omitted, text[-tail:]),
        True,
    )


class LocalSubprocessRunner(CommandRunner):
    """在本机用 `subprocess` 执行命令的执行器。

    Args:
        timeout: 单条命令的超时上限（秒）。
        max_stdout_chars: stdout 保留的最大字符数。
        max_stderr_chars: stderr 保留的最大字符数。
        env: 子进程环境变量；`None` 表示继承当前进程（默认）。
    """

    def __init__(self,
                 timeout: float = _DEFAULT_TIMEOUT,
                 max_stdout_chars: int = _DEFAULT_MAX_STDOUT_CHARS,
                 max_stderr_chars: int = _DEFAULT_MAX_STDERR_CHARS,
                 env: dict[str, str] | None = None) -> None:
        self._timeout = timeout
        self._max_stdout_chars = max_stdout_chars
        self._max_stderr_chars = max_stderr_chars
        self._env = env

    # ------------------------------------------------------------------
    @property
    def timeout(self) -> float:
        return self._timeout

    def run(self, command: str, args: Sequence[str] = ()) -> CommandResult:
        argv = [command, *args]
        started = time.perf_counter()

        try:
            proc = subprocess.run(
                argv,
                shell=False,               # 关键：参数以列表传递，不经 shell 解释
                capture_output=True,       # 分别捕获 stdout / stderr
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,  # 避免交互等待把 Agent 卡住
                env=self._env,
            )
        except FileNotFoundError:
            raise CliNotFoundError(command)
        except PermissionError as exc:
            raise CliExecutionError(command, "权限不足或不是可执行文件（%s）" % exc)
        except OSError as exc:
            raise CliExecutionError(command, str(exc))
        except subprocess.TimeoutExpired as exc:
            # 超时不是「工具故障」：把已产生的输出交给模型，让它判断下一步
            return self._build(
                command, args,
                stdout_raw=exc.stdout or b"",
                stderr_raw=exc.stderr or b"",
                exit_code=None,
                started=started,
                timed_out=True,
            )

        return self._build(
            command, args,
            stdout_raw=proc.stdout or b"",
            stderr_raw=proc.stderr or b"",
            exit_code=proc.returncode,
            started=started,
            timed_out=False,
        )

    # ------------------------------------------------------------------
    def _build(self,
               command: str,
               args: Sequence[str],
               *,
               stdout_raw: bytes,
               stderr_raw: bytes,
               exit_code: int | None,
               started: float,
               timed_out: bool) -> CommandResult:
        """解码 + 截断 + 组装不可变结果对象。"""
        stdout = self._decode(stdout_raw)
        stderr = self._decode(stderr_raw)
        stdout, stdout_truncated = truncate_text(stdout, self._max_stdout_chars)
        stderr, stderr_truncated = truncate_text(stderr, self._max_stderr_chars)

        return CommandResult(
            command=command,
            args=tuple(str(a) for a in args),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            timed_out=timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout_bytes=len(stdout_raw),
            stderr_bytes=len(stderr_raw),
        )

    @staticmethod
    def _decode(data: bytes | str) -> str:
        """按 utf-8 → gb18030 → latin-1 顺序解码。

        Windows 中文终端（GBK）输出的程序用 utf-8 解会乱码，
        gb18030 是 GBK 的超集，作为第二选择最实用。
        """
        if isinstance(data, str):  # text=True 等场景的兜底
            return data
        for encoding in _ENCODINGS:
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, ValueError):
                continue
        return data.decode("utf-8", "replace")  # pragma: no cover - 兜底


__all__ = ["LocalSubprocessRunner", "truncate_text"]
