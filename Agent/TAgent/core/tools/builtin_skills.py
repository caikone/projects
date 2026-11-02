# -*- coding: utf-8 -*-
"""内置 Skill 工具：LoadSkill（按需加载）、createSkill（按用户要求创建）。

本模块是**适配器**：把 `services/skills.py` 的加载与创建能力
翻译成模型可调用的 function schema + 参数解析。
业务规则（名称归一化、Frontmatter 合成、冲突检测、路径解析、渲染格式）
一律在 SkillService，不在这里重复。
"""

from __future__ import annotations

from ..ports.tools import ToolOutcome
from ..services.skills import SkillService
from .base import ToolBuilder, text_param


def build_skill_tools(skills: SkillService) -> list:
    """构造 Skill 类工具列表。"""
    builder = ToolBuilder(namespace="skills")

    @builder.tool(
        name="LoadSkill",
        description=(
            "按需加载一个 Skill 的完整说明或它的附加文件。"
            "何时调用：当用户的任务与 System Prompt 里列出的某个 Skill 描述相符时，"
            "先用本工具把该 Skill 的完整指令读进来，再按指令执行；"
            "不要凭猜测行事，也不要在没读指令的情况下声称自己会用某个 Skill。"
            "参数：name 为 Skill 名称（必填）；filename 可选，"
            "省略时加载该 Skill 的 SKILL.md 全文，给出时只加载指定的那一个文件。"
            "路径写法：filename 可以是相对 Skill 目录的路径（如 scripts/lint.py）、"
            "项目相对路径、或该 Skill 目录内的绝对路径；"
            "只给文件名（如 lint.py）也能自动在该 Skill 目录内递归查找。"
            "同名文件有多个时，会返回候选路径列表，请用更精确的路径重试。"
            "读取范围仅限该 Skill 目录内部，越界路径会被拒绝。"
            "加载后可以照常配合 RunCli、readFile 等其它工具一起完成任务。"
        ),
        params={
            "name": text_param(
                "要加载的 Skill 名称，必须与 System Prompt 中列出的名称一致"),
            "filename": text_param(
                "可选。要加载的附加文件路径或文件名；省略则加载该 Skill 的 SKILL.md 全文"),
        },
        required=["name"],
    )
    def _load_skill(args: dict):
        name = args.get("name")
        if name is None or not str(name).strip():
            return ToolOutcome.failure("缺少必需参数 name（Skill 名称）")
        filename = args.get("filename")
        filename = str(filename).strip() if filename is not None else ""
        return skills.load(str(name).strip(), filename or None)

    # ------------------------------------------------------------------
    @builder.tool(
        name="createSkill",
        description=(
            "创建一个新的 Agent Skill：在项目的 .agents/skills/<name>/ 下生成 SKILL.md。"
            "何时调用：用户明确要求「创建一个技能 / 把这个流程固化成 Skill / 保存为技能」时；"
            "用户只是提出一个普通任务时不要调用。"
            "参数 name 是 Skill 名称，将作为子目录名，只能使用小写英文字母、数字和连字符"
            "（如 code-review）；用户用中文或带空格的名字时，请自己转成英文短横线风格。"
            "参数 skillContext 是要写入 SKILL.md 的完整内容，推荐格式："
            "---\\nname: code-review\\ndescription: 一句话说明何时该用这个技能\\n---\\n\\n"
            "正文写成分步骤的操作指令（必要时注明可配合哪些工具）。"
            "若 skillContext 缺少 Frontmatter 或 description，本工具会自动补齐，"
            "并把 name 统一成与目录名一致的规范格式，因此正文写清楚才是关键。"
            "同名 Skill 已存在时本工具不会覆盖，会返回明确提示；"
            "要修改已有 Skill 请改用 readFile + editFile。"
            "还需要 references/、scripts/ 等辅助文件时，请在创建成功后继续用 "
            "createFile 等文件工具在该目录下创建。"
        ),
        params={
            "name": text_param(
                "Skill 名称，作为 .agents/skills/ 下的子目录名：小写字母、数字、连字符"),
            "skillContext": text_param(
                "要写入 SKILL.md 的完整内容（YAML Frontmatter + Markdown 正文）"),
        },
        required=["name", "skillContext"],
    )
    def _create_skill(args: dict):
        name = args.get("name")
        if name is None or not str(name).strip():
            return ToolOutcome.failure("缺少必需参数 name（Skill 名称）")
        context = args.get("skillContext")
        if context is None or not str(context).strip():
            return ToolOutcome.failure(
                "缺少必需参数 skillContext（要写入 SKILL.md 的完整内容）")
        return skills.create(str(name).strip(), str(context))

    return builder.build()


__all__ = ["build_skill_tools"]
