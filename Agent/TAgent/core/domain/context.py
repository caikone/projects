# -*- coding: utf-8 -*-
"""上下文压缩（compact）领域模型与纯规则。

本模块**不含任何 I/O / LLM / 框架依赖**，只负责：

    1. 归档编号的生成规则（`20261022-153000` 这样的「日期-时间」格式）；
    2. Token 数的**估算规则**（没有 API 返回时的兜底方案）；
    3. 上下文用量的数据结构与阈值判断；
    4. 归档记录的序列化格式（与磁盘上 jsonl 的每行一一对应）；
    5. 关键词检索的匹配规则（recallContext 使用的纯函数部分）。

归档的落盘在 `infrastructure/context/local_files.py`，
压缩的编排（保存 → 调 LLM → 替换历史）在 `services/context.py`。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .messages import ChatMessage, Role

#: 无法获知模型上下文窗口时的默认值（教学场景的合理缺省）
DEFAULT_CONTEXT_WINDOW = 128 * 1024
#: 触发「建议压缩」的默认阈值（占窗口的比例）
DEFAULT_COMPACT_THRESHOLD = 0.8
#: 压缩摘要的目标 Token 数（写进压缩提示词，也是本地截断的软上限）
TARGET_SUMMARY_TOKENS = 5000

#: 每条消息的固定协议开销（角色、分隔符等）的粗略估算
_TOKENS_PER_MESSAGE = 4


# ======================================================================
# 归档编号
# ======================================================================
def generate_archive_id(now: float | None = None) -> str:
    """生成归档编号：`YYYYMMDD-HHMMSS`（本地时间）。

    同一秒内重复压缩的可能性极低；真发生时由存储层追加序号
    （`20261022-153000-1`）保证不覆盖。
    """
    moment = time.localtime(now if now is not None else time.time())
    return time.strftime("%Y%m%d-%H%M%S", moment)


def with_collision_suffix(archive_id: str, exists: Any) -> str:
    """编号已存在时追加 `-1`、`-2`…（`exists` 是可调用的判断函数）。"""
    candidate = archive_id
    index = 0
    while exists(candidate):
        index += 1
        candidate = "%s-%d" % (archive_id, index)
    return candidate


# ======================================================================
# Token 估算
# ======================================================================
def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 统一表意文字
        or 0x3400 <= code <= 0x4DBF   # 扩展 A
        or 0xF900 <= code <= 0xFAFF   # 兼容表意文字
        or 0x3000 <= code <= 0x303F   # CJK 标点
        or 0xFF00 <= code <= 0xFFEF   # 全角形式
    )


def estimate_text_tokens(text: str) -> int:
    """估算一段文本的 Token 数（无 API 数据时的兜底）。

    规则刻意简单、可解释：

    * 1 个 CJK 字符 ≈ 1 token（实测中文大致 0.6~1.1，取保守上界）；
    * 4 个其它字符（英文/数字/符号）≈ 1 token。

    它不追求精确，只用于「还剩多少空间」的粗略判断——精确值永远
    优先采用模型 API 返回的 usage。
    """
    if not text:
        return 0
    cjk = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
    other = len(text) - cjk
    return cjk + other // 4


def estimate_message_tokens(message: ChatMessage) -> int:
    """估算一条消息的 Token 数（含工具调用参数与固定开销）。"""
    total = _TOKENS_PER_MESSAGE
    if message.content:
        total += estimate_text_tokens(message.content)
    for call in message.tool_calls:
        # 工具调用按「函数名 + 参数 JSON」的文本量估算
        total += estimate_text_tokens(call.name) + estimate_text_tokens(
            call.arguments_json or "")
    return total


def estimate_history_tokens(messages: Iterable[ChatMessage]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def estimate_tool_specs_tokens(tool_specs: Iterable[Mapping[str, Any]]) -> int:
    """估算工具声明（OpenAI tools 数组）占用的 Token 数。"""
    try:
        payload = json.dumps(list(tool_specs), ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - 序列化失败按 0 处理
        return 0
    return estimate_text_tokens(payload)


def usage_tokens_from_api(usage: Mapping[str, Any] | None) -> int | None:
    """从模型 API 返回的 usage 里提取当前上下文占用。

    OpenAI 兼容协议的字段是 `prompt_tokens` / `completion_tokens`。
    取二者之和：上一轮的「输入 + 输出」基本就是下一轮的输入下限，
    比只取 prompt_tokens 更贴近「当前会话占用了多少窗口」。
    """
    if not isinstance(usage, Mapping):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    values = [v for v in (prompt, completion)
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not values:
        return None
    return int(sum(values))


# ======================================================================
# 用量数据结构
# ======================================================================
@dataclass(frozen=True)
class ContextUsage:
    """一次上下文用量快照（状态栏与阈值判断共用）。"""

    #: 当前占用的 Token 数（API 值或估算值）
    used_tokens: int = 0
    #: 上下文窗口大小（未知时为默认值）
    window_tokens: int = DEFAULT_CONTEXT_WINDOW
    #: `window_tokens` 是否为「拿不到真实值时的默认假设」
    window_is_default: bool = True
    #: 是否来自模型 API 的精确数据（False = 本地估算）
    from_api: bool = False

    @property
    def percent(self) -> int:
        if self.window_tokens <= 0:
            return 0
        return min(100, round(self.used_tokens * 100 / self.window_tokens))

    @property
    def exceeded(self) -> bool:
        """占用是否不低于阈值（浮点阈值由调用方换算后传入比较）。"""
        return self.percent >= 80

    def format_status(self) -> str:
        """状态栏一行字：`12.3k / 128k（默认）· 10%`。"""
        window = "%s%s" % (format_tokens(self.window_tokens),
                           "（默认）" if self.window_is_default else "")
        return "上下文 %s / %s · %d%%" % (
            format_tokens(self.used_tokens), window, self.percent)


def format_tokens(tokens: int) -> str:
    """Token 数的紧凑显示：`980`、`12k`、`12.3k`、`128k`、`1.6M`。"""
    if tokens >= 1024 * 1024:
        text = "%.1fM" % (tokens / 1024 / 1024)
        return text[:-3] + "M" if text.endswith(".0M") else text
    if tokens >= 1024:
        value = tokens / 1024
        if value >= 100:  # 三位数以上不再显示小数，避免 "128.0k" 这种噪声
            return "%.0fk" % value
        text = "%.1fk" % value
        return text[:-3] + "k" if text.endswith(".0k") else text
    return str(tokens)


def is_over_threshold(usage: ContextUsage, threshold: float) -> bool:
    """按浮点阈值判断（`0.8` = 80%），避免整数百分比带来的边界误差。"""
    if usage.window_tokens <= 0:
        return False
    return usage.used_tokens / usage.window_tokens >= threshold


# ======================================================================
# 归档记录（jsonl 的每一行）
# ======================================================================
@dataclass(frozen=True)
class ArchiveRecord:
    """一条归档后的原始消息。

    jsonl 每行一个记录；字段与 `ChatMessage` 一一对应，
    保证日后检索 / 还原时信息不丢失。
    """

    seq: int
    role: str
    content: str = ""
    name: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[Mapping[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"seq": self.seq, "role": self.role,
                                "content": self.content}
        if self.name:
            data["name"] = self.name
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            data["tool_calls"] = [dict(c) for c in self.tool_calls]
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ArchiveRecord":
        calls = data.get("tool_calls") or []
        if not isinstance(calls, list):
            calls = []
        return cls(
            seq=int(data.get("seq") or 0),
            role=str(data.get("role") or ""),
            content=str(data.get("content") or ""),
            name=str(data.get("name") or ""),
            tool_call_id=str(data.get("tool_call_id") or ""),
            tool_calls=tuple(dict(c) for c in calls if isinstance(c, Mapping)),
        )

    @property
    def searchable_text(self) -> str:
        """检索用的全文：正文 + 工具调用参数。"""
        parts = [self.content]
        for call in self.tool_calls:
            parts.append(str(call.get("name") or ""))
            parts.append(str(call.get("arguments") or ""))
        return "\n".join(p for p in parts if p)


def serialize_messages(messages: Iterable[ChatMessage]) -> list[ArchiveRecord]:
    """把会话消息转成归档记录（**不包含** system 消息）。

    system 提示词属于「能力定义」而不是「会话内容」，压缩后仍会保留，
    因此不进入归档。
    """
    records: list[ArchiveRecord] = []
    for index, message in enumerate(messages, start=1):
        if message.role == Role.SYSTEM:
            continue
        records.append(ArchiveRecord(
            seq=len(records) + 1,
            role=message.role.value,
            content=message.content or "",
            name=message.name or "",
            tool_call_id=message.tool_call_id or "",
            tool_calls=tuple(
                {"id": call.id, "name": call.name,
                 "arguments": call.arguments_json or "{}"}
                for call in message.tool_calls
            ),
        ))
    # seq 按归档内顺序重新编号，不依赖入参顺序
    return records


def render_records_for_llm(records: Iterable[ArchiveRecord]) -> str:
    """把归档记录渲染成给压缩 LLM 阅读的纯文本（带序号与角色标注）。"""
    lines: list[str] = []
    for record in records:
        header = "[%d] %s" % (record.seq, record.role)
        if record.name:
            header += "（%s）" % record.name
        lines.append(header + "：")
        if record.content:
            lines.append(record.content)
        for call in record.tool_calls:
            lines.append("    → 调用工具 %s，参数：%s" % (
                call.get("name") or "?", call.get("arguments") or "{}"))
        if record.tool_call_id and not record.content:
            lines.append("    （工具结果为空）")
        lines.append("")
    return "\n".join(lines).strip()


# ======================================================================
# 关键词检索（recallContext 的纯规则部分）
# ======================================================================
@dataclass(frozen=True)
class SearchHit:
    """一次检索命中：哪份归档、哪条消息、什么片段。"""

    archive_id: str
    seq: int
    role: str
    #: 以首个关键词出现位置为中心截取的片段
    snippet: str


#: 单个命中片段的最大字符数（前后各留一半）
SNIPPET_CHARS = 600

#: 检索结果的总字符上限（避免检索结果本身把上下文塞满）
MAX_SEARCH_RESULT_CHARS = 8000


def split_keywords(text: str) -> list[str]:
    """把检索文本拆成关键词（大小写归一，空白分词）。

    中文没有空格：整段就是一个关键词；中英混写时空格自然分开。
    """
    return [part for part in text.lower().split() if part]


def search_records(records: Iterable[ArchiveRecord], keywords: list[str],
                   archive_id: str = "") -> list[SearchHit]:
    """在归档记录中做**全部关键词同时命中**的检索（AND 语义）。"""
    if not keywords:
        return []
    hits: list[SearchHit] = []
    for record in records:
        haystack = record.searchable_text.lower()
        if not haystack:
            continue
        if not all(keyword in haystack for keyword in keywords):
            continue
        hits.append(SearchHit(
            archive_id=archive_id,
            seq=record.seq,
            role=record.role,
            snippet=extract_snippet(record.searchable_text, keywords),
        ))
    return hits


def extract_snippet(text: str, keywords: list[str]) -> str:
    """以第一个关键词的首次出现位置为中心截取片段。"""
    lowered = text.lower()
    anchor = -1
    for keyword in keywords:
        found = lowered.find(keyword)
        if found >= 0 and (anchor < 0 or found < anchor):
            anchor = found
    if anchor < 0:  # 理论上不会发生（调用前已确认命中）
        anchor = 0
    half = SNIPPET_CHARS // 2
    start = max(0, anchor - half)
    end = min(len(text), anchor + half)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def truncate_hits(hits: list[SearchHit], limit: int = MAX_SEARCH_RESULT_CHARS
                  ) -> tuple[list[SearchHit], bool]:
    """按总长度截断命中列表，返回 (保留的命中, 是否发生了截断)。"""
    kept: list[SearchHit] = []
    used = 0
    for hit in hits:
        cost = len(hit.snippet) + 32  # 序号、角色等渲染开销的粗略估计
        if used + cost > limit:
            return kept, True
        kept.append(hit)
        used += cost
    return kept, False


# ======================================================================
# 压缩提示词与归档说明
# ======================================================================
COMPACT_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 `recallContext` 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


def archive_footer(archive_id: str, when: float | None = None) -> str:
    """压缩摘要末尾必须追加的归档说明。"""
    moment = time.localtime(when if when is not None else time.time())
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", moment)
    return ("此前上下文已于 %s 完成压缩，原始上下文保存在编号为 %s 的归档文件中。"
            "如需查找历史信息，请使用 recallContext 工具。" % (stamp, archive_id))


#: 摘要本地软上限（超过则截断并标注）。约 5000 Tokens ≈ 5000 个中文字符。
MAX_SUMMARY_CHARS = TARGET_SUMMARY_TOKENS * 2


@dataclass(frozen=True)
class CompactionResult:
    """一次成功压缩的结果（供 UI 提示与状态栏刷新）。"""

    archive_id: str
    archive_path: str
    #: 压缩前的上下文估算 Token 数
    tokens_before: int = 0
    #: 压缩后的上下文估算 Token 数
    tokens_after: int = 0
    #: 归档了多少条原始消息
    message_count: int = 0
    #: 摘要是否因超长被本地截断
    summary_truncated: bool = False
    #: 摘要的估算 Token 数
    summary_tokens: int = 0


@dataclass(frozen=True)
class RecallResult:
    """一次历史检索的结果。"""

    #: 已渲染成给模型看的文本
    text: str = ""
    #: 命中的总条数（截断前）
    total_hits: int = 0
    #: 实际返回的条数
    returned: int = 0
    #: 是否因结果过长被截断
    truncated: bool = False
    #: 检索过的归档编号
    archive_ids: tuple[str, ...] = ()



def clamp_summary(summary: str, max_chars: int = MAX_SUMMARY_CHARS) -> tuple[str, bool]:
    """把超长摘要截到软上限内，返回 (摘要, 是否截断)。

    Args:
        summary: 模型返回的摘要原文。
        max_chars: 软上限（来自 `[context] summary_tokens`，默认 5000 Tokens 的等价字符数）。
    """
    limit = max(200, int(max_chars or MAX_SUMMARY_CHARS))
    if len(summary) <= limit:
        return summary, False
    return summary[:limit] + "\n\n[摘要超出约 %d Tokens 上限，已截断]" % (
        limit // 2), True


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_COMPACT_THRESHOLD",
    "TARGET_SUMMARY_TOKENS",
    "MAX_SEARCH_RESULT_CHARS",
    "SNIPPET_CHARS",
    "ArchiveRecord",
    "ContextUsage",
    "SearchHit",
    "CompactionResult",
    "RecallResult",
    "COMPACT_PROMPT",
    "generate_archive_id",
    "with_collision_suffix",
    "estimate_text_tokens",
    "estimate_message_tokens",
    "estimate_history_tokens",
    "estimate_tool_specs_tokens",
    "usage_tokens_from_api",
    "format_tokens",
    "is_over_threshold",
    "serialize_messages",
    "render_records_for_llm",
    "split_keywords",
    "search_records",
    "extract_snippet",
    "truncate_hits",
    "archive_footer",
    "clamp_summary",
    "MAX_SUMMARY_CHARS",
]
