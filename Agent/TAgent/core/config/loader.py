# -*- coding: utf-8 -*-
"""配置加载器：把 `config.ini` + 环境变量 + 默认值合并为 `AgentSettings`。

优先级（高 → 低）：
    1. 显式传入的路径（`--config`）
    2. `TAGENT_CONFIG` 环境变量
    3. 当前工作目录下的 `config.ini`
    4. 项目根目录下的 `config.ini`
    5. `TAGENT_*` 环境变量覆盖任意字段（对 1~4 的结果做字段级覆盖）
    6. 代码内置默认值

加载器只依赖标准库，属于纯函数式基础设施，便于单测。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import replace
from typing import Iterable

from ..errors import ConfigError
from .models import AgentSettings, LLMSettings, default_config_paths, env_overrides

SECTION_LLM = "llm"
SECTION_AGENT = "agent"

_INT_FIELDS = {"max_tool_rounds", "max_read_bytes", "max_tool_display_len", "history_limit"}


def _first_existing(paths: Iterable[str]) -> str | None:
    for path in paths:
        if path and os.path.isfile(path):
            return path
    return None


def _read_ini(path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str  # 保留键名大小写，配置可读性更好
    try:
        read_ok = parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as exc:
        raise ConfigError("配置文件解析失败：%s（%s）" % (path, exc))
    if not read_ok:
        raise ConfigError("配置文件无法读取：%s" % path)
    return parser


def _build_llm(parser: configparser.ConfigParser) -> LLMSettings:
    if not parser.has_section(SECTION_LLM):
        raise ConfigError("配置文件缺少 [%s] 段" % SECTION_LLM)

    def get(key: str, default: str | None = None) -> str:
        value = parser.get(SECTION_LLM, key, fallback=default)
        return value.strip() if isinstance(value, str) else (default or "")

    base_url = get("base_url")
    api_key = get("api_key")
    model = get("model")

    missing = [k for k, v in (("base_url", base_url), ("api_key", api_key), ("model", model)) if not v]
    if missing:
        raise ConfigError("[%s] 段缺少必需配置项：%s" % (SECTION_LLM, "、".join(missing)))

    timeout_raw = get("timeout", "60")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 60.0

    temperature: float | None = None
    temp_raw = get("temperature")
    if temp_raw:
        try:
            temperature = float(temp_raw)
        except ValueError:
            temperature = None

    max_tokens: int | None = None
    mt_raw = get("max_tokens")
    if mt_raw:
        try:
            max_tokens = int(mt_raw)
        except ValueError:
            max_tokens = None

    return LLMSettings(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _agent_overrides(parser: configparser.ConfigParser) -> dict[str, object]:
    if not parser.has_section(SECTION_AGENT):
        return {}
    overrides: dict[str, object] = {}
    for key, raw in parser.items(SECTION_AGENT):
        raw = raw.strip()
        if key in _INT_FIELDS:
            try:
                overrides[key] = int(raw)
            except ValueError:
                raise ConfigError("[%s] 段的 %s 必须是整数，实际为：%s" % (SECTION_AGENT, key, raw))
        elif key == "enable_system_prompt":
            overrides[key] = raw.lower() in ("1", "true", "yes", "on")
        elif key == "system_prompt":
            overrides[key] = raw
    return overrides


def load_settings(config_path: str | None = None,
                  search_paths: Iterable[str] | None = None) -> tuple[AgentSettings, str]:
    """加载配置。

    Args:
        config_path: 显式指定的配置文件路径；为 None 时按默认顺序搜索。
        search_paths: 自定义搜索路径列表（供测试注入）。

    Returns:
        (配置对象, 实际使用的配置文件路径或描述)

    Raises:
        ConfigError: 找不到配置文件、缺字段或格式非法。
    """
    if config_path:
        if not os.path.isfile(config_path):
            raise ConfigError("找不到配置文件：%s" % config_path)
        used = config_path
    else:
        used = _first_existing(search_paths or default_config_paths())
        if used is None:
            raise ConfigError(
                "找不到配置文件 config.ini。已尝试：%s；"
                "也可以通过 --config 指定路径，或用 TAGENT_BASE_URL / TAGENT_API_KEY / "
                "TAGENT_MODEL 环境变量提供配置。"
                % "、".join(search_paths or default_config_paths())
            )

    parser = _read_ini(used)
    try:
        llm = _build_llm(parser)
    except ConfigError as exc:
        raise ConfigError("%s（文件：%s）" % (exc.message, used))

    settings = AgentSettings(llm=llm)
    settings = settings.with_overrides(**_agent_overrides(parser))

    # 环境变量做字段级覆盖（密钥等敏感信息不必落盘）
    env = env_overrides()
    if env:
        settings = settings.with_overrides(llm=replace(settings.llm, **env))

    return settings, used


__all__ = ["load_settings"]
