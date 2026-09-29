"""调用 Go 宠物医院 REST API 的 httpx 客户端。

- 每次调用有超时；
- 超时 / 连接异常有有限重试；
- 所有异常被归一化为 BackendError（统一错误结构），不向 MCP 层泄漏 httpx/Python 堆栈。

测试时可通过 transport= 注入 httpx.MockTransport，禁止访问真实 Go 服务。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .errors import (
    BACKEND_API_ERROR,
    BACKEND_INVALID_RESPONSE,
    BACKEND_TIMEOUT,
    BACKEND_UNAVAILABLE,
    BackendError,
)

logger = logging.getLogger("pet_hospital_mcp.rest")


class PetHospitalClient:
    """Go 宠物医院 REST API 的薄客户端。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        # 外部可注入 client 或 transport（测试用 MockTransport）
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                timeout=timeout,
                transport=transport,
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def list_pets(self, params: dict[str, Any]) -> dict[str, Any]:
        """GET /api/v1/pets，返回响应信封中的 data 字段。"""
        url = f"{self.base_url}/api/v1/pets"
        return await self._get_with_retry(url, params=params)

    async def _get_with_retry(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_exc: BackendError | None = None
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.get(url, params=params, timeout=self.timeout)
                return self._parse_envelope(response, url)
            except httpx.TimeoutException as exc:
                last_exc = BackendError(
                    BACKEND_TIMEOUT,
                    f"调用后端超时: {exc.__class__.__name__}",
                    {"url": url, "attempt": attempt, "timeout": self.timeout},
                )
                logger.warning("backend timeout attempt=%d/%d url=%s", attempt, attempts, url)
            except (httpx.ConnectError, httpx.NetworkError, httpx.TransportError) as exc:
                last_exc = BackendError(
                    BACKEND_UNAVAILABLE,
                    f"无法连接后端: {exc.__class__.__name__}",
                    {"url": url, "attempt": attempt},
                )
                logger.warning(
                    "backend unavailable attempt=%d/%d url=%s err=%s",
                    attempt,
                    attempts,
                    url,
                    exc.__class__.__name__,
                )
            except httpx.HTTPError as exc:
                # 其他 httpx 错误不重试，直接归一化
                raise BackendError(
                    BACKEND_UNAVAILABLE,
                    f"后端请求异常: {exc.__class__.__name__}",
                    {"url": url},
                )
        # 重试耗尽
        assert last_exc is not None
        raise last_exc

    def _parse_envelope(self, response: httpx.Response, url: str) -> dict[str, Any]:
        """解析统一响应信封 {code,message,data,time}，返回 data。"""
        # 非 2xx：后端 HTTP 错误
        if response.status_code >= 400:
            snippet = response.text[:200] if response.text else ""
            raise BackendError(
                BACKEND_API_ERROR,
                f"后端返回 HTTP {response.status_code}",
                {
                    "url": url,
                    "status_code": response.status_code,
                    "body": snippet,
                },
            )

        # 解析 JSON
        try:
            payload = response.json()
        except Exception:
            snippet = response.text[:200] if response.text else ""
            raise BackendError(
                BACKEND_INVALID_RESPONSE,
                "后端返回非 JSON 内容",
                {"url": url, "body": snippet},
            )

        # 信封结构校验
        if not isinstance(payload, dict):
            raise BackendError(
                BACKEND_INVALID_RESPONSE,
                "后端响应不是 JSON 对象",
                {"url": url, "body": str(payload)[:200]},
            )
        code = payload.get("code")
        data = payload.get("data")
        if code != 200:
            message = str(payload.get("message") or "后端业务错误")
            raise BackendError(
                BACKEND_API_ERROR,
                message,
                {"url": url, "code": code, "message": message},
            )
        if not isinstance(data, dict):
            raise BackendError(
                BACKEND_INVALID_RESPONSE,
                "后端响应 data 不是对象",
                {"url": url, "data_type": type(data).__name__},
            )
        return data
