"""工具模块。

后续阶段新增工具时，只需在本包内新增模块并在 register_all 中注册。
"""

from __future__ import annotations

from typing import Any

from ..logging_config import setup_logging
from ..rest_client import PetHospitalClient
from . import list_pets


def register_all(mcp: Any, client: PetHospitalClient) -> None:
    """注册当前阶段全部工具。阶段一仅 list_pets。"""
    logger = setup_logging()
    list_pets.register(mcp, client, logger)
