# -*- coding: utf-8 -*-
"""百度千帆智能搜索（生成版）适配器。

接口：`{base_url}/chat/completions`（OpenAI 兼容协议），
默认地址为 `https://qianfan.baidubce.com/v2/ai_search`。

与 `openai_compat.py` 一样，本模块负责：
    1. 隔离第三方依赖 `requests`；
    2. 把 HTTP / JSON 层的各种异常统一翻译成 `core.errors.SearchError`，
       核心服务与工具层因此完全不知道 HTTP 的存在。
"""

from __future__ import annotations

import re
from typing import Any

import requests

from ...config.models import SearchSettings
from ...domain.search import SearchReport, SearchSource
from ...errors import SearchError
from ...ports.search import SearchClient

#: 错误响应体的最大保留长度（便于定位问题，又不至于刷屏）
MAX_ERROR_BODY = 500

#: 智能搜索生成版在正文中插入的引用角标，例如 [[source_group_web_1]]
_CITATION_RE = re.compile(r"\[\[source_group_web_\d+\]\]")


class BaiduQianfanSearchClient(SearchClient):
    """百度千帆智能搜索（生成版）客户端。"""

    def __init__(self, settings: SearchSettings,
                 session: requests.Session | None = None) -> None:
        self._settings = settings
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    @property
    def settings(self) -> SearchSettings:
        return self._settings

    def search(self, query: str) -> SearchReport:
        """调用智能搜索生成 API，返回结构化搜索报告。

        Raises:
            SearchError: 缺少配置、网络失败、HTTP 错误、API 返回错误、
                         响应无法解析或没有可用结果。
        """
        if not self._settings.configured:
            raise SearchError(
                "缺少搜索配置：请在 config.ini 的 [search] 段配置 baidu_qianfan_api_key")

        url = self._settings.chat_completions_url
        headers = {
            "Authorization": "Bearer " + self._settings.baidu_qianfan_api_key,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": query}],
            "stream": False,
        }

        try:
            response = self._session.post(
                url, headers=headers, json=payload, timeout=self._settings.timeout)
        except requests.exceptions.Timeout as exc:
            raise SearchError("搜索请求超时（%s 秒）：%s" % (self._settings.timeout, exc))
        except requests.exceptions.ConnectionError as exc:
            raise SearchError("无法访问搜索接口 %s：%s" % (url, exc))
        except requests.exceptions.RequestException as exc:
            raise SearchError("搜索请求异常：%s" % exc)

        try:
            if response.status_code >= 400:
                raise SearchError("搜索接口返回 HTTP %d：%s"
                                  % (response.status_code, _safe_body(response)))
            try:
                data = response.json()
            except ValueError:
                raise SearchError("搜索接口返回了无法解析的响应：%s" % _safe_body(response))
        finally:
            response.close()

        return self._parse(data)

    # ------------------------------------------------------------------
    @staticmethod
    def _parse(data: Any) -> SearchReport:
        """把 API 响应解析为 `SearchReport`（对字段缺失保持宽容）。"""
        if not isinstance(data, dict):
            raise SearchError("搜索接口响应格式不符合预期（不是 JSON 对象）")

        # 百度 BCE 风格错误：{"error_code": ..., "error_msg": ...}
        error_code = data.get("error_code")
        error_msg = data.get("error_msg")
        if error_code or error_msg:
            raise SearchError("搜索 API 返回错误（%s）：%s" % (error_code, error_msg))

        # OpenAI 风格错误：{"error": {"message": ...}}
        error = data.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise SearchError("搜索 API 返回错误：%s" % (message or "未知错误"))

        # 摘要：choices[0].message.content（去掉引用角标）
        summary = ""
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message, dict):
                summary = (message.get("content") or "").strip()
        summary = _CITATION_RE.sub("", summary).strip()

        # 参考来源：顶层 search_results 数组
        sources: list[SearchSource] = []
        for item in data.get("search_results") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if title or url:
                sources.append(SearchSource(title=title or url, url=url))

        if not summary and not sources:
            raise SearchError("搜索 API 未返回有效结果，请更换关键词后重试")

        return SearchReport(summary=summary, sources=sources)

    # ------------------------------------------------------------------
    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover
            pass


def _safe_body(response: requests.Response) -> str:
    """尽量读取响应体文本，失败时返回空串。"""
    try:
        return response.text[:MAX_ERROR_BODY]
    except Exception:  # pragma: no cover - 读取失败不影响错误上报
        return ""


__all__ = ["BaiduQianfanSearchClient"]
