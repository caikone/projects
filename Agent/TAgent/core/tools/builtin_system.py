# -*- coding: utf-8 -*-
"""内置系统工具：日期时间、闰年判断。

本模块是**适配器**：把 `services/calendar.py` 的业务能力
翻译成模型可调用的 function schema + 参数解析。
业务规则本身一律在 CalendarService 中，不在这里重复实现。
"""

from __future__ import annotations

from ..services.calendar import CalendarService
from .base import ToolBuilder, int_param


def build_system_tools(calendar: CalendarService | None = None) -> list:
    """构造系统类工具列表。"""
    calendar = calendar or CalendarService()
    builder = ToolBuilder(namespace="system")

    @builder.tool(
        name="getDate",
        description="获取当前电脑的日期和时间。当用户询问现在的时间、日期、星期几等问题时调用。",
    )
    def _get_date(args: dict) -> str:
        return calendar.current_datetime_text()

    @builder.tool(
        name="isLeapYear",
        description="判断输入的年份是否为闰年。当用户询问某一年是不是闰年时调用。",
        params={"year": int_param("要判断的年份，例如 2024")},
        required=["year"],
    )
    def _is_leap_year(args: dict) -> str:
        raw = args.get("year")
        if raw is None:
            return "缺少必需参数 year"
        try:
            year = int(raw)
        except (TypeError, ValueError):
            return "参数 year 必须是整数，实际收到：%r" % (raw,)
        return calendar.describe_leap_year(year)

    return builder.build()


__all__ = ["build_system_tools"]
