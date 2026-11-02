# -*- coding: utf-8 -*-
"""Skill 基础设施：技能从哪儿来、怎么读。

目前只有本地目录一种实现（`local_directory`）；
将来要支持「从 Git 仓库拉取技能」时，在这里新增一个 `SkillStore` 实现即可，
`services/skills.py` 与工具层一行都不用改。
"""

from .local_directory import LocalDirectorySkillStore

__all__ = ["LocalDirectorySkillStore"]
