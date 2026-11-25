from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable


_LEGACY_MOULD_SHEET_PATTERN = re.compile(
    r"^(赣瑞模具|志威模具)\s*(?:[（(]\s*7\s*月\s*(?:前|后)\s*[）)]|7\s*月\s*(?:前|后))$"
)


def canonical_sheet_name(value: Any) -> str:
    name = str(value or "").strip() or "未分 Sheet"
    match = _LEGACY_MOULD_SHEET_PATTERN.fullmatch(name)
    return match.group(1) if match else name


def canonical_sheet_order(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw_name = str(value or "").strip()
        if not raw_name:
            continue
        name = canonical_sheet_name(raw_name)
        if name == "全部" or not name or name in seen:
            continue
        result.append(name)
        seen.add(name)
    return result


def _workbench_name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(char for char in text if not char.isspace() and not unicodedata.combining(char))


_WORKBENCH_GROUP_ALIASES = {
    "广州凌翔": (
        "凌翔", "凌翔产品&开发", "凌翔供应链及采购执行单元", "凌翔产品&开发及供应链",
        "Guangzhou Lingxiang广州凌翔", "Guangzhou Lingxiang", "广州凌翔Guangzhou Lingxiang",
    ),
    "星铭 HR": ("星铭HR人力资源中心",),
    "星铭 财务": ("星铭FC财务中心", "星铭FC"),
    "YW MODELS": (
        "YW MOLDES MX模具", "YW MOLDES MX/模具", "YW MOLDES MX", "YW MOLDES",
        "YW MODELS模具", "YW MODELS MX模具",
    ),
    "YUEWEI MX": ("YUEWEI MX核心制造",),
    "LEMOS MX": ("LEMOS MX供应链开发及管理", "LEMOS MX 销售"),
    "拉丁购": (),
    "LATINGO": (),
    "柠檬树": (),
}
_WORKBENCH_ALIAS_GROUPS = {
    _workbench_name_key(alias): group
    for group, aliases in _WORKBENCH_GROUP_ALIASES.items()
    for alias in (group, *aliases)
}
_WORKBENCH_MIXED_LATIN_NAMES = {
    _workbench_name_key(name) for name in ("LatínGo拉丁购", "拉丁购LATINGO")
}
_WORKBENCH_MIXED_CENTER = _workbench_name_key("凌翔/星铭供应链及职能中心")
_WORKBENCH_FINANCE_NAMES = {
    _workbench_name_key(name) for name in (
        "FC 财务中心Centro Financiero (FC)", "FC 财务中心", "FC", "财务中心",
    )
}
_WORKBENCH_HR_NAMES = {
    _workbench_name_key(name) for name in ("HR", "HR人力资源中心", "人力资源中心")
}
_WORKBENCH_XINGMING_GROUPS = {"星铭", "星铭 HR", "星铭 财务"}
_WORKBENCH_SUBJECT_ROOTS = {
    **_WORKBENCH_ALIAS_GROUPS,
    **{_workbench_name_key(alias): group for alias, group in (
        ("凌翔", "广州凌翔"),
        ("星铭", "星铭"),
        ("东莞星铭", "星铭"),
        ("Dongguan Xingming东莞星铭", "星铭"),
        ("Dongguan Xingming", "星铭"),
        ("星铭供应链及职能中心", "星铭"),
    )},
}


def _workbench_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, dict) else {}


