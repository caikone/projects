# -*- coding: utf-8 -*-
"""MCP（Model Context Protocol）领域模型与纯规则。

本模块**不含任何 I/O、网络或 SDK 依赖**，只负责三件事：

    1. 把 `.mcp.json` 的原始字典翻译成 `McpServerSpec`（纯函数，便于单测）；
    2. 规定「服务器名 / 工具名」如何变成模型可见的函数名（去冲突、限长）；
    3. 承载连接状态与调用结果这些跨层传递的数据结构。

真正的协议通信在 `infrastructure/mcp/sdk_client.py`，
编排与名称映射在 `services/mcp.py`，两者都只依赖这里的模型。
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

#: OpenAI 兼容接口对 function name 的硬约束：^[a-zA-Z0-9_-]{1,64}$
MAX_TOOL_NAME_LEN = 64
#: 服务器键（用于前缀）的最大长度，给工具名留出空间
MAX_SERVER_KEY_LEN = 20

#: 前缀分隔符：与常见 MCP 客户端保持一致，一眼能看出这是 MCP 工具
PREFIX = "mcp__"
SEPARATOR = "__"

_INVALID_TOOL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_INVALID_KEY_CHARS = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

#: 对外接受各种写法，内部统一成三种传输方式
_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "http": "http",
    "streamable_http": "http",
    "streamable-http": "http",
    "streamablehttp": "http",
    "streamable": "http",
    "sse": "sse",
}

SUPPORTED_TRANSPORTS = ("stdio", "http", "sse")


# ======================================================================
# 名称规则
# ======================================================================
def _digest(text: str, size: int = 5) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:size]


def normalize_server_key(name: str) -> str:
    """把服务器名归一化成可做函数名前缀的键（小写字母/数字/连字符）。

    ``"Bazi-MCP"`` → ``"bazi-mcp"``，``"高德 地图"`` → ``"server-<hash>"``。
    中文等非 ASCII 名称无法保留语义，退化为稳定哈希后缀，避免不同服务器撞键。
    """
    raw = unicodedata.normalize("NFKC", name or "").strip()
    key = _INVALID_KEY_CHARS.sub("-", raw.lower()).strip("-")
    if not key:
        key = "server-%s" % _digest(raw or "mcp", 4)
    if len(key) > MAX_SERVER_KEY_LEN:
        key = "%s-%s" % (key[: MAX_SERVER_KEY_LEN - 6], _digest(name, 5))
    return key


def sanitize_tool_segment(name: str, limit: int = MAX_TOOL_NAME_LEN) -> str:
    """把 MCP 工具名压成合法片段：非字母数字连字符 → 下划线，超长截断加哈希。"""
    raw = unicodedata.normalize("NFKC", name or "").strip()
    segment = _INVALID_TOOL_CHARS.sub("_", raw).strip("_")
    if not segment:
        segment = "tool"
    if len(segment) > limit:
        segment = "%s_%s" % (segment[: max(1, limit - 6)], _digest(raw, 5))
    return segment


def exposed_tool_name(server_key: str, tool_name: str) -> str:
    """生成暴露给模型的工具名：``mcp__<server>__<tool>``。

    加前缀的理由：不同 MCP Server 常常提供同名工具（如 ``search``），
    前缀把「服务器」这一层信息编码进名字，既避免与本地工具冲突，
    也让模型从名字就能判断该调用哪个服务器。
    """
    prefix = "%s%s%s" % (PREFIX, server_key, SEPARATOR)
    room = MAX_TOOL_NAME_LEN - len(prefix)
    if room < 8:  # 极端情况下压缩服务器键，保证工具名还有可读空间
        prefix = "%s%s%s" % (PREFIX, sanitize_tool_segment(server_key, 10), SEPARATOR)
        room = MAX_TOOL_NAME_LEN - len(prefix)
    segment = sanitize_tool_segment(tool_name, limit=room)
    return prefix + segment


def dedupe_tool_name(candidate: str, taken: set[str]) -> str:
    """名称冲突时追加序号：``..._2`` / ``..._3``，仍遵守 64 字符上限。"""
    if candidate not in taken:
        return candidate
    stem = candidate
    for index in range(2, 1000):
        suffix = "_%d" % index
        stem = candidate[: MAX_TOOL_NAME_LEN - len(suffix)] + suffix
        if stem not in taken:
            return stem
    return candidate[: MAX_TOOL_NAME_LEN - 9] + "_" + _digest(candidate, 8)


# ======================================================================
# 配置模型
# ======================================================================
@dataclass(frozen=True)
class McpServerSpec:
    """一个 MCP Server 的连接规格（由 `.mcp.json` 解析而来）。"""

    #: 配置里的原始名称，用于展示与错误提示
    name: str
    #: 归一化后的键，用于工具名前缀与内部索引
    key: str
    #: "stdio" | "http" | "sse"
    transport: str = "stdio"
    #: stdio：要启动的可执行程序与参数
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""
    #: http/sse：服务地址
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    #: 单次工具调用超时（秒）
    timeout: float = 60.0
    #: 配置里显式关闭的服务器不会发起连接
    disabled: bool = False

    @property
    def endpoint(self) -> str:
        """用于日志与错误提示的目标描述。"""
        if self.transport == "stdio":
            return " ".join([self.command, *self.args]).strip() or "(未配置 command)"
        return self.url or "(未配置 url)"


#: 各传输方式下 MCP 端点常见的路径后缀
_ENDPOINT_SUFFIXES = {
    "http": ("/mcp",),
    "sse": ("/sse",),
}

_MULTI_SLASH = re.compile(r"/{2,}")


def endpoint_candidates(spec: McpServerSpec) -> tuple[str, ...]:
    """按优先级返回可尝试的端点地址（第一个永远是配置里的原地址）。

    托管型 MCP 服务的控制台经常只给出「前缀」地址，真正的端点在后面还要
    补 `/mcp`（或 `/sse`），例如::

        https://mcp.api-inference.modelscope.net/4a3222faa31a4e        ← 404
        https://mcp.api-inference.modelscope.net/4a3222faa31a4e/mcp    ← 200

    这里只为「看起来不完整」的地址生成备用候选：

    * 已带已知后缀（`/mcp`、`/sse`）→ 只试原地址，不做多余请求；
    * 路径为空、根路径或只有一段（常见是服务 uuid）→ 补后缀再试一次；
    * 路径已是多级（如 `/api/v1`）→ 认为用户写全了，只试原地址。

    顺带把路径里连续的斜杠折叠成一个：`//mcp` 这种写法部分网关会返回 404。
    """
    url = (spec.url or "").strip()
    if spec.transport not in _ENDPOINT_SUFFIXES or not url:
        return (url,) if url else ()

    try:
        parsed = urlsplit(url)
    except ValueError:  # 畸形 URL 交给下游报错，这里不猜
        return (url,)

    raw_path = parsed.path or ""
    path = _MULTI_SLASH.sub("/", raw_path).rstrip("/")
    suffixes = _ENDPOINT_SUFFIXES[spec.transport]

    def build(target_path: str) -> str:
        return urlunsplit((parsed.scheme, parsed.netloc, target_path,
                           parsed.query, parsed.fragment))

    primary = build(path) if path != raw_path else url
    lowered = path.lower()
    if any(lowered.endswith(suffix) for suffix in suffixes):
        return (primary,)
    if path.count("/") <= 1:
        return (primary, *[build(path + suffix) for suffix in suffixes])
    return (primary,)


def looks_like_missing_endpoint(spec: McpServerSpec) -> bool:
    """配置地址是否「看起来缺后缀」——用于把 404 翻译成可执行的建议。"""
    return len(endpoint_candidates(spec)) > 1



@dataclass(frozen=True)
class McpConfigIssue:
    """单条配置问题：只跳过这一项，不影响其它服务器与本地工具。"""

    server: str
    message: str


@dataclass(frozen=True)
class McpToolInfo:
    """服务器返回的原始工具信息（还没加前缀、还没做去重）。"""

    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    #: MCP tool annotations 里的 readOnlyHint
    read_only: bool = False


@dataclass(frozen=True)
class McpToolDescriptor:
    """一个已发现的 MCP 工具。"""

    server_key: str
    server_name: str
    #: 服务器内部的原始工具名（调用时必须原样回传）
    tool_name: str
    #: 暴露给模型的名称（带前缀，可能因去重而带序号）
    exposed_name: str
    description: str = ""
    #: MCP 的 inputSchema（原样透传给模型，可能是嵌套结构）
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    #: 服务器声明的只读/破坏性提示（MCP annotations）
    read_only: bool = False

    def to_tool_spec_kwargs(self) -> dict[str, Any]:
        """转换成 `ToolSpec` 的构造参数（避免领域层依赖端口层）。"""
        return {
            "name": self.exposed_name,
            "description": self.description,
            "raw_schema": dict(self.input_schema) if self.input_schema else None,
        }


@dataclass
class McpServerStatus:
    """一个服务器的连接结果（可变，供状态栏与服务汇总使用）。"""

    spec: McpServerSpec
    connected: bool = False
    error: str = ""
    tool_count: int = 0
    server_info: str = ""
    #: 补充说明（例如「地址已自动补全为 …」），成功与失败都可能出现
    note: str = ""

    @property
    def label(self) -> str:
        if self.connected:
            return "%s(%d)" % (self.spec.name, self.tool_count)
        return "%s(失败)" % self.spec.name


@dataclass(frozen=True)
class McpServerView:
    """一台服务器在 UI 上的展示视图（已经拍平成纯数据）。"""

    name: str
    key: str
    transport: str
    endpoint: str
    connected: bool = False
    disabled: bool = False
    tool_count: int = 0
    server_info: str = ""
    error: str = ""
    note: str = ""
    #: 该服务器暴露给模型的工具名（带 mcp__ 前缀）
    tools: tuple[str, ...] = ()

    @property
    def status_text(self) -> str:
        if self.disabled:
            return "已禁用"
        if self.connected:
            return "已连接"
        return "未连接"


@dataclass(frozen=True)
class McpSnapshot:
    """某一时刻 MCP 的整体状况（UI 侧栏 / 状态栏一次性渲染用）。"""

    servers: tuple[McpServerView, ...] = ()
    total_tools: int = 0
    config_source: str = ""
    config_error: str = ""
    notices: tuple[str, ...] = ()
    #: 是否已经完成加载（False = 还在后台连接中）
    loaded: bool = False

    @property
    def connected_count(self) -> int:
        return sum(1 for server in self.servers if server.connected)

    @property
    def total_count(self) -> int:
        return len(self.servers)

    @property
    def summary(self) -> str:
        """状态栏用的一句话（没有任何服务器时返回空串）。"""
        if self.config_error:
            return "MCP 配置错误"
        if not self.servers:
            return ""
        text = "MCP %d/%d" % (self.connected_count, self.total_count)
        if self.total_tools:
            text += " · %d 个工具" % self.total_tools
        failed = [s.name for s in self.servers if not s.connected and not s.disabled]
        if failed:
            text += "（%s 未连接）" % "、".join(failed)
        return text


@dataclass(frozen=True)
class McpCallResult:
    """一次 MCP 工具调用的原始结果（已从协议对象还原成纯数据）。"""

    #: 文本内容（多个文本块按顺序拼接；非文本块用占位说明）
    text: str = ""
    #: 服务器返回的结构化数据（structuredContent）
    structured: Mapping[str, Any] | None = None
    #: 服务器是否把本次调用标记为失败（isError）
    is_error: bool = False


# ======================================================================
# 配置解析（纯函数）
# ======================================================================
def ensure_transport(value: Any) -> str:
    """把配置里的 type 归一化；不认识的类型抛出 ValueError。"""
    if value is None or value == "":
        return ""
    text = _WHITESPACE.sub("", str(value)).lower()
    if text not in _TRANSPORT_ALIASES:
        raise ValueError(
            "不支持的传输方式 %s（可选：%s）"
            % (value, "、".join(SUPPORTED_TRANSPORTS)))
    return _TRANSPORT_ALIASES[text]


def _as_str_map(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("%s 必须是对象（键值对）" % field_name)
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("%s 的键必须是字符串" % field_name)
        result[key] = "" if item is None else str(item)
    return result


def _as_str_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError("%s 必须是字符串数组，不能是单个字符串" % field_name)
    if not isinstance(value, (list, tuple)):
        raise ValueError("%s 必须是字符串数组" % field_name)
    return tuple("" if item is None else str(item) for item in value)


def parse_server_spec(name: str, raw: Any, *, default_timeout: float = 60.0,
                      base_dir: str = "") -> McpServerSpec:
    """解析单个服务器配置。非法时抛出 `ValueError`（由调用方转成 issue）。"""
    if not isinstance(raw, Mapping):
        raise ValueError("配置必须是对象")

    disabled = bool(raw.get("disabled", False))
    transport = ensure_transport(raw.get("type") or raw.get("transport"))

    command = str(raw.get("command") or "").strip()
    url = str(raw.get("url") or "").strip()

    # 没写 type 时按字段推断，兼容常见的极简写法
    if not transport:
        if command:
            transport = "stdio"
        elif url:
            transport = "http"
        else:
            raise ValueError("缺少 command（stdio）或 url（http/sse）")

    if transport == "stdio" and not command:
        raise ValueError("stdio 类型必须提供 command")
    if transport in ("http", "sse"):
        if not url:
            raise ValueError("%s 类型必须提供 url" % transport)
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头：%s" % url)

    try:
        timeout = float(raw.get("timeout", default_timeout) or default_timeout)
    except (TypeError, ValueError):
        raise ValueError("timeout 必须是数字")
    if timeout <= 0:
        raise ValueError("timeout 必须大于 0")

    cwd = str(raw.get("cwd") or "").strip()
    if cwd and base_dir and not os.path.isabs(cwd):
        # 相对路径按「配置文件所在目录」解析，避免受启动目录影响
        cwd = os.path.normpath(os.path.join(base_dir, cwd))

    return McpServerSpec(
        name=name,
        key=normalize_server_key(name),
        transport=transport,
        command=command,
        args=_as_str_list(raw.get("args"), "args"),
        env=_as_str_map(raw.get("env"), "env"),
        cwd=cwd,
        url=url,
        headers=_as_str_map(raw.get("headers"), "headers"),
        timeout=timeout,
        disabled=disabled,
    )


def parse_mcp_config(payload: Any, *, default_timeout: float = 60.0,
                     base_dir: str = "") -> tuple[list[McpServerSpec], list[McpConfigIssue]]:
    """把 `.mcp.json` 的内容解析为服务器规格列表。

    设计取向：**单条配置有问题只跳过这一条**，把问题收集成 `McpConfigIssue`
    返回，让其余服务器（以及全部本地工具）照常工作。只有顶层结构完全不可用时
    才由调用方抛 `McpConfigError`。

    Returns:
        (服务器规格列表, 配置问题列表)
    """
    if not isinstance(payload, Mapping):
        raise ValueError("配置文件顶层必须是 JSON 对象")

    servers = payload.get("mcpServers", payload.get("servers"))
    if servers is None:
        raise ValueError("配置文件缺少 mcpServers 字段")
    if not isinstance(servers, Mapping):
        raise ValueError("mcpServers 必须是对象（名称 → 配置）")

    specs: list[McpServerSpec] = []
    issues: list[McpConfigIssue] = []
    seen: dict[str, str] = {}

    for name, raw in servers.items():
        if not isinstance(name, str) or not name.strip():
            issues.append(McpConfigIssue(server=str(name), message="服务器名必须是非空字符串"))
            continue
        label = name.strip()
        try:
            spec = parse_server_spec(label, raw, default_timeout=default_timeout,
                                     base_dir=base_dir)
        except ValueError as exc:
            issues.append(McpConfigIssue(server=label, message=str(exc)))
            continue
        # 归一化后撞键（如 "Bazi-MCP" 与 "bazi_mcp"）时保留先出现的
        if spec.key in seen:
            issues.append(McpConfigIssue(
                server=label,
                message="与「%s」归一化后同名（%s），已忽略；请改用可区分的名字"
                        % (seen[spec.key], spec.key)))
            continue
        seen[spec.key] = label
        specs.append(spec)

    return specs, issues


def flatten_text(*parts: str) -> str:
    """把多个文本块用空行连接，忽略空白块。"""
    return "\n\n".join(part.strip("\n") for part in parts if part and part.strip())


__all__ = [
    "MAX_TOOL_NAME_LEN",
    "PREFIX",
    "SEPARATOR",
    "SUPPORTED_TRANSPORTS",
    "McpServerSpec",
    "McpServerStatus",
    "McpServerView",
    "McpSnapshot",
    "McpConfigIssue",
    "McpToolInfo",
    "McpToolDescriptor",
    "McpCallResult",
    "normalize_server_key",
    "sanitize_tool_segment",
    "exposed_tool_name",
    "dedupe_tool_name",
    "ensure_transport",
    "endpoint_candidates",
    "looks_like_missing_endpoint",
    "parse_server_spec",
    "parse_mcp_config",
    "flatten_text",
]
