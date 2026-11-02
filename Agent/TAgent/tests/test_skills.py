# -*- coding: utf-8 -*-
"""Agent Skills 动态加载机制的测试。

覆盖四件事：
    1. 发现与 Frontmatter 解析（只取 name / description，坏 Skill 不拖垮全局）；
    2. System Prompt 注入的措辞与「无技能时不注入」；
    3. LoadSkill 的路径解析（相对 Skill 目录 / 项目相对 / 绝对 / 仅文件名）；
    4. 安全边界：越界拒绝、歧义交给模型、二进制拒绝、超长截断。

全部用临时目录构造，不依赖真实项目里的 .agents/skills。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from core.domain.skills import normalize_skill_name
from core.errors import (
    SkillAlreadyExistsError,
    SkillAmbiguousFileError,
    SkillArgumentError,
    SkillFileError,
    SkillFileNotFoundError,
    SkillNotFoundError,
    SkillPathError,
)
from core.infrastructure.skills.local_directory import (
    ENTRY_FILENAME,
    LocalDirectorySkillStore,
    parse_frontmatter,
)
from core.services.skills import SkillService
from core.tools.builtin_skills import build_skill_tools

SKILL_TEMPLATE = """---
name: {name}
description: {description}
---

# {name}

正文内容 BODY。
"""

BLOCK_SKILL = """---
name: blocky
description: >
  多行描述第一行
  多行描述第二行
---

