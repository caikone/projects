# -*- coding: utf-8 -*-
"""配置加载器：把 `config.ini` + 环境变量 + 默认值合并为 `AgentSettings`。

优先级（高 → 低）：
    1. 显式传入的路径（`--config`）
    2. `TAGENT_CONFIG` 环境变量
    3. 当前工作目录下的 `config.ini`
    4. 项目根目录下的 `config.ini`
    5. `TAGENT_*` 环境变量覆盖任意字段（对 1~4 的结果做字段级覆盖）
    6. 代码内置默认值

分段约定（每段都可整段省略，省略即用代码默认值）：

    [llm]         模型接口（必填）
    [agent]       Agent 运行策略（历史兼容段）
    [ui]          界面表现（刷新间隔、边栏宽度…）
    [sessions]    会话持久化（目录、自动保存）
    [agent_docs]  AGENTS.md 注入
    [mcp]         外部工具服务器
    [context]     上下文窗口 / 压缩 / 检索
    [cli]         本地命令执行
    [webfetch]    网页抓取
    [search]      联网搜索
    [web]         Web 界面（监听地址、端口、静态资源目录）

同名配置项分散在 `[agent]` 与专属段时，**专属段优先**。

加载器只依赖标准库，属于纯函数式基础设施，便于单测。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import replace
from typing import Iterable

from ..errors import ConfigError
from .models import (
    AgentSettings,
    AgentsDocSettings,
    LLMSettings,
    SearchSettings,
    SessionSettings,
    UISettings,
    WebSettings,
    default_config_paths,
    env_overrides,
)

SECTION_LLM = "llm"
SECTION_AGENT = "agent"
SECTION_SEARCH = "search"
SECTION_UI = "ui"
SECTION_SESSIONS = "sessions"
SECTION_AGENT_DOCS = "agent_docs"
SECTION_MCP = "mcp"
SECTION_CONTEXT = "context"
SECTION_CLI = "cli"
SECTION_WEBFETCH = "webfetch"
SECTION_WEB = "web"

_INT_FIELDS = {"max_tool_rounds", "max_read_bytes", "max_tool_display_len",
               "history_limit", "context_window", "context_summary_tokens",
               "context_recall_max_chars", "mcp_max_result_chars",
               "mcp_tool_description_chars", "cli_max_stdout_chars",
               "cli_max_stderr_chars", "webfetch_max_html_bytes"}
#: 浮点型 [agent] 配置项（MCP 超时、压缩阈值等）
_FLOAT_FIELDS = {"mcp_timeout", "mcp_connect_timeout", "compact_threshold",
                 "cli_timeout", "webfetch_timeout", "webfetch_connect_timeout"}
#: 布尔型 [agent] 配置项
_BOOL_FIELDS = {"enable_system_prompt", "mcp_enabled"}
#: 直接按字符串取用的 [agent] 配置项
_STR_FIELDS = {"system_prompt", "skills_dir", "mcp_config", "context_dir"}

#: 专属段：配置项 → (AgentSettings 字段, 类型)
_UI_SECTION = {
    "event_drain_interval_ms": ("event_drain_interval_ms", int),
    "stream_flush_interval_ms": ("stream_flush_interval_ms", int),
    "spinner_interval_ms": ("spinner_interval_ms", int),
    "tool_preview_chars": ("tool_preview_chars", int),
    "sidebar_width": ("sidebar_width", int),
    "tool_panel_width": ("tool_panel_width", int),
    "command_panel_rows": ("command_panel_rows", int),
    "input_height": ("input_height", int),
    "sidebar_tool_limit": ("sidebar_tool_limit", int),
}
_SESSIONS_SECTION = {
    "dir": ("directory", str),
    "directory": ("directory", str),
    "autosave": ("autosave", bool),
    "max_listed": ("max_listed", int),
}
_AGENT_DOCS_SECTION = {
    "enabled": ("enabled", bool),
    "filename": ("filename", str),
    "file": ("filename", str),
    "max_bytes": ("max_bytes", int),
}
_MCP_SECTION = {
    "enabled": ("mcp_enabled", bool),
    "config": ("mcp_config", str),
    "timeout": ("mcp_timeout", float),
    "connect_timeout": ("mcp_connect_timeout", float),
    "max_result_chars": ("mcp_max_result_chars", int),
    "tool_description_chars": ("mcp_tool_description_chars", int),
}
_CONTEXT_SECTION = {
    "window": ("context_window", int),
    "context_window": ("context_window", int),
    "threshold": ("compact_threshold", float),
    "compact_threshold": ("compact_threshold", float),
    "dir": ("context_dir", str),
    "context_dir": ("context_dir", str),
    "summary_tokens": ("context_summary_tokens", int),
    "recall_max_chars": ("context_recall_max_chars", int),
}
_CLI_SECTION = {
    "timeout": ("cli_timeout", float),
    "max_stdout_chars": ("cli_max_stdout_chars", int),
    "max_stderr_chars": ("cli_max_stderr_chars", int),
}
_WEBFETCH_SECTION = {
    "timeout": ("webfetch_timeout", float),
    "connect_timeout": ("webfetch_connect_timeout", float),
    "max_html_bytes": ("webfetch_max_html_bytes", int),
}

_WEB_SECTION = {
    "host": ("host", str),
    "port": ("port", int),
    "open_browser": ("open_browser", bool),
    "assets_dir": ("assets_dir", str),
    "heartbeat_seconds": ("heartbeat_seconds", float),
}

_TRUE_TEXTS = ("1", "true", "yes", "on")
_FALSE_TEXTS = ("0", "false", "no", "off")


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


def _convert(section: str, key: str, raw: str, kind: type) -> object:
    """把字符串配置项转成目标类型；失败时抛出带段名的 `ConfigError`。"""
    text = raw.strip()
    try:
        if kind is bool:
            lowered = text.lower()
            if lowered in _TRUE_TEXTS:
                return True
            if lowered in _FALSE_TEXTS or not lowered:
                return False
            raise ValueError(text)
        if kind is int:
            return int(text)
        if kind is float:
            return float(text)
        return text
    except ValueError:
        names = {int: "整数", float: "数字", bool: "布尔值（true/false）"}.get(kind, "文本")
        raise ConfigError("[%s] 段的 %s 必须是%s，实际为：%s" % (section, key, names, raw))


def _agent_overrides(parser: configparser.ConfigParser) -> dict[str, object]:
    if not parser.has_section(SECTION_AGENT):
        return {}
    overrides: dict[str, object] = {}
    for key, raw in parser.items(SECTION_AGENT):
        key = key.strip()
        if key in _INT_FIELDS:
            overrides[key] = _convert(SECTION_AGENT, key, raw, int)
        elif key in _FLOAT_FIELDS:
            overrides[key] = _convert(SECTION_AGENT, key, raw, float)
        elif key in _BOOL_FIELDS:
            overrides[key] = _convert(SECTION_AGENT, key, raw, bool)
        elif key in _STR_FIELDS:
            overrides[key] = raw.strip()
    return overrides


def _mapped_section(parser: configparser.ConfigParser, section: str,
                    mapping: dict[str, tuple[str, type]]) -> dict[str, object]:
    """按映射表读取一个专属段，返回 `AgentSettings` 字段级覆盖。"""
    if not parser.has_section(section):
        return {}
    overrides: dict[str, object] = {}
    for key, raw in parser.items(section):
        entry = mapping.get(key.strip())
        if entry is None:  # 未知键忽略：允许用户写注释性的自定义项
            continue
        field_name, kind = entry
        overrides[field_name] = _convert(section, key.strip(), raw, kind)
    return overrides


def _nested_settings(parser: configparser.ConfigParser) -> dict[str, object]:
    """构造 `[ui]` / `[sessions]` / `[agent_docs]` 三个嵌套配置对象。"""
    ui_overrides = _mapped_section(parser, SECTION_UI, _UI_SECTION)
    session_overrides = _mapped_section(parser, SECTION_SESSIONS, _SESSIONS_SECTION)
    doc_overrides = _mapped_section(parser, SECTION_AGENT_DOCS, _AGENT_DOCS_SECTION)

    ui = UISettings(**ui_overrides) if ui_overrides else UISettings()
    sessions = SessionSettings(**session_overrides) if session_overrides else SessionSettings()
    docs = AgentsDocSettings(**doc_overrides) if doc_overrides else AgentsDocSettings()
    web_overrides = _mapped_section(parser, SECTION_WEB, _WEB_SECTION)
    web = WebSettings(**web_overrides) if web_overrides else WebSettings()
    return {"ui": ui, "sessions": sessions, "agents_doc": docs, "web": web}


def _build_search(parser: configparser.ConfigParser) -> SearchSettings:
    """解析 [search] 段。整段可缺省：返回默认（未配置）的 SearchSettings。"""
    if not parser.has_section(SECTION_SEARCH):
        return SearchSettings()

    def get(key: str, default: str = "") -> str:
        value = parser.get(SECTION_SEARCH, key, fallback=default)
        return value.strip() if isinstance(value, str) else default

    timeout_raw = get("timeout", "30")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 30.0

    return SearchSettings(
        baidu_qianfan_api_key=get("baidu_qianfan_api_key"),
        base_url=get("base_url") or SearchSettings.base_url,
        timeout=timeout,
    )


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

    settings = AgentSettings(llm=llm, search=_build_search(parser))
    settings = settings.with_overrides(**_nested_settings(parser))

    # [agent] 是历史兼容段，先应用；专属段随后覆盖同名项
    settings = settings.with_overrides(**_agent_overrides(parser))
    for section, mapping in (
        (SECTION_MCP, _MCP_SECTION),
        (SECTION_CONTEXT, _CONTEXT_SECTION),
        (SECTION_CLI, _CLI_SECTION),
        (SECTION_WEBFETCH, _WEBFETCH_SECTION),
    ):
        overrides = _mapped_section(parser, section, mapping)
        if overrides:
            settings = settings.with_overrides(**overrides)

    # 环境变量做字段级覆盖（密钥等敏感信息不必落盘）
    env = env_overrides()
    if env:
        settings = settings.with_overrides(llm=replace(settings.llm, **env))

    return settings, used


def read_raw_section(config_path: str, section: str) -> dict[str, str]:
    """读取某个段的原始键值（供界面「查看原始配置」时使用）。"""
    parser = _read_ini(config_path)
    if not parser.has_section(section):
        return {}
    return {key.strip(): value.strip() for key, value in parser.items(section)}


__all__ = ["load_settings", "read_raw_section"]
