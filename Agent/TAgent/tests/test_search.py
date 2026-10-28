# -*- coding: utf-8 -*-
"""联网搜索功能（searchwdb）的单元测试：不依赖真实网络。

覆盖：配置加载、响应解析、错误映射（网络/HTTP/API/缺配置）、
结果整理格式、工具 schema 与工具护栏。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core.config.loader import load_settings
from core.config.models import SearchSettings
from core.domain.search import SearchReport, SearchSource
from core.errors import SearchError
from core.infrastructure.search.baidu_qianfan import BaiduQianfanSearchClient
from core.ports.search import SearchClient
from core.services.websearch import WebSearchService
from core.tools.builtin_search import build_search_tools
from core.tools.registry import InMemoryToolRegistry

API_KEY = "bce-v3/TEST-KEY"

# 一份典型的千帆智能搜索响应（含引用角标与参考来源）
SAMPLE_RESPONSE = {
    "id": "as-1",
    "object": "chat.completion",
    "choices": [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": "今天北京晴[[source_group_web_1]]，气温 12 度[[source_group_web_2]]。",
        },
        "finish_reason": "stop",
    }],
    "search_results": [
        {"index": 1, "title": "北京天气预报", "url": "https://example.com/weather"},
        {"index": 2, "title": "", "url": "https://example.com/temp"},
        "noise",  # 非法条目应被跳过
    ],
}


# ----------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def close(self):
        pass


class FakeSession:
    """模拟 requests.Session：可注入响应或异常，并记录最后一次请求。"""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.last_request = None

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.last_request = {"url": url, "headers": headers,
                             "json": json, "timeout": timeout}
        if self._exc is not None:
            raise self._exc
        return self._response

    def close(self):
        pass


class FakeSearchClient(SearchClient):
    """可注入报告或异常的假搜索客户端。"""

    def __init__(self, report=None, exc=None):
        self._report = report
        self._exc = exc
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self._exc is not None:
            raise self._exc
        return self._report


def make_settings(api_key=API_KEY):
    return SearchSettings(baidu_qianfan_api_key=api_key)


def make_client(session, api_key=API_KEY):
    return BaiduQianfanSearchClient(make_settings(api_key), session=session)


# ----------------------------------------------------------------------
# 配置加载
# ----------------------------------------------------------------------
class SearchConfigTest(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".ini", delete=False, encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            settings, _ = load_settings(config_path=path)
            return settings
        finally:
            os.unlink(path)

    def test_search_section_loaded(self) -> None:
        settings = self._load(
            "[llm]\nbase_url = https://x\napi_key = k\nmodel = m\n"
            "[search]\nbaidu_qianfan_api_key = %s\ntimeout = 15\n" % API_KEY)
        self.assertTrue(settings.search.configured)
        self.assertEqual(settings.search.baidu_qianfan_api_key, API_KEY)
        self.assertEqual(settings.search.timeout, 15.0)
        self.assertTrue(settings.search.chat_completions_url.endswith(
            "/v2/ai_search/chat/completions"))
        # [search] 段不影响 LLM 配置
        self.assertEqual(settings.llm.model, "m")

    def test_missing_search_section_ok(self) -> None:
        settings = self._load("[llm]\nbase_url = https://x\napi_key = k\nmodel = m\n")
        self.assertFalse(settings.search.configured)
        self.assertEqual(settings.search.baidu_qianfan_api_key, "")


# ----------------------------------------------------------------------
# 响应解析
# ----------------------------------------------------------------------
class ParseResponseTest(unittest.TestCase):
    def test_parse_success(self) -> None:
        report = BaiduQianfanSearchClient._parse(SAMPLE_RESPONSE)
        # 摘要去掉了引用角标
        self.assertEqual(report.summary, "今天北京晴，气温 12 度。")
        # 标题为空的来源用 URL 兜底；非法条目被跳过
        self.assertEqual(len(report.sources), 2)
        self.assertEqual(report.sources[0].title, "北京天气预报")
        self.assertEqual(report.sources[0].url, "https://example.com/weather")
        self.assertEqual(report.sources[1].title, "https://example.com/temp")

    def test_parse_bce_style_error(self) -> None:
        with self.assertRaises(SearchError) as ctx:
            BaiduQianfanSearchClient._parse(
                {"error_code": 110, "error_msg": "Invalid api key"})
        self.assertIn("Invalid api key", str(ctx.exception))

    def test_parse_openai_style_error(self) -> None:
        with self.assertRaises(SearchError) as ctx:
            BaiduQianfanSearchClient._parse(
                {"error": {"message": "quota exceeded"}})
        self.assertIn("quota exceeded", str(ctx.exception))

    def test_parse_empty_result(self) -> None:
        with self.assertRaises(SearchError) as ctx:
            BaiduQianfanSearchClient._parse({"choices": []})
        self.assertIn("未返回有效结果", str(ctx.exception))

    def test_parse_not_dict(self) -> None:
        with self.assertRaises(SearchError):
            BaiduQianfanSearchClient._parse(["unexpected"])


# ----------------------------------------------------------------------
# 客户端：网络与 HTTP 错误映射
# ----------------------------------------------------------------------
class ClientErrorMappingTest(unittest.TestCase):
    def test_missing_api_key(self) -> None:
        client = make_client(FakeSession(), api_key="")
        with self.assertRaises(SearchError) as ctx:
            client.search("任意问题")
        self.assertIn("缺少搜索配置", str(ctx.exception))

    def test_timeout(self) -> None:
        client = make_client(FakeSession(exc=requests.exceptions.Timeout("t")))
        with self.assertRaises(SearchError) as ctx:
            client.search("q")
        self.assertIn("超时", str(ctx.exception))

    def test_connection_error(self) -> None:
        client = make_client(FakeSession(exc=requests.exceptions.ConnectionError("c")))
        with self.assertRaises(SearchError) as ctx:
            client.search("q")
        self.assertIn("无法访问搜索接口", str(ctx.exception))

    def test_http_error(self) -> None:
        session = FakeSession(response=FakeResponse(status_code=401, text="unauthorized"))
        client = make_client(session)
        with self.assertRaises(SearchError) as ctx:
            client.search("q")
        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertIn("unauthorized", str(ctx.exception))

    def test_invalid_json_body(self) -> None:
        session = FakeSession(response=FakeResponse(status_code=200, text="<html>"))
        client = make_client(session)
        with self.assertRaises(SearchError) as ctx:
            client.search("q")
        self.assertIn("无法解析", str(ctx.exception))

    def test_success_request_shape(self) -> None:
        session = FakeSession(response=FakeResponse(json_data=SAMPLE_RESPONSE))
        client = make_client(session)
        report = client.search("北京天气")
        self.assertIsInstance(report, SearchReport)
        # 请求形态：正确的 URL、鉴权头、非流式、用户消息即查询词
        req = session.last_request
        self.assertTrue(req["url"].endswith("/v2/ai_search/chat/completions"))
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + API_KEY)
        self.assertFalse(req["json"]["stream"])
        self.assertEqual(req["json"]["messages"][0]["content"], "北京天气")


# ----------------------------------------------------------------------
# 业务服务：输入校验、缺配置、结果整理
# ----------------------------------------------------------------------
class WebSearchServiceTest(unittest.TestCase):
    def test_empty_query_rejected(self) -> None:
        service = WebSearchService(make_settings(), FakeSearchClient())
        with self.assertRaises(SearchError) as ctx:
            service.search_text("   ")
        self.assertIn("不能为空", str(ctx.exception))

    def test_missing_config_message(self) -> None:
        service = WebSearchService(make_settings(api_key=""), FakeSearchClient())
        with self.assertRaises(SearchError) as ctx:
            service.search_text("任意问题")
        self.assertIn("baidu_qianfan_api_key", str(ctx.exception))

    def test_format_report(self) -> None:
        report = SearchReport(
            summary="摘要内容",
            sources=[SearchSource(title="标题A", url="https://a.example"),
                     SearchSource(title="标题B", url="")])
        text = WebSearchService.format_report(report)
        self.assertIn("【搜索摘要】", text)
        self.assertIn("摘要内容", text)
        self.assertIn("【参考来源】", text)
        self.assertIn("1. 标题A", text)
        self.assertIn("https://a.example", text)
        self.assertIn("2. 标题B", text)

    def test_search_text_success(self) -> None:
        report = SearchReport(summary="ok", sources=[SearchSource("T", "https://t")])
        client = FakeSearchClient(report=report)
        service = WebSearchService(make_settings(), client)
        text = service.search_text("  问题  ")
        self.assertEqual(client.queries, ["问题"])  # 已去除首尾空白
        self.assertIn("ok", text)
        self.assertIn("https://t", text)

    def test_client_error_propagates(self) -> None:
        service = WebSearchService(
            make_settings(), FakeSearchClient(exc=SearchError("搜索请求超时")))
        with self.assertRaises(SearchError) as ctx:
            service.search_text("q")
        self.assertIn("搜索请求超时", str(ctx.exception))


# ----------------------------------------------------------------------
# 工具适配器：schema、参数校验、护栏
# ----------------------------------------------------------------------
class SearchToolTest(unittest.TestCase):
    def _registry(self, client) -> InMemoryToolRegistry:
        service = WebSearchService(make_settings(), client)
        registry = InMemoryToolRegistry()
        registry.register_many(build_search_tools(service))
        return registry

    def test_tool_schema(self) -> None:
        registry = self._registry(FakeSearchClient())
        self.assertIn("searchwdb", registry.names())
        spec = registry.specs()
        self.assertEqual(len(spec), 1)
        function = spec[0]["function"]
        self.assertEqual(function["name"], "searchwdb")
        self.assertIn("query", function["parameters"]["properties"])
        self.assertEqual(function["parameters"]["required"], ["query"])

    def test_invoke_success(self) -> None:
        report = SearchReport(summary="答案", sources=[SearchSource("T", "https://t")])
        registry = self._registry(FakeSearchClient(report=report))
        outcome = registry.invoke_by_arguments("searchwdb", {"query": "问题"})
        self.assertTrue(outcome.ok)
        self.assertIn("答案", outcome.content)
        self.assertIn("https://t", outcome.content)

    def test_invoke_missing_query(self) -> None:
        registry = self._registry(FakeSearchClient())
        outcome = registry.invoke_by_arguments("searchwdb", {})
        self.assertFalse(outcome.ok)
        self.assertIn("query", outcome.content)

    def test_invoke_search_error_becomes_failure(self) -> None:
        registry = self._registry(FakeSearchClient(exc=SearchError("无法访问搜索接口")))
        outcome = registry.invoke_by_arguments("searchwdb", {"query": "问题"})
        # 错误被护栏转成失败结果返回给模型，而不是中断会话
        self.assertFalse(outcome.ok)
        self.assertIn("无法访问搜索接口", outcome.content)


if __name__ == "__main__":
    unittest.main()
