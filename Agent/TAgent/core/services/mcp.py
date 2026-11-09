# -*- coding: utf-8 -*-
"""MCP 应用服务：连接编排、工具发现、名称映射与调用转发。

这一层是 MCP 能力的「大脑」，也是唯一持有连接的地方：

    - 启动时读 `.mcp.json`，并行连接各服务器（互不阻塞、故障隔离）；
    - 把每个服务器的工具加前缀后暴露给模型，并维护
      `暴露名 → (服务器 key, 原始工具名)` 的映射；
    - 模型调用时按映射找到服务器、转发原始工具名与参数；
    - 把结果（文本 / 结构化数据 / 错误）渲染成 `ToolOutcome`。

它不 import 任何 SDK 或第三方库，只依赖 ports 与 domain，
因此可以用内存假客户端完整单测。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, Mapping

from ..domain.mcp import (
    McpCallResult,
    McpConfigIssue,
    McpServerSpec,
    McpServerStatus,
    McpServerView,
    McpSnapshot,
    McpToolDescriptor,
    dedupe_tool_name,
    exposed_tool_name,
)
from ..errors import McpConfigError, McpToolCallError, McpToolNotFoundError
from ..ports.mcp import McpClient, McpClientFactory
from ..ports.tools import ToolOutcome

#: 单次工具结果的字符上限（超出保留头 70% 与尾 30%）
DEFAULT_MAX_RESULT_CHARS = 20000


class McpService:
    """MCP 服务器集合的运行时门面。"""

    def __init__(self, loader: Any | None, factory: McpClientFactory | None, *,
                 connect_timeout: float = 15.0,
                 max_result_chars: int = DEFAULT_MAX_RESULT_CHARS) -> None:
        self._loader = loader
        self._factory = factory
        self._connect_timeout = connect_timeout
        self._max_result_chars = max_result_chars

        self._clients: dict[str, McpClient] = {}
        self._routes: dict[str, McpToolDescriptor] = {}
        self._tools: list[McpToolDescriptor] = []
        self._statuses: list[McpServerStatus] = []
        self._issues: list[McpConfigIssue] = []
        self._source = ""
        self._config_error = ""
        self._loaded = False
        self._reserved: set[str] = set()

    # ------------------------------------------------------------------
    # 只读信息
    # ------------------------------------------------------------------
    @property
    def tools(self) -> list[McpToolDescriptor]:
        return list(self._tools)

    @property
    def statuses(self) -> list[McpServerStatus]:
        return list(self._statuses)

    @property
    def issues(self) -> list[McpConfigIssue]:
        return list(self._issues)

    @property
    def config_source(self) -> str:
        return self._source

    @property
    def config_error(self) -> str:
        return self._config_error

    @property
    def has_tools(self) -> bool:
        return bool(self._tools)

    @property
    def connected_count(self) -> int:
        return sum(1 for status in self._statuses if status.connected)

    @property
    def total_count(self) -> int:
        return len(self._statuses)

    def describe(self) -> str:
        """状态栏用的一句话概括；没有任何 MCP 配置时返回空串。"""
        if self._config_error:
            return "MCP 配置错误"
        if not self._statuses:
            return ""
        text = "MCP %d/%d" % (self.connected_count, self.total_count)
        if self._tools:
            text += " · %d 个工具" % len(self._tools)
        failed = [s.spec.name for s in self._statuses if not s.connected and not s.spec.disabled]
        if failed:
            text += "（%s 未连接）" % "、".join(failed)
        return text

    def notices(self) -> list[str]:
        """启动阶段需要让用户看到的提示（连接失败 / 配置问题）。"""
        messages: list[str] = []
        if self._config_error:
            messages.append(self._config_error)
        for issue in self._issues:
            messages.append("MCP 配置项「%s」已跳过：%s" % (issue.server, issue.message))
        for status in self._statuses:
            if status.connected:
                if status.note:
                    messages.append("MCP 服务器「%s」：%s" % (status.spec.name, status.note))
                continue
            if status.spec.disabled:
                continue
            # status.error 已经是带服务器名的完整句子
            message = status.error or ("MCP 服务器「%s」不可用：未知原因" % status.spec.name)
            if status.spec.transport == "stdio":
                message += "；请确认 command 存在且可执行，或设 TAGENT_MCP_STDERR=1 查看子进程日志"
            messages.append(message)
        return messages

    def snapshot(self) -> McpSnapshot:
        """把当前状态拍平成 UI 可直接渲染的视图（加载中也可安全调用）。"""
        tools_by_server: dict[str, list[str]] = {}
        for tool in self._tools:
            tools_by_server.setdefault(tool.server_key, []).append(tool.exposed_name)

        servers = tuple(
            McpServerView(
                name=status.spec.name,
                key=status.spec.key,
                transport=status.spec.transport,
                endpoint=status.spec.endpoint,
                connected=status.connected,
                disabled=status.spec.disabled,
                tool_count=status.tool_count,
                server_info=status.server_info,
                error=status.error,
                note=status.note,
                tools=tuple(sorted(tools_by_server.get(status.spec.key, ()))),
            )
            for status in self._statuses
        )
        return McpSnapshot(
            servers=servers,
            total_tools=len(self._tools),
            config_source=self._source,
            config_error=self._config_error,
            notices=tuple(self.notices()),
            loaded=self._loaded,
        )

    # ------------------------------------------------------------------
    # 连接与发现
    # ------------------------------------------------------------------
    def load(self, reserved_names: Iterable[str] | None = None) -> None:
        """读取配置、连接全部服务器并完成工具注册。幂等（只执行一次）。

        Args:
            reserved_names: 已被本地工具占用的函数名，避免 MCP 工具撞名。
        """
        if self._loaded:
            return
        self._loaded = True
        self._reserved = set(reserved_names or ())

        if self._loader is None or self._factory is None:
            return

        try:
            specs, issues, source = self._loader.load()
        except McpConfigError as exc:
            self._config_error = exc.message
            return

        self._source = source
        self._issues.extend(issues)
        if not specs:
            return

        active: list[McpServerSpec] = []
        for spec in specs:
            if spec.disabled:
                self._statuses.append(McpServerStatus(
                    spec=spec, connected=False, error="已在配置中禁用（disabled=true）"))
            else:
                active.append(spec)

        if not active:
            return

        # 并行连接：某台服务器慢/挂不会拖住其它服务器，也不会累加启动时间
        clients: dict[str, McpClient] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(active)),
                                thread_name_prefix="mcp-connect") as pool:
            submitted = {}
            for spec in active:
                try:
                    client = self._factory.create(spec)
                except Exception as exc:  # 工厂自身出错（如依赖缺失）
                    self._statuses.append(McpServerStatus(
                        spec=spec, connected=False,
                        error="无法创建 MCP 客户端「%s」：%s" % (spec.name, exc)))
                    continue
                clients[spec.key] = client
                submitted[pool.submit(client.connect)] = spec

            for future in as_completed(submitted):
                spec = submitted[future]
                client = clients[spec.key]
                try:
                    handshake = future.result()
                except Exception as exc:
                    client.close()
                    clients.pop(spec.key, None)
                    self._statuses.append(McpServerStatus(
                        spec=spec, connected=False,
                        error=getattr(exc, "message", "") or str(exc)))
                    continue

                self._clients[spec.key] = client
                count = self._register_tools(spec, handshake.tools)
                resolved = getattr(client, "resolved_endpoint", spec.endpoint)
                note = ""
                if resolved and resolved != spec.endpoint:
                    note = "配置里的地址不完整，已自动补全为 %s（建议直接改成这个）" % resolved
                self._statuses.append(McpServerStatus(
                    spec=spec, connected=True, tool_count=count,
                    server_info=handshake.summary(), note=note))

        # 失败/被禁用的排在后面，便于状态栏阅读
        self._statuses.sort(key=lambda s: (not s.connected, s.spec.name.lower()))

    def _register_tools(self, spec: McpServerSpec, infos: list[Any]) -> int:
        count = 0
        for info in infos:
            candidate = exposed_tool_name(spec.key, info.name)
            if candidate in self._reserved:
                # 与本地工具同名（理论上前缀已避免，仍兜一层）
                candidate = dedupe_tool_name(candidate, self._reserved)
            final = dedupe_tool_name(candidate, set(self._routes))
            descriptor = McpToolDescriptor(
                server_key=spec.key,
                server_name=spec.name,
                tool_name=info.name,
                exposed_name=final,
                description=info.description or "",
                input_schema=info.input_schema or {},
                read_only=bool(getattr(info, "read_only", False)),
            )
            self._routes[final] = descriptor
            self._tools.append(descriptor)
            count += 1
        return count

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
    def call(self, exposed_name: str, arguments: Mapping[str, Any] | None) -> ToolOutcome:
        """按暴露名转发调用。任何失败都转成 `ToolOutcome.failure`。"""
        descriptor = self._routes.get(exposed_name)
        if descriptor is None:
            error = McpToolNotFoundError(exposed_name, sorted(self._routes))
            return ToolOutcome.failure(error.message)

        client = self._clients.get(descriptor.server_key)
        if client is None:
            return ToolOutcome.failure(
                "MCP 服务器「%s」未连接，无法调用 %s。请检查 .mcp.json 配置或网络。"
                % (descriptor.server_name, descriptor.tool_name))

        payload = dict(arguments or {})
        try:
            result = client.call_tool(descriptor.tool_name, payload)
        except McpToolCallError as exc:
            return ToolOutcome.failure(exc.message)
        except Exception as exc:  # 端口实现漏网之鱼
            return ToolOutcome.failure(
                "调用 MCP 工具 %s 失败：%s" % (descriptor.tool_name, exc))

        return self._render(descriptor, result)

    def _render(self, descriptor: McpToolDescriptor, result: McpCallResult) -> ToolOutcome:
        parts: list[str] = []
        if result.text:
            parts.append(result.text)
        if result.structured and not _structured_is_redundant(result.text, result.structured):
            try:
                dumped = json.dumps(result.structured, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                dumped = str(result.structured)
            parts.append("【结构化数据】\n%s" % dumped)

        body = "\n\n".join(parts).strip() or "（服务器返回了空内容）"
        if result.is_error:
            body = "MCP 工具 %s 执行失败：\n%s" % (descriptor.tool_name, body)
            return ToolOutcome.failure(_truncate(body, self._max_result_chars))

        body = "【MCP %s · %s】\n%s" % (
            descriptor.server_name, descriptor.tool_name,
            _truncate(body, self._max_result_chars))
        return ToolOutcome.success(body)

    # ------------------------------------------------------------------
    def close(self) -> None:
        """关闭所有连接（幂等，不抛异常）。"""
        for key, client in list(self._clients.items()):
            try:
                client.close()
            except Exception:  # 关闭阶段的异常不应影响退出流程
                pass
            self._clients.pop(key, None)


# ----------------------------------------------------------------------
# 渲染辅助（纯函数，便于单测）
# ----------------------------------------------------------------------
def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    omitted = len(text) - limit
    return "%s\n\n…（中略 %d 字符，完整内容取决于服务器返回量）…\n\n%s" % (
        text[:head], omitted, text[-tail:])


def _structured_is_redundant(text: str, structured: Mapping[str, Any]) -> bool:
    """结构化数据是否只是把同样的文本又包了一层（很多服务器会这样返回）。"""
    if not text.strip():
        return False

    def _leaves(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            out: list[str] = []
            for item in value.values():
                out.extend(_leaves(item))
            return out
        if isinstance(value, (list, tuple)):
            out = []
            for item in value:
                out.extend(_leaves(item))
            return out
        return []

    leaves = [leaf.strip() for leaf in _leaves(structured) if len(leaf.strip()) > 8]
    return bool(leaves) and all(leaf in text for leaf in leaves)


__all__ = ["McpService", "DEFAULT_MAX_RESULT_CHARS"]
