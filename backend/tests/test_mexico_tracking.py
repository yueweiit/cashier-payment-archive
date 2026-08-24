from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.app.mexico_tracking import (
    build_bilingual_reminder,
    node_age_days,
    resolve_region,
    warning_level,
)


@pytest.mark.parametrize(
    ("source_sheet", "expected_region"),
    [
        ("悦为智能 YW Tech_Ai", "china"),
        ("拉丁购", "china"),
        ("凌翔产品&开发", "china"),
        ("凌翔供应链及采购执行单元", "china"),
        ("星铭HR人力资源中心", "china"),
        ("星铭FC财务中心", "china"),
        ("凌翔/星铭供应链及职能中心", "china"),
        ("YW MOLDES MX模具", "mexico"),
        ("YUEWEI MX核心制造", "mexico"),
        ("LEMOS MX供应链开发及管理", "mexico"),
        ("LEMOS MX 销售", "mexico"),
        ("UV IMPRESION MX彩印", "mexico"),
        ("FC 财务中心 Centro Financiero (FC)", "mexico"),
    ],
)
def test_region_falls_back_to_exact_confirmed_sheet_mapping(
    source_sheet: str, expected_region: str
) -> None:
    decision = resolve_region(source_sheet=source_sheet)

    assert decision.region == expected_region
    assert decision.source == "sheet_mapping"


def test_explicit_execution_region_takes_priority_without_conflict() -> None:
    decision = resolve_region(
        execution_region="墨西哥 Mexico",
        source_sheet="未登记的新公司",
    )

    assert decision.region == "mexico"
    assert decision.source == "execution_region"
    assert decision.execution_region_raw == "墨西哥 Mexico"


def test_execution_region_and_sheet_conflict_requires_review() -> None:
    decision = resolve_region(
        execution_region="中国 China",
        source_sheet="YW MOLDES MX模具",
    )

    assert decision.region == "review"
    assert decision.source == "conflict"
    assert decision.sheet_region == "mexico"
    assert decision.conflict_reason


def test_admin_resolution_is_kept_when_new_raw_fact_conflicts() -> None:
    decision = resolve_region(
        execution_region="Mexico",
        source_sheet="YW MOLDES MX模具",
        admin_region="china",
    )

    assert decision.region == "china"
    assert decision.source == "admin_override"
    assert decision.conflict_reason
    assert "Mexico" in decision.conflict_reason


def test_currency_alone_never_decides_region() -> None:
    decision = resolve_region(currency="MXN", source_sheet="新公司")

    assert decision.region == "review"
    assert decision.source == "unknown"


def test_similar_finance_sheet_names_are_not_confused() -> None:
    assert resolve_region(source_sheet="星铭FC财务中心").region == "china"
    assert (
        resolve_region(source_sheet="FC 财务中心 Centro Financiero (FC)").region
        == "mexico"
    )
    assert resolve_region(source_sheet="FC财务中心").region == "review"


def test_node_age_uses_shanghai_calendar_days() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")

    assert node_age_days(
        datetime(2026, 8, 24, 0, 1, tzinfo=shanghai),
        now=datetime(2026, 8, 24, 23, 59, tzinfo=shanghai),
    ) == 0
    assert node_age_days(
        datetime(2026, 8, 21, 23, 59, tzinfo=shanghai),
        now=datetime(2026, 8, 24, 0, 1, tzinfo=shanghai),
    ) == 3


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, "normal"),
        (2, "normal"),
        (3, "yellow"),
        (5, "yellow"),
        (6, "red"),
    ],
)
def test_warning_thresholds_are_strictly_greater_than_config(age: int, expected: str) -> None:
    assert warning_level(age, yellow_days=2, red_days=5) == expected


def test_warning_thresholds_must_be_valid() -> None:
    with pytest.raises(ValueError):
        warning_level(3, yellow_days=5, red_days=2)


def test_bilingual_reminder_contains_operational_context() -> None:
    reminder = build_bilingual_reminder(
        approval_no="2026082412340001",
        applicant="María López",
        current_node="CEO 审批",
        current_approver="Eduardo Gómez",
        age_days=4,
        workflow_url="https://oa.dingtalk.com/example",
    )

    for value in (
        "2026082412340001",
        "María López",
        "CEO 审批",
        "Eduardo Gómez",
        "4",
        "https://oa.dingtalk.com/example",
    ):
        assert value in reminder["zh"]
        assert value in reminder["es"]
    assert "请协助" in reminder["zh"]
    assert "favor" in reminder["es"].lower()
