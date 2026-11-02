# -*- coding: utf-8 -*-
"""Skill 仓库端口（Port）：技能发现与按需读取的抽象契约。

服务层只依赖这个抽象，因此「技能从本地目录来」这件事可以被替换成
将来的「技能从 Git 仓库 / 远端注册表来」，而不必改动 `SkillService`。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.skills import (
    SkillCatalog,
    SkillContent,
    SkillCreation,
    SkillDraft,
    SkillMetadata,
)


class SkillStore(ABC):
    """技能存储：负责「在哪里找 Skill」以及「怎么读 Skill 里的文件」。

    实现方必须保证两条安全约束：

        1. **路径不越界**：无论传入什么路径，读取范围都限制在该 Skill 目录内部；
        2. **发现阶段不读正文**：`discover()` 只允许解析 Frontmatter 元信息。
    """

    @abstractmethod
    def root(self) -> str:
        """技能根目录的绝对路径（`.agents/skills`）。"""
        raise NotImplementedError

    @abstractmethod
    def discover(self) -> SkillCatalog:
        """扫描根目录，产出只含 name / description 的技能清单。

        目录不存在、子目录没有 `SKILL.md`、Frontmatter 缺字段等情况
        都**不抛异常**，而是记进 `SkillCatalog.issues` 后跳过——
        一个坏掉的 Skill 不该让整个应用起不来。
        """
        raise NotImplementedError

    @abstractmethod
    def create(self, draft: SkillDraft) -> SkillCreation:
        """在根目录下创建 `<name>/SKILL.md`。

        实现方必须保证两条约束：

            1. **绝不覆盖**：目标 `SKILL.md` 已存在时抛
               `SkillAlreadyExistsError`，不写任何字节；
            2. **不越界**：实际写入路径必须落在技能根目录内部，
               防止名称里的 `..` 把文件写到项目其它位置。

        目录已存在但没有 `SKILL.md` 时应当复用该目录
        （模型可能先用 createDirectory 建好了 `references/` 再来建入口文件）。

        Raises:
            SkillAlreadyExistsError: 目标 SKILL.md 已存在。
            SkillPathError:         解析后的目录逃出了技能根目录。
            SkillFileError:         建目录或写文件失败。
        """
        raise NotImplementedError

    @abstractmethod
    def read(self, skill: SkillMetadata, relative: str) -> SkillContent:
        """读取 Skill 目录内的指定文件（相对 Skill 目录的路径）。

        Raises:
            SkillPathError:        解析后的路径逃出了 Skill 目录。
            SkillFileNotFoundError: 文件不存在或不是普通文件。
            SkillFileError:        疑似二进制、无法解码、读取失败。
        """
        raise NotImplementedError

    @abstractmethod
    def candidates(self, skill: SkillMetadata, filename: str) -> list[str]:
        """解析模型给出的文件名，返回**相对 Skill 目录**的候选路径列表。

        兼容三种写法（优先级从高到低）：

            1. 精确路径匹配：相对 Skill 目录 / 相对项目根 / 绝对路径；
            2. 递归搜索：模型只给了文件名时，在 Skill 目录内按文件名查找。

        返回空列表表示没找到；多于一个表示有歧义，由上层交给模型决策。

        Raises:
            SkillPathError: 给出的绝对路径或 `..` 穿越落在 Skill 目录之外。
        """
        raise NotImplementedError


__all__ = ["SkillStore"]
