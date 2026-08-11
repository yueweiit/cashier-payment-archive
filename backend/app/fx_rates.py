from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable

SUPPORTED_CURRENCIES = ("CNY", "USD", "MXN")
CURRENCY_ALIASES = {
    "CNY": "CNY",
    "RMB": "CNY",
    "CNH": "CNY",
    "人民币": "CNY",
    "人民币元": "CNY",
    "USD": "USD",
    "US$": "USD",
    "$": "USD",
    "美元": "USD",
    "美金": "USD",
    "DOLAR": "USD",
    "DÓLAR": "USD",
    "MXN": "MXN",
    "MX$": "MXN",
    "墨西哥比索": "MXN",
    "比索": "MXN",
    "PESO": "MXN",
    "PESOS": "MXN",
}


class FxRateError(RuntimeError):
    pass


def normalize_currency(value: Any, *, default: str | None = None) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return default
    compact = text.replace(" ", "")
    if compact in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[compact]
    for alias, currency in CURRENCY_ALIASES.items():
        if alias and alias in text:
            return currency
    return default


def money(value: Any) -> float:
    try:
        decimal_value = Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FxRateError("金额无效") from exc
    return float(decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def multiply_money(value: Any, rate: Any) -> float:
    return money(Decimal(str(value or 0)) * Decimal(str(rate or 0)))


def divide_money(value: Any, rate: Any) -> float:
    rate_decimal = Decimal(str(rate or 0))
    if rate_decimal <= 0:
        raise FxRateError("汇率必须大于 0")
    return money(Decimal(str(value or 0)) / rate_decimal)


def fetch_rates(rate_date: date, currencies: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    requested = []
    for value in currencies:
        currency = normalize_currency(value)
        if not currency or currency not in SUPPORTED_CURRENCIES:
            raise FxRateError("仅支持 CNY、USD 和 MXN")
        if currency not in requested:
            requested.append(currency)
    result: Dict[str, Dict[str, Any]] = {}
    if "CNY" in requested:
        result["CNY"] = {
            "currency": "CNY",
            "cny_per_unit": 1.0,
            "requested_date": rate_date.isoformat(),
            "actual_date": rate_date.isoformat(),
            "fallback": False,
        }
    external = [currency for currency in requested if currency != "CNY"]
    if external:
        from .external_expenses import ExternalExpenseError, source_connection

        try:
            with source_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT ON (UPPER(TRIM(currency)))
                           UPPER(TRIM(currency)) AS currency,
                           rate_date,
                           cny_per_unit
                    FROM public.fx_rates_daily
                    WHERE UPPER(TRIM(currency)) = ANY(%s)
                      AND rate_date <= %s
                      AND cny_per_unit IS NOT NULL
                      AND cny_per_unit > 0
                    ORDER BY UPPER(TRIM(currency)), rate_date DESC
                    """,
                    [external, rate_date],
                ).fetchall()
        except ExternalExpenseError as exc:
            raise FxRateError(str(exc)) from exc
        for row in rows:
            currency = normalize_currency(row.get("currency"))
            if not currency:
                continue
            actual_date = row["rate_date"].isoformat() if hasattr(row["rate_date"], "isoformat") else str(row["rate_date"])
            result[currency] = {
                "currency": currency,
                "cny_per_unit": float(row["cny_per_unit"]),
                "requested_date": rate_date.isoformat(),
                "actual_date": actual_date,
                "fallback": actual_date != rate_date.isoformat(),
            }
    missing = [currency for currency in requested if currency not in result]
    if missing:
        raise FxRateError(f"找不到 {'/'.join(missing)} 在 {rate_date.isoformat()} 或此前的汇率")
    return result
