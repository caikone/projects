"""运行时配置。

所有配置从环境变量读取，教学场景保持最小必要集合：
- MCP_HOST / MCP_PORT：MCP 服务监听地址，默认 127.0.0.1:8000
- PET_HOSPITAL_BASE_URL：上游 Go 宠物医院 REST API 地址
- PET_HOSPITAL_TIMEOUT：单次后端调用超时（秒）
- PET_HOSPITAL_RETRIES：超时/连接异常时的有限重试次数
- MCP_ENDPOINT_PATH：MCP 端点路径，默认 /mcp
- LOG_LEVEL：日志级别
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value if value is not None and value != "" else default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """不可变运行时配置。"""

    mcp_host: str
    mcp_port: int
    mcp_endpoint_path: str
    pet_hospital_base_url: str
    pet_hospital_timeout: float
    pet_hospital_retries: int
    log_level: str

    @property
    def mcp_url(self) -> str:
        return f"http://{self.mcp_host}:{self.mcp_port}{self.mcp_endpoint_path}"


def load_settings() -> Settings:
    """从环境变量加载配置。"""
    host = _env_str("MCP_HOST", "127.0.0.1")
    return Settings(
        mcp_host=host,
        mcp_port=_env_int("MCP_PORT", 8000),
        mcp_endpoint_path=_env_str("MCP_ENDPOINT_PATH", "/mcp"),
        pet_hospital_base_url=_env_str(
            "PET_HOSPITAL_BASE_URL", "http://127.0.0.1:8080"
        ).rstrip("/"),
        pet_hospital_timeout=_env_float("PET_HOSPITAL_TIMEOUT", 10.0),
        pet_hospital_retries=_env_int("PET_HOSPITAL_RETRIES", 2),
        log_level=_env_str("LOG_LEVEL", "INFO"),
    )
