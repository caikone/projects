"""结构化 JSON 日志配置。

每条工具调用日志至少包含：
timestamp、tool_name、params、status、duration_ms。

敏感字段（ownerPhone、ownerAddr、chipNo 及其 snake_case 写法）
在写入日志前递归脱敏，不会把完整敏感数据落盘。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

# 需要递归脱敏的键（camelCase 与 snake_case 写法均覆盖）
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "ownerPhone",
        "owner_phone",
        "ownerAddr",
        "owner_addr",
        "chipNo",
        "chip_no",
    }
)

_REDACTED = "***REDACTED***"


def redact(obj: Any) -> Any:
    """递归复制并脱敏敏感键的值。"""
    if isinstance(obj, dict):
        return {k: (_REDACTED if k in SENSITIVE_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact(item) for item in obj)
    return obj


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志格式器。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 把通过 extra 注入的结构化字段合并进来
        for key in ("tool_name", "params", "status", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                if key == "params":
                    value = redact(value) if isinstance(value, (dict, list, tuple)) else value
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """配置根日志器为 JSON 输出。幂等。"""
    root = logging.getLogger()
    # 清理可能已存在的 handler，避免重复输出
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # 降低 HTTP 库噪声
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return logging.getLogger("pet_hospital_mcp")


def log_tool_call(
    logger: logging.Logger,
    tool_name: str,
    params: Any,
    status: str,
    duration_ms: int,
    level: int = logging.INFO,
    message: str = "",
) -> None:
    """记录一次工具调用。"""
    logger.log(
        level,
        message or f"{tool_name} {status}",
        extra={
            "tool_name": tool_name,
            "params": params,
            "status": status,
            "duration_ms": duration_ms,
        },
    )
