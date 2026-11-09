# -*- coding: utf-8 -*-
"""MCP 基础设施适配器（组合根之外唯一接触 SDK 的地方）。

职责边界：

    - 把官方 `mcp` SDK 的**异步**接口翻译成端口要求的**同步**接口；
    - 把 SDK / anyio / httpx 的异常统一翻译成 `McpConnectionError` /
      `McpToolCallError`；
    - 把协议对象还原成 `McpCallResult`（文本 / 结构化数据 / 错误标记）。

为什么需要独立线程 + 事件循环
------------------------------
TAgent 的 Agent 循环是同步生成器，而 MCP SDK 全程 async，且它的
transport 用 `anyio` 的 `AsyncExitStack`（cancel scope 要求**进入与退出
必须在同一个 task 内**）。因此这里给每个 Server 起一个专属线程，线程里
跑一个长期存活的 task：连接、`list_tools`、以及后续所有 `call_tool`
都在**同一个 task** 里串行执行，既满足 cancel scope 的约束，也避免了
跨 task 共享 session 的竞态。

串行并不影响体验：Agent 本来就是顺序执行工具调用（`execute_all`）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import os
import sys
import threading
from contextlib import AsyncExitStack
from typing import Any, Mapping

from ...domain.mcp import (
    McpCallResult,
    McpServerSpec,
    McpToolInfo,
    endpoint_candidates,
    flatten_text,
)
from ...errors import McpConnectionError, McpToolCallError
from ...ports.mcp import McpClient, McpClientFactory, McpHandshake


# ======================================================================
# 异常翻译
# ======================================================================
def describe_exception(exc: BaseException) -> str:
    """把可能被 task group 包裹的异常还原成一句可读的话。

    SDK 内部大量使用 anyio task group，异常往往被包成 `ExceptionGroup`，
    直接 `str()` 只会得到 "unhandled errors in a TaskGroup"，对用户毫无意义。
    """
    leaf = _first_leaf(exc)
    if leaf is None:
        return "未知错误"
    text = str(leaf).strip()
    name = type(leaf).__name__
    if isinstance(leaf, (OSError, FileNotFoundError)) and not text:
        text = str(leaf)
    # 有些 SDK 异常自带 message 属性（如 MCPError）
    if not text:
        text = getattr(leaf, "message", "") or ""
    return "%s: %s" % (name, text) if text else name


def _first_leaf(exc: BaseException) -> BaseException | None:
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            leaf = _first_leaf(child)
            if leaf is not None:
                return leaf
        return None
    if isinstance(exc, asyncio.CancelledError):
        return None
    return exc


#: 判定「地址不对」而非「服务器出错」的线索。托管服务在缺少路径后缀时
#: 往往不是标准 404，而是回一句 "Invalid session id."，因此一并识别。
_ENDPOINT_MISMATCH_HINTS = (
    "404",
    "not found",
    "invalid session id",
    "invalid session",
    "session not found",
    "method not allowed",
)


def _is_endpoint_mismatch(exc: BaseException) -> bool:
    """异常是否更像「端点地址不完整」，值得换一个候选地址再试。

    task group 里的异常可能有多个（连接关闭时常伴随 CancelledError），
    因此逐个叶子检查，只要有一个像 404 就认为地址可疑。
    """
    for leaf in _iter_leaves(exc):
        if getattr(leaf, "status_code", None) in (404, 405):
            return True
        text = str(leaf).lower()
        if any(hint in text for hint in _ENDPOINT_MISMATCH_HINTS):
            return True
    return False


def _iter_leaves(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from _iter_leaves(child)
        return
    if isinstance(exc, asyncio.CancelledError):
        return
    yield exc


# ======================================================================
# 失败诊断：把「连不上」变成「为什么连不上」
# ======================================================================
#: 响应体在提示里的最大展示长度
_BODY_SNIPPET_CHARS = 160


def _snippet(text: str, limit: int = _BODY_SNIPPET_CHARS) -> str:
    """压成一行、去掉多余空白，再截断——提示要能塞进一行状态栏。"""
    flat = " ".join((text or "").split())
    return flat[:limit] + "…" if len(flat) > limit else flat


def explain_http_failure(status: int, body: str = "") -> str:
    """把探测到的 HTTP 状态码翻译成一句「照着做就行」的提示。

    背景：MCP SDK 在非 2xx 时只回一句 `Server returned an error response`，
    状态码和响应体都被丢掉，用户看到的就只是「连接失败」四个字。
    """
    if status == 410:
        text = ("服务器返回 HTTP 410：这个托管地址已经过期（ModelScope 等平台的"
                "临时链接默认只保留 24 小时）。请回服务方控制台重新生成地址；"
                "如果要长期使用，建议改成本地 stdio 方式运行")
    elif status in (401, 403):
        text = ("服务器返回 HTTP %d：需要身份验证。请在 .mcp.json 里给这台服务器"
                "补上 headers，例如 \"headers\": {\"Authorization\": \"Bearer <token>\"}"
                % status)
    elif status == 404:
        text = ("服务器返回 HTTP 404：路径不对。请确认是否漏了 /mcp"
                "（SSE 是 /sse）一类的后缀")
    elif status == 405:
        text = ("服务器返回 HTTP 405：传输方式不匹配——这个地址可能是 SSE（/sse），"
                "而不是 Streamable HTTP（/mcp）")
    elif status == 429:
        text = "服务器返回 HTTP 429：请求过于频繁，稍后重试"
    elif 500 <= status < 600:
        text = "服务器返回 HTTP %d：对方服务故障，与本地配置无关" % status
    else:
        text = "服务器返回 HTTP %d" % status

    detail = _snippet(body)
    if detail and detail not in ("{}",):
        text += "；响应内容：%s" % detail
    return text


def probe_http_endpoint(url: str, headers: Mapping[str, str] | None = None,
                        timeout: float = 8.0) -> tuple[int, str] | None:
    """绕过 SDK 直接 POST 一次 initialize，拿回真实状态码与响应体。

    只在**连接失败之后**调用，纯诊断用途。任何网络异常都返回 None——
    诊断本身失败，不应该覆盖原始错误。
    """
    if not url:
        return None

    import json as _json
    import urllib.error
    import urllib.request

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "TAgent", "version": "0"},
        },
    }
    request = urllib.request.Request(
        url, data=_json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/event-stream")
    for key, value in (headers or {}).items():
        request.add_header(str(key), str(value))

    try:
        with urllib.request.urlopen(request, timeout=max(1.0, timeout)) as response:
            return int(getattr(response, "status", 0) or 0), response.read(2048).decode(
                "utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), exc.read(2048).decode("utf-8", "replace")
        except Exception:  # pragma: no cover - 读取响应体失败不影响结论
            return int(exc.code), ""
    except Exception:
        return None


async def _close_quietly(stack: AsyncExitStack) -> None:
    """关闭失败的连接上下文；清理阶段的异常不应覆盖真正的失败原因。"""
    try:
        await stack.aclose()
    except BaseException:  # noqa: BLE001 - 包括取消与 ExceptionGroup
        pass


# ======================================================================
# 结果转换
# ======================================================================
def _block_text(block: Any) -> str:
    """把一个 content block 渲染成文本；二进制内容只保留可读的占位说明。"""
    kind = getattr(block, "type", "") or ""
    if kind == "text":
        return getattr(block, "text", "") or ""

    if kind in ("image", "audio"):
        data = getattr(block, "data", "") or ""
        mime = getattr(block, "mime_type", "") or getattr(block, "mimeType", "") or "未知类型"
        approx_kb = max(1, int(len(data) * 3 / 4 / 1024)) if data else 0
        label = "图片" if kind == "image" else "音频"
        return "[%s内容已省略：%s，约 %d KB]" % (label, mime, approx_kb)

    if kind == "resource":
        resource = getattr(block, "resource", None)
        if resource is None:
            return "[资源]"
        text = getattr(resource, "text", None)
        if text:
            uri = getattr(resource, "uri", "")
            return "【资源 %s】\n%s" % (uri, text)
        uri = getattr(resource, "uri", "")
        mime = getattr(resource, "mime_type", "") or "未知类型"
        return "[二进制资源：%s（%s），内容已省略]" % (uri, mime)

    if kind == "resource_link":
        uri = getattr(block, "uri", "")
        name = getattr(block, "name", "") or ""
        return "[资源链接：%s%s]" % (name or uri, " (%s)" % uri if name and uri else "")

    # 未知类型：尽量给出可读信息，方便排查服务器实现
    return "[未支持的 content 类型：%s]" % (kind or type(block).__name__)


def to_call_result(result: Any) -> McpCallResult:
    """把 SDK 的 `CallToolResult` 转成纯数据。"""
    blocks = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        try:
            parts.append(_block_text(block))
        except Exception as exc:  # 单个块渲染失败不应毁掉整次调用
            parts.append("[内容渲染失败：%s]" % exc)

    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if structured is not None and not isinstance(structured, Mapping):
        structured = {"value": structured}

    is_error = bool(getattr(result, "is_error", False)
                   or getattr(result, "isError", False))

    return McpCallResult(text=flatten_text(*parts),
                         structured=structured,
                         is_error=is_error)


# ======================================================================
# 客户端实现
# ======================================================================
class SdkMcpClient(McpClient):
    """基于官方 `mcp` Python SDK 的同步客户端。

    一个实例 = 一个独立的线程、事件循环与连接，因此某一台服务器卡死
    或崩溃不会波及其它服务器。
    """

    def __init__(self, spec: McpServerSpec, connect_timeout: float = 15.0) -> None:
        self._spec = spec
        self._connect_timeout = connect_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._state_lock = threading.Lock()
        self._connect_error: BaseException | None = None
        self._handshake: McpHandshake | None = None
        self._closed = False
        #: 实际连通的地址（可能与配置不同：缺 `/mcp` 之类的后缀会被自动补全）
        self._resolved_url: str = ""
        #: 本次连接实际尝试过的地址，失败时原样告知用户
        self._tried_urls: list[str] = []
        #: stdio 服务器的 stderr 默认丢弃，避免污染 TUI 画面
        self._errlog: Any = None

    # ------------------------------------------------------------------
    @property
    def spec(self) -> McpServerSpec:
        return self._spec

    @property
    def resolved_endpoint(self) -> str:
        return self._resolved_url or self._spec.endpoint

    def _endpoint_detail(self) -> str:
        """错误提示里的目标描述：试过多个地址时全部列出。"""
        urls = self._tried_urls
        if len(urls) > 1:
            return "已尝试 %s" % "、".join(urls)
        if urls:
            return urls[0]
        return self._spec.endpoint

    def _failure_hint(self) -> str:
        """连接失败后补一枪 HTTP 探测，返回可读结论；探不出来就返回空串。

        仅对 Streamable HTTP 有意义：SDK 不给状态码，只能自己再问一次。
        SSE 端点只吃 GET，用 POST 探测会得到 405，反而误导，故跳过。
        """
        if self._spec.transport != "http":
            return ""
        budget = min(8.0, max(2.0, self._connect_timeout))
        probe = probe_http_endpoint(self.resolved_endpoint, self._spec.headers,
                                    timeout=budget)
        if probe is None:
            return ""
        status, body = probe
        if 200 <= status < 300:
            return ("诊断探测返回 HTTP %d：端点其实可达，问题多半在握手阶段"
                    "（协议版本或服务器实现）" % status)
        return explain_http_failure(status, body)

    # ------------------------------------------------------------------
    def connect(self) -> McpHandshake:
        if self._ready.is_set() and self._handshake is not None:
            return self._handshake
        self._start_worker()

        if not self._ready.wait(self._connect_timeout + 5):
            self.close()
            raise McpConnectionError(
                self._spec.name,
                "初始化超时（%.0f 秒）" % self._connect_timeout,
                self._spec.endpoint)

        if self._connect_error is not None:
            detail = describe_exception(self._connect_error)
            hint = self._failure_hint()
            if hint:
                # 把结论放前面：边栏/状态栏会截断，截掉的应是 SDK 的原始噪音
                detail = "%s（原始错误：%s）" % (hint, detail)
            elif (self._spec.transport in ("http", "sse")
                    and _is_endpoint_mismatch(self._connect_error)):
                detail += ("；该地址可能不是完整的 MCP 端点——请核对是否漏了 /mcp"
                           "（SSE 为 /sse）一类的路径后缀，或服务器是否需要鉴权 headers")
            self.close()
            raise McpConnectionError(self._spec.name, detail, self._endpoint_detail())

        if self._handshake is None:  # pragma: no cover - 理论上不会发生
            self.close()
            raise McpConnectionError(self._spec.name, "握手未返回结果", self._spec.endpoint)
        return self._handshake

    # ------------------------------------------------------------------
    def call_tool(self, tool_name: str, arguments: dict[str, Any],
                  timeout: float | None = None) -> McpCallResult:
        loop, queue = self._loop, self._queue
        if self._closed or loop is None or queue is None:
            raise McpToolCallError(self._spec.name, tool_name, "连接已关闭")
        if self._connect_error is not None:
            raise McpToolCallError(
                self._spec.name, tool_name, describe_exception(self._connect_error))
        if not self._ready.is_set():
            raise McpToolCallError(self._spec.name, tool_name, "尚未完成初始化")

        effective = float(timeout or self._spec.timeout)
        future: concurrent.futures.Future = concurrent.futures.Future()
        payload = dict(arguments or {})

        def _job(session: Any) -> Any:
            return self._call(session, tool_name, payload, effective)

        try:
            loop.call_soon_threadsafe(queue.put_nowait, (_job, future))
        except RuntimeError as exc:  # 事件循环已停止
            raise McpToolCallError(self._spec.name, tool_name, "事件循环已停止：%s" % exc)

        try:
            # 比协议层超时再多给一点余量，用于传输与反序列化
            return future.result(timeout=effective + 5)
        except concurrent.futures.TimeoutError:
            raise McpToolCallError(
                self._spec.name, tool_name, "调用超时（%.0f 秒）" % effective)
        except McpToolCallError:
            raise
        except BaseException as exc:
            raise McpToolCallError(self._spec.name, tool_name, describe_exception(exc))

    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True

        loop, queue, thread = self._loop, self._queue, self._thread
        if loop is not None and queue is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except RuntimeError:
                pass

        # 先让 _serve 自己退出（会正常关闭 transport 与子进程）
        if thread is not None and thread.is_alive():
            self._finished.wait(5.0)

        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if loop is not None and not loop.is_closed() and not loop.is_running():
            try:
                loop.close()
            except RuntimeError:  # pragma: no cover
                pass
        if self._errlog is not None:
            try:
                self._errlog.close()
            except Exception:  # pragma: no cover
                pass
            self._errlog = None

    # ------------------------------------------------------------------
    # 以下全部运行在独立线程的事件循环里
    # ------------------------------------------------------------------
    def _start_worker(self) -> None:
        with self._state_lock:
            if self._thread is not None:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="mcp-%s" % self._spec.key, daemon=True)
            self._thread.start()

    def _run_loop(self) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        try:
            self._bootstrap()  # 必须在 loop 线程里创建 task
            loop.run_forever()
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:  # pragma: no cover - 清理阶段的异常无需上抛
                pass

    def _bootstrap(self) -> None:
        """在事件循环线程里创建常驻 task。"""
        self._queue = asyncio.Queue()
        assert self._loop is not None
        self._task = self._loop.create_task(self._serve())

    async def _serve(self) -> None:
        try:
            stack, session, init = await self._connect_session()
        except BaseException as exc:  # 连接阶段失败：留给 connect() 翻译成用户可读错误
            self._connect_error = exc
            self._ready.set()
            self._finished.set()
            return

        try:
            tools = await asyncio.wait_for(
                self._list_tools(session), timeout=self._connect_timeout)

            info = getattr(init, "server_info", None)
            self._handshake = McpHandshake(
                server_name=getattr(info, "name", "") or self._spec.name,
                server_version=getattr(info, "version", "") or "",
                protocol_version=str(getattr(init, "protocol_version", "") or ""),
                tools=tools,
            )
            self._ready.set()

            assert self._queue is not None
            while True:
                item = await self._queue.get()
                if item is None:
                    break
                job, future = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = await job(session)
                except BaseException as exc:  # 交给调用线程翻译
                    future.set_exception(exc)
                else:
                    future.set_result(result)
        except BaseException as exc:  # 连接阶段失败 / 连接中途断开
            self._connect_error = exc
        finally:
            self._ready.set()
            self._finished.set()
            await _close_quietly(stack)

    async def _connect_session(self) -> tuple[AsyncExitStack, Any, Any]:
        """建立传输与 session，必要时为「缺后缀」的地址补一次候选再试。

        顺序严格遵守 MCP 生命周期：传输 → ClientSession → initialize，
        全部在同一个 task 内进出（anyio 的 cancel scope 有此硬性要求）。
        """
        candidates = endpoint_candidates(self._spec) or (self._spec.url,)
        last_error: BaseException | None = None
        for index, url in enumerate(candidates):
            self._tried_urls.append(url)
            stack = AsyncExitStack()
            try:
                read, write = await self._open_transport(stack, url)
                from mcp import ClientSession

                session = await stack.enter_async_context(
                    ClientSession(read, write, read_timeout_seconds=self._spec.timeout))
                init = await asyncio.wait_for(
                    session.initialize(), timeout=self._connect_timeout)
            except BaseException as exc:
                await _close_quietly(stack)
                # 只有「地址疑似不完整 + 还有候选」时才继续，其它错误立即上抛
                if index + 1 < len(candidates) and _is_endpoint_mismatch(exc):
                    last_error = exc
                    continue
                raise
            self._resolved_url = url
            return stack, session, init

        raise last_error or McpConnectionError(
            self._spec.name, "没有可用的端点地址", self._spec.endpoint)

    async def _open_transport(self, stack: AsyncExitStack,
                              url: str = "") -> tuple[Any, Any]:
        spec = self._spec
        target = url or spec.url
        if spec.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            params = StdioServerParameters(
                command=spec.command,
                args=list(spec.args),
                env=dict(spec.env) or None,
                cwd=spec.cwd or None,
            )
            read, write = await stack.enter_async_context(
                stdio_client(params, errlog=self._stdio_errlog()))
            return read, write

        if spec.transport == "http":
            from mcp.client.streamable_http import (
                create_mcp_http_client,
                streamable_http_client,
            )

            client = create_mcp_http_client(headers=dict(spec.headers) or None)
            streams = await stack.enter_async_context(
                streamable_http_client(target, http_client=client))
            return streams[0], streams[1]

        if spec.transport == "sse":
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(
                sse_client(target, headers=dict(spec.headers) or None))
            return read, write

        raise McpConnectionError(
            spec.name, "不支持的传输方式：%s" % spec.transport)

    def _stdio_errlog(self) -> Any:
        """stdio 服务器的 stderr 去哪。

        默认丢弃：TUI 是占满整屏的界面，子进程往 stderr 写日志会直接把画面
        冲花。排查问题时设 `TAGENT_MCP_STDERR=1`，日志就回到终端。
        """
        flag = os.environ.get("TAGENT_MCP_STDERR", "").strip().lower()
        if flag in ("1", "true", "yes", "on"):
            return sys.stderr
        if self._errlog is None:
            self._errlog = open(os.devnull, "w", encoding="utf-8")
        return self._errlog

    async def _list_tools(self, session: Any) -> list[McpToolInfo]:
        """拉取全部工具（自动翻页）。"""
        from mcp import types

        collected: list[McpToolInfo] = []
        cursor: str | None = None
        for _ in range(50):  # 防御：避免异常服务器导致无限翻页
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await session.list_tools(params=params)
            for tool in getattr(result, "tools", []) or []:
                schema = getattr(tool, "input_schema", None)
                if schema is None:
                    schema = getattr(tool, "inputSchema", None)
                annotations = getattr(tool, "annotations", None)
                collected.append(McpToolInfo(
                    name=getattr(tool, "name", "") or "",
                    description=getattr(tool, "description", "") or "",
                    input_schema=schema if isinstance(schema, Mapping) else {},
                    read_only=bool(getattr(annotations, "read_only_hint", False))
                    if annotations is not None else False,
                ))
            cursor = getattr(result, "next_cursor", None)
            if not cursor:
                break
        return [tool for tool in collected if tool.name]

    async def _call(self, session: Any, tool_name: str,
                    arguments: dict[str, Any], timeout: float) -> McpCallResult:
        result = await session.call_tool(
            tool_name, arguments or None, read_timeout_seconds=timeout)
        return to_call_result(result)


class SdkMcpClientFactory(McpClientFactory):
    """默认工厂：用官方 SDK 建立真实连接。"""

    def __init__(self, connect_timeout: float = 15.0) -> None:
        self._connect_timeout = connect_timeout

    def create(self, spec: McpServerSpec) -> McpClient:
        # 依赖缺失时给出可执行的修复建议，而不是让 ImportError 冒到界面
        available, reason = sdk_available()
        if not available:
            raise McpConnectionError(
                spec.name,
                '未安装 mcp 依赖，请执行 pip install "mcp>=2.0.0"（%s）' % reason)
        return SdkMcpClient(spec, connect_timeout=self._connect_timeout)


def sdk_available() -> tuple[bool, str]:
    """检查 `mcp` SDK 是否可用，返回 (可用, 原因)。"""
    try:
        for module in ("mcp", "mcp.client.stdio", "mcp.client.streamable_http"):
            importlib.import_module(module)
    except Exception as exc:  # ImportError 或底层依赖缺失
        return False, "%s: %s" % (type(exc).__name__, exc)
    return True, ""


__all__ = [
    "SdkMcpClient",
    "SdkMcpClientFactory",
    "describe_exception",
    "explain_http_failure",
    "probe_http_endpoint",
    "to_call_result",
    "sdk_available",
]
