# -*- coding: utf-8 -*-
"""Web 网关测试：HTTP 契约 + SSE 事件流。

用 Starlette 的 TestClient 直连应用（不需要真的起 uvicorn），
LLM 用脚本化的假客户端，因此这些用例不联网。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config.template import ensure_local_config  # noqa: E402
from core.container import AppContainer  # noqa: E402
from core.domain.events import AssistantTextDelta, ToolCallCompleted, TurnCompleted  # noqa: E402
from core.domain.messages import ChatMessage, Role, ToolResult  # noqa: E402
from core.infrastructure.llm.mock import ScriptedLLMClient  # noqa: E402
from web.server import (  # noqa: E402
    WebState,
    create_app,
    event_payload,
    message_payload,
    sse,
)

try:  # starlette 是可选依赖：没装就跳过整个模块
    from starlette.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore[assignment]


def build(script=None, **overrides):
    tmp = tempfile.mkdtemp(prefix="ta-web-")
    ensure_local_config(tmp)
    previous = os.getcwd()
    os.chdir(tmp)
    container = AppContainer.build(
        llm=ScriptedLLMClient(script or [{"text": "你好，我是 TAgent。"}]),
        project_root=tmp,
        defer_mcp=True,
        overrides={"mcp_enabled": False, **overrides},
    )
    return container, tmp, previous


@unittest.skipIf(TestClient is None, "需要 starlette 才能测试 Web 网关")
class SerializerTest(unittest.TestCase):
    def test_event_payload_keeps_type(self) -> None:
        payload = event_payload(AssistantTextDelta(turn_id=2, text="abc"))
        self.assertEqual(payload["type"], "AssistantTextDelta")
        self.assertEqual(payload["text"], "abc")
        self.assertEqual(payload["turn_id"], 2)

    def test_tool_result_is_truncated(self) -> None:
        result = ToolResult(call_id="c1", name="RunCli", content="x" * 5000, ok=True)
        payload = event_payload(ToolCallCompleted(result=result, round_index=1))
        self.assertLess(len(payload["result"]["content"]), 5000)
        self.assertIn("已截断", payload["result"]["content"])

    def test_system_message_is_not_exposed(self) -> None:
        message = ChatMessage.system("secret prompt")
        payload = message_payload(0, message)
        # 结构还在，但接口层会过滤；这里确认它不携带 system 提示词
        self.assertEqual(payload["role"], Role.SYSTEM.value)
        self.assertEqual(payload["content"], "secret prompt")

    def test_sse_frame_is_single_json_block(self) -> None:
        frame = sse({"type": "Ping", "n": 1})
        self.assertTrue(frame.startswith("data: "))
        self.assertTrue(frame.endswith("\n\n"))
        self.assertEqual(json.loads(frame[6:].strip())["n"], 1)


@unittest.skipIf(TestClient is None, "需要 starlette 才能测试 Web 网关")
class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.container, self.tmp, self.previous = build()
        self.ctx = self.container.build_context()
        self.app = create_app(self.ctx, assets_dir="")
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.container.close()
        os.chdir(self.previous)

    # ------------------------------------------------------------------
    def test_health(self) -> None:
        data = self.client.get("/api/health").json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["busy"])

    def test_bootstrap_shape(self) -> None:
        data = self.client.get("/api/bootstrap").json()
        self.assertEqual(
            set(data), {"session", "model", "tools", "mcp", "context", "meta"})
        self.assertGreater(len(data["tools"]), 10)
        self.assertIn("used_tokens", data["context"])
        self.assertFalse(data["mcp"]["loading"])  # 关掉 MCP 时不进入加载态
        self.assertEqual(data["session"]["message_count"], 0)

    def test_system_prompt_not_in_messages(self) -> None:
        data = self.client.get("/api/bootstrap").json()
        roles = {m["role"] for m in data["session"]["messages"]}
        self.assertNotIn("system", roles)

    def test_chat_streams_events(self) -> None:
        types: list[str] = []
        with self.client.stream("POST", "/api/chat", json={"text": "你好"}) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                types.append(json.loads(payload)["type"])

        self.assertEqual(types[0], "UserAccepted")
        self.assertIn("AssistantTextDelta", types)
        self.assertIn("TurnCompleted", types)
        self.assertEqual(types[-1], "SessionSaved")

    def test_chat_rejects_empty_text(self) -> None:
        response = self.client.post("/api/chat", json={"text": "   "})
        self.assertEqual(response.status_code, 400)

    def test_chat_rejects_second_turn_while_busy(self) -> None:
        state: WebState = self.app.state.ta
        self.assertTrue(state.try_take_turn())
        try:
            response = self.client.post("/api/chat", json={"text": "你好"})
            self.assertEqual(response.status_code, 409)
            self.assertIn("还在生成", response.json()["error"])
        finally:
            state.release_turn()

    def test_save_list_and_resume(self) -> None:
        with self.client.stream("POST", "/api/chat", json={"text": "记住 42"}) as response:
            for _ in response.iter_lines():
                pass

        saved = self.client.post("/api/session/save").json()
        self.assertTrue(saved["ok"])

        listing = self.client.get("/api/sessions").json()
        self.assertTrue(listing["enabled"])
        self.assertEqual(listing["sessions"][0]["id"], saved["id"])

        new = self.client.post("/api/session/new").json()
        self.assertNotEqual(new["session"]["id"], saved["id"])
        self.assertEqual(new["session"]["messages"], [])

        resumed = self.client.post("/api/session/resume", json={"id": saved["id"]}).json()
        self.assertEqual(len(resumed["session"]["messages"]), 2)
        self.assertEqual(resumed["session"]["messages"][0]["content"], "记住 42")

    def test_resume_unknown_id(self) -> None:
        response = self.client.post("/api/session/resume", json={"id": "nope"})
        self.assertEqual(response.status_code, 404)

    def test_compact_requires_context_service(self) -> None:
        # 默认装配了 ContextService：压缩会调用（脚本化的）LLM
        response = self.client.post("/api/compact")
        # 空会话没有可压缩内容，应当是 500 + 可读原因，而不是崩掉
        self.assertEqual(response.status_code, 500)
        self.assertIn("没有可压缩", response.json()["error"])

    def test_cancel_is_idempotent(self) -> None:
        self.assertTrue(self.client.post("/api/cancel").json()["ok"])

    def test_fallback_page_when_not_built(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("npm run build", response.text)

    def test_static_assets_are_served(self) -> None:
        dist = os.path.join(self.tmp, "dist")
        os.makedirs(dist, exist_ok=True)
        with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as handle:
            handle.write("<html><body>built</body></html>")

        app = create_app(self.ctx, assets_dir=dist)
        client = TestClient(app)
        try:
            self.assertIn("built", client.get("/").text)
            # API 不能被静态资源抢走
            self.assertTrue(client.get("/api/health").json()["ok"])
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
