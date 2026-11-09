# -*- coding: utf-8 -*-
"""上下文压缩服务：监控用量、归档原始上下文、调用 LLM 生成摘要、替换历史。

一次 `/compact` 的完整顺序（**顺序不能变**）：

    1. 计算用量 → 2. 序列化历史 → 3. **写入归档文件**
      → 4. 调 LLM 生成 ~5000 Tokens 摘要（不带任何工具定义）
      → 5. 校验摘要 → 6. 用「摘要 + 归档说明」替换会话历史

任何一步失败都会**保留原有上下文**，用户可以重新执行 `/compact`：

* 第 3 步失败 → 抛 `ContextArchiveError`，会话完全未动；
* 第 4 步失败 → 抛 `ContextCompactError`，归档已留存（无害），会话未动。

为什么压缩期间禁止工具调用：压缩请求的 `tool_specs` 传空列表，
模型即使想调工具也没有可用的声明，因此不可能产生 `tool_calls`。
"""

from __future__ import annotations

from ..config.models import AgentSettings
from ..domain.context import (
    COMPACT_PROMPT,
    DEFAULT_COMPACT_THRESHOLD,
    DEFAULT_CONTEXT_WINDOW,
    CompactionResult,
    ContextUsage,
    RecallResult,
    SearchHit,
    archive_footer,
    clamp_summary,
    estimate_history_tokens,
    estimate_text_tokens,
    estimate_tool_specs_tokens,
    generate_archive_id,
    is_over_threshold,
    render_records_for_llm,
    serialize_messages,
    split_keywords,
    truncate_hits,
    usage_tokens_from_api,
    with_collision_suffix,
)
from ..errors import ContextCompactError, ContextError, TAgentError
from ..ports.context import ContextArchiveStore
from ..ports.llm import LLMClient, LLMRequest
from .session import Session
from ..domain.messages import ChatMessage


