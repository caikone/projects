# -*- coding: utf-8 -*-
"""Agent Skills 应用服务：发现、注入 System Prompt、按需加载、按需创建。

职责边界（与 `services/cli.py` 一致）：
    - **怎么找 / 怎么读 / 怎么写**：交给 `ports.SkillStore`（磁盘细节在 infrastructure）；
    - **怎么呈现给模型**：全部在本文件，业务规则只实现一次；
    - `tools/builtin_skills.py` 只做 schema 翻译，不含业务逻辑。

「动态加载」这条原则在这里落地：
    `catalog` 里只有 name / description，正文只有 `load()` 被调用时才产生。

「规范由工具保证」这条原则在 `create()` 里落地：
    模型给的名称与 Frontmatter 都会被重写成合法格式，
    写不出规范 SKILL.md 的情况根本不会落盘。
"""

from __future__ import annotations

import re

from ..domain.frontmatter import compose_skill_markdown, parse_frontmatter
from ..domain.skills import (
    SKILL_NAME_MAX_LENGTH,
    SkillCatalog,
    SkillContent,
    SkillCreation,
    SkillDraft,
    SkillMetadata,
    normalize_skill_name,
)
from ..errors import (
    SkillAlreadyExistsError,
    SkillAmbiguousFileError,
    SkillArgumentError,
    SkillFileNotFoundError,
    SkillNotFoundError,
)
from ..ports.skills import SkillStore

#: 注入 System Prompt 的技能清单模板（Agent Skills 规范约定的措辞）
SKILLS_PROMPT_HEADER = "You have access to the following skills:"
SKILLS_PROMPT_FOOTER = (
    "Use the 'LoadSkill' tool to load the full instructions of a skill when needed. "
    "You can also load additional files referenced by a skill."
)


