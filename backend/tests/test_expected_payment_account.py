from __future__ import annotations

import pytest

from backend.app.expected_payment_account import (
    SOURCE_DINGTALK_EXPLICIT,
    SOURCE_MANUAL,
    SOURCE_SERVICE_SUBJECT_DEFAULT,
    ExpectedPaymentAccountCandidate,
    resolve_expected_payment_account,
    transition_synced_expected_payment_account,
)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("悦为智能 YW Tech_Ai", "悦为智能公司账户"),
        ("Yuewei Intelligent", "悦为智能公司账户"),
        ("凌翔产品&开发", "凌翔公司账户"),
        ("凌翔供应链及采购执行单元", "凌翔公司账户"),
        ("星铭HR人力资源中心", "星铭公司账户"),
        ("拉丁购", "拉丁购公司账户"),
        ("Latin Buy", "拉丁购公司账户"),
        ("YW MOLDES MX模具", "YW MOLDES公司账户"),
        ("yw moldes", "YW MOLDES公司账户"),
    ],
)
def test_resolve_expected_payment_account_from_service_subject(subject, expected):
    candidate = resolve_expected_payment_account(None, subject)

    assert candidate == ExpectedPaymentAccountCandidate(
        value=expected,
        source=SOURCE_SERVICE_SUBJECT_DEFAULT,
    )


def test_explicit_value_wins_and_preserves_display_text():
    candidate = resolve_expected_payment_account(
        "  拉丁购 USD 主账户  ",
        "悦为智能 YW Tech_Ai",
    )

    assert candidate == ExpectedPaymentAccountCandidate(
        value="拉丁购 USD 主账户",
        source=SOURCE_DINGTALK_EXPLICIT,
    )


def test_unknown_service_subject_is_blank_and_warns():
    candidate = resolve_expected_payment_account(None, "新公司")

    assert candidate == ExpectedPaymentAccountCandidate(
        value=None,
        source=None,
        warning="服务主体无法匹配预计支付账户，请人工填写",
    )


def test_absent_expected_account_inputs_are_silent():
    assert resolve_expected_payment_account("", "") == ExpectedPaymentAccountCandidate(
        value=None,
        source=None,
    )


@pytest.mark.parametrize(
    ("current_value", "current_source", "candidate", "expected"),
    [
        (
            None,
            None,
            ExpectedPaymentAccountCandidate("悦为智能公司账户", SOURCE_SERVICE_SUBJECT_DEFAULT),
            ("悦为智能公司账户", SOURCE_SERVICE_SUBJECT_DEFAULT),
        ),
        (
            "",
            None,
            ExpectedPaymentAccountCandidate("明确账户", SOURCE_DINGTALK_EXPLICIT),
            ("明确账户", SOURCE_DINGTALK_EXPLICIT),
        ),
        (
            "人工账户",
            SOURCE_MANUAL,
            ExpectedPaymentAccountCandidate("明确账户", SOURCE_DINGTALK_EXPLICIT),
            ("人工账户", SOURCE_MANUAL),
        ),
        (
            "历史人工账户",
            None,
            ExpectedPaymentAccountCandidate("明确账户", SOURCE_DINGTALK_EXPLICIT),
            ("历史人工账户", None),
        ),
        (
            "默认账户 A",
            SOURCE_SERVICE_SUBJECT_DEFAULT,
            ExpectedPaymentAccountCandidate("默认账户 B", SOURCE_SERVICE_SUBJECT_DEFAULT),
            ("默认账户 B", SOURCE_SERVICE_SUBJECT_DEFAULT),
        ),
        (
            "默认账户 A",
            SOURCE_SERVICE_SUBJECT_DEFAULT,
            ExpectedPaymentAccountCandidate("明确账户", SOURCE_DINGTALK_EXPLICIT),
            ("明确账户", SOURCE_DINGTALK_EXPLICIT),
        ),
        (
            "明确账户 A",
            SOURCE_DINGTALK_EXPLICIT,
            ExpectedPaymentAccountCandidate("明确账户 B", SOURCE_DINGTALK_EXPLICIT),
            ("明确账户 B", SOURCE_DINGTALK_EXPLICIT),
        ),
        (
            "明确账户",
            SOURCE_DINGTALK_EXPLICIT,
            ExpectedPaymentAccountCandidate("悦为智能公司账户", SOURCE_SERVICE_SUBJECT_DEFAULT),
            ("明确账户", SOURCE_DINGTALK_EXPLICIT),
        ),
        (
            "明确账户",
            SOURCE_DINGTALK_EXPLICIT,
            ExpectedPaymentAccountCandidate(None, None),
            ("明确账户", SOURCE_DINGTALK_EXPLICIT),
        ),
        (
            "默认账户",
            SOURCE_SERVICE_SUBJECT_DEFAULT,
            ExpectedPaymentAccountCandidate(None, None),
            ("默认账户", SOURCE_SERVICE_SUBJECT_DEFAULT),
        ),
        (
            None,
            None,
            ExpectedPaymentAccountCandidate(None, None),
            (None, None),
        ),
        (
            "人工账户",
            SOURCE_MANUAL,
            ExpectedPaymentAccountCandidate("伪造人工候选", SOURCE_MANUAL),
            ("人工账户", SOURCE_MANUAL),
        ),
    ],
)
def test_transition_synced_expected_payment_account(
    current_value,
    current_source,
    candidate,
    expected,
):
    assert transition_synced_expected_payment_account(
        current_value,
        current_source,
        candidate,
    ) == expected
