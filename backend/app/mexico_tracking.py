from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

CHINA_SHEETS = frozenset(
    {
        "悦为智能 YW Tech_Ai",
        "拉丁购",
        "凌翔产品&开发",
        "凌翔供应链及采购执行单元",
        "星铭HR人力资源中心",
        "星铭FC财务中心",
        "凌翔/星铭供应链及职能中心",
    }
)

MEXICO_SHEETS = frozenset(
    {
        "YW MOLDES MX模具",
        "YUEWEI MX核心制造",
        "LEMOS MX供应链开发及管理",
        "LEMOS MX 销售",
        "UV IMPRESION MX彩印",
        "FC 财务中心 Centro Financiero (FC)",
    }
)


@dataclass(frozen=True)
class RegionDecision:
    region: str
    source: str
    execution_region_raw: Optional[str] = None
    sheet_region: Optional[str] = None
    conflict_reason: Optional[str] = None


def _normalized_token(value: Optional[str]) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value).strip()).casefold()
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _execution_region(value: Optional[str]) -> Optional[str]:
    normalized = _normalized_token(value)
    if not normalized:
        return None
    if "墨西哥" in normalized or "mexico" in normalized:
        return "mexico"
    if "中国" in normalized or "china" in normalized:
        return "china"
    return None


def sheet_region(source_sheet: Optional[str]) -> Optional[str]:
    name = str(source_sheet or "").strip()
    if name in CHINA_SHEETS:
        return "china"
    if name in MEXICO_SHEETS:
        return "mexico"
    return None


def resolve_region(
    *,
    execution_region: Optional[str] = None,
    source_sheet: Optional[str] = None,
    currency: Optional[str] = None,
    admin_region: Optional[str] = None,
) -> RegionDecision:
    """Resolve the application region without treating currency as proof.

    An administrator's prior resolution remains authoritative, while newly
    observed conflicting facts are retained in ``conflict_reason`` for audit.
    """

    del currency  # A currency is only a review hint and never decides a region.
    raw_region = str(execution_region).strip() if execution_region is not None else None
    explicit_region = _execution_region(execution_region)
    mapped_sheet_region = sheet_region(source_sheet)
    override = _normalized_token(admin_region)

    if override in {"china", "mexico"}:
        facts = []
        if explicit_region and explicit_region != override:
            facts.append(f"execution_region={raw_region}")
        if mapped_sheet_region and mapped_sheet_region != override:
            facts.append(f"sheet_region={mapped_sheet_region}")
        return RegionDecision(
            region=override,
            source="admin_override",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
            conflict_reason=("管理员结论与新来源事实不一致: " + ", ".join(facts)) if facts else None,
        )

    if explicit_region and mapped_sheet_region and explicit_region != mapped_sheet_region:
        return RegionDecision(
            region="review",
            source="conflict",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
            conflict_reason=(
                f"execution_region={raw_region} 与 Sheet 判定={mapped_sheet_region} 不一致"
            ),
        )

    if explicit_region:
        return RegionDecision(
            region=explicit_region,
            source="execution_region",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
        )

    if mapped_sheet_region:
        return RegionDecision(
            region=mapped_sheet_region,
            source="sheet_mapping",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
        )

    return RegionDecision(
        region="review",
        source="unknown",
        execution_region_raw=raw_region,
        sheet_region=None,
        conflict_reason="缺少可确认的执行地区和 Sheet 映射",
    )


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def node_age_days(entered_at: datetime, *, now: Optional[datetime] = None) -> int:
    current = _as_shanghai(now or datetime.now(tz=SHANGHAI_TZ))
    entered = _as_shanghai(entered_at)
    return max(0, (current.date() - entered.date()).days)


def warning_level(age_days: int, *, yellow_days: int, red_days: int) -> str:
    if yellow_days < 0 or red_days < 0 or red_days <= yellow_days:
        raise ValueError("red_days must be greater than yellow_days and both must be non-negative")
    if age_days > red_days:
        return "red"
    if age_days > yellow_days:
        return "yellow"
    return "normal"


def build_bilingual_reminder(
    *,
    approval_no: str,
    applicant: str,
    current_node: str,
    current_approver: str,
    age_days: int,
    workflow_url: str,
) -> Dict[str, str]:
    return {
        "zh": (
            f"请协助跟进钉钉审批 {approval_no}。申请人：{applicant}；"
            f"当前节点：{current_node}；当前审批人：{current_approver}；"
            f"已停留 {age_days} 天。流程链接：{workflow_url}"
        ),
        "es": (
            f"Por favor, ayude a dar seguimiento a la solicitud de DingTalk {approval_no}. "
            f"Solicitante: {applicant}; etapa actual: {current_node}; "
            f"responsable actual: {current_approver}; lleva {age_days} días en esta etapa. "
            f"Enlace del flujo: {workflow_url}"
        ),
    }