class SkillService:
    """技能服务：发现清单 → 渲染提示词 → 按需加载正文。

    Args:
        store: 技能存储实现（本地目录 / 将来的远端注册表）。
        max_read_bytes: 单个文件的读取上限。
    """

    def __init__(self, store: SkillStore, max_read_bytes: int = 1024 * 1024) -> None:
        self._store = store
        self.max_read_bytes = max_read_bytes
        self._catalog: SkillCatalog | None = None

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------
    @property
    def root(self) -> str:
        """技能根目录（用于向用户说明「技能应该放在哪」）。"""
        return self._store.root()

    @property
    def catalog(self) -> SkillCatalog:
        """技能清单（惰性扫描并缓存）。"""
        if self._catalog is None:
            self._catalog = self._store.discover()
        return self._catalog

    def refresh(self) -> SkillCatalog:
        """重新扫描（运行期新增 / 修改了 Skill 时调用）。"""
        self._catalog = self._store.discover()
        return self._catalog

    @property
    def has_skills(self) -> bool:
        return not self.catalog.empty

    # ------------------------------------------------------------------
    # System Prompt 注入
    # ------------------------------------------------------------------
    def render_catalog(self) -> str:
        """把技能清单渲染成注入 System Prompt 的一段文本。

        没有技能时返回空串——此时不该向模型宣传一个用不上的工具。
        """
        catalog = self.catalog
        if catalog.empty:
            return ""
        lines = [SKILLS_PROMPT_HEADER]
        lines.extend("- '%s': %s" % (s.name, s.description) for s in catalog.skills)
        lines.append(SKILLS_PROMPT_FOOTER)
        return "\n".join(lines)

    def compose_system_prompt(self, base_prompt: str) -> str:
        """把技能清单追加到既有系统提示词之后。"""
        block = self.render_catalog()
        base = (base_prompt or "").strip()
        if not block:
            return base
        return "%s\n\n%s" % (base, block) if base else block

    # ------------------------------------------------------------------
    # 按需加载
    # ------------------------------------------------------------------
    def load(self, name: str, filename: str | None = None) -> str:
        """加载一个 Skill 的正文或它的附加文件。

        Args:
            name:     Skill 名称（必填）。
            filename: 可选。省略时加载 `SKILL.md`；给出时只加载该文件。

        Returns:
            渲染好、可直接回传给模型的文本。

        Raises:
            SkillArgumentError / SkillNotFoundError / SkillFileNotFoundError /
            SkillAmbiguousFileError: 见 `core.errors`（工具层会转成
            `ToolOutcome.failure`，模型据此自行决策）。
        """
        wanted = (name or "").strip()
        if not wanted:
            raise SkillArgumentError("缺少必需参数 name（Skill 名称）")

        catalog = self.catalog
        skill = catalog.get(wanted)
        if skill is None:
            raise SkillNotFoundError(wanted, catalog.names())

        if not (filename or "").strip():
            return self._render_entry(skill)

        candidates = self._store.candidates(skill, filename)
        if not candidates:
            raise SkillFileNotFoundError(skill.name, filename)
        if len(candidates) > 1:
            raise SkillAmbiguousFileError(skill.name, filename, candidates)

        content = self._store.read(skill, candidates[0])
        return self._render_file(skill, content)

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    def create(self, name: str, skill_context: str) -> str:
        """把模型给出的自然语言内容落成一个符合规范的 Skill。

        业务规则全部在这里集中：

            1. 名称归一化成「小写字母 / 数字 / 连字符」，纯中文名直接报错；
            2. `skillContext` 无论有没有 Frontmatter，最终都重写成规范格式，
               并强制 `name` 与目录名一致；
            3. 缺 `description` 时从正文首行提炼（否则发现阶段会被判为无效技能）；
            4. 已存在同名 Skill 时明确拒绝，绝不覆盖。

        Returns:
            渲染好的确认信息（含目录路径，方便模型紧接着创建 references/ 等）。

        Raises:
            SkillArgumentError:      名称为空 / 无法归一化 / 正文为空。
            SkillAlreadyExistsError: 同名 Skill 已存在。
            SkillFileError:          写盘失败。
        """
        draft = self._build_draft(name, skill_context)

        # 先按「已声明的名称」查一遍：目录名可能和 Frontmatter 里的 name 不同，
        # 只查目录会漏掉这种重名。
        existing = self.catalog.get(draft.name)
        if existing is not None:
            raise SkillAlreadyExistsError(
                existing.name, existing.directory, existing.entry)

        creation = self._store.create(draft)
        # 刷新清单：本次会话里紧接着的 LoadSkill 就能找到它
        self.refresh()
        return self._render_creation(creation)

    def _build_draft(self, name: str, skill_context: str) -> SkillDraft:
        """校验参数并拼出规范化的 `SKILL.md` 全文。"""
        raw_name = (name or "").strip()
        if not raw_name:
            raise SkillArgumentError("缺少必需参数 name（Skill 名称）")

        slug = normalize_skill_name(raw_name)
        if not slug:
            raise SkillArgumentError(
                "Skill 名称「%s」无法转换成合法目录名。名称必须使用小写英文字母、"
                "数字和连字符（例如 code-review），请改用英文名重试。" % raw_name)
        if len(slug) > SKILL_NAME_MAX_LENGTH:
            raise SkillArgumentError(
                "Skill 名称「%s」归一化后长 %d 个字符，超过上限 %d，请改用更短的名称。"
                % (slug, len(slug), SKILL_NAME_MAX_LENGTH))

        text = (skill_context or "").replace("\r\n", "\n").replace("\r", "\n")
        text = text.lstrip("\ufeff \t\n")
        if not text.strip():
            raise SkillArgumentError(
                "skillContext 不能为空：请把要写入 SKILL.md 的完整内容传进来"
                "（Frontmatter + 给模型看的操作指令正文）。")

        mapping, body = parse_frontmatter(text)
        body = body.strip()
        if not body:
            raise SkillArgumentError(
                "skillContext 只有 Frontmatter、没有正文。"
                "请在 Frontmatter 之后写入这个 Skill 的操作指令正文，否则加载后模型无据可依。")

        declared = str(mapping.get("name") or "").strip()
        description = " ".join(str(mapping.get("description") or "").split())
        derived = False
        if not description:
            description = _derive_description(body, slug)
            derived = True

        return SkillDraft(
            name=slug,
            original_name=raw_name,
            description=description,
            markdown=compose_skill_markdown(slug, description, body, mapping),
            declared_name=declared,
            derived_description=derived,
        )

    def _render_creation(self, creation: SkillCreation) -> str:
        """渲染创建结果：既要报告「写在哪」，也要说清「接下来怎么做」。"""
        draft = creation.draft
        skill = creation.skill
        parts = [
            "【Skill 已创建：%s】" % skill.name,
            "",
            "目录：%s" % skill.directory,
            "入口：%s（%d 字节）" % (skill.entry, creation.bytes_written),
            "Frontmatter：name=%s，description=%s" % (skill.name, draft.description),
        ]
        if draft.renamed:
            parts.append(
                "说明：目录名已按规范归一化（%s → %s）。"
                % (draft.original_name, skill.name))
        if draft.declared_name and draft.declared_name != skill.name:
            parts.append(
                "说明：Frontmatter 里的 name「%s」已统一为 %s，与目录名保持一致。"
                % (draft.declared_name, skill.name))
        if draft.derived_description:
            parts.append(
                "说明：skillContext 未提供 description，已从正文首行提炼；"
                "如需修改请用 editFile 更新该 SKILL.md。")
        if not creation.created_directory:
            parts.append("说明：目录已存在，本次复用了它并只在其中新增了 SKILL.md。")

        parts += [
            "",
            "后续：",
            "1. 本次会话内可以直接用 LoadSkill(name=\"%s\") 加载它（技能清单已刷新）。" % skill.name,
            "2. System Prompt 中的技能列表会在下次启动 / 新会话时列出它。",
            "3. 如需 references/、scripts/ 等辅助文件，请继续用 createFile 等文件工具"
            "在 %s 下创建。" % skill.directory,
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    def _render_entry(self, skill: SkillMetadata) -> str:
        """加载并渲染 `SKILL.md` 全文。"""
        content = self._store.read(skill, skill.entry_name)
        parts = [
            "【Skill: %s】已加载 %s（%d 字符）" % (
                skill.name, content.relative, len(content.text)),
            "",
            content.text,
        ]
        if content.truncated:
            parts += ["", "（该文件共 %d 字节，过长已截断）" % content.size]
        parts += [
            "",
            "（该 Skill 引用其它文件时，用 LoadSkill(name=\"%s\", filename=\"<相对 Skill 目录的路径>\") "
            "单独加载；只给文件名也可以，工具会在该 Skill 目录内递归查找。）" % skill.name,
        ]
        return "\n".join(parts)

    def _render_file(self, skill: SkillMetadata, content: SkillContent) -> str:
        """渲染一个附加文件（不与 `SKILL.md` 重复输出）。"""
        parts = [
            "【Skill: %s】已加载附加文件 %s（%d 字符）" % (
                skill.name, content.relative, len(content.text)),
            "",
            content.text,
        ]
        if content.truncated:
            parts += ["", "（该文件共 %d 字节，过长已截断）" % content.size]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """给 UI / 诊断用的一句话摘要。"""
        catalog = self.catalog
        if catalog.empty:
            return "未发现 Skill（目录：%s）" % self.root
        text = "已发现 %d 个 Skill：%s" % (len(catalog), "、".join(catalog.names()))
        if catalog.issues:
            text += "（%d 个目录被跳过）" % len(catalog.issues)
        return text


_SENTENCE_ENDINGS = "。！？.!?"
_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s*")
_LIST_MARKER_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _derive_description(body: str, name: str, limit: int = 120) -> str:
    """从正文首行提炼一句 description。

    模型调用 `createSkill` 时经常只给正文（甚至直接从 `# 标题` 开始），
    而 `description` 是**必填**字段——缺了会在发现阶段被判为无效技能。
    所以这里兜底提炼：去掉标题符号与列表符号，取到第一个句号为止，超长截断。
    """
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        line = _HEADING_PREFIX_RE.sub("", line)
        line = _LIST_MARKER_RE.sub("", line).strip("*_` ")
        if not line:
            continue
        for index, char in enumerate(line):
            if char in _SENTENCE_ENDINGS:
                line = line[: index + 1]
                break
        if len(line) > limit:
            line = line[: limit - 1].rstrip() + "…"
        return line
    return "%s 技能" % name


__all__ = ["SkillService"]
