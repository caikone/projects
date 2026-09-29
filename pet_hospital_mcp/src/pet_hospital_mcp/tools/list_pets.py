"""list_pets 工具：将 Go REST API `GET /api/v1/pets` 暴露给 AI Agent。

严格适配后端允许值与查询参数，使用 Pydantic 做输入/输出校验，
所有上游异常归一化为统一错误结构并以 ToolError 抛出（MCP is_error=True）。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ..errors import (
    BACKEND_INVALID_RESPONSE,
    BackendError,
    ErrorOutput,
    InternalError,
)
from ..logging_config import log_tool_call
from ..rest_client import PetHospitalClient

# 与 Go 后端 model.ValidSpecies / ValidStatus 完全一致的枚举
Species = Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
Status = Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
# 与 Go 后端 /api/v1/meta 中 sortFields 一致
SortBy = Literal[
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
]
Order = Literal["asc", "desc"]


class ListPetsInput(BaseModel):
    """list_pets 工具输入模型，严格对应 `GET /api/v1/pets` 的查询参数。

    所有字段可选；未提供的字段不转发给后端，由后端使用自身默认值。
    严格拒绝未知字段、类型不正确、NaN、Infinity 以及 min>max 的输入。
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False, strict=True)

    q: str | None = Field(default=None, description="全文模糊匹配（跨字段）")
    name: str | None = Field(default=None, description="宠物姓名模糊匹配")
    ownerName: str | None = Field(default=None, description="主人姓名模糊匹配")
    ownerPhone: str | None = Field(default=None, description="主人电话模糊匹配")
    species: Species | None = Field(default=None, description="种类精确匹配")
    doctor: str | None = Field(default=None, description="主治医生模糊匹配")
    disease: str | None = Field(default=None, description="疾病模糊匹配")
    status: Status | None = Field(default=None, description="就诊状态精确匹配")
    min: float | None = Field(default=None, ge=0, description="最低总花费（非负）")
    max: float | None = Field(default=None, ge=0, description="最高总花费（非负）")
    sortBy: SortBy | None = Field(default=None, description="排序字段")
    order: Order | None = Field(default=None, description="排序方向：asc / desc")
    page: int | None = Field(default=None, ge=1, description="页码，从 1 开始")
    pageSize: int | None = Field(
        default=None, ge=1, le=500, description="每页条数，1-500"
    )

    @field_validator("min", "max")
    @classmethod
    def _reject_non_finite(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not math.isfinite(v):
            raise ValueError("min/max 不允许 NaN 或 Infinity")
        return v

    @model_validator(mode="after")
    def _check_min_max(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min 不能大于 max")
        return self

    def to_query(self) -> dict[str, Any]:
        """构造转发给后端的查询参数（仅非空字段）。"""
        dumped = self.model_dump(exclude_none=True)
        # Literal 已是字符串，int/float 保持原类型，httpx 可正确编码
        return dumped


# ---------------------------------------------------------------------------
# 输出模型：与 Go Result + Pet 结构对齐
# ---------------------------------------------------------------------------


class Treatment(BaseModel):
    """Go model.Treatment：一次诊疗收费明细。"""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    item: str | None = None
    category: str | None = None
    amount: float | None = None
    doctor: str | None = None
    date: str | None = None
    note: str | None = None


class MedicalRecord(BaseModel):
    """Go model.MedicalRecord：一条历史病历。"""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    visitDate: str | None = None
    doctor: str | None = None
    diagnosis: str | None = None
    symptoms: str | None = None
    treatment: str | None = None
    prescription: list[str] | None = None
    weightKg: float | None = None
    temperature: float | None = None
    followUp: str | None = None
    charge: float | None = None
    createdAt: str | None = None


class Pet(BaseModel):
    """Go model.Pet：宠物档案（聚合病历与消费）。

    records / charges 在真实后端响应中可能是 null 或数组，这里用
    list | None 兼容两种表现。
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str | None = None
    species: str | None = None
    breed: str | None = None
    gender: str | None = None
    ageMonths: int | None = None
    color: str | None = None
    chipNo: str | None = None
    ownerName: str | None = None
    ownerPhone: str | None = None
    ownerAddr: str | None = None
    doctor: str | None = None
    disease: str | None = None
    status: str | None = None
    allergy: str | None = None
    note: str | None = None
    # 兼容 null 或数组
    records: list[MedicalRecord] | None = None
    charges: list[Treatment] | None = None
    totalCost: float | None = None
    visitCount: int | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class ListPetsOutput(BaseModel):
    """list_pets 成功输出模型，对应 Go API 成功响应 data。"""

    items: list[Pet] = Field(default_factory=list, description="当前页宠物档案列表")
    total: int = Field(description="过滤后总记录数")
    page: int = Field(description="当前页码")
    pageSize: int = Field(description="每页条数")
    totalPages: int = Field(description="总页数")
    totalCost: float = Field(description="结果集花费合计")


def _raise_tool_error(err: BackendError) -> None:
    """把 BackendError 转成统一错误 JSON 并以 ToolError 抛出。

    延迟导入 ToolError 以避免在模块加载期对 mcp 包产生硬依赖（便于单测）。
    """
    from mcp.server.mcpserver.exceptions import ToolError

    payload: ErrorOutput = err.to_error_output()
    raise ToolError(payload.model_dump_json()) from None


def register(mcp: Any, client: PetHospitalClient, logger: Any) -> None:
    """在 MCPServer 实例上注册 list_pets 工具。"""

    @mcp.tool()
    async def list_pets(filters: ListPetsInput) -> ListPetsOutput:
        """查询宠物医院档案列表（过滤 + 排序 + 分页）。

        用途：调用上游 Go REST API `GET /api/v1/pets`，返回符合分页结构的宠物档案，
        适合在 AI Agent 需要浏览、筛选或统计宠物档案时使用。

        参数（全部可选，未提供则不转发给后端）：
          - q: 全文模糊匹配（跨字段）
          - name: 宠物姓名模糊
          - ownerName / ownerPhone: 主人姓名 / 电话模糊
          - species: 种类（犬/猫/兔/鸟/仓鼠/爬宠/其他，精确）
          - doctor: 主治医生模糊
          - disease: 疾病模糊
          - status: 就诊状态（待就诊/就诊中/住院中/已康复/慢性病随访，精确）
          - min / max: 总花费区间（非负，min<=max）
          - sortBy: 排序字段（id/name/ownerName/species/doctor/disease/status/
            totalCost/visitCount/createdAt/updatedAt）
          - order: 排序方向（asc/desc）
          - page: 页码（>=1）
          - pageSize: 每页条数（1-500）

        返回值：ListPetsOutput，含 items、total、page、pageSize、totalPages、totalCost。
        调用失败时返回统一错误结构 {error:{code,message,details}} 并标记失败状态。
        """
        tool_name = "list_pets"
        params_log = filters.model_dump(exclude_none=True)
        start = time.monotonic()
        try:
            data = await client.list_pets(filters.to_query())
            output = ListPetsOutput.model_validate(data)
        except BackendError as err:
            duration = int((time.monotonic() - start) * 1000)
            log_tool_call(
                logger, tool_name, params_log, err.code, duration,
                level=logging.ERROR,
                message=err.message,
            )
            _raise_tool_error(err)
            return  # 仅为类型检查，不会执行
        except ValidationError as exc:
            # 后端响应体不匹配 ListPetsOutput/Pet 等模型——按协议归一化为
            # BACKEND_INVALID_RESPONSE 而非 INTERNAL_ERROR
            duration = int((time.monotonic() - start) * 1000)
            err = BackendError(
                code=BACKEND_INVALID_RESPONSE,
                message="后端响应不符合预期数据模型",
                details={"validation_errors": exc.errors()},
            )
            log_tool_call(
                logger, tool_name, params_log, err.code, duration,
                level=logging.ERROR,
                message=str(exc),
            )
            _raise_tool_error(err)
            return
        except Exception as exc:  # noqa: BLE001 — 归一化所有未预期异常
            duration = int((time.monotonic() - start) * 1000)
            err = InternalError(
                "内部错误",
                {"error_type": type(exc).__name__},
            )
            log_tool_call(
                logger, tool_name, params_log, err.code, duration,
                level=logging.ERROR,
                message=str(exc),
            )
            _raise_tool_error(err)
            return
        duration = int((time.monotonic() - start) * 1000)
        log_tool_call(logger, tool_name, params_log, "ok", duration, message="ok")
        return output
