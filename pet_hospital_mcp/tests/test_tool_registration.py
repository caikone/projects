"""测试场景 6：工具注册、名称、JSON Schema。

- list_tools 恰好注册 1 个工具；
- 名称为 list_pets；
- description 非空且包含中文用途说明；
- inputSchema 顶层含 filters 参数，filters 经 $ref 引用 ListPetsInput，
  其内层含 14 个字段且关键字段类型符合预期；
- 同时直接校验 Pydantic 模型的 model_json_schema，作为权威 schema；
- outputSchema 存在且为对象类型；
- PetHospitalClient 通过 transport 注入，未访问真实后端。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp import Client

from pet_hospital_mcp.tools.list_pets import ListPetsInput, ListPetsOutput

EXPECTED_INPUT_FIELDS = {
    "q", "name", "ownerName", "ownerPhone", "species", "doctor",
    "disease", "status", "min", "max", "sortBy", "order", "page", "pageSize",
}


def _handler_factory():
    """默认成功后端 handler。"""
    from .conftest import _envelope, _result

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(data=_result()))
    return handler


# ---------------------------------------------------------------------------
# Pydantic 权威 schema 校验（不经过 SDK，避免 $ref 解析问题）
# ---------------------------------------------------------------------------


def test_pydantic_input_schema_has_14_fields():
    """ListPetsInput.model_json_schema 必须含 14 个查询字段。"""
    schema = ListPetsInput.model_json_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    assert set(props.keys()) == EXPECTED_INPUT_FIELDS, (
        f"字段集合不符: 缺 {EXPECTED_INPUT_FIELDS - set(props.keys())}, "
        f"多 {set(props.keys()) - EXPECTED_INPUT_FIELDS}"
    )
    # extra='forbid' → additionalProperties=False
    assert schema.get("additionalProperties") is False
    # 全部可选 → required 应缺失或为空
    assert schema.get("required", []) in (None, [])


def test_pydantic_input_schema_field_types_and_constraints():
    """关键字段类型 + 约束：枚举、范围、上下界。"""
    schema = ListPetsInput.model_json_schema()
    props = schema["properties"]

    # 枚举字段
    species_schema = props["species"]["anyOf"][0]
    assert species_schema["type"] == "string"
    assert set(species_schema["enum"]) == {"犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"}

    status_schema = props["status"]["anyOf"][0]
    assert set(status_schema["enum"]) == {
        "待就诊", "就诊中", "住院中", "已康复", "慢性病随访"
    }

    sort_schema = props["sortBy"]["anyOf"][0]
    assert set(sort_schema["enum"]) == {
        "id", "name", "ownerName", "species", "doctor", "disease", "status",
        "totalCost", "visitCount", "createdAt", "updatedAt",
    }

    order_schema = props["order"]["anyOf"][0]
    assert set(order_schema["enum"]) == {"asc", "desc"}

    # 数值字段
    min_schema = props["min"]["anyOf"][0]
    assert min_schema["type"] == "number"
    assert min_schema["minimum"] == 0

    max_schema = props["max"]["anyOf"][0]
    assert max_schema["type"] == "number"
    assert max_schema["minimum"] == 0

    # 分页字段
    page_schema = props["page"]["anyOf"][0]
    assert page_schema["type"] == "integer"
    assert page_schema["minimum"] == 1

    page_size_schema = props["pageSize"]["anyOf"][0]
    assert page_size_schema["type"] == "integer"
    assert page_size_schema["minimum"] == 1
    assert page_size_schema["maximum"] == 500


def test_pydantic_output_schema_fields():
    """ListPetsOutput.model_json_schema 必须含 6 个必填字段。"""
    schema = ListPetsOutput.model_json_schema()
    assert schema["type"] == "object"
    for field in ("items", "total", "page", "pageSize", "totalPages", "totalCost"):
        assert field in schema["properties"], f"输出 schema 缺少 {field}"
    # items 是数组
    assert schema["properties"]["items"]["type"] == "array"
    # 必填字段（除 items 外都必填，items 也有 default_factory 但仍应在 schema 中）
    required = set(schema.get("required", []))
    assert {"total", "page", "pageSize", "totalPages", "totalCost"}.issubset(required)


# ---------------------------------------------------------------------------
# 通过 SDK 暴露的 input_schema（含 $ref 结构）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_list_has_single_tool_named_list_pets(mock_backend_factory):
    client, mcp = mock_backend_factory(_handler_factory())
    async with Client(mcp) as c:
        listing = await c.list_tools()

    tools = listing.tools
    assert len(tools) == 1, f"期望仅注册 1 个工具，实际 {len(tools)}"
    assert tools[0].name == "list_pets"


@pytest.mark.asyncio
async def test_tool_description_nonempty(mock_backend_factory):
    client, mcp = mock_backend_factory(_handler_factory())
    async with Client(mcp) as c:
        listing = await c.list_tools()

    desc = listing.tools[0].description or ""
    assert desc, "工具 description 不能为空"
    assert "宠物" in desc or "list_pets" in desc
    assert "species" in desc and "status" in desc and "page" in desc


@pytest.mark.asyncio
async def test_sdk_input_schema_top_level_has_filters_ref(mock_backend_factory):
    """SDK 暴露的 input_schema：顶层 properties 仅含 filters；filters 用 $ref 引用 ListPetsInput。"""
    client, mcp = mock_backend_factory(_handler_factory())
    async with Client(mcp) as c:
        listing = await c.list_tools()

    schema = listing.tools[0].input_schema
    assert schema["type"] == "object"
    top_props = schema["properties"]
    assert set(top_props.keys()) == {"filters"}
    # filters 通过 $ref 引用 $defs.ListPetsInput
    filters_prop = top_props["filters"]
    assert "$ref" in filters_prop, f"filters 应使用 $ref 引用，实际: {filters_prop}"
    ref = filters_prop["$ref"]
    assert ref.endswith("ListPetsInput"), f"$ref 应指向 ListPetsInput，实际: {ref}"

    # $defs 中的 ListPetsInput 应含 14 字段
    defs = schema.get("$defs", {})
    list_pets_input_def = defs.get("ListPetsInput")
    assert list_pets_input_def is not None, "缺少 $defs.ListPetsInput"
    inner_props = list_pets_input_def["properties"]
    assert set(inner_props.keys()) == EXPECTED_INPUT_FIELDS, (
        f"$defs.ListPetsInput 内层字段不符: 缺 {EXPECTED_INPUT_FIELDS - set(inner_props.keys())}, "
        f"多 {set(inner_props.keys()) - EXPECTED_INPUT_FIELDS}"
    )
    # extra='forbid' 必须传递到 $defs.ListPetsInput
    assert list_pets_input_def.get("additionalProperties") is False
    # 顶层 required 应为 ["filters"]
    assert schema.get("required") == ["filters"]
    # 内层 required 缺失或为空（所有字段可选）
    inner_required = list_pets_input_def.get("required", [])
    assert inner_required in (None, [])


@pytest.mark.asyncio
async def test_sdk_output_schema_present(mock_backend_factory):
    """SDK 2.x 暴露 output_schema（结构化输出）。

    Pydantic 把 ListPetsOutput 自身 inlined 在 top-level schema，
    而把嵌套类型（Pet/MedicalRecord/Treatment）放进 $defs。
    """
    client, mcp = mock_backend_factory(_handler_factory())
    async with Client(mcp) as c:
        listing = await c.list_tools()

    out_schema = listing.tools[0].output_schema
    assert out_schema is not None, "output_schema 不能为空（SDK 2.x 结构化输出）"
    assert out_schema["type"] == "object"
    assert out_schema.get("title") == "ListPetsOutput"
    out_props = out_schema.get("properties", {})
    for field in ("items", "total", "page", "pageSize", "totalPages", "totalCost"):
        assert field in out_props, f"output_schema 缺少 {field}"
    # items 是数组
    assert out_props["items"]["type"] == "array"
    # 嵌套类型应在 $defs
    defs = out_schema.get("$defs", {})
    assert "Pet" in defs, "output_schema.$defs 应包含 Pet 模型"
    assert "MedicalRecord" in defs, "output_schema.$defs 应包含 MedicalRecord"
    assert "Treatment" in defs, "output_schema.$defs 应包含 Treatment"
    # ListPetsOutput 必填字段
    required = set(out_schema.get("required", []))
    assert {"total", "page", "pageSize", "totalPages", "totalCost"}.issubset(required)
