from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional


SOURCE_DINGTALK_EXPLICIT = "dingtalk_explicit"
SOURCE_SERVICE_SUBJECT_DEFAULT = "service_subject_default"
SOURCE_MANUAL = "manual"

AUTO_EXPECTED_PAYMENT_ACCOUNT_SOURCES = {
    SOURCE_DINGTALK_EXPLICIT,
    SOURCE_SERVICE_SUBJECT_DEFAULT,
}
VALID_EXPECTED_PAYMENT_ACCOUNT_SOURCES = {
    *AUTO_EXPECTED_PAYMENT_ACCOUNT_SOURCES,
    SOURCE_MANUAL,
}

SERVICE_SUBJECT_ACCOUNT_ALIASES = (
    ("悦为智能公司账户", ("悦为智能", "YW Tech_Ai", "Yuewei Intelligent")),
    ("凌翔公司账户", ("凌翔", "凌翔产品&开发", "凌翔供应链及采购执行单元")),
    ("星铭公司账户", ("星铭", "星铭HR人力资源中心")),
    ("拉丁购公司账户", ("拉丁购", "Latin Buy")),
    ("YW MOLDES公司账户", ("YW MOLDES", "YW MOLDES MX模具")),
)
SERVICE_SUBJECT_DEFAULT_ACCOUNTS = frozenset(
    account for account, _aliases in SERVICE_SUBJECT_ACCOUNT_ALIASES
)


@dataclass(frozen=True)
class ExpectedPaymentAccountCandidate:
    value: Optional[str]
    source: Optional[str]
    warning: Optional[str] = None


def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fold_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", clean_text(value) or "").casefold()
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        "",
        "".join(char for char in text if not unicodedata.combining(char)),
    )


def default_account_for_service_subject(service_subject: Any) -> Optional[str]:
    normalized_subject = _fold_match_text(service_subject)
    if not normalized_subject:
        return None
    for account, aliases in SERVICE_SUBJECT_ACCOUNT_ALIASES:
        normalized_aliases = sorted(
            (_fold_match_text(alias) for alias in aliases),
            key=len,
            reverse=True,
        )
        if any(alias and alias in normalized_subject for alias in normalized_aliases):
            return account
    return None


def resolve_expected_payment_account(
    explicit_value: Any,
    service_subject: Any,
) -> ExpectedPaymentAccountCandidate:
    explicit = clean_text(explicit_value)
    if explicit:
        return ExpectedPaymentAccountCandidate(
            value=explicit,
            source=SOURCE_DINGTALK_EXPLICIT,
        )
    subject = clean_text(service_subject)
    if not subject:
        return ExpectedPaymentAccountCandidate(value=None, source=None)
    default = default_account_for_service_subject(subject)
    if default:
        return ExpectedPaymentAccountCandidate(
            value=default,
            source=SOURCE_SERVICE_SUBJECT_DEFAULT,
        )
    return ExpectedPaymentAccountCandidate(
        value=None,
        source=None,
        warning="服务主体无法匹配预计支付账户，请人工填写",
    )


def transition_synced_expected_payment_account(
    current_value: Any,
    current_source: Any,
    candidate: ExpectedPaymentAccountCandidate,
) -> tuple[Optional[str], Optional[str]]:
    current = clean_text(current_value)
    stored_source = clean_text(current_source)
    candidate_value = clean_text(candidate.value)
    candidate_source = clean_text(candidate.source)

    if current and stored_source not in AUTO_EXPECTED_PAYMENT_ACCOUNT_SOURCES:
        return current, stored_source
    if (
        not candidate_value
        or candidate_source not in AUTO_EXPECTED_PAYMENT_ACCOUNT_SOURCES
    ):
        return current, stored_source
    if not current:
        return candidate_value, candidate_source
    if stored_source == SOURCE_DINGTALK_EXPLICIT:
        if candidate_source == SOURCE_DINGTALK_EXPLICIT:
            return candidate_value, candidate_source
        return current, stored_source
    if stored_source == SOURCE_SERVICE_SUBJECT_DEFAULT:
        return candidate_value, candidate_source
    return current, stored_source
