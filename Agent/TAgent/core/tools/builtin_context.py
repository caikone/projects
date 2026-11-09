# -*- coding: utf-8 -*-
"""内置上下文工具：recallContext（检索历史归档）。

本模块是**适配器**：把 `services/context.py` 的检索能力翻译成
模型可调用的 function schema。检索规则（分词、匹配、片段截取、
长度控制）一律在领域层与服务层，不在这里重复。
"""

from __future__ import annotations

from ..errors import ContextError
from ..ports.tools import ToolOutcome
from ..services.context import ContextService
from .base import ToolBuilder, text_param


def build_context_tools(context: ContextService) -> list:
    """构造上下文相关工具列表。"""
    builder = ToolBuilder(namespace="context")

    @builder.tool(
        name="recallContext",
        description=(
            "检索已被压缩的历史上下文归档（原始对话记录）。"
            "何时调用：当上下文摘要里缺少某个细节（例如具体文件路径、"
            "某次命令输出、之前讨论过的参数），而你需要这些原始信息才能继续时。"
            "不要凭猜测补全历史，也不要每次都调用——只在确实需要时查。"
            "参数：text 为要查找的关键词或短语（必填，多个词用空格分隔，"
            "要求同时命中）；id 可选，指定只检索某一份归档，"
            "省略时检索全部归档（优先返回最近的）。"
            "返回：命中的原始片段，以及归档编号和它在归档中的位置；"
            "结果过长时会截断，请用更具体的关键词或指定 id 再查。"
        ),
        params={
            "text": text_param("要查找的关键词或文本内容；多个关键词用空格分隔（同时命中才算匹配）"),
            "id": text_param("可选。归档编号（如 20261022-153000）；省略则检索全部归档"),
        },
        required=["text"],
    )
    def _recall(args: dict):
        text = args.get("text")
        if text is None or not str(text).strip():
            return ToolOutcome.failure("缺少必需参数 text（要查找的关键词或文本内容）")
        archive_id = args.get("id")
        archive_id = str(archive_id).strip() if archive_id is not None else ""
        try:
            result = context.recall(str(text).strip(), archive_id or None)
        except ContextError as exc:
            return ToolOutcome.failure(exc.message)
        if not result.total_hits:
            return ToolOutcome.success(result.text, display="未命中任何归档片段")
        display = "命中 %d 条（返回 %d 条）" % (result.total_hits, result.returned)
        if result.truncated:
            display += "，已截断"
        return ToolOutcome.success(result.text, display=display)

    return builder.build()


__all__ = ["build_context_tools"]
