"""测试场景 2：输入校验失败。

直接对 ListPetsInput 做模型校验断言（更快、更聚焦），覆盖：
- 未知字段（extra="forbid"）；
- species/status/sortBy/order 枚举非法值；
- page<1、pageSize 越界（<1 或 >500）；
- min/max 非有限（NaN、Infinity）；
- min>max；
- 类型错误（如 page 传字符串、pageSize 传浮点）。
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from pet_hospital_mcp.tools.list_pets import ListPetsInput


# ---------------------------------------------------------------------------
# 枚举校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_species", ["", "狗", "dog", "DOG", "鱼", "猫狗"])
def test_species_rejects_unknown(bad_species: str):
    with pytest.raises(ValidationError):
        ListPetsInput(species=bad_species)  # type: ignore[arg-type]


@pytest.mark.parametrize("ok_species", ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"])
def test_species_accepts_known(ok_species: str):
    m = ListPetsInput(species=ok_species)  # type: ignore[arg-type]
    assert m.species == ok_species


@pytest.mark.parametrize("bad_status", ["", "就诊", "已愈", "DEAD", "待诊"])
def test_status_rejects_unknown(bad_status: str):
    with pytest.raises(ValidationError):
        ListPetsInput(status=bad_status)  # type: ignore[arg-type]


@pytest.mark.parametrize("ok_status", ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"])
def test_status_accepts_known(ok_status: str):
    m = ListPetsInput(status=ok_status)  # type: ignore[arg-type]
    assert m.status == ok_status


@pytest.mark.parametrize(
    "bad_sort", ["", "name1", "owner", "doctor_name", "totalcost", "TOTAL_COST", "createdat"]
)
def test_sortby_rejects_unknown(bad_sort: str):
    with pytest.raises(ValidationError):
        ListPetsInput(sortBy=bad_sort)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ok_sort",
    ["id", "name", "ownerName", "species", "doctor", "disease", "status",
     "totalCost", "visitCount", "createdAt", "updatedAt"],
)
def test_sortby_accepts_known(ok_sort: str):
    m = ListPetsInput(sortBy=ok_sort)  # type: ignore[arg-type]
    assert m.sortBy == ok_sort


@pytest.mark.parametrize("bad_order", ["", "ASC", "DESC", "up", "down", "random"])
def test_order_rejects_unknown(bad_order: str):
    with pytest.raises(ValidationError):
        ListPetsInput(order=bad_order)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 分页校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_page", [0, -1, -100])
def test_page_rejects_below_one(bad_page: int):
    with pytest.raises(ValidationError):
        ListPetsInput(page=bad_page)


@pytest.mark.parametrize("ok_page", [1, 2, 100, 10_000])
def test_page_accepts_positive(ok_page: int):
    assert ListPetsInput(page=ok_page).page == ok_page


@pytest.mark.parametrize("bad_size", [0, -1, 501, 10_000])
def test_pagesize_rejects_out_of_range(bad_size: int):
    with pytest.raises(ValidationError):
        ListPetsInput(pageSize=bad_size)


@pytest.mark.parametrize("ok_size", [1, 20, 500])
def test_pagesize_accepts_in_range(ok_size: int):
    assert ListPetsInput(pageSize=ok_size).pageSize == ok_size


# ---------------------------------------------------------------------------
# 花费区间校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_min", [-0.01, -1.0, -100])
def test_min_rejects_negative(bad_min: float):
    with pytest.raises(ValidationError):
        ListPetsInput(min=bad_min)


@pytest.mark.parametrize("bad_max", [-0.01, -1.0])
def test_max_rejects_negative(bad_max: float):
    with pytest.raises(ValidationError):
        ListPetsInput(max=bad_max)


def test_min_rejects_nan():
    with pytest.raises(ValidationError):
        ListPetsInput(min=math.nan)


def test_max_rejects_nan():
    with pytest.raises(ValidationError):
        ListPetsInput(max=math.nan)


def test_min_rejects_infinity():
    with pytest.raises(ValidationError):
        ListPetsInput(min=math.inf)


def test_max_rejects_infinity():
    with pytest.raises(ValidationError):
        ListPetsInput(max=-math.inf)


def test_max_rejects_infinity_positive():
    with pytest.raises(ValidationError):
        ListPetsInput(max=math.inf)


def test_min_greater_than_max_rejected():
    with pytest.raises(ValidationError):
        ListPetsInput(min=100.0, max=50.0)


def test_min_equal_max_accepted():
    """min == max 视为闭区间单点，允许。"""
    m = ListPetsInput(min=100.0, max=100.0)
    assert m.min == 100.0 and m.max == 100.0


def test_min_only_accepted():
    ListPetsInput(min=50.0)


def test_max_only_accepted():
    ListPetsInput(max=50.0)


# ---------------------------------------------------------------------------
# 未知字段 / 类型错误
# ---------------------------------------------------------------------------


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        ListPetsInput(unknownField="x")  # type: ignore[call-arg]


def test_wrong_type_page_string_rejected():
    with pytest.raises(ValidationError):
        ListPetsInput(page="1")  # type: ignore[arg-type]


def test_wrong_type_pagesize_float_rejected():
    """pageSize 期望 int；传入 1.5 应被拒绝。"""
    with pytest.raises(ValidationError):
        ListPetsInput(pageSize=1.5)  # type: ignore[arg-type]


def test_wrong_type_min_string_rejected():
    with pytest.raises(ValidationError):
        ListPetsInput(min="100")  # type: ignore[arg-type]


def test_to_query_excludes_none():
    """to_query 仅输出已提供字段。"""
    m = ListPetsInput(species="犬", page=2)
    q = m.to_query()
    assert q == {"species": "犬", "page": 2}


def test_to_query_empty_for_no_fields():
    assert ListPetsInput().to_query() == {}
