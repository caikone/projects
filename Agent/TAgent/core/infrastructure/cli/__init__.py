# -*- coding: utf-8 -*-
"""本地命令执行基础设施包。"""

from .local_subprocess import LocalSubprocessRunner, truncate_text

__all__ = ["LocalSubprocessRunner", "truncate_text"]