正文。
"""


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


class FrontmatterTest(unittest.TestCase):
    """YAML Frontmatter 解析（内置最小解析器，零依赖可用）。"""

    def test_parses_name_and_description(self) -> None:
        meta, body = parse_frontmatter(
            SKILL_TEMPLATE.format(name="demo", description="做一件事"))
        self.assertEqual(meta["name"], "demo")
        self.assertEqual(meta["description"], "做一件事")
        self.assertIn("正文内容 BODY", body)
        self.assertNotIn("name: demo", body)

    def test_block_scalar_description(self) -> None:
        meta, _ = parse_frontmatter(BLOCK_SKILL)
        self.assertEqual(meta["name"], "blocky")
        self.assertIn("多行描述第一行", meta["description"])
        self.assertIn("多行描述第二行", meta["description"])

    def test_invisible_chars_do_not_break_delimiters(self) -> None:
        """从 Word / 微信复制来的零宽字符不得让 `---` 失配、把正文吞进 Frontmatter。"""
        text = (
            "\ufeff---\n"
            "\u200b---\n"          # 混进零宽字符的伪分隔符
            "name: notice\n"
            "description: 撰写通知\n"
            "---\n"
            "正文第一行\n"
        )
        meta, body = parse_frontmatter(text)
        # 清洗后元信息照常解析出来
        self.assertEqual(meta["name"], "notice")
        self.assertEqual(meta["description"], "撰写通知")
        # 关键：正文必须还在，不能被吞进 Frontmatter
        self.assertIn("正文第一行", body)

    def test_fullwidth_colon_is_accepted(self) -> None:
        meta, _ = parse_frontmatter(
            "---\nname：notice\ndescription：撰写通知\n---\n正文")
        self.assertEqual(meta["name"], "notice")
        self.assertEqual(meta["description"], "撰写通知")

    def test_clean_frontmatter_still_works(self) -> None:
        meta, body = parse_frontmatter(
            "---\nname: notice\ndescription: 撰写通知\n---\n1. 标题要带部门名\n")
        self.assertEqual(meta["name"], "notice")
        self.assertIn("1. 标题要带部门名", body)

    def test_no_frontmatter(self) -> None:
        meta, body = parse_frontmatter("# 直接就是正文\n没有 frontmatter")
        self.assertEqual(meta, {})
        self.assertIn("直接就是正文", body)

    def test_quoted_and_inline_comment(self) -> None:
        meta, _ = parse_frontmatter(
            "---\nname: 'quoted'\ndescription: \"带引号\" # 这是注释\n---\nbody")
        self.assertEqual(meta["name"], "quoted")
        self.assertEqual(meta["description"], "带引号")


class DiscoveryTest(unittest.TestCase):
    """扫描 .agents/skills/ 的行为。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-skills-")
        self.skills_root = os.path.join(self.tmp, ".agents", "skills")
        os.makedirs(self.skills_root)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _store(self, **kwargs) -> LocalDirectorySkillStore:
        return LocalDirectorySkillStore(project_root=self.tmp, **kwargs)

    def test_discovers_valid_skills(self) -> None:
        write(os.path.join(self.skills_root, "alpha", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="alpha", description="第一个技能"))
        write(os.path.join(self.skills_root, "beta", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="beta", description="第二个技能"))
        catalog = self._store().discover()
        self.assertEqual(catalog.names(), ["alpha", "beta"])
        self.assertEqual(catalog.issues, ())

    def test_discovery_does_not_load_body(self) -> None:
        """发现阶段只保留 name / description，正文不得进入元信息。"""
        write(os.path.join(self.skills_root, "alpha", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="alpha", description="第一个技能"))
        catalog = self._store().discover()
        skill = catalog.get("alpha")
        self.assertIsNotNone(skill)
        self.assertNotIn("BODY", skill.description)
        # 元信息对象上没有承载正文的字段
        self.assertFalse(hasattr(skill, "text"))

    def test_missing_entry_is_recorded_as_issue(self) -> None:
        os.makedirs(os.path.join(self.skills_root, "broken"))
        catalog = self._store().discover()
        self.assertTrue(catalog.empty)
        self.assertEqual(len(catalog.issues), 1)
        self.assertIn("broken", catalog.issues[0])
        self.assertIn(ENTRY_FILENAME, catalog.issues[0])

    def test_invalid_frontmatter_is_skipped(self) -> None:
        write(os.path.join(self.skills_root, "no-desc", ENTRY_FILENAME),
              "---\nname: no-desc\n---\nbody")
        write(os.path.join(self.skills_root, "no-name", ENTRY_FILENAME),
              "---\ndescription: 没有名字\n---\nbody")
        catalog = self._store().discover()
        self.assertTrue(catalog.empty)
        self.assertEqual(len(catalog.issues), 2)

    def test_duplicate_name_is_recorded(self) -> None:
        write(os.path.join(self.skills_root, "dir1", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="same", description="一"))
        write(os.path.join(self.skills_root, "dir2", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="same", description="二"))
        catalog = self._store().discover()
        self.assertEqual(len(catalog), 1)
        self.assertTrue(any("重复" in i for i in catalog.issues))

    def test_missing_root_is_empty_not_error(self) -> None:
        empty = tempfile.mkdtemp(prefix="tagent-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        catalog = LocalDirectorySkillStore(project_root=empty).discover()
        self.assertTrue(catalog.empty)
        self.assertEqual(catalog.issues, ())  # 没放技能是正常状态，不算问题

    def test_hidden_dirs_and_files_are_ignored(self) -> None:
        os.makedirs(os.path.join(self.skills_root, ".hidden"))
        write(os.path.join(self.skills_root, "README.md"), "不是技能")
        self.assertTrue(self._store().discover().empty)


class CatalogLookupTest(unittest.TestCase):
    def test_case_insensitive_lookup(self) -> None:
        from core.domain.skills import SkillCatalog, SkillMetadata

        catalog = SkillCatalog(
            root="/x", skills=(SkillMetadata(name="code-review", description="d",
                                             directory="/x/code-review", entry="/x/code-review/SKILL.md"),))
        self.assertIsNotNone(catalog.get("code-review"))
        self.assertIsNotNone(catalog.get("Code-Review"))  # 模型常写错大小写
        self.assertIsNone(catalog.get("nope"))
        self.assertIsNone(catalog.get(""))


class SystemPromptInjectionTest(unittest.TestCase):
    """技能清单注入 System Prompt 的措辞与条件。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-skills-")
        self.skills_root = os.path.join(self.tmp, ".agents", "skills")
        os.makedirs(self.skills_root)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_block_uses_spec_wording(self) -> None:
        write(os.path.join(self.skills_root, "a", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="skill-a", description="做 A"))
        service = SkillService(LocalDirectorySkillStore(project_root=self.tmp))
        block = service.render_catalog()
        self.assertIn("You have access to the following skills:", block)
        self.assertIn("- 'skill-a': 做 A", block)
        self.assertIn("Use the 'LoadSkill' tool to load the full instructions", block)

    def test_no_skills_means_no_injection(self) -> None:
        service = SkillService(LocalDirectorySkillStore(project_root=self.tmp))
        self.assertEqual(service.render_catalog(), "")
        self.assertEqual(service.compose_system_prompt("基础提示词"), "基础提示词")

    def test_compose_appends_after_base(self) -> None:
        write(os.path.join(self.skills_root, "a", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="skill-a", description="做 A"))
        service = SkillService(LocalDirectorySkillStore(project_root=self.tmp))
        prompt = service.compose_system_prompt("基础提示词")
        self.assertTrue(prompt.startswith("基础提示词"))
        self.assertIn("You have access to the following skills:", prompt)


class LoadSkillTest(unittest.TestCase):
    """LoadSkill 的路径解析、歧义处理与安全边界。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-skills-")
        self.skills_root = os.path.join(self.tmp, ".agents", "skills")
        os.makedirs(self.skills_root)
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.skill_dir = os.path.join(self.skills_root, "toolkit")
        write(os.path.join(self.skill_dir, ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="toolkit", description="工具箱"))
        write(os.path.join(self.skill_dir, "scripts", "lint.py"), "print('lint')")
        write(os.path.join(self.skill_dir, "docs", "guide.md"), "# 指南")
        # 项目根下的文件（用于验证「项目相对路径」兼容）
        write(os.path.join(self.tmp, "project-note.txt"), "来自项目根")
        # Skill 目录外、项目内的文件（用于验证越界拒绝）
        write(os.path.join(self.skills_root, "other", "secret.txt"), "不该被读到")
        self.service = SkillService(LocalDirectorySkillStore(project_root=self.tmp))

    # ------------------------------------------------------------------
    def test_load_entry_by_name_only(self) -> None:
        text = self.service.load("toolkit")
        self.assertIn("【Skill: toolkit】", text)
        self.assertIn("正文内容 BODY", text)          # 正文确实进来了
        self.assertIn(ENTRY_FILENAME, text)           # 回传了加载的文件名

    def test_load_additional_file_relative_to_skill_dir(self) -> None:
        text = self.service.load("toolkit", "scripts/lint.py")
        self.assertIn("print('lint')", text)
        self.assertIn("scripts/lint.py", text)
        self.assertNotIn("正文内容 BODY", text)       # 不重复加载 SKILL.md

    def test_load_by_bare_filename_recursive(self) -> None:
        text = self.service.load("toolkit", "lint.py")
        self.assertIn("print('lint')", text)
        self.assertIn("scripts/lint.py", text)

    def test_load_by_project_relative_path(self) -> None:
        rel = os.path.relpath(os.path.join(self.skill_dir, "docs", "guide.md"), self.tmp)
        text = self.service.load("toolkit", rel.replace(os.sep, "/"))
        self.assertIn("# 指南", text)

    def test_load_by_absolute_path_inside_skill_dir(self) -> None:
        absolute = os.path.join(self.skill_dir, "docs", "guide.md")
        text = self.service.load("toolkit", absolute)
        self.assertIn("# 指南", text)

    def test_ambiguous_filename_returns_candidates(self) -> None:
        write(os.path.join(self.skill_dir, "other", "lint.py"), "print('other')")
        with self.assertRaises(SkillAmbiguousFileError) as ctx:
            self.service.load("toolkit", "lint.py")
        self.assertEqual(len(ctx.exception.candidates), 2)
        self.assertIn("scripts/lint.py", ctx.exception.candidates)

    def test_exact_path_beats_basename_match(self) -> None:
        """给了精确路径就不该再产生歧义。"""
        write(os.path.join(self.skill_dir, "other", "lint.py"), "print('other')")
        text = self.service.load("toolkit", "scripts/lint.py")
        self.assertIn("print('lint')", text)

    def test_unknown_file_raises(self) -> None:
        with self.assertRaises(SkillFileNotFoundError):
            self.service.load("toolkit", "nope.py")

    def test_unknown_skill_lists_available(self) -> None:
        with self.assertRaises(SkillNotFoundError) as ctx:
            self.service.load("nope")
        self.assertIn("toolkit", ctx.exception.message)

    def test_missing_name_argument(self) -> None:
        from core.errors import SkillArgumentError

        with self.assertRaises(SkillArgumentError):
            self.service.load("   ")

    # ------------------------------------------------------------------
    # 安全边界
    # ------------------------------------------------------------------
    def test_parent_escape_is_rejected(self) -> None:
        with self.assertRaises(SkillPathError):
            self.service.load("toolkit", "../other/secret.txt")
        # 即使路径存在，内容也不得泄漏
        for attempt in ("../other/secret.txt", "..\\other\\secret.txt"):
            try:
                text = self.service.load("toolkit", attempt)
            except (SkillPathError, SkillFileNotFoundError):
                continue
            self.assertNotIn("不该被读到", text)

    def test_absolute_path_outside_skill_dir_is_rejected(self) -> None:
        outside = os.path.join(self.skills_root, "other", "secret.txt")
        with self.assertRaises(SkillPathError):
            self.service.load("toolkit", outside)

    def test_trying_to_read_sibling_skill_file(self) -> None:
        """穿越到同级 Skill 目录：必须拒绝，且绝不能误读成自己的 SKILL.md。"""
        write(os.path.join(self.skills_root, "sibling", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="sibling", description="另一个"))
        with self.assertRaises(SkillPathError):
            self.service.load("toolkit", "../sibling/SKILL.md")

    def test_binary_file_is_rejected(self) -> None:
        path = os.path.join(self.skill_dir, "blob.bin")
        with open(path, "wb") as f:
            f.write(b"\x00\x01\x02\x03")
        with self.assertRaises(SkillFileError):
            self.service.load("toolkit", "blob.bin")

    def test_truncation_is_annotated(self) -> None:
        long_text = "x" * 5000
        write(os.path.join(self.skill_dir, "big.txt"), long_text)
        service = SkillService(
            LocalDirectorySkillStore(project_root=self.tmp, max_read_bytes=500))
        text = service.load("toolkit", "big.txt")
        self.assertIn("过长已截断", text)
        self.assertIn("5000", text)


class LoadSkillToolTest(unittest.TestCase):
    """工具层：schema 声明 + 异常转 `ToolOutcome.failure`。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-skills-")
        self.skills_root = os.path.join(self.tmp, ".agents", "skills")
        write(os.path.join(self.skills_root, "toolkit", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="toolkit", description="工具箱"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.service = SkillService(LocalDirectorySkillStore(project_root=self.tmp))
        self.tool = build_skill_tools(self.service)[0]

    def test_spec_declaration(self) -> None:
        spec = self.tool.spec
        self.assertEqual(spec.name, "LoadSkill")
        self.assertEqual(spec.required, ["name"])     # filename 必须可选
        self.assertIn("filename", spec.params)
        schema = spec.to_openai_tool()
        self.assertEqual(schema["function"]["parameters"]["required"], ["name"])

    def test_success_path(self) -> None:
        outcome = self.tool.invoke({"name": "toolkit"})
        self.assertTrue(outcome.ok)
        self.assertIn("正文内容 BODY", outcome.content)

    def test_success_with_filename(self) -> None:
        write(os.path.join(self.skills_root, "toolkit", "extra.md"), "附加内容")
        outcome = self.tool.invoke({"name": "toolkit", "filename": "extra.md"})
        self.assertTrue(outcome.ok)
        self.assertIn("附加内容", outcome.content)

    def test_missing_name_is_failure_not_exception(self) -> None:
        outcome = self.tool.invoke({})
        self.assertFalse(outcome.ok)
        self.assertIn("name", outcome.content)

    def test_unknown_skill_is_failure(self) -> None:
        outcome = self.tool.invoke({"name": "nope"})
        self.assertFalse(outcome.ok)
        self.assertIn("toolkit", outcome.content)     # 把可用项告诉模型

    def test_outcome_display_defaults_to_content(self) -> None:
        outcome = self.tool.invoke({"name": "toolkit"})
        self.assertEqual(outcome.shown(), outcome.content)


class SkillNameNormalizationTest(unittest.TestCase):
    """名称统一为小写字母 / 数字 / 连字符。"""

    def test_spaces_and_case(self) -> None:
        self.assertEqual(normalize_skill_name("Code Review"), "code-review")

    def test_underscores_and_dots(self) -> None:
        self.assertEqual(normalize_skill_name("  my_skill.v2  "), "my-skill-v2")

    def test_fullwidth_characters(self) -> None:
        self.assertEqual(normalize_skill_name("Ｃｏｄｅ　Ｒｅｖｉｅｗ"), "code-review")

    def test_pure_chinese_cannot_be_normalized(self) -> None:
        self.assertEqual(normalize_skill_name("代码审查"), "")


class CreateSkillTest(unittest.TestCase):
    """createSkill 的业务规则：规范化、补全 Frontmatter、绝不覆盖。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-create-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.skills_root = os.path.join(self.tmp, ".agents", "skills")
        self.service = SkillService(LocalDirectorySkillStore(project_root=self.tmp))

    # ------------------------------------------------------------------
    def entry(self, name: str) -> str:
        return os.path.join(self.skills_root, name, ENTRY_FILENAME)

    def read(self, name: str) -> str:
        with open(self.entry(name), encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------
    def test_creates_directory_and_entry(self) -> None:
        text = self.service.create("code-review", SKILL_TEMPLATE.format(
            name="code-review", description="代码审查流程"))
        self.assertTrue(os.path.isfile(self.entry("code-review")))
        self.assertIn("【Skill 已创建：code-review】", text)

        meta, body = parse_frontmatter(self.read("code-review"))
        self.assertEqual(meta["name"], "code-review")
        self.assertEqual(meta["description"], "代码审查流程")
        self.assertIn("正文内容 BODY", body)

    def test_name_is_normalized_to_slug(self) -> None:
        text = self.service.create("Code Review", "# 步骤\n\n先看 diff。")
        self.assertTrue(os.path.isdir(os.path.join(self.skills_root, "code-review")))
        self.assertIn("归一化", text)
        meta, _ = parse_frontmatter(self.read("code-review"))
        self.assertEqual(meta["name"], "code-review")

    def test_non_english_name_is_rejected_with_hint(self) -> None:
        with self.assertRaises(SkillArgumentError) as ctx:
            self.service.create("代码审查", "# 步骤\n\n先看 diff。")
        self.assertIn("英文", ctx.exception.message)
        self.assertFalse(os.path.isdir(os.path.join(self.skills_root, "代码审查")))

    def test_empty_context_is_rejected(self) -> None:
        with self.assertRaises(SkillArgumentError):
            self.service.create("code-review", "   \n  ")
        self.assertFalse(os.path.isdir(os.path.join(self.skills_root, "code-review")))

    def test_frontmatter_only_is_rejected(self) -> None:
        with self.assertRaises(SkillArgumentError) as ctx:
            self.service.create("code-review", "---\nname: code-review\ndescription: x\n---\n")
        self.assertIn("正文", ctx.exception.message)

    def test_missing_frontmatter_is_generated(self) -> None:
        """模型只给正文时，工具要自己补出合法 Frontmatter。"""
        self.service.create("code-review", "# 代码审查\n\n1. 先看 git diff。\n2. 关注空指针。")
        meta, body = parse_frontmatter(self.read("code-review"))
        self.assertEqual(meta["name"], "code-review")
        self.assertEqual(meta["description"], "代码审查")   # 从标题提炼
        self.assertIn("关注空指针", body)
        # 补出来的技能必须能被正常发现（description 非空）
        self.assertIn("code-review", self.service.refresh().names())

    def test_declared_name_is_aligned_with_directory(self) -> None:
        self.service.create("code-review", SKILL_TEMPLATE.format(
            name="whatever", description="说明"))
        meta, _ = parse_frontmatter(self.read("code-review"))
        self.assertEqual(meta["name"], "code-review")

    def test_extra_frontmatter_keys_are_kept(self) -> None:
        self.service.create("code-review",
                            "---\nname: code-review\ndescription: 说明\nlicense: MIT\n---\n\n正文")
        meta, _ = parse_frontmatter(self.read("code-review"))
        self.assertEqual(meta["license"], "MIT")

    def test_description_is_quoted_when_needed(self) -> None:
        """含 `:` 的描述必须被引号包住，否则 YAML 会解析错。"""
        self.service.create("code-review",
                            "---\nname: code-review\ndescription: 审查流程: 先看 diff\n---\n\n正文")
        meta, _ = parse_frontmatter(self.read("code-review"))
        self.assertEqual(meta["description"], "审查流程: 先看 diff")

    def test_existing_skill_is_not_overwritten(self) -> None:
        self.service.create("code-review", SKILL_TEMPLATE.format(
            name="code-review", description="第一版"))
        before = self.read("code-review")
        with self.assertRaises(SkillAlreadyExistsError) as ctx:
            self.service.create("code-review", "# 新内容\n\n不该写进去")
        self.assertIn("已存在", ctx.exception.message)
        self.assertEqual(self.read("code-review"), before)   # 一字未改

    def test_conflict_by_declared_name(self) -> None:
        """目录名不同、但 Frontmatter 里已声明了同名 Skill → 同样拒绝。"""
        write(os.path.join(self.skills_root, "toolkit", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="code-review", description="已有的"))
        with self.assertRaises(SkillAlreadyExistsError):
            self.service.create("code-review", "# 正文\n\n内容")
        self.assertFalse(os.path.isdir(os.path.join(self.skills_root, "code-review")))

    def test_existing_directory_without_entry_is_reused(self) -> None:
        """模型可能先用 createDirectory 建好 references/，此时要复用目录。"""
        write(os.path.join(self.skills_root, "code-review", "references", "checklist.md"),
              "# 清单")
        text = self.service.create("code-review", "# 步骤\n\n正文")
        self.assertIn("复用", text)
        self.assertTrue(os.path.isfile(self.entry("code-review")))
        self.assertTrue(os.path.isfile(os.path.join(
            self.skills_root, "code-review", "references", "checklist.md")))

    def test_created_skill_is_immediately_loadable(self) -> None:
        self.service.create("code-review", "# 步骤\n\n先看 git diff。")
        self.assertIn("code-review", self.service.catalog.names())
        self.assertIn("先看 git diff", self.service.load("code-review"))
        self.assertIn("先看 git diff", self.service.load("Code-Review"))  # 大小写不敏感

    def test_result_tells_model_where_to_add_extra_files(self) -> None:
        text = self.service.create("code-review", "# 步骤\n\n正文")
        self.assertIn(os.path.join(self.skills_root, "code-review"), text)
        self.assertIn("createFile", text)


class CreateSkillToolTest(unittest.TestCase):
    """工具层：schema 声明 + 参数校验 + 异常转 `ToolOutcome.failure`。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-create-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.skills_root = os.path.join(self.tmp, ".agents", "skills")
        self.service = SkillService(LocalDirectorySkillStore(project_root=self.tmp))
        tools = {t.spec.name: t for t in build_skill_tools(self.service)}
        self.tool = tools["createSkill"]

    def test_spec_declaration(self) -> None:
        spec = self.tool.spec
        self.assertEqual(spec.name, "createSkill")
        self.assertEqual(sorted(spec.required), ["name", "skillContext"])
        schema = spec.to_openai_tool()
        self.assertEqual(sorted(schema["function"]["parameters"]["required"]),
                         ["name", "skillContext"])

    def test_creates_skill(self) -> None:
        outcome = self.tool.invoke({
            "name": "code-review",
            "skillContext": "# 代码审查\n\n1. 先看 git diff。",
        })
        self.assertTrue(outcome.ok)
        self.assertIn("code-review", outcome.content)
        self.assertTrue(os.path.isfile(
            os.path.join(self.skills_root, "code-review", ENTRY_FILENAME)))

    def test_missing_name_is_failure(self) -> None:
        outcome = self.tool.invoke({"skillContext": "# 正文"})
        self.assertFalse(outcome.ok)
        self.assertIn("name", outcome.content)

    def test_missing_context_is_failure(self) -> None:
        outcome = self.tool.invoke({"name": "code-review"})
        self.assertFalse(outcome.ok)
        self.assertIn("skillContext", outcome.content)

    def test_existing_skill_is_failure_not_exception(self) -> None:
        payload = {"name": "code-review", "skillContext": "# 正文\n\n内容"}
        self.assertTrue(self.tool.invoke(payload).ok)
        second = self.tool.invoke(payload)
        self.assertFalse(second.ok)
        self.assertIn("已存在", second.content)


class SkillCoordinationTest(unittest.TestCase):
    """Skill 与既有工具协同，且不改变既有工作流。"""

    def test_container_registers_loadskill_alongside_existing_tools(self) -> None:
        from core.container import AppContainer

        tmp = tempfile.mkdtemp(prefix="tagent-skills-")
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, ".agents", "skills", "toolkit")
        write(os.path.join(root, ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="toolkit", description="工具箱"))

        original = os.getcwd()
        os.chdir(tmp)  # 项目根 = 启动时的工作目录
        try:
            container = AppContainer.build(
                llm=_NullLLM(),
                search_paths=[os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "config.ini")],
            )
        finally:
            os.chdir(original)

        names = {t.spec.name for t in container.tools.all()}
        self.assertIn("LoadSkill", names)
        self.assertIn("createSkill", names)
        self.assertIn("RunCli", names)      # 既有工具一个都不能少
        self.assertIn("fetchWeb", names)
        self.assertEqual(len(container.tools.all()), 17)

    def test_system_prompt_carries_skill_catalog(self) -> None:
        from core.container import AppContainer

        tmp = tempfile.mkdtemp(prefix="tagent-skills-")
        self.addCleanup(shutil.rmtree, tmp, True)
        write(os.path.join(tmp, ".agents", "skills", "toolkit", ENTRY_FILENAME),
              SKILL_TEMPLATE.format(name="toolkit", description="工具箱"))

        original = os.getcwd()
        os.chdir(tmp)
        try:
            container = AppContainer.build(
                llm=_NullLLM(),
                search_paths=[os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "config.ini")],
            )
        finally:
            os.chdir(original)

        session = container.new_session()
        system_text = session.messages[0].content
        self.assertIn("You have access to the following skills:", system_text)
        self.assertIn("- 'toolkit': 工具箱", system_text)
        # 状态栏摘要（无技能时应为空串，不打扰）
        self.assertIn("toolkit", container.build_context().metadata["skills"])

    def test_without_skills_prompt_is_untouched(self) -> None:
        from core.container import AppContainer

        tmp = tempfile.mkdtemp(prefix="tagent-empty-")
        self.addCleanup(shutil.rmtree, tmp, True)
        original = os.getcwd()
        os.chdir(tmp)
        try:
            container = AppContainer.build(
                llm=_NullLLM(),
                search_paths=[os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "config.ini")],
            )
        finally:
            os.chdir(original)
        session = container.new_session()
        self.assertNotIn("You have access to the following skills:",
                         session.messages[0].content)
        self.assertEqual(container.build_context().metadata["skills"], "")


class _NullLLM:
    """不做任何网络请求的最小 LLM 桩（仅用于装配测试）。"""

    def stream(self, request):
        return iter(())

    def complete(self, request):
        raise NotImplementedError

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
