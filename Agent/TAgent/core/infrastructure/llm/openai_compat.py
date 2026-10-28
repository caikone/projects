# -*- coding: utf-8 -*-
"""OpenAI 兼容协议的 LLM 适配器（基于 requests + SSE）。

这是 import `requests` 的基础设施适配器之一（另一个是
`infrastructure/search/baidu_qianfan.py`），也是唯一把 HTTP 异常
翻译为 `core.errors.LLMError` 的地方。
核心服务因此完全不知道 HTTP 的存在。

已验证兼容：OpenAI / DeepSeek / 通义千问兼容模式 / 本地 vLLM / Ollama(openai 兼容) 等。
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import requests

from ...config.models import LLMSettings
from ...errors import (
    LLMConnectionError,
    LLMError,
    LLMHTTPError,
    LLMProtocolError,
    LLMStreamError,
    LLMTimeoutError,
)
from ...ports.llm import LLMChunk, LLMClient, LLMRequest, ToolCallDelta

MAX_ERROR_BODY = 500


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容 `/chat/completions` 流式客户端。"""

    def __init__(self, settings: LLMSettings, session: requests.Session | None = None) -> None:
        self._settings = settings
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    @property
    def settings(self) -> LLMSettings:
        return self._settings

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self._settings.api_key,
            "Content-Type": "application/json",
        }

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        from ...domain.messages import to_openai_payload

        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": to_openai_payload(request.messages),
            "stream": True,
        }
        if request.tool_specs:
            payload["tools"] = request.tool_specs
        if self._settings.temperature is not None:
            payload["temperature"] = self._settings.temperature
        if self._settings.max_tokens is not None:
            payload["max_tokens"] = self._settings.max_tokens
        return payload

    # ------------------------------------------------------------------
    def stream(self, request: LLMRequest) -> Iterator[LLMChunk]:
        """发起流式请求并逐块产出。

        Raises:
            LLMTimeoutError / LLMConnectionError / LLMHTTPError: 网络与 HTTP 层错误。
            LLMStreamError: 流内返回 error 对象，或流传输中途断开。
        """
        response = self._post(request)
        try:
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue

                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                if not data:
                    continue

                try:
                    obj = json.loads(data)
                except ValueError:
                    continue  # 跳过无法解析的噪声行

                if not isinstance(obj, dict):
                    continue

                # 服务端可能在流中返回 error 对象
                error = obj.get("error")
                if error:
                    message = error.get("message") if isinstance(error, dict) else str(error)
                    raise LLMStreamError(message or "接口返回错误")

                chunk = self._to_chunk(obj)
                if chunk is not None:
                    yield chunk
        except requests.exceptions.RequestException as exc:
            # 流式读取中途断网 / 服务端提前断开：requests 抛的不是 LLMError，
            # 若不在这里翻译，会穿透 AgentService 的 `except LLMError`，
            # 表现为「回复输出到一半突然没了，界面也没有任何错误提示」。
            raise LLMStreamError("流式传输中断：%s" % exc) from exc
        finally:
            response.close()

    # ------------------------------------------------------------------
    def _post(self, request: LLMRequest) -> requests.Response:
        url = self._settings.chat_completions_url
        try:
            response = self._session.post(
                url,
                headers=self._headers(),
                json=self._payload(request),
                timeout=self._settings.timeout,
                stream=True,
            )
        except requests.exceptions.Timeout as exc:
            raise LLMTimeoutError("请求超时（%s 秒）：%s" % (self._settings.timeout, exc))
        except requests.exceptions.ConnectionError as exc:
            raise LLMConnectionError("无法访问接口地址 %s：%s" % (url, exc))
        except requests.exceptions.RequestException as exc:
            raise LLMConnectionError("请求异常：%s" % exc)

        if response.status_code >= 400:
            body = ""
            try:
                body = response.text[:MAX_ERROR_BODY]
            except Exception:  # pragma: no cover - 读取失败不影响错误上报
                body = ""
            response.close()
            raise LLMHTTPError(response.status_code, body)
        return response

    # ------------------------------------------------------------------
    @staticmethod
    def _to_chunk(obj: dict[str, Any]) -> LLMChunk | None:
        """把一条 SSE 数据解析为 `LLMChunk`；不关心的帧返回 None。"""
        choices = obj.get("choices") or []
        if not choices:
            # 部分厂商会把 usage 放在没有 choices 的最后一帧
            usage = obj.get("usage")
            if usage:
                return LLMChunk(usage=usage)
            return None

        try:
            first = choices[0]
        except (TypeError, IndexError):
            raise LLMProtocolError()

        delta = first.get("delta") or {}
        finish_reason = first.get("finish_reason")

        deltas: list[ToolCallDelta] = []
        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            deltas.append(
                ToolCallDelta(
                    index=tc.get("index") or 0,
                    id=tc.get("id") or "",
                    name=fn.get("name") or "",
                    arguments_fragment=fn.get("arguments") or "",
                )
            )

        text = delta.get("content")
        if text is None and not deltas and finish_reason is None and not obj.get("usage"):
            return None

        return LLMChunk(
            text=text,
            tool_call_deltas=deltas,
            finish_reason=finish_reason,
            usage=obj.get("usage"),
        )

    # ------------------------------------------------------------------
    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover
            pass


__all__ = ["OpenAICompatibleClient", "LLMError"]