def _workbench_subject_text(text: str) -> str | None:
    key = _workbench_name_key(text)
    if key in _WORKBENCH_MIXED_LATIN_NAMES or key == _WORKBENCH_MIXED_CENTER:
        return None
    if key in _WORKBENCH_SUBJECT_ROOTS:
        return _WORKBENCH_SUBJECT_ROOTS[key]
    parts = text.split("/")
    root = _WORKBENCH_SUBJECT_ROOTS.get(_workbench_name_key(parts[0]))
    if len(parts) == 1 or not root or any(not part.strip() for part in parts):
        return None
    company = "星铭" if root in _WORKBENCH_XINGMING_GROUPS else root
    departments = {root} if root in {"星铭 HR", "星铭 财务"} else set()
    for part in parts[1:]:
        part_key = _workbench_name_key(part)
        if part_key in _WORKBENCH_MIXED_LATIN_NAMES or part_key == _WORKBENCH_MIXED_CENTER:
            return None
        other = _WORKBENCH_SUBJECT_ROOTS.get(part_key)
        other_company = "星铭" if other in _WORKBENCH_XINGMING_GROUPS else other
        if other_company and other_company != company:
            return None
        if company == "星铭":
            if other in {"星铭 HR", "星铭 财务"}:
                departments.add(other)
            elif part_key in _WORKBENCH_FINANCE_NAMES:
                departments.add("星铭 财务")
            elif part_key in _WORKBENCH_HR_NAMES:
                departments.add("星铭 HR")
    if company == "星铭":
        return next(iter(departments)) if len(departments) == 1 else "星铭" if not departments else None
    return root


def _workbench_subject_group(value: Any, depth: int = 0) -> str | None:
    if depth > 5:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in {"[", "{", '"'}:
            try:
                return _workbench_subject_group(json.loads(text), depth + 1)
            except (ValueError, RecursionError):
                return None
        return _workbench_subject_text(text)
    if isinstance(value, dict):
        for field in ("name", "label", "value", "text"):
            if value.get(field) not in (None, ""):
                return _workbench_subject_group(value[field], depth + 1)
        return None
    if isinstance(value, list):
        groups = [_workbench_subject_group(item, depth + 1) for item in value]
        return groups[0] if groups and groups[0] and all(group == groups[0] for group in groups) else None
    return None


def workbench_sheet_name(value: Any, raw_extra_json: Any = None) -> str:
    """Project an organization into a display group without changing its identity."""
    name = canonical_sheet_name(value)
    key = _workbench_name_key(name)
    if key in _WORKBENCH_ALIAS_GROUPS:
        return _WORKBENCH_ALIAS_GROUPS[key]

    extra = _workbench_json_object(raw_extra_json)
    source = _workbench_json_object(extra["external_source"]) if "external_source" in extra else extra
    subject = _workbench_subject_group(source.get("service_subject"))
    employee_mapping = _workbench_json_object(extra.get("employee_department_mapping"))
    third_groups = {
        group for department in (
            employee_mapping.get("third_level_department"), source.get("applicant_department_level3"),
        ) if (group := _WORKBENCH_ALIAS_GROUPS.get(_workbench_name_key(department)))
    }
    third_group = next(iter(third_groups)) if len(third_groups) == 1 else None

    if key in _WORKBENCH_MIXED_LATIN_NAMES:
        return subject if subject in {"拉丁购", "LATINGO"} else "待归组"
    if key == _workbench_name_key("星铭"):
        if subject in {"星铭 HR", "星铭 财务"}:
            return subject
        return third_group if third_group in {"星铭 HR", "星铭 财务"} else "待归组"
    if key == _WORKBENCH_MIXED_CENTER:
        if subject == "星铭":
            return third_group if third_group in _WORKBENCH_XINGMING_GROUPS else "待归组"
        return subject or third_group or "待归组"
    if subject in _WORKBENCH_XINGMING_GROUPS:
        if key in _WORKBENCH_FINANCE_NAMES:
            return "星铭 财务"
        if key in _WORKBENCH_HR_NAMES:
            return "星铭 HR"

    already_assigned = (
        employee_mapping.get("assigned_department") == value
        or source.get("sheet_assignment_source") in ("employee_info.level2_department", "employee_info.level3_department")
    )
    automatic_origin = isinstance(value, str) and bool(value.strip()) and source.get("applicant_department") == value
    if automatic_origin and not already_assigned:
        if subject == "星铭":
            return third_group if third_group in _WORKBENCH_XINGMING_GROUPS else "待归组"
        if subject:
            return subject
    return name
