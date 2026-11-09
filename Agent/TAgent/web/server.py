# -*- coding: utf-8 -*-
"""Web 后端网关：Starlette + SSE。

设计要点
--------
1. **只做搬运**：不重写任何业务逻辑，`/api/chat` 就是把
   `AgentService.stream_turn()` yield 出来的事件逐条塞进 SSE 帧。
   这样 Web 与 TUI 走的是同一条流水线，行为天然一致。
2. **同步生成器 + 工作线程**：core 的事件流是阻塞生成器，这里放进后台线程，
   结果经 `queue.Queue` 回到请求线程，顺便拿到「心跳」能力
   （工具卡住时也能定期发 `: ping`，避免代理掐断连接）。
3. **单会话单轮次**：`turn_lock` 保证同一时刻只有一轮对话在跑；
   上一条没结束时新请求收到 409，而不是把会话历史搅乱。
4. **静态资源自带托管**：`frontend/dist` 存在时直接由本服务提供，
   用户只需要 `python run.py --ui web` 一条命令。

API
---
    GET  /api/bootstrap          一次性拿到会话、工具、MCP、用量、模型信息
    POST /api/chat               text/event-stream（body: {"text": "..."}）
    POST /api/cancel             中止本轮生成
    POST /api/session/new        保存并开新会话
    POST /api/session/save       保存当前会话
    GET  /api/sessions           历史会话列表
    POST /api/session/resume     恢复指定会话
    GET  /api/mcp                重新读取 MCP 状态（后台加载完成后轮询用）
    POST /api/compact            手动压缩上下文
    GET  /api/health             探活（前端启动时用）
"""

from __future__ import annotations

import json
import os
import queue
import threading
from dataclasses import asdict, fields, is_dataclass
from typing import Any, Iterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from core.domain.events import AgentEvent, TurnCompleted
from core.domain.agents import looks_like_agents_message
from core.domain.messages import ChatMessage, Role
from core.domain.mcp import McpSnapshot
from core.errors import TAgentError
from core.ports.frontend import FrontendContext

#: SSE 默认心跳间隔（秒），实际值来自 `[web] heartbeat_seconds`
DEFAULT_HEARTBEAT = 15.0

#: 单条工具结果回传前端的最大字符数（界面只做预览，完整内容仍在模型上下文里）
TOOL_PREVIEW_CHARS = 2000


# ======================================================================
# 序列化：core 的 dataclass → 前端能直接用的 JSON
# ======================================================================
def _value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    return value


def event_payload(event: AgentEvent) -> dict[str, Any]:
    """事件 → `{"type": "AssistantTextDelta", ...}`。"""
    payload: dict[str, Any] = {"type": type(event).__name__}
    for item in fields(event):
        payload[item.name] = _value(getattr(event, item.name))
    # 工具结果可能很长：截断后再给界面（原始内容仍在会话里）
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("content"), str):
        result["content"] = _clip(result["content"], TOOL_PREVIEW_CHARS)
    return payload


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（共 %d 字符，已截断）" % len(text)


def message_payload(index: int, message: ChatMessage) -> dict[str, Any]:
    """会话消息 → 界面气泡。system 消息不下发（属于内部提示词）。"""
    return {
        "id": "m-%d" % index,
        "index": index,
        "role": message.role.value,
        "content": message.content or "",
        "name": message.name or "",
        "tool_call_id": message.tool_call_id or "",
        "tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments or {},
            }
            for call in (message.tool_calls or [])
        ],
        "ts": 0,
    }


def snapshot_payload(snapshot: McpSnapshot | None, loading: bool) -> dict[str, Any]:
    if snapshot is None:
        return {"loading": loading, "servers": [], "total_tools": 0,
                "connected_count": 0, "total_count": 0, "summary": "",
                "config_error": "", "notices": [], "loaded": False}
    return {
        "loading": loading,
        "loaded": snapshot.loaded,
        "servers": [
            {
                "name": server.name,
                "key": server.key,
                "transport": server.transport,
                "endpoint": server.endpoint,
                "connected": server.connected,
                "disabled": server.disabled,
                "tool_count": server.tool_count,
                "server_info": server.server_info,
                "error": server.error,
                "note": server.note,
                "tools": list(server.tools),
            }
            for server in snapshot.servers
        ],
        "total_tools": snapshot.total_tools,
        "connected_count": snapshot.connected_count,
        "total_count": snapshot.total_count,
        "summary": snapshot.summary,
        "config_error": snapshot.config_error,
        "config_source": snapshot.config_source,
        "notices": list(snapshot.notices),
    }