class ContextService:
    """上下文长度监控 + 压缩 + 历史检索。

    Args:
        store:   归档存储（默认写项目本地 `.agents/context/`）。
        llm:     用来生成摘要的大模型客户端（**不带工具定义**调用）。
        settings: 运行时配置（窗口大小、阈值）。
    """

    def __init__(self, store: ContextArchiveStore, llm: LLMClient,
                 settings: AgentSettings) -> None:
        self._store = store
        self._llm = llm
        configured = int(settings.context_window or 0)
        #: 配置为 0 / 负数时视为「不知道」，退回默认窗口并标注「默认」
        self._window = configured if configured > 0 else DEFAULT_CONTEXT_WINDOW
        self._window_is_default = configured <= 0
        self._threshold = float(settings.compact_threshold or DEFAULT_COMPACT_THRESHOLD)
        if not 0 < self._threshold <= 1:
            self._threshold = DEFAULT_COMPACT_THRESHOLD
        #: 摘要软上限：目标 Tokens 的 2 倍字符数（中文约 1 字 1 Token）
        self._max_summary_chars = max(200, int(settings.context_summary_tokens or 0) * 2)
        #: 检索结果总长度上限
        self._recall_max_chars = max(500, int(settings.context_recall_max_chars or 0))

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------
    @property
    def window_tokens(self) -> int:
        return self._window

    @property
    def window_is_default(self) -> bool:
        return self._window_is_default

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def store(self) -> ContextArchiveStore:
        return self._store

    # ------------------------------------------------------------------
    # 用量监控
    # ------------------------------------------------------------------
    def usage(self, session: Session, tool_specs: list | None = None,
              api_usage: dict | None = None) -> ContextUsage:
        """计算当前会话的上下文占用。

        优先用模型 API 返回的 `usage`（精确）；拿不到时本地估算，
        估算口径包括 System Prompt、全部对话消息与工具声明。
        """
        from_api = usage_tokens_from_api(api_usage)
        if from_api is not None and from_api > 0:
            return ContextUsage(
                used_tokens=from_api,
                window_tokens=self._window,
                window_is_default=self._window_is_default,
                from_api=True,
            )
        estimated = estimate_history_tokens(session.messages)
        estimated += estimate_tool_specs_tokens(tool_specs or [])
        return ContextUsage(
            used_tokens=estimated,
            window_tokens=self._window,
            window_is_default=self._window_is_default,
            from_api=False,
        )

    def should_warn(self, usage: ContextUsage) -> bool:
        """是否已达到「建议 / 必须压缩」的阈值。"""
        return is_over_threshold(usage, self._threshold)

    # ------------------------------------------------------------------
    # 压缩
    # ------------------------------------------------------------------
    def compact(self, session: Session, tool_specs: list | None = None,
                now: float | None = None) -> CompactionResult:
        """执行一次完整压缩；失败时抛出异常且**不改动会话**。

        Args:
            session: 要压缩的会话（会被原地替换历史）。
            tool_specs: 当前工具声明，用于计算压缩前后的用量。
            now: 注入时间戳（测试用）。

        Raises:
            ContextArchiveError: 归档写入失败（会话未动）。
            ContextCompactError: 摘要生成失败或摘要为空（会话未动）。
        """
        records = serialize_messages(session.messages)
        if not records:
            raise ContextCompactError("当前会话没有可压缩的历史内容")

        before = self.usage(session, tool_specs)
        archive_id = with_collision_suffix(
            generate_archive_id(now), lambda candidate: candidate in set(self._store.list_ids()))
        archive_path = self._store.save(archive_id, records, {
            "session_id": session.id,
            "title": session.title,
            "tokens_before": before.used_tokens,
        })

        content, truncated = self._summarize(records, archive_id, session, now)
        tokens_after = estimate_text_tokens(content)
        return CompactionResult(
            archive_id=archive_id,
            archive_path=archive_path,
            tokens_before=before.used_tokens,
            tokens_after=tokens_after,
            message_count=len(records),
            summary_truncated=truncated,
            summary_tokens=tokens_after,
        )

    def _summarize(self, records: list, archive_id: str, session: Session,
                   now: float | None) -> tuple[str, bool]:
        """调用 LLM 生成摘要并写回会话历史，返回 (最终摘要, 是否截断)。

        失败时抛出 `ContextCompactError`——此时**会话尚未被修改**，
        归档文件已经留存（对后续检索无害）。
        """
        history = render_records_for_llm(records)
        prompt = "%s\n\n===== 历史上下文开始 =====\n%s\n===== 历史上下文结束 =====" % (
            COMPACT_PROMPT, history)

        # 关键：tool_specs 传空——压缩期间模型无法发起任何工具调用
        request = LLMRequest(messages=[_user_message(prompt)], tool_specs=[])
        try:
            turn = self._llm.complete_turn(request)
        except TAgentError as exc:
            raise ContextCompactError(
                "生成上下文摘要失败：%s。原始上下文仍完整保留，可重新执行 /compact。"
                % exc.message)
        except Exception as exc:  # 兜底：任何底层异常都不能让会话处于半压缩状态
            raise ContextCompactError(
                "生成上下文摘要时出现异常：%s。原始上下文仍完整保留，可重新执行 /compact。"
                % exc)

        raw = (turn.text or "").strip()
        if not raw:
            raise ContextCompactError(
                "模型没有返回摘要内容。原始上下文仍完整保留，可重新执行 /compact。")

        summary, truncated = clamp_summary(raw, self._max_summary_chars)
        # 摘要 + 归档说明一起替换历史；System Prompt 由 Session 自动保留
        content = summary + "\n\n" + archive_footer(archive_id, now)
        session.replace_history([_user_message(content)])
        return content, truncated

    # ------------------------------------------------------------------
    # 历史检索
    # ------------------------------------------------------------------
    def recall(self, text: str, archive_id: str | None = None) -> RecallResult:
        """在所有（或指定）归档里做关键词全文检索。"""
        keywords = split_keywords(text)
        if not keywords:
            raise ContextError("检索关键词不能为空")

        if archive_id:
            hits = self._store.search(keywords, archive_id)
            searched = (archive_id,)
        else:
            hits = self._store.search(keywords)
            searched = tuple(sorted({hit.archive_id for hit in hits}))
            if not hits:
                # 没命中时也把「搜过哪些归档」告诉模型，便于它判断是否换词
                searched = tuple(self._store.list_ids())

        if not hits:
            return RecallResult(
                text="在归档中没有找到包含「%s」的内容。可以换个关键词，"
                     "或省略 id 扩大检索范围。" % text.strip(),
                total_hits=0, returned=0, archive_ids=searched)

        kept, truncated = truncate_hits(hits, self._recall_max_chars)
        return RecallResult(
            text=_render_hits(kept, len(hits), text.strip(), truncated),
            total_hits=len(hits),
            returned=len(kept),
            truncated=truncated,
            archive_ids=searched,
        )

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """一句话描述（启动提示用）。"""
        return "上下文窗口 %s%s · 阈值 %d%%" % (
            _format_window(self._window),
            "（默认）" if self._window_is_default else "",
            round(self._threshold * 100))


# ======================================================================
# 辅助
# ======================================================================
def _user_message(content: str) -> ChatMessage:
    return ChatMessage.user(content)


def _format_window(tokens: int) -> str:
    if tokens >= 1024:
        return "%.0fk" % (tokens / 1024)
    return str(tokens)


def _render_hits(hits: list[SearchHit], total: int, query: str,
                 truncated: bool) -> str:
    lines = ["在归档中找到 %d 条匹配（关键词：%s）：" % (total, query), ""]
    for hit in hits:
        lines.append("【归档 %s · 第 %d 条 · %s】" % (hit.archive_id, hit.seq, hit.role))
        lines.append(hit.snippet)
        lines.append("")
    if truncated:
        lines.append("（匹配结果过多，已按相关度截断。请缩小关键词，"
                     "或指定 id 只检索某一份归档。）")
    return "\n".join(lines).rstrip()


__all__ = ["ContextService"]
