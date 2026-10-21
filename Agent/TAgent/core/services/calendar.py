# -*- coding: utf-8 -*-
"""时间与历法领域服务。

与 `filesystem.py` 同理：只做业务，不做参数解析，不做输出。
"""

from __future__ import annotations

import subprocess


class CalendarService:
    """日期与历法计算。"""

    def __init__(self, use_system_command: bool = True, timeout: int = 10) -> None:
        self.use_system_command = use_system_command
        self.timeout = timeout

    def current_datetime_text(self) -> str:
        """获取当前电脑的日期和时间。

        优先调用系统 `date` 命令（与原练习保持一致），
        失败时回退到 Python `datetime`，保证服务永不因环境差异而崩溃。
        """
        if not self.use_system_command:
            return self._python_datetime()

        import os

        try:
            if os.name == "nt":
                # Windows：date / time 不加 /t 会进入交互模式；
                # 它们是 cmd 内部命令，必须 shell=True
                date_part = subprocess.run(
                    "date /t", shell=True, capture_output=True, timeout=self.timeout)
                time_part = subprocess.run(
                    "time /t", shell=True, capture_output=True, timeout=self.timeout)
                stdout = (self._decode(date_part.stdout).strip() + " "
                          + self._decode(time_part.stdout).strip()).strip()
            else:
                result = subprocess.run(
                    ["date"], capture_output=True, timeout=self.timeout)
                stdout = self._decode(result.stdout).strip()
        except Exception:
            return self._python_datetime()

        return stdout or self._python_datetime()

    @staticmethod
    def _decode(raw: bytes) -> str:
        """按 UTF-8 → GBK → 忽略错误 的顺序解码子进程输出。

        Windows 中文环境下 `date /t` 输出的是 GBK 字节，直接 `text=True`
        会因默认编码不匹配而抛 UnicodeDecodeError（且发生在子线程里）。
        """
        if not raw:
            return ""
        for encoding in ("utf-8", "gbk"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _python_datetime() -> str:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def is_leap_year(year: int) -> bool:
        """格里高利历闰年判断。

        - 能被 4 整除但不能被 100 整除 → 闰年（如 2024）
        - 能被 400 整除 → 闰年（如 2000）
        - 其余 → 平年（如 1900、2023）
        """
        return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)

    def describe_leap_year(self, year: int) -> str:
        return "%d 年%s闰年" % (year, "是" if self.is_leap_year(year) else "不是")