def sse(payload: dict[str, Any]) -> str:
    """一条 SSE 帧。`data` 里放完整 JSON，前端按 `type` 分派。"""
    return "data: %s\n\n" % json.dumps(payload, ensure_ascii=False)


# ======================================================================
# 运行时状态
# ======================================================================
class WebState:
    """一份存活期与进程相同的界面状态。

    刻意保持「单会话」：TAgent 是本地单人助手，多标签页共享同一个会话，
    比给每个标签页复制一份上下文更符合直觉。
    """

    def __init__(self, ctx: FrontendContext) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self._turn_lock = threading.Lock()
        self._mcp_snapshot = ctx.metadata.get("mcp_snapshot")
        self._mcp_loading = bool(ctx.metadata.get("mcp_loading"))
        self._mcp_started = False
        self.heartbeat = max(3.0, float(getattr(ctx.settings.web, "heartbeat_seconds", DEFAULT_HEARTBEAT)))

    # ------------------------------------------------------------------
    # MCP：先进界面，后台连接
    # ------------------------------------------------------------------
    def ensure_mcp_loading(self) -> None:
        if self._mcp_started or not self._mcp_loading:
            return
        self._mcp_started = True

        def worker() -> None:
            try:
                loader = self.ctx.load_mcp
                snapshot = loader() if callable(loader) else None
                if isinstance(snapshot, McpSnapshot):
                    self._mcp_snapshot = snapshot
            except Exception:  # noqa: BLE001 - 加载失败不该影响界面
                pass
            finally:
                self._mcp_loading = False

        threading.Thread(target=worker, name="mcp-loader", daemon=True).start()

    @property
    def busy(self) -> bool:
        return self._turn_lock.locked()

    def try_take_turn(self) -> bool:
        return self._turn_lock.acquire(blocking=False)

    def release_turn(self) -> None:
        try:
            self._turn_lock.release()
        except RuntimeError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    def usage(self) -> dict[str, Any]:
        context = self.ctx.context
        if context is None:
            return {"used_tokens": 0, "window_tokens": 0, "percent": 0,
                    "window_is_default": True, "from_api": False, "threshold": 0.8}
        usage = context.usage(self.session, self.ctx.agent.tool_specs())
        return {
            "used_tokens": usage.used_tokens,
            "window_tokens": usage.window_tokens,
            "percent": usage.percent,
            "window_is_default": usage.window_is_default,
            "from_api": usage.from_api,
            "threshold": context.threshold,
        }

    def session_payload(self) -> dict[str, Any]:
        session = self.session
        filename = self.ctx.settings.agents_doc.filename
        # system 提示词与 AGENTS.md 注入都属于「内部约定」：
        # 前者是角色设定，后者是一条伪装的 user 消息，都不该出现在对话区。
        visible = [
            m for m in session.messages
            if m.role is not Role.SYSTEM
            and not (m.role is Role.USER
                     and looks_like_agents_message(m.content or "", filename))
        ]
        return {
            "id": session.id,
            "title": session.title,
            "agents_doc": session.agents_doc,
            "message_count": len(visible),
            "messages": [message_payload(i, m) for i, m in enumerate(visible)],
        }

    def bootstrap(self) -> dict[str, Any]:
        ctx = self.ctx
        tools = [
            {"name": spec.name, "description": (spec.description or "").strip()[:280]}
            for spec in ctx.agent.available_tools()
        ]
        return {
            "session": self.session_payload(),
            "model": {
                "name": ctx.settings.llm.model,
                "base_url": ctx.settings.llm.base_url,
                "temperature": ctx.settings.llm.temperature,
            },
            "tools": tools,
            "mcp": snapshot_payload(self._mcp_snapshot, self._mcp_loading),
            "context": self.usage(),
            "meta": {
                "config_source": ctx.metadata.get("config_source", ""),
                "config_created": bool(ctx.metadata.get("config_created")),
                "skills": ctx.metadata.get("skills", ""),
                "agents_doc": ctx.metadata.get("agents_doc", ""),
                "sessions": ctx.metadata.get("sessions", ""),
                "cwd": os.getcwd(),
                "busy": self.busy,
                "version": "web-1",
            },
        }

    # ------------------------------------------------------------------
    # 对话：阻塞生成器 → SSE 帧
    # ------------------------------------------------------------------
    def chat_frames(self, text: str) -> Iterator[str]:
        """本轮对话的 SSE 帧序列（含心跳与收尾）。"""
        user_message = message_payload(-1, ChatMessage.user(text))
        yield sse({"type": "UserAccepted", "message": user_message})

        frames: queue.Queue[str | None] = queue.Queue()
        session = self.session

        def worker() -> None:
            try:
                for event in self.ctx.agent.stream_turn(session, text):
                    frames.put(sse(event_payload(event)))
                    if isinstance(event, TurnCompleted):
                        frames.put(sse({"type": "SessionSaved",
                                        "session": self.session_payload(),
                                        "context": self.usage()}))
                        self._autosave()
            except TAgentError as exc:
                frames.put(sse({"type": "ErrorOccurred", "message": exc.message,
                                "error_type": "agent", "recoverable": True}))
            except Exception as exc:  # noqa: BLE001 - 任何异常都要让前端收尾
                frames.put(sse({"type": "ErrorOccurred",
                                "message": "%s: %s" % (type(exc).__name__, exc),
                                "error_type": "internal", "recoverable": False}))
            finally:
                frames.put(None)
                self.release_turn()

        threading.Thread(target=worker, name="turn-worker", daemon=True).start()

        while True:
            try:
                frame = frames.get(timeout=self.heartbeat)
            except queue.Empty:
                yield ": ping\n\n"  # 注释帧：只为保活，前端会忽略
                continue
            if frame is None:
                break
            yield frame

        yield "data: [DONE]\n\n"

    def _autosave(self) -> None:
        service = self.ctx.sessions
        if service is None or not getattr(service, "autosave", False):
            return
        try:
            service.autosave_session(self.session)
        except Exception:  # noqa: BLE001 - 存档失败不影响对话
            pass


