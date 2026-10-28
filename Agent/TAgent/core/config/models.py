# -*- coding: utf-8 -*-
"""配置数据模型：配置一旦加载完成，全系统只传递这两个不可变对象。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class LLMSettings:
    """LLM 接口相关配置。"""

    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0
    temperature: float | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url 不能为空")
        if not self.api_key:
            raise ValueError("api_key 不能为空")
        if not self.model:
            raise ValueError("model 不能为空")

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def masked_api_key(self) -> str:
        """脱敏后的密钥，供 UI 安全展示。"""
        key = self.api_key
        if len(key) <= 8:
            return "*" * len(key)
        return "%s***%s" % (key[:4], key[-4:])


@dataclass(frozen=True)
class SearchSettings:
    """联网搜索相关配置（百度千帆智能搜索）。

    全部字段均可为空/默认值：缺少密钥时不阻止应用启动，
    而是在 searchwdb 工具被调用时返回清晰的错误信息。
    """

    #: 百度千帆智能搜索 API 密钥（config.ini [search] 段）
    baidu_qianfan_api_key: str = ""
    #: 智能搜索接口地址（OpenAI 兼容，代码自动拼接 /chat/completions）
    base_url: str = "https://qianfan.baidubce.com/v2/ai_search"
    #: 搜索请求超时时间（秒）
    timeout: float = 30.0

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def configured(self) -> bool:
        """是否已配置好可用的搜索密钥。"""
        return bool(self.baidu_qianfan_api_key)


@dataclass(frozen=True)
class AgentSettings:
    """Agent 运行时配置（LLM 配置 + 会话/工具策略）。"""

    llm: LLMSettings
    #: 联网搜索配置（可缺省，缺省时 searchwdb 工具调用会返回错误提示）
    search: SearchSettings = field(default_factory=SearchSettings)

    #: 单轮对话中允许的最大「模型请求 → 工具执行」循环次数（防止无限循环）
    max_tool_rounds: int = 10
    #: 读取纯文本文件的最大字节数，超出截断
    max_read_bytes: int = 1024 * 1024
    #: 工具结果在 UI 上的最大展示长度（发给模型的仍是完整结果）
    max_tool_display_len: int = 300
    #: 系统提示词
    system_prompt: str = (
        "你是一个可以操作本地文件的智能助手。"
        "需要获取信息或改变文件系统状态时，优先调用合适的工具，"
        "再根据工具返回的结果用中文回答用户。"
        "回答原则：当用户只是想要概况、总结或询问问题时，用简洁的语言概括；"
        "但当用户明确要求「原文」「全文」「完整内容」「详细内容」或"
        "「把网页内容原样贴出来」时，必须把工具（如 fetchWeb）返回的"
        "Markdown 正文尽可能完整、逐字地复述出来，不要自己概括、压缩或改写。"
    )
    #: 是否把系统提示词插入会话
    enable_system_prompt: bool = True
    #: 会话历史最多保留的消息条数（0 表示不限制）
    history_limit: int = 0

    def with_overrides(self, **changes: object) -> "AgentSettings":
        """返回一份改过若干字段的新配置（保持不可变语义）。"""
        return replace(self, **changes)


# ----------------------------------------------------------------------
# 环境变量覆盖：方便 CI / 容器场景不落盘密钥
# ----------------------------------------------------------------------
ENV_PREFIX = "TAGENT_"

_ENV_MAP = {
    "BASE_URL": "base_url",
    "API_KEY": "api_key",
    "MODEL": "model",
    "TIMEOUT": "timeout",
}


def env_overrides() -> dict[str, object]:
    """从环境变量读取配置覆盖项，键形如 `TAGENT_API_KEY`。"""
    overrides: dict[str, object] = {}
    for env_key, field_name in _ENV_MAP.items():
        raw = os.environ.get(ENV_PREFIX + env_key)
        if raw is None or not raw.strip():
            continue
        value: object = raw.strip()
        if field_name == "timeout":
            try:
                value = float(raw)
            except ValueError:
                continue
        overrides[field_name] = value
    return overrides


def default_config_paths() -> list[str]:
    """配置文件的默认搜索顺序（越靠前优先级越高）。"""
    env_path = os.environ.get(ENV_PREFIX + "CONFIG")
    paths: list[str] = []
    if env_path:
        paths.append(env_path)
    paths.append(os.path.join(os.getcwd(), "config.ini"))
    # models.py -> config/ -> core/ -> TAgent/（项目根）
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    paths.append(os.path.join(project_root, "config.ini"))
    return paths


__all__ = ["LLMSettings", "SearchSettings", "AgentSettings", "env_overrides", "default_config_paths"]
