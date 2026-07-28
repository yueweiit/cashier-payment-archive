from __future__ import annotations

import re
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
