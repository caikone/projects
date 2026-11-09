# -*- coding: utf-8 -*-
"""MCP Client 的测试。

覆盖五层，全部可离线运行（只有最后的 stdio 集成测试会起子进程）：

    1. 命名规则：服务器键归一化、`mcp__<server>__<tool>` 前缀、长度上限、去重；
    2. 配置解析：`.mcp.json` → `McpServerSpec`，坏条目只跳过自己；
    3. 结果渲染：文本 / 结构化数据 / isError / 超长截断；
    4. 服务编排：工具注册、名称映射、失败隔离、调用转发（用内存假客户端）；
    5. 真实链路：通过官方 SDK 拉起 stdio 测试服务器，验证握手、发现与调用。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

from core.domain.mcp import (
    MAX_TOOL_NAME_LEN,
    McpCallResult,
    McpConfigIssue,
    McpServerSpec,
    McpToolInfo,
    dedupe_tool_name,
    endpoint_candidates,
    exposed_tool_name,
    looks_like_missing_endpoint,
    normalize_server_key,
    parse_mcp_config,
    sanitize_tool_segment,
)
from core.errors import McpConfigError, McpConnectionError, McpToolCallError
from core.infrastructure.mcp.json_config import JsonMcpConfigLoader
from core.infrastructure.mcp.sdk_client import (
    SdkMcpClient,
    _is_endpoint_mismatch,
    explain_http_failure,
    probe_http_endpoint,
    to_call_result,
)
from core.ports.mcp import McpClient, McpClientFactory, McpHandshake
from core.services.mcp import McpService
from core.tools.mcp_tools import (
    build_mcp_tools,
    describe_tool,
    normalize_input_schema,
)


# ======================================================================
# 测试替身
# ======================================================================
class FakeClient(McpClient):
    def __init__(self, spec: McpServerSpec, tools=None, error=None,
                 results=None, call_error=None, resolved: str = "") -> None:
        self._spec = spec
        self._tools = list(tools or [])
        self._error = error
        self._results = dict(results or {})
        self._call_error = call_error
        self._resolved = resolved
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    @property
    def spec(self) -> McpServerSpec:
        return self._spec

    @property
    def resolved_endpoint(self) -> str:
        return self._resolved or self._spec.endpoint

    def connect(self) -> McpHandshake:
        if self._error is not None:
            raise self._error
        return McpHandshake(server_name=self._spec.name,
                            server_version="9.9.9", tools=self._tools)

    def call_tool(self, tool_name, arguments, timeout=None):
        self.calls.append((tool_name, dict(arguments)))
        if self._call_error is not None:
            raise self._call_error
        return self._results.get(tool_name, McpCallResult(text="默认结果"))

    def close(self) -> None:
        self.closed = True


class FakeFactory(McpClientFactory):
    def __init__(self, mapping: dict[str, McpClient]) -> None:
        self._mapping = mapping
        self.created: list[str] = []

    def create(self, spec: McpServerSpec) -> McpClient:
        self.created.append(spec.key)
        if spec.key not in self._mapping:
            raise RuntimeError("未准备该服务器：%s" % spec.key)
        return self._mapping[spec.key]


class FakeLoader:
    def __init__(self, specs, issues=None, source="<test>", error=None) -> None:
        self._specs = list(specs)
        self._issues = list(issues or [])
        self._source = source
        self._error = error

    @property
    def path(self) -> str:
        return self._source

    def load(self):
        if self._error is not None:
            raise self._error
        return list(self._specs), list(self._issues), self._source


def http_spec(name: str = "Bazi", key: str | None = None) -> McpServerSpec:
    return McpServerSpec(name=name, key=key or normalize_server_key(name),
                         transport="http", url="https://example.com/mcp")


def tool_info(name: str, description: str = "", schema=None) -> McpToolInfo:
    return McpToolInfo(name=name, description=description,
                       input_schema=schema or {"type": "object", "properties": {}})


# ======================================================================
# 1. 命名规则
# ======================================================================
class NamingTest(unittest.TestCase):
    def test_server_key_normalized(self) -> None:
        self.assertEqual(normalize_server_key("Bazi-MCP"), "bazi-mcp")
        self.assertEqual(normalize_server_key("  My Server  "), "my-server")
        self.assertEqual(normalize_server_key("a__b"), "a-b")
        self.assertEqual(normalize_server_key("A.B"), "a-b")

    def test_non_ascii_server_key_falls_back_to_hash(self) -> None:
        key = normalize_server_key("八字排盘")
        self.assertTrue(key.startswith("server-"))
        # 同一个名字要稳定，不能每次启动都变
        self.assertEqual(key, normalize_server_key("八字排盘"))

    def test_server_key_length_capped(self) -> None:
        key = normalize_server_key("x" * 100)
        self.assertLessEqual(len(key), 20)

    def test_exposed_name_has_prefix_and_is_valid(self) -> None:
        name = exposed_tool_name("bazi-mcp", "calculate_bazi")
        self.assertEqual(name, "mcp__bazi-mcp__calculate_bazi")
        self.assertLessEqual(len(name), MAX_TOOL_NAME_LEN)
        # OpenAI 函数名约束：只允许字母数字下划线连字符
        self.assertRegex(name, r"^[a-zA-Z0-9_-]{1,64}$")

    def test_exposed_name_sanitizes_illegal_chars(self) -> None:
        name = exposed_tool_name("srv", "search.web/文章")
        self.assertRegex(name, r"^[a-zA-Z0-9_-]{1,64}$")
        self.assertTrue(name.startswith("mcp__srv__"))

    def test_exposed_name_truncates_long_tool(self) -> None:
        name = exposed_tool_name("srv", "a" * 200)
        self.assertLessEqual(len(name), MAX_TOOL_NAME_LEN)
        self.assertTrue(name.startswith("mcp__srv__a"))

    def test_exposed_name_handles_empty_tool(self) -> None:
        self.assertTrue(exposed_tool_name("srv", "").endswith("__tool"))

    def test_sanitize_segment_keeps_case(self) -> None:
        self.assertEqual(sanitize_tool_segment("ReadFile"), "ReadFile")

    def test_dedupe_appends_index(self) -> None:
        taken = {"a"}
        self.assertEqual(dedupe_tool_name("b", taken), "b")
        self.assertEqual(dedupe_tool_name("a", taken), "a_2")
        taken.add("a_2")
        self.assertEqual(dedupe_tool_name("a", taken), "a_3")

    def test_dedupe_respects_length_limit(self) -> None:
        long_name = "x" * MAX_TOOL_NAME_LEN
        result = dedupe_tool_name(long_name, {long_name})
        self.assertLessEqual(len(result), MAX_TOOL_NAME_LEN)


# ======================================================================
# 2. 配置解析
# ======================================================================
class EndpointCandidatesTest(unittest.TestCase):
    """地址补全规则：只对「看起来不完整」的地址生成备用候选。"""

    def spec(self, url: str, transport: str = "http") -> McpServerSpec:
        return McpServerSpec(name="X", key="x", transport=transport, url=url)

    def test_missing_suffix_gets_candidate(self) -> None:
        base = "https://mcp.api-inference.modelscope.net/4a3222faa31a4e"
        self.assertEqual(endpoint_candidates(self.spec(base)),
                         (base, base + "/mcp"))
        self.assertTrue(looks_like_missing_endpoint(self.spec(base)))

    def test_complete_url_has_no_extra_candidate(self) -> None:
        url = "https://mcp.deepwiki.com/mcp"
        self.assertEqual(endpoint_candidates(self.spec(url)), (url,))
        self.assertFalse(looks_like_missing_endpoint(self.spec(url)))

    def test_trailing_slash_is_normalized(self) -> None:
        self.assertEqual(endpoint_candidates(self.spec("https://host/mcp/")),
                         ("https://host/mcp",))

    def test_double_slash_in_path_is_collapsed(self) -> None:
        # 部分网关对 `//mcp` 直接返回 404
        self.assertEqual(endpoint_candidates(self.spec("https://host//mcp")),
                         ("https://host/mcp",))

    def test_deep_path_is_treated_as_complete(self) -> None:
        # 多级路径视为用户已写全，不做猜测
        self.assertEqual(endpoint_candidates(self.spec("https://host/api/v1")),
                         ("https://host/api/v1",))

    def test_root_url_gets_candidate(self) -> None:
        self.assertEqual(endpoint_candidates(self.spec("https://host")),
                         ("https://host", "https://host/mcp"))

    def test_sse_uses_sse_suffix(self) -> None:
        self.assertEqual(endpoint_candidates(self.spec("https://host", "sse")),
                         ("https://host", "https://host/sse"))

    def test_stdio_has_no_candidates(self) -> None:
        spec = McpServerSpec(name="x", key="x", transport="stdio", command="python")
        self.assertEqual(endpoint_candidates(spec), ())

    def test_query_string_is_preserved(self) -> None:
        url = "https://host/abc?key=1"
        self.assertEqual(endpoint_candidates(self.spec(url)),
                         (url, "https://host/abc/mcp?key=1"))


class EndpointMismatchTest(unittest.TestCase):
    def test_not_found_is_mismatch(self) -> None:
        self.assertTrue(_is_endpoint_mismatch(RuntimeError("404 Not Found")))
        self.assertTrue(_is_endpoint_mismatch(RuntimeError("Not Found (https://x)")))
        # 托管服务缺后缀时可能这样回，而不是标准 404
        self.assertTrue(_is_endpoint_mismatch(RuntimeError("Invalid session id.")))

    def test_other_errors_are_not_mismatch(self) -> None:
        self.assertFalse(_is_endpoint_mismatch(RuntimeError("Connection refused")))
        self.assertFalse(_is_endpoint_mismatch(RuntimeError("401 Unauthorized")))

    def test_looks_inside_exception_group(self) -> None:
        group = BaseExceptionGroup("g", [RuntimeError("boom"),
                                         RuntimeError("404 Not Found")])
        self.assertTrue(_is_endpoint_mismatch(group))


class HttpFailureExplainTest(unittest.TestCase):
    """连接失败要说人话：410 过期 / 401 鉴权 / 404 路径 / 5xx 对方故障。"""

    def test_gone_means_expired_url(self) -> None:
        text = explain_http_failure(410, '{"error":{"message":"Url is expired"}}')
        self.assertIn("410", text)
        self.assertIn("过期", text)
        self.assertIn("Url is expired", text)
        # 必须给出下一步
        self.assertIn("重新生成", text)

    def test_unauthorized_mentions_headers(self) -> None:
        text = explain_http_failure(401)
        self.assertIn("401", text)
        self.assertIn("headers", text)
        self.assertIn("Authorization", text)

    def test_forbidden_also_counts_as_auth(self) -> None:
        self.assertIn("headers", explain_http_failure(403))

    def test_not_found_mentions_path_suffix(self) -> None:
        self.assertIn("/mcp", explain_http_failure(404))

    def test_method_not_allowed_hints_sse(self) -> None:
        self.assertIn("sse", explain_http_failure(405).lower())

    def test_server_error_blames_remote(self) -> None:
        text = explain_http_failure(502, "bad gateway")
        self.assertIn("502", text)
        self.assertIn("对方服务故障", text)

    def test_unknown_status_still_reported(self) -> None:
        self.assertIn("418", explain_http_failure(418))

    def test_body_is_flattened_and_truncated(self) -> None:
        text = explain_http_failure(400, "line1\nline2" + "x" * 500)
        self.assertNotIn("\n", text)
        self.assertLess(len(text), 400)


class ProbeHttpTest(unittest.TestCase):
    """探测是纯诊断：拿到真实状态码，探不到就返回 None。"""

    def test_reads_status_and_body(self) -> None:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - 标准库要求的命名
                body = '{"error":{"message":"Url is expired"}}'.encode()
                self.send_response(410)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # 静默
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d/mcp" % server.server_port
            result = probe_http_endpoint(url, timeout=3)
            self.assertIsNotNone(result)
            status, body = result
            self.assertEqual(status, 410)
            self.assertIn("Url is expired", body)
            self.assertIn("过期", explain_http_failure(status, body))
        finally:
            server.shutdown()
            server.server_close()

    def test_unreachable_returns_none(self) -> None:
        # 端口 1 基本不可能有人监听：探测失败必须安静返回 None
        self.assertIsNone(probe_http_endpoint("http://127.0.0.1:1/mcp", timeout=1))

    def test_empty_url_returns_none(self) -> None:
        self.assertIsNone(probe_http_endpoint(""))


class ConfigParseTest(unittest.TestCase):
    def test_parse_stdio_and_http(self) -> None:
        specs, issues = parse_mcp_config({
            "mcpServers": {
                "local": {"command": "python", "args": ["-m", "server"], "env": {"A": "1"}},
                "remote": {"type": "streamable_http", "url": "https://x/mcp"},
            }
        })
        self.assertEqual(issues, [])
        self.assertEqual([s.name for s in specs], ["local", "remote"])
        local, remote = specs
        self.assertEqual(local.transport, "stdio")
        self.assertEqual(local.command, "python")
        self.assertEqual(local.args, ("-m", "server"))
        self.assertEqual(dict(local.env), {"A": "1"})
        self.assertEqual(remote.transport, "http")
        self.assertEqual(remote.url, "https://x/mcp")

    def test_transport_aliases(self) -> None:
        for raw_type in ("streamable_http", "streamable-http", "STREAMABLE_HTTP", "http"):
            specs, _ = parse_mcp_config(
                {"mcpServers": {"s": {"type": raw_type, "url": "https://x/mcp"}}})
            self.assertEqual(specs[0].transport, "http", raw_type)
        specs, _ = parse_mcp_config(
            {"mcpServers": {"s": {"type": "sse", "url": "https://x/sse"}}})
        self.assertEqual(specs[0].transport, "sse")

    def test_infer_transport_when_type_missing(self) -> None:
        specs, _ = parse_mcp_config({"mcpServers": {
            "a": {"command": "npx"},
            "b": {"url": "https://x/mcp"},
        }})
        self.assertEqual({s.name: s.transport for s in specs},
                         {"a": "stdio", "b": "http"})

    def test_bad_entries_are_skipped_not_fatal(self) -> None:
        specs, issues = parse_mcp_config({"mcpServers": {
            "ok": {"command": "python"},
            "no-command": {"type": "stdio"},
            "bad-url": {"type": "http", "url": "ftp://x"},
            "args-not-list": {"command": "python", "args": "a b"},
            "bad-timeout": {"command": "python", "timeout": "abc"},
            "not-an-object": "oops",
        }})
        self.assertEqual([s.name for s in specs], ["ok"])
        self.assertEqual(len(issues), 5)
        self.assertTrue(all(isinstance(i, McpConfigIssue) for i in issues))
        self.assertIn("no-command", [i.server for i in issues])

    def test_duplicate_key_after_normalization(self) -> None:
        specs, issues = parse_mcp_config({"mcpServers": {
            "Bazi-MCP": {"command": "python"},
            "bazi_mcp": {"command": "python"},
        }})
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "Bazi-MCP")
        self.assertEqual(len(issues), 1)
        self.assertIn("归一化", issues[0].message)

    def test_top_level_structure_errors(self) -> None:
        with self.assertRaises(ValueError):
            parse_mcp_config([])
        with self.assertRaises(ValueError):
            parse_mcp_config({"foo": {}})
        with self.assertRaises(ValueError):
            parse_mcp_config({"mcpServers": []})

    def test_disabled_and_timeout(self) -> None:
        specs, _ = parse_mcp_config({"mcpServers": {
            "s": {"command": "python", "disabled": True, "timeout": 5},
        }})
        self.assertTrue(specs[0].disabled)
        self.assertEqual(specs[0].timeout, 5.0)

    def test_relative_cwd_resolved_against_config_dir(self) -> None:
        specs, _ = parse_mcp_config(
            {"mcpServers": {"s": {"command": "python", "cwd": "sub"}}},
            base_dir=os.path.join("D:" + os.sep, "proj"))
        self.assertTrue(os.path.isabs(specs[0].cwd))
        self.assertTrue(specs[0].cwd.endswith("sub"))

    def test_endpoint_description(self) -> None:
        stdio = McpServerSpec(name="x", key="x", transport="stdio",
                              command="python", args=("-m", "server"))
        self.assertEqual(stdio.endpoint, "python -m server")
        self.assertEqual(http_spec("x").endpoint, "https://example.com/mcp")


# ======================================================================
# 3. .mcp.json 读取
# ======================================================================
class JsonLoaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text: str, name: str = ".mcp.json") -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_missing_file_is_not_an_error(self) -> None:
        loader = JsonMcpConfigLoader(project_root=self.tmp)
        self.assertFalse(loader.exists())
        self.assertEqual(loader.load(), ([], [], ""))

    def test_explicit_missing_path_raises(self) -> None:
        loader = JsonMcpConfigLoader(os.path.join(self.tmp, "nope.json"))
        with self.assertRaises(McpConfigError):
            loader.load()

    def test_invalid_json_raises(self) -> None:
        self._write("{ not json ")
        loader = JsonMcpConfigLoader(project_root=self.tmp)
        with self.assertRaises(McpConfigError) as caught:
            loader.load()
        self.assertIn("JSON", caught.exception.message)

    def test_reads_utf8_bom_file(self) -> None:
        path = os.path.join(self.tmp, ".mcp.json")
        with open(path, "w", encoding="utf-8-sig") as handle:
            handle.write(json.dumps({"mcpServers": {"s": {"command": "python"}}}))
        loader = JsonMcpConfigLoader(project_root=self.tmp)
        specs, issues, source = loader.load()
        self.assertEqual(len(specs), 1)
        self.assertEqual(issues, [])
        self.assertEqual(source, path)

    def test_empty_file_yields_nothing(self) -> None:
        self._write("   \n")
        specs, issues, source = JsonMcpConfigLoader(project_root=self.tmp).load()
        self.assertEqual((specs, issues), ([], []))
        self.assertTrue(source.endswith(".mcp.json"))

    def test_explicit_path_takes_precedence(self) -> None:
        path = self._write(json.dumps({"mcpServers": {"s": {"command": "python"}}}),
                           name="custom.json")
        loader = JsonMcpConfigLoader(path, project_root=self.tmp)
        specs, _, source = loader.load()
        self.assertEqual(specs[0].name, "s")
        self.assertEqual(source, path)


# ======================================================================
# 4. 结果渲染
# ======================================================================
class RenderTest(unittest.TestCase):
    def _service(self) -> McpService:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        return service

    def test_text_result_is_wrapped_with_source(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")],
                            results={"t": McpCallResult(text="hello")})
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        outcome = service.call("mcp__srv__t", {})
        self.assertTrue(outcome.ok)
        self.assertIn("hello", outcome.content)
        self.assertIn("srv", outcome.content)

    def test_structured_data_appended_when_not_redundant(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")], results={
            "t": McpCallResult(text="见结构化数据", structured={"sum": 42})})
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        content = service.call("mcp__srv__t", {}).content
        self.assertIn("结构化数据", content)
        self.assertIn('"sum": 42', content)

    def test_redundant_structured_data_is_dropped(self) -> None:
        spec = http_spec("srv")
        text = "Available pages for demo:\n- 1 Overview\n- 2 Details"
        client = FakeClient(spec, tools=[tool_info("t")], results={
            "t": McpCallResult(text=text, structured={"result": text})})
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        content = service.call("mcp__srv__t", {}).content
        self.assertEqual(content.count("Available pages for demo"), 1)
        self.assertNotIn("结构化数据", content)

    def test_error_result_becomes_failure(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")], results={
            "t": McpCallResult(text="参数错误", is_error=True)})
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        outcome = service.call("mcp__srv__t", {})
        self.assertFalse(outcome.ok)
        self.assertIn("参数错误", outcome.content)
        self.assertIn("执行失败", outcome.content)

    def test_empty_result_placeholder(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")],
                            results={"t": McpCallResult()})
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        self.assertIn("空内容", service.call("mcp__srv__t", {}).content)

    def test_long_result_truncated_with_marker(self) -> None:
        spec = http_spec("srv")
        body = "A" * 5000 + "B" * 5000
        client = FakeClient(spec, tools=[tool_info("t")],
                            results={"t": McpCallResult(text=body)})
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}),
                             max_result_chars=200)
        service.load()
        content = service.call("mcp__srv__t", {}).content
        self.assertIn("中略", content)
        self.assertLess(len(content), 600)


# ======================================================================
# 5. 服务编排
# ======================================================================
class ServiceTest(unittest.TestCase):
    def test_tools_registered_with_prefix_and_routing(self) -> None:
        spec = http_spec("Bazi-MCP")
        client = FakeClient(spec, tools=[tool_info("calc", "算八字"),
                                         tool_info("luck", "运势")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        names = [t.exposed_name for t in service.tools]
        self.assertEqual(names, ["mcp__bazi-mcp__calc", "mcp__bazi-mcp__luck"])
        self.assertEqual(service.connected_count, 1)
        self.assertEqual(service.total_count, 1)
        self.assertIn("1/1", service.describe())

    def test_call_forwards_original_tool_name_and_arguments(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("do_it")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        service.call("mcp__srv__do_it", {"x": 1})
        self.assertEqual(client.calls, [("do_it", {"x": 1})])

    def test_unknown_tool_returns_failure_with_available(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("a")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        outcome = service.call("mcp__srv__nope", {})
        self.assertFalse(outcome.ok)
        self.assertIn("mcp__srv__a", outcome.content)

    def test_same_tool_name_across_servers_does_not_collide(self) -> None:
        spec_a, spec_b = http_spec("A"), http_spec("B")
        client_a = FakeClient(spec_a, tools=[tool_info("search")])
        client_b = FakeClient(spec_b, tools=[tool_info("search")])
        service = McpService(
            FakeLoader([spec_a, spec_b]),
            FakeFactory({spec_a.key: client_a, spec_b.key: client_b}))
        service.load()
        names = sorted(t.exposed_name for t in service.tools)
        self.assertEqual(names, ["mcp__a__search", "mcp__b__search"])

    def test_one_server_failure_is_isolated(self) -> None:
        good, bad = http_spec("Good"), http_spec("Bad")
        good_client = FakeClient(good, tools=[tool_info("ok")])
        bad_client = FakeClient(bad, error=McpConnectionError("Bad", "连接被拒绝"))
        factory = FakeFactory({good.key: good_client, bad.key: bad_client})
        service = McpService(FakeLoader([good, bad]), factory)
        service.load()

        self.assertEqual([t.exposed_name for t in service.tools], ["mcp__good__ok"])
        self.assertEqual(service.connected_count, 1)
        self.assertEqual(service.total_count, 2)
        notices = service.notices()
        self.assertTrue(any("Bad" in n for n in notices))
        self.assertIn("未连接", service.describe())
        # 失败的客户端要关闭，避免泄漏子进程
        self.assertTrue(bad_client.closed)

    def test_failure_when_factory_raises(self) -> None:
        spec = http_spec("X")
        service = McpService(FakeLoader([spec]), FakeFactory({}))
        service.load()
        self.assertEqual(service.tools, [])
        self.assertTrue(any("X" in n for n in service.notices()))

    def test_disabled_server_never_connects(self) -> None:
        spec = McpServerSpec(name="off", key="off", transport="http",
                             url="https://x/mcp", disabled=True)
        factory = FakeFactory({})
        service = McpService(FakeLoader([spec]), factory)
        service.load()
        self.assertEqual(factory.created, [])
        self.assertEqual(service.tools, [])
        # 被禁用的服务器不算「失败」，不进 notices
        self.assertEqual(service.notices(), [])

    def test_config_error_recorded_without_crash(self) -> None:
        service = McpService(
            FakeLoader([], error=McpConfigError("配置坏了")),
            FakeFactory({}))
        service.load()
        self.assertEqual(service.config_error, "配置坏了")
        self.assertIn("配置错误", service.describe())
        self.assertEqual(service.notices(), ["配置坏了"])

    def test_config_issues_surface_as_notices(self) -> None:
        spec = http_spec("ok")
        client = FakeClient(spec, tools=[])
        issues = [McpConfigIssue(server="broken", message="缺少 url")]
        service = McpService(FakeLoader([spec], issues=issues),
                             FakeFactory({spec.key: client}))
        service.load()
        self.assertTrue(any("broken" in n and "缺少 url" in n for n in service.notices()))

    def test_call_error_translated_to_failure(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")],
                            call_error=McpToolCallError("srv", "t", "连接已断开"))
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        outcome = service.call("mcp__srv__t", {})
        self.assertFalse(outcome.ok)
        self.assertIn("连接已断开", outcome.content)

    def test_call_on_disconnected_server(self) -> None:
        """工具注册过、但连接已关闭（例如服务已 close）：要给出「未连接」而不是「未知工具」。"""
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        service.close()
        outcome = service.call("mcp__srv__t", {})
        self.assertFalse(outcome.ok)
        self.assertIn("未连接", outcome.content)

    def test_reserved_local_tool_name_avoids_collision(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load(reserved_names=["mcp__srv__t"])
        self.assertEqual(service.tools[0].exposed_name, "mcp__srv__t_2")

    def test_close_closes_all_clients(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        service.close()
        self.assertTrue(client.closed)
        service.close()  # 幂等

    def test_load_is_idempotent(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")])
        loader = FakeLoader([spec])
        service = McpService(loader, FakeFactory({spec.key: client}))
        service.load()
        service.load()
        self.assertEqual(len(service.tools), 1)

    def test_describe_empty_without_config(self) -> None:
        service = McpService(None, None)
        service.load()
        self.assertEqual(service.describe(), "")
        self.assertEqual(service.tools, [])

    def test_auto_corrected_endpoint_is_reported(self) -> None:
        """地址被补全后连上了：要告诉用户该把配置改成本次实际使用的地址。"""
        spec = http_spec("Bazi")
        corrected = spec.url + "/mcp"
        client = FakeClient(spec, tools=[tool_info("t")], resolved=corrected)
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        notices = service.notices()
        self.assertTrue(any("自动补全" in n and corrected in n for n in notices), notices)

    def test_unchanged_endpoint_has_no_notice(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        self.assertEqual(service.notices(), [])


# ======================================================================
# 6. 工具包装
# ======================================================================
class ToolTest(unittest.TestCase):
    def test_spec_carries_raw_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "where": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
            "required": ["where"],
        }
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[McpToolInfo("t", "描述", schema)])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        tool = build_mcp_tools(service)[0]

        self.assertEqual(tool.spec.name, "mcp__srv__t")
        self.assertEqual(tool.spec.to_json_schema(), schema)
        # 嵌套 schema 必须原样进 OpenAI tools 数组
        openai_tool = tool.spec.to_openai_tool()
        self.assertEqual(openai_tool["function"]["parameters"], schema)

    def test_description_mentions_source_and_original_name(self) -> None:
        spec = http_spec("Bazi-MCP")
        client = FakeClient(spec, tools=[McpToolInfo("paipan", "根据生辰八字排盘")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        text = build_mcp_tools(service)[0].spec.description
        self.assertIn("根据生辰八字排盘", text)
        self.assertIn("Bazi-MCP", text)
        self.assertIn("paipan", text)

    def test_empty_description_gets_fallback(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[McpToolInfo("t", "")])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        tool = build_mcp_tools(service)[0]
        self.assertIn("未提供描述", tool.spec.description)

    def test_normalize_input_schema(self) -> None:
        self.assertEqual(normalize_input_schema(None),
                         {"type": "object", "properties": {}})
        self.assertEqual(normalize_input_schema({}),
                         {"type": "object", "properties": {}})
        self.assertEqual(normalize_input_schema({"properties": {"a": {"type": "string"}}}),
                         {"type": "object", "properties": {"a": {"type": "string"}}})

    def test_invoke_delegates_to_service(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[tool_info("t")],
                            results={"t": McpCallResult(text="结果")})
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        tool = build_mcp_tools(service)[0]
        outcome = tool.invoke({"q": "x"})
        self.assertTrue(outcome.ok)
        self.assertIn("结果", outcome.content)
        self.assertEqual(client.calls, [("t", {"q": "x"})])

    def test_describe_tool_truncates_long_description(self) -> None:
        spec = http_spec("srv")
        client = FakeClient(spec, tools=[McpToolInfo("t", "描" * 3000)])
        service = McpService(FakeLoader([spec]), FakeFactory({spec.key: client}))
        service.load()
        text = describe_tool(service.tools[0])
        self.assertLess(len(text), 1700)
        self.assertIn("已截断", text)


# ======================================================================
# 7. SDK 结果转换（不联网）
# ======================================================================
class _Block:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _Result:
    def __init__(self, content, structured=None, is_error=False) -> None:
        self.content = content
        self.structured_content = structured
        self.is_error = is_error


class ResultConversionTest(unittest.TestCase):
    def test_text_blocks_joined(self) -> None:
        result = to_call_result(_Result([_Block(type="text", text="a"),
                                        _Block(type="text", text="b")]))
        self.assertEqual(result.text, "a\n\nb")
        self.assertFalse(result.is_error)

    def test_image_block_rendered_as_placeholder(self) -> None:
        result = to_call_result(_Result([
            _Block(type="image", data="A" * 4000, mime_type="image/png")]))
        self.assertIn("图片", result.text)
        self.assertIn("image/png", result.text)
        self.assertNotIn("AAAA", result.text)

    def test_resource_link(self) -> None:
        result = to_call_result(_Result([
            _Block(type="resource_link", uri="file:///a.txt", name="a.txt")]))
        self.assertIn("a.txt", result.text)

    def test_embedded_resource_with_text(self) -> None:
        resource = _Block(uri="file:///a.md", text="内容", mime_type="text/markdown")
        result = to_call_result(_Result([_Block(type="resource", resource=resource)]))
        self.assertIn("内容", result.text)

    def test_structured_and_error_flags(self) -> None:
        result = to_call_result(_Result([], structured={"k": 1}, is_error=True))
        self.assertEqual(result.structured, {"k": 1})
        self.assertTrue(result.is_error)

    def test_unknown_block_does_not_crash(self) -> None:
        result = to_call_result(_Result([_Block(type="weird")]))
        self.assertIn("未支持", result.text)


# ======================================================================
# 8. 真实链路（stdio，起子进程）
# ======================================================================
class StdioIntegrationTest(unittest.TestCase):
    """用官方 SDK 连接本地 stdio 测试服务器，验证端到端链路。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "mcp_echo_server.py")
        if not os.path.isfile(cls.script):
            raise unittest.SkipTest("缺少测试用 MCP Server 脚本")
        cls.spec = McpServerSpec(
            name="Echo", key="echo", transport="stdio",
            command=sys.executable, args=(cls.script,), timeout=30)
        cls.client = SdkMcpClient(cls.spec, connect_timeout=30)
        cls.handshake = cls.client.connect()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_handshake_lists_tools(self) -> None:
        names = {tool.name for tool in self.handshake.tools}
        self.assertEqual(names, {"echo", "add", "boom"})
        echo_tool = next(t for t in self.handshake.tools if t.name == "echo")
        self.assertIn("text", echo_tool.input_schema.get("properties", {}))

    def test_call_text_tool(self) -> None:
        result = self.client.call_tool("echo", {"text": "你好"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.text, "echo: 你好")

    def test_call_structured_tool(self) -> None:
        result = self.client.call_tool("add", {"a": 2, "b": 40})
        self.assertFalse(result.is_error)
        self.assertIn("42", result.text)

    def test_server_side_error_flag(self) -> None:
        result = self.client.call_tool("boom", {})
        self.assertTrue(result.is_error)
        self.assertIn("boom", result.text)

    def test_close_is_idempotent_and_stops_thread(self) -> None:
        extra = SdkMcpClient(
            McpServerSpec(name="Echo2", key="echo2", transport="stdio",
                          command=sys.executable, args=(self.script,), timeout=30),
            connect_timeout=30)
        extra.connect()
        thread = extra._thread
        extra.close()
        extra.close()
        self.assertIsNotNone(thread)
        self.assertFalse(thread.is_alive())

    def test_service_end_to_end_registers_and_calls(self) -> None:
        loader = FakeLoader([self.spec])
        service = McpService(loader, SdkMcpClientFactoryForTest())
        try:
            service.load()
            names = {t.exposed_name for t in service.tools}
            self.assertEqual(names, {"mcp__echo__echo", "mcp__echo__add",
                                     "mcp__echo__boom"})
            outcome = service.call("mcp__echo__echo", {"text": "hi"})
            self.assertTrue(outcome.ok)
            self.assertIn("echo: hi", outcome.content)
            failed = service.call("mcp__echo__boom", {})
            self.assertFalse(failed.ok)
        finally:
            service.close()


class SdkMcpClientFactoryForTest(McpClientFactory):
    """复用同一个真实客户端，避免集成测试重复拉子进程。"""

    def __init__(self) -> None:
        self._client: SdkMcpClient | None = None

    def create(self, spec: McpServerSpec) -> McpClient:
        if self._client is None:
            self._client = SdkMcpClient(spec, connect_timeout=30)
        return self._client


class DeferredContainerLoadTest(unittest.TestCase):
    """TUI 的「先出界面，再后台连 MCP」：容器延迟加载后工具应可立即使用。"""

    def test_defer_then_load_registers_tools(self) -> None:
        from core.container import AppContainer
        from core.infrastructure.llm.mock import ScriptedLLMClient

        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "mcp_echo_server.py")
        if not os.path.isfile(script):
            raise unittest.SkipTest("缺少测试用 MCP Server 脚本")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, ".mcp.json"), "w", encoding="utf-8") as handle:
            json.dump({"mcpServers": {"echo": {
                "type": "stdio", "command": sys.executable, "args": [script]}}}, handle)

        container = AppContainer.build(
            llm=ScriptedLLMClient([{"text": "x"}]),
            project_root=tmp.name,
            defer_mcp=True,
            overrides={"mcp_enabled": True},
        )
        try:
            self.assertFalse(container.mcp_loaded)
            self.assertFalse([n for n in container.tools.names() if n.startswith("mcp__")])

            snapshot = container.load_mcp()
            self.assertTrue(container.mcp_loaded)
            self.assertEqual(snapshot.connected_count, 1)
            self.assertIn("mcp__echo__echo", container.tools.names())
            # 工具声明缓存已刷新：下一轮对话模型就能看到这些工具
            self.assertTrue(any("mcp__echo__echo" in json.dumps(spec, ensure_ascii=False)
                                for spec in container.agent.tool_specs()))
        finally:
            container.close()


if __name__ == "__main__":
    unittest.main()
