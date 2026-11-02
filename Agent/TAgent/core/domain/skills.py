# -*- coding: utf-8 -*-
"""Agent Skills 领域值对象。

与 `domain/cli.py` / `domain/webpage.py` 同一思路：
基础设施产出的**结构化对象**只在服务层渲染成文本，
将来 Web / Desktop 界面可以直接消费字段（例如侧栏列出技能卡片），
而不必反解析字符串。

关键设计：**惰性加载**。
`SkillMetadata` 只携带 `name` / `description` / 路径，
正文 `SkillContent` 只有在模型调用 `LoadSkill` 时才产生。
因此一个项目里放 50 个 Skill，也只有被用到的那一个进入上下文。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Skill 名称（= 目录名）的长度上限，与 Agent Skills 规范一致
SKILL_NAME_MAX_LENGTH = 64
#: 合法名称：小写字母 / 数字，单词之间用单个连字符
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: 非字母数字的连续片段（空格、下划线、标点、中文……）一律压成连字符
_NAME_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def normalize_skill_name(raw: str) -> str:
    """把任意输入归一化成合法的 Skill 名称（小写字母、数字、连字符）。

    规则：

        1. NFKC 归一化（全角字母/数字 → 半角）后转小写；
        2. 空格、下划线、点号等非字母数字片段统一压成一个连字符；
        3. 去掉首尾连字符。

    返回空串表示**无法**转成合法名称（典型情况：纯中文名）。
    此时调用方应给出「请改用英文名（如 code-review）」的明确提示，
    而不是悄悄生成一个空目录名。
    """
    text = unicodedata.normalize("NFKC", raw or "").strip().lower()
    text = _NAME_SEPARATOR_RE.sub("-", text).strip("-")
    return text


def is_valid_skill_name(name: str) -> bool:
    """名称是否符合规范（小写字母/数字/连字符，且长度不超限）。"""
    return bool(name) and len(name) <= SKILL_NAME_MAX_LENGTH \
        and bool(_SKILL_NAME_RE.match(name))


@dataclass(frozen=True)
class SkillMetadata:
    """初始化阶段扫描到的 Skill 元信息（**不含正文**）。

    Attributes:
        name:        Frontmatter 中声明的技能名（模型用它调用 `LoadSkill`）。
        description: 一句话说明，注入 System Prompt 让模型判断该不该用。
        directory:   Skill 目录的绝对路径（附加文件的解析基准）。
        entry:       `SKILL.md` 的绝对路径。
    """

    name: str = ""
    description: str = ""
    directory: str = ""
    entry: str = ""

    @property
    def valid(self) -> bool:
        """Frontmatter 的 name / description 是否都齐全。"""
        return bool(self.name and self.description)

    @property
    def entry_name(self) -> str:
        """入口文件相对 Skill 目录的路径（通常是 `SKILL.md`）。"""
        if not self.entry:
            return ""
        import os

        return os.path.basename(self.entry)


@dataclass(frozen=True)
class SkillContent:
    """一次 `LoadSkill` 真正读到的文件内容。

    Attributes:
        skill:     所属 Skill 名称。
        path:      文件的绝对路径。
        relative:  相对 Skill 目录的路径（POSIX 分隔符，便于模型原样回传）。
        text:      文件内容（可能已被截断）。
        truncated: 是否因超过 `max_read_bytes` 被截断。
        size:      文件原始字节数（截断前）。
    """

    skill: str = ""
    path: str = ""
    relative: str = ""
    text: str = ""
    truncated: bool = False
    size: int = 0


@dataclass(frozen=True)
class SkillDraft:
    """一次 `createSkill` 请求经业务校验后的**待落盘**内容。

    服务层负责把模型给的自然语言内容整理成这个对象，
    基础设施只负责「原样写到 <root>/<name>/SKILL.md」，不再做任何业务判断。

    Attributes:
        name:          归一化后的名称（同时是目录名与 Frontmatter 的 name）。
        original_name: 模型传入的原始名称（用于回显「已归一化」）。
        description:   最终写入 Frontmatter 的说明。
        markdown:      完整的 `SKILL.md` 文本（含 Frontmatter 与正文）。
        declared_name: 模型自己写的 Frontmatter name（若与 name 不同，需要提示）。
        derived_description: description 是否由正文自动提炼。
    """

    name: str = ""
    original_name: str = ""
    description: str = ""
    markdown: str = ""
    declared_name: str = ""
    derived_description: bool = False

    @property
    def renamed(self) -> bool:
        """原始名称是否被归一化过（用于在结果里说明）。"""
        return self.original_name.strip() != self.name


@dataclass(frozen=True)
class SkillCreation:
    """一次成功的 Skill 创建结果（结构化，便于 UI / 提示词各自消费）。

    Attributes:
        skill:             落盘后的技能元信息（可直接交给 `LoadSkill`）。
        draft:             实际写入的内容。
        bytes_written:     `SKILL.md` 的字节数（UTF-8）。
        created_directory: 目录是否是本次新建的（false = 复用已存在的空目录）。
    """

    skill: SkillMetadata
    draft: SkillDraft
    bytes_written: int = 0
    created_directory: bool = True


@dataclass(frozen=True)
class SkillCatalog:
    """一次扫描得到的技能清单。

    Attributes:
        root:   `.agents/skills` 目录绝对路径（不存在时仍会记录，便于提示用户）。
        skills: 解析成功的技能元信息（按名称排序）。
        issues: 扫描过程中被跳过的原因。刻意保留下来——
                一个格式写错的 `SKILL.md` 如果「静默消失」，排查成本极高。
    """

    root: str = ""
    skills: tuple[SkillMetadata, ...] = ()
    issues: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.skills)

    def __iter__(self):  # pragma: no cover - 便于迭代
        return iter(self.skills)

    @property
    def empty(self) -> bool:
        return not self.skills

    def names(self) -> list[str]:
        return [s.name for s in self.skills]

    def get(self, name: str) -> SkillMetadata | None:
        """按名称查找：先精确匹配，再忽略大小写匹配。

        两段式是刻意的——模型经常把 `code-review` 写成 `Code-Review`，
        直接判「不存在」会浪费一整轮对话。
        """
        target = (name or "").strip()
        if not target:
            return None
        for skill in self.skills:
            if skill.name == target:
                return skill
        lowered = target.lower()
        for skill in self.skills:
            if skill.name.lower() == lowered:
                return skill
        return None


__all__ = [
    "SKILL_NAME_MAX_LENGTH",
    "normalize_skill_name",
    "is_valid_skill_name",
    "SkillMetadata",
    "SkillContent",
    "SkillDraft",
    "SkillCreation",
    "SkillCatalog",
]
