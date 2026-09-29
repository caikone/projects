"""统一错误模型与后端异常。

MCP 客户端收到的所有工具失败都遵循统一结构：

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "可读错误信息",
    "details": {}
  }
}
```

错误码语义：
- VALIDATION_ERROR：工具输入校验失败
- BACKEND_TIMEOUT：调用 Go REST API 超时
- BACKEND_UNAVAILABLE：无法连接到 Go REST API
- BACKEND_API_ERROR：Go REST API 返回 4xx/5xx 或业务错误码
- BACKEND_INVALID_RESPONSE：后端返回非法 JSON 或不符合数据模型
- INTERNAL_ERROR：其他未预期的内部错误
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# 错误码常量
VALIDATION_ERROR = "VALIDATION_ERROR"
BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_API_ERROR = "BACKEND_API_ERROR"
BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """单条错误详情。"""

    code: str = Field(description="错误码（大写下划线）")
    message: str = Field(description="人类可读错误信息")
    details: dict[str, Any] = Field(
        default_factory=dict, description="可选的结构化附加信息"
    )


class ErrorOutput(BaseModel):
    """工具失败时返回的统一错误结构。"""

    error: ErrorDetail


class BackendError(Exception):
    """调用 Go REST API 时发生的、需要转换成统一错误结构的异常。

    工具层捕获此异常后，构造 ErrorOutput 并以 ToolError 抛出，
    使 MCP 客户端得到 is_error=True 的结果，且 content 中携带统一错误 JSON。
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_error_output(self) -> ErrorOutput:
        return ErrorOutput(
            error=ErrorDetail(code=self.code, message=self.message, details=self.details)
        )


class InternalError(BackendError):
    """未预期的内部错误。"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(INTERNAL_ERROR, message, details)