# ======================================================================
# HTTP 路由
# ======================================================================
def _json(data: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def create_app(ctx: FrontendContext, assets_dir: str = "") -> Starlette:
    """组装 Starlette 应用（`web/frontend.py` 调用）。"""
    state = WebState(ctx)
    state.ensure_mcp_loading()

    # ----------------------------------------------------------------
    def health(_: Request) -> Response:
        return _json({"ok": True, "busy": state.busy, "session": state.session.id})

    def bootstrap(_: Request) -> Response:
        return _json(state.bootstrap())

    async def chat(request: Request) -> Response:
        body = await _body(request)
        text = str(body.get("text") or "").strip()
        if not text:
            return _json({"error": "内容为空"}, 400)
        if not state.try_take_turn():
            return _json({"error": "上一轮还在生成中，请稍候或先点停止"}, 409)
        return StreamingResponse(
            state.chat_frames(text),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    def cancel(_: Request) -> Response:
        ctx.agent.request_cancel()
        return _json({"ok": True})

    def new_session(_: Request) -> Response:
        if state.busy:
            return _json({"error": "生成中不能切换会话"}, 409)
        factory = ctx.new_session
        if factory is None:
            return _json({"error": "当前不支持新建会话"}, 400)
        service = ctx.sessions
        if service is not None and service.enabled:
            try:
                service.save(state.session)
            except TAgentError:
                pass
        session = factory()
        prepare = ctx.prepare_session
        if callable(prepare):
            session = prepare(session)
        state.session = session
        return _json({"session": state.session_payload(), "context": state.usage()})

    def save_session(_: Request) -> Response:
        service = ctx.sessions
        if service is None or not service.enabled:
            return _json({"error": "未启用会话存档"}, 400)
        try:
            summary = service.save(state.session)
        except TAgentError as exc:
            return _json({"error": exc.message}, 500)
        return _json({"ok": True, "id": summary.id, "title": summary.display_title,
                      "path": summary.path})

    def list_sessions(_: Request) -> Response:
        service = ctx.sessions
        if service is None or not service.enabled:
            return _json({"sessions": [], "enabled": False})
        items = [
            {
                "id": s.id,
                "title": s.display_title,
                "updated_at": s.updated_at,
                "message_count": s.message_count,
                "preview": s.preview,
                "path": s.path,
            }
            for s in service.list_summaries()
        ]
        return _json({"sessions": items, "enabled": True, "current": state.session.id})

    async def resume(request: Request) -> Response:
        if state.busy:
            return _json({"error": "生成中不能切换会话"}, 409)
        body = await _body(request)
        session_id = str(body.get("id") or "").strip()
        service = ctx.sessions
        if service is None or not service.enabled:
            return _json({"error": "未启用会话存档"}, 400)
        try:
            session = service.load(session_id)
        except TAgentError as exc:
            return _json({"error": exc.message}, 404)
        prepare = ctx.prepare_session
        if callable(prepare):
            session = prepare(session)
        state.session = session
        return _json({"session": state.session_payload(), "context": state.usage()})

    def mcp(_: Request) -> Response:
        return _json(snapshot_payload(state._mcp_snapshot, state._mcp_loading))

    def compact(_: Request) -> Response:
        context = ctx.context
        if context is None:
            return _json({"error": "未启用上下文压缩"}, 400)
        if state.busy:
            return _json({"error": "生成中不能压缩上下文"}, 409)
        try:
            result = context.compact(state.session, ctx.agent.tool_specs())
        except TAgentError as exc:
            return _json({"error": exc.message}, 500)
        return _json({
            "ok": True,
            "archive_id": result.archive_id,
            "archive_path": result.archive_path,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "message_count": result.message_count,
            "session": state.session_payload(),
            "context": state.usage(),
        })

    routes = [
        Route("/api/health", health),
        Route("/api/bootstrap", bootstrap),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/cancel", cancel, methods=["POST"]),
        Route("/api/session/new", new_session, methods=["POST"]),
        Route("/api/session/save", save_session, methods=["POST"]),
        Route("/api/session/resume", resume, methods=["POST"]),
        Route("/api/sessions", list_sessions),
        Route("/api/mcp", mcp),
        Route("/api/compact", compact, methods=["POST"]),
    ]

    index = os.path.join(assets_dir, "index.html") if assets_dir else ""
    if index and os.path.isfile(index):
        routes.append(Mount("/", app=StaticFiles(directory=assets_dir, html=True),
                            name="assets"))
    else:
        routes.append(Route("/", _fallback_page, methods=["GET"]))

    app = Starlette(routes=routes)
    app.state.ta = state
    return app


async def _fallback_page(_: Request) -> Response:
    """前端还没构建时给出可照做的说明，而不是一个 404。"""
    return HTMLResponse(_FALLBACK_HTML, status_code=200)


_FALLBACK_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>反诈助手 · Web 界面尚未构建</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         background:#0b0d10; color:#e6e8eb;
         font:15px/1.7 "Segoe UI",system-ui,-apple-system,sans-serif; }
  .card { max-width:640px; padding:40px 44px; border-radius:20px;
          background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08); }
  h1 { margin:0 0 6px; font-size:22px; letter-spacing:.2px; }
  p  { margin:0 0 14px; color:#9aa4b2; }
  code, pre { font-family: ui-monospace, "JetBrains Mono", Consolas, monospace; }
  pre { background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.07);
        border-radius:12px; padding:14px 16px; color:#cfd6e0; overflow:auto; }
  .hint { color:#7c8798; font-size:13px; }
  b { color:#ffb86b; font-weight:600; }
</style></head>
<body><div class="card">
  <h1>前端还没构建</h1>
  <p>后端已经跑起来了，只差一套静态资源。在项目根目录执行：</p>
  <pre>cd frontend
npm install
npm run build</pre>
  <p>然后刷新本页即可。开发时也可以用 <code>npm run dev</code>，它会把 <code>/api</code> 代理到本服务。</p>
  <p class="hint">构建产物输出到 <b>frontend/dist</b>，后端会自动托管。</p>
</div></body></html>
"""


__all__ = ["create_app", "WebState", "event_payload", "message_payload",
           "snapshot_payload", "sse"]
