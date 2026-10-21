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
class AgentSettings:
    """Agent 运行时配置（LLM 配置 + 会话/工具策略）。"""

    llm: LLMSettings

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
        "再根据工具返回的结果用简洁的中文回答用户。"
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


__all__ = ["LLMSettings", "AgentSettings", "env_overrides", "default_config_paths"]
