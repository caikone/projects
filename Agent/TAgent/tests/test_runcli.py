# -*- coding: utf-8 -*-
"""本地命令行（RunCli）工具的单元测试。

分层验证：
    1. `CommandService`：参数校验 + 结果渲染（纯逻辑，不真跑进程）。
    2. `LocalSubprocessRunner`：真的用 `subprocess` 跑命令（离线，用当前解释器）。
       重点覆盖「退出码 / stdout / stderr / 超时 / 截断 / 找不到程序」。
    3. 工具注册与异常映射：适配器是否把业务异常转成 `ToolOutcome.failure`。

安全边界：本工具教学阶段**不做**白名单，因此测试里也刻意只用
当前解释器执行无害片段（打印文本 / 睡眠），不碰任何破坏性命令。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.domain.cli import CommandResult  # noqa: E402
from core.errors import (  # noqa: E402
    CliArgumentError,
    CliExecutionError,
    CliNotFoundError,
)
from core.infrastructure.cli.local_subprocess import (  # noqa: E402
    LocalSubprocessRunner,
    truncate_text,
)
from core.ports.cli import CommandRunner  # noqa: E402
from core.services.cli import CommandService  # noqa: E402
from core.tools.builtin_cli import build_cli_tools  # noqa: E402

#: 用当前 Python 解释器当「已安装的命令行程序」，跨平台且无需额外依赖
PYTHON = sys.executable


class _StubRunner(CommandRunner):
    """按脚本回放的假执行器：让服务层测试不依赖真实进程。"""

    def __init__(self, result=None, error=None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, list[str]]] = []

    def run(self, command: str, args=()) -> CommandResult:
        self.calls.append((command, list(args)))
        if self._error is not None:
            raise self._error
        return self._result or CommandResult(command=command, args=tuple(args), exit_code=0)


# ==========================================================================
# 参数校验
# ==========================================================================
class ValidateArgumentsTest(unittest.TestCase):
    def test_normalizes_command_and_args(self) -> None:
        command, args = CommandService.validate("  git  ", ["status", "--short"])
        self.assertEqual(command, "git")
        self.assertEqual(args, ["status", "--short"])

    def test_args_defaults_to_empty_list(self) -> None:
        self.assertEqual(CommandService.validate("ipconfig", None), ("ipconfig", []))
        self.assertEqual(CommandService.validate("ipconfig"), ("ipconfig", []))

    def test_rejects_empty_command(self) -> None:
        for command in ("", "   ", None):
            with self.assertRaises(CliArgumentError):
                CommandService.validate(command)

    def test_rejects_args_as_bare_string(self) -> None:
        """args 必须是数组：这里若偷偷 shlex.split 就退化成 shell 语义。"""
        with self.assertRaises(CliArgumentError):
            CommandService.validate("ls", "-la")

    def test_rejects_non_string_items(self) -> None:
        with self.assertRaises(CliArgumentError):
            CommandService.validate("ls", ["-l", {"a": 1}])
        with self.assertRaises(CliArgumentError):
            CommandService.validate("ls", [True])

    def test_numeric_items_are_stringified(self) -> None:
        _, args = CommandService.validate("sleep", [1])
        self.assertEqual(args, ["1"])


# ==========================================================================
# 结果渲染
# ==========================================================================
class RenderResultTest(unittest.TestCase):
    def test_render_success_includes_stdout_and_exit_code(self) -> None:
        result = CommandResult(command="git", args=("status",), exit_code=0,
                               stdout="On branch main", elapsed_ms=12)
        text = CommandService.render(result)
        self.assertIn("$ git status", text)
        self.assertIn("退出码：0", text)
        self.assertIn("On branch main", text)
        self.assertIn("【stdout】", text)

    def test_render_keeps_nonzero_exit_code_as_information(self) -> None:
        """退出码非 0 是「命令的结论」而不是「工具失败」，必须原样呈现。"""
        result = CommandResult(command="git", args=("status",), exit_code=128,
                               stderr="fatal: not a git repository", stdout_bytes=0)
        text = CommandService.render(result)
        self.assertIn("退出码：128", text)
        self.assertIn("not a git repository", text)
        self.assertIn("【stderr】", text)

    def test_render_marks_empty_output(self) -> None:
        text = CommandService.render(CommandResult(command="cd", exit_code=0))
        self.assertIn("没有产生任何输出", text)

    def test_render_marks_truncation_and_timeout(self) -> None:
        result = CommandResult(
            command="find", args=("/",), exit_code=None, stdout="x" * 10,
            timed_out=True, stdout_truncated=True, stdout_bytes=99999,
            stderr_truncated=True, stderr_bytes=42)
        text = CommandService.render(result)
        self.assertIn("超时", text)
        self.assertIn("无（执行超时被终止）", text)
        self.assertIn("stdout 共 99999 字节，过长已截断", text)
        self.assertIn("stderr 共 42 字节，过长已截断", text)

    def test_command_line_quotes_special_characters(self) -> None:
        """命令行回显要把含空格的参数加引号（只是展示，执行仍走列表）。"""
        result = CommandResult(command="echo", args=("a b", "&", "c"))
        self.assertIn("'a b'", result.command_line)
        self.assertIn("'&'", result.command_line)
        self.assertEqual(result.argv, ["echo", "a b", "&", "c"])


# ==========================================================================
# 截断工具
# ==========================================================================
class TruncateTextTest(unittest.TestCase):
    def test_short_text_untouched(self) -> None:
        text, truncated = truncate_text("hello", 100)
        self.assertEqual(text, "hello")
        self.assertFalse(truncated)

    def test_keeps_head_and_tail_with_marker(self) -> None:
        text = "".join(str(i % 10) for i in range(1000))
        cut, truncated = truncate_text(text, 200)
        self.assertTrue(truncated)
        self.assertLessEqual(len(cut), 200 + 60)  # 正文 + 省略标记
        self.assertIn("已省略", cut)
        self.assertTrue(cut.startswith(text[:10]))
        self.assertTrue(cut.rstrip().endswith(text[-10:]))

    def test_disabled_when_limit_not_positive(self) -> None:
        self.assertEqual(truncate_text("abc" * 100, 0), ("abc" * 100, False))


# ==========================================================================
# 真实执行（subprocess）
# ==========================================================================
class LocalSubprocessRunnerTest(unittest.TestCase):
    def test_captures_stdout_and_exit_code(self) -> None:
        runner = LocalSubprocessRunner(timeout=20)
        result = runner.run(PYTHON, ["-c", "print('hello-cli')"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello-cli", result.stdout)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.succeeded)

    def test_captures_stderr_and_nonzero_exit_code(self) -> None:
        runner = LocalSubprocessRunner(timeout=20)
        result = runner.run(PYTHON, ["-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
        self.assertEqual(result.exit_code, 3)
        self.assertIn("boom", result.stderr)
        self.assertFalse(result.succeeded)

    def test_missing_program_raises_not_found(self) -> None:
        runner = LocalSubprocessRunner(timeout=5)
        with self.assertRaises(CliNotFoundError):
            runner.run("definitely-not-a-real-program-xyz", ["--version"])

    def test_directory_as_command_raises_execution_error(self) -> None:
        """给一个目录当程序：操作系统会拒绝，应翻译成 CliExecutionError。"""
        runner = LocalSubprocessRunner(timeout=5)
        with self.assertRaises(CliExecutionError):
            runner.run(os.getcwd())

    def test_timeout_terminates_and_keeps_partial_output(self) -> None:
        runner = LocalSubprocessRunner(timeout=0.5)
        result = runner.run(PYTHON, ["-c", "import sys,time; sys.stdout.write('part');"
                                            " sys.stdout.flush(); time.sleep(10)"])
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)

    def test_long_output_is_truncated(self) -> None:
        runner = LocalSubprocessRunner(timeout=20, max_stdout_chars=500,
                                       max_stderr_chars=100)
        result = runner.run(PYTHON, ["-c", "print('x' * 20000)"])
        self.assertTrue(result.stdout_truncated)
        self.assertIn("已省略", result.stdout)
        self.assertGreater(result.stdout_bytes, 20000)

    def test_special_characters_are_not_shell_interpreted(self) -> None:
        """`&`、`|`、空格必须当作普通参数，而不是 shell 语法。"""
        runner = LocalSubprocessRunner(timeout=20)
        result = runner.run(PYTHON, ["-c", "import sys; print('|'.join(sys.argv[1:]))",
                                     "a & b", "c|d", "e > f"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("a & b", result.stdout)
        self.assertIn("c|d", result.stdout)
        self.assertIn("e > f", result.stdout)

    def test_env_override_is_passed_to_child(self) -> None:
        runner = LocalSubprocessRunner(timeout=20, env={"TAGENT_TEST_KEY": "42", "PATH": os.environ.get("PATH", "")})
        result = runner.run(PYTHON, ["-c", "import os; print(os.environ['TAGENT_TEST_KEY'])"])
        self.assertIn("42", result.stdout)


# ==========================================================================
# 服务层 + 工具适配器
# ==========================================================================
class CommandServiceTest(unittest.TestCase):
    def test_run_returns_structured_result_and_text(self) -> None:
        runner = _StubRunner(CommandResult(command="echo", args=("hi",), exit_code=0,
                                           stdout="hi"))
        run = CommandService(runner).run("echo", ["hi"])
        self.assertEqual(runner.calls[0], ("echo", ["hi"]))
        self.assertEqual(run.result.exit_code, 0)
        self.assertIn("hi", run.text)

    def test_run_propagates_cli_errors(self) -> None:
        service = CommandService(_StubRunner(error=CliNotFoundError("nope")))
        with self.assertRaises(CliNotFoundError):
            service.run("nope")


class RunCliToolTest(unittest.TestCase):
    def _tool(self, runner: CommandRunner):
        tools = build_cli_tools(CommandService(runner))
        return tools[0]

    def test_schema(self) -> None:
        tool = self._tool(_StubRunner())
        spec = tool.spec
        self.assertEqual(spec.name, "RunCli")
        self.assertEqual(spec.required, ["command"])

        schema = spec.to_json_schema()
        self.assertEqual(schema["properties"]["command"]["type"], "string")
        args_schema = schema["properties"]["args"]
        self.assertEqual(args_schema["type"], "array")
        self.assertEqual(args_schema["items"], {"type": "string"})

    def test_invoke_success(self) -> None:
        tool = self._tool(_StubRunner(CommandResult(command="ipconfig", exit_code=0,
                                                    stdout="IPv4 地址")))
        outcome = tool.invoke({"command": "ipconfig"})
        self.assertTrue(outcome.ok)
        self.assertIn("退出码：0", outcome.content)
        self.assertIn("IPv4 地址", outcome.content)

    def test_missing_command_returns_failure(self) -> None:
        tool = self._tool(_StubRunner())
        for payload in ({}, {"command": "  "}):
            outcome = tool.invoke(payload)
            self.assertFalse(outcome.ok)
            self.assertIn("command", outcome.content)

    def test_bad_args_type_returns_failure(self) -> None:
        """args 传字符串：护栏把 CliArgumentError 转成失败结果，不能中断会话。"""
        tool = self._tool(_StubRunner())
        outcome = tool.invoke({"command": "ls", "args": "-la"})
        self.assertFalse(outcome.ok)
        self.assertIn("字符串数组", outcome.content)

    def test_missing_program_returns_failure_not_exception(self) -> None:
        tool = self._tool(_StubRunner(error=CliNotFoundError("no-such-cmd")))
        outcome = tool.invoke({"command": "no-such-cmd"})
        self.assertFalse(outcome.ok)
        self.assertIn("找不到可执行程序", outcome.content)


if __name__ == "__main__":
    unittest.main()
