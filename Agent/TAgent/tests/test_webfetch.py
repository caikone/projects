# -*- coding: utf-8 -*-
"""网页抓取（fetchWeb）工具的单元测试。

三层分别验证：
    1. `WebFetchService`：URL 校验 + 文本渲染（纯逻辑，离线）。
    2. `CurlTrafilaturaFetcher`：真实 curl 抓取（联网，用稳定站点）。
    3. 工具注册与异常映射：适配器是否把业务异常转成 `ToolOutcome.failure`。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.domain.webpage import WebDocument  # noqa: E402
from core.errors import (  # noqa: E402
    InvalidURLError,
    WebFetchConnectionError,
    WebFetchEmptyError,
    WebFetchHTTPError,
    WebFetchParseError,
    WebFetchTimeoutError,
)
from core.infrastructure.webpage.curl_trafilatura import CurlTrafilaturaFetcher  # noqa: E402
from core.ports.webpage import WebPageFetcher  # noqa: E402
from core.services.webfetch import WebFetchService  # noqa: E402
from core.tools.builtin_webfetch import build_webfetch_tools  # noqa: E402


class _StubFetcher(WebPageFetcher):
    """按脚本回放的假抓取器：让服务层测试完全离线。"""

    def __init__(self, result=None, error=None) -> None:
        self._result = result
        self._error = error
        self.calls: list[str] = []

    def fetch(self, url: str) -> WebDocument:
        self.calls.append(url)
        if self._error is not None:
            raise self._error
        return self._result or WebDocument(url=url, markdown="正文", html_bytes=100)


# ==========================================================================
# 服务层：URL 校验 + 渲染
# ==========================================================================
class ValidateUrlTest(unittest.TestCase):
    def test_valid_urls(self) -> None:
        for url in ("https://example.com", "http://a.b/c?x=1#frag"):
            self.assertEqual(WebFetchService.validate_url(url), url)

    def test_rejects_non_http(self) -> None:
        for url in ("ftp://x.com", "www.baidu.com", "", "   "):
            with self.assertRaises(InvalidURLError):
                WebFetchService.validate_url(url)


class RenderDocumentTest(unittest.TestCase):
    def test_render_full_document(self) -> None:
        doc = WebDocument(
            url="https://a.com/",
            final_url="https://a.com/",
            title="标题",
            markdown="第一段\n\n- 列表",
            html_bytes=1000,
            status_code=200,
        )
        text = WebFetchService(_StubFetcher()).render_document(doc)
        self.assertIn("# 标题", text)
        self.assertIn("第一段", text)
        self.assertIn("【来源】 https://a.com/", text)
        self.assertIn("HTML 1000 字节", text)

    def test_render_truncates_long_body(self) -> None:
        doc = WebDocument(url="https://a.com/", markdown="x" * 5000, html_bytes=10000)
        text = WebFetchService(_StubFetcher(), max_chars=100).render_document(doc)
        self.assertIn("已截断至 100 字符", text)
        self.assertLessEqual(len(text.split("x" * 100)[0]), 100 + 20)


# ==========================================================================
# 适配器：真实 curl 抓取（联网）
# ==========================================================================
class CurlFetcherLiveTest(unittest.TestCase):
    """用稳定站点做冒烟测试；网络不可用时自动跳过。

    注意不要用 baidu.com 这类首页做断言：它会间歇性返回反爬/空壳页，
    提取结果为空是**正确行为**（WebFetchEmptyError），不能作为测试基准。
    """

    @classmethod
    def setUpClass(cls) -> None:
        fetcher = CurlTrafilaturaFetcher()
        try:
            doc = fetcher.fetch("https://docs.python.org/3/tutorial/introduction.html")
        except WebFetchConnectionError:
            raise unittest.SkipTest("网络不可用，跳过联网测试")
        cls.fetcher = fetcher

    def test_fetch_real_page(self) -> None:
        doc = self.fetcher.fetch("https://docs.python.org/3/tutorial/introduction.html")
        self.assertEqual(doc.status_code, 200)
        self.assertGreater(doc.html_bytes, 0)
        self.assertGreater(len(doc.markdown), 1000, "文档页正文应完整提取")
        self.assertIn("An Informal Introduction to Python", doc.markdown)

    def test_404_raises_http_error(self) -> None:
        with self.assertRaises(WebFetchHTTPError):
            self.fetcher.fetch("https://example.com/definitely-not-exist-xyz-123")

    def test_bad_host_raises_connection_error(self) -> None:
        with self.assertRaises((WebFetchConnectionError, WebFetchTimeoutError)):
            self.fetcher.fetch("https://no-such-host-xyz-123.invalid/")


# ==========================================================================
# 异常映射（离线，用假 curl）
# ==========================================================================
class FetcherErrorMappingTest(unittest.TestCase):
    def test_missing_curl(self) -> None:
        fetcher = CurlTrafilaturaFetcher(curl_path="C:/no/such/curl.exe")
        with self.assertRaises(WebFetchConnectionError):
            fetcher.fetch("https://example.com")

    def test_empty_body(self) -> None:
        # 用子类覆盖 _download，返回空 HTML，验证「无正文」异常
        fetcher = CurlTrafilaturaFetcher()
        fetcher._download = lambda url: (b"", 200, url)  # type: ignore[assignment]
        with self.assertRaises(WebFetchEmptyError):
            fetcher.fetch("https://example.com")

    def test_parse_failure_maps_to_parse_error(self) -> None:
        fetcher = CurlTrafilaturaFetcher()
        # 非 2xx 但状态码 < 400 的边界之外：直接让提取阶段抛异常
        fetcher._download = lambda url: (b"<html>" + b"\x00" * 100, 200, url)  # type: ignore[assignment]
        # 二进制内容无法解析，应落入 WebFetchEmptyError（正文过短/无正文）而非崩溃
        with self.assertRaises((WebFetchEmptyError, WebFetchParseError)):
            fetcher.fetch("https://example.com")


class CascadeExtractionTest(unittest.TestCase):
    """多策略级联提取的回归测试。

    背景：trafilatura 默认参数在国内高校 CMS（table 布局 + 图片正文）页面上
    会把整篇正文判成 None，而 favor_recall 能完整提取——
    因此必须级联尝试并取最长结果，不能只信默认参数。
    """

    def setUp(self) -> None:
        self.fetcher = CurlTrafilaturaFetcher()

    def test_picks_longest_across_strategies(self) -> None:
        import trafilatura
        from unittest import mock

        long_text = "正文" * 500
        with mock.patch.object(trafilatura, "extract",
                               side_effect=[None, "短", long_text]) as extract:
            markdown = self.fetcher._extract_markdown(trafilatura, "<html/>")
        self.assertEqual(extract.call_count, 3, "前两级不够长时必须继续放宽")
        self.assertEqual(markdown, long_text)

    def test_stops_early_when_long_enough(self) -> None:
        import trafilatura
        from unittest import mock

        good = "x" * 2000
        with mock.patch.object(trafilatura, "extract", return_value=good) as extract:
            markdown = self.fetcher._extract_markdown(trafilatura, "<html/>")
        self.assertEqual(extract.call_count, 1, "第一级已足够长时不应继续放宽")
        self.assertEqual(markdown, good)

    def test_all_strategies_fail_raises_parse_error(self) -> None:
        import trafilatura
        from unittest import mock

        with mock.patch.object(trafilatura, "extract",
                               side_effect=[Exception("a"), Exception("b"), Exception("c")]):
            with self.assertRaises(WebFetchParseError):
                self.fetcher._extract_markdown(trafilatura, "<html/>")

    def test_real_cms_page_extracts_full_body(self) -> None:
        """真实高校 CMS 页面（table 布局）：修复前默认参数提取为 0 字符。"""
        try:
            document = self.fetcher.fetch(
                "https://nsu.edu.cn/HTML/teach/2025/11/article_4411.html")
        except WebFetchConnectionError:
            self.skipTest("网络不可用，跳过联网测试")
        self.assertGreater(len(document.markdown), 3000,
                           "该页正文应完整提取（约 7600 字符）")
        self.assertIn("最受欢迎教师", document.markdown)


# ==========================================================================
# 工具适配器：schema + 失败回传
# ==========================================================================
class WebFetchToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = build_webfetch_tools(
            WebFetchService(_StubFetcher(WebDocument(
                url="https://a.com/", title="T", markdown="正文内容", html_bytes=50))))

    def test_schema(self) -> None:
        spec = self.tool[0].spec
        self.assertEqual(spec.name, "fetchWeb")
        self.assertIn("url", spec.params)
        self.assertEqual(spec.required, ["url"])

    def test_invoke_success(self) -> None:
        outcome = self.tool[0].invoke({"url": "https://a.com/"})
        self.assertTrue(outcome.ok)
        self.assertIn("正文内容", outcome.content)

    def test_missing_param_returns_failure(self) -> None:
        outcome = self.tool[0].invoke({})
        self.assertFalse(outcome.ok)
        self.assertIn("url", outcome.content)

    def test_invalid_url_returns_failure(self) -> None:
        tool = build_webfetch_tools(WebFetchService(_StubFetcher()))[0]
        outcome = tool.invoke({"url": "ftp://x.com"})
        self.assertFalse(outcome.ok)
        self.assertIn("http", outcome.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
