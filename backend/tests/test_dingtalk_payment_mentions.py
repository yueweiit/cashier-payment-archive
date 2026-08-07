import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.external_expenses import classify_dingtalk_payment_event


def test_numeric_dingtalk_user_mention_is_not_an_approval_reference():
    classification, reason = classify_dingtalk_payment_event(
        {
            "comment": "2026-8-7已支付300元[黄海森](275014473739954459)",
            "trusted_finance": True,
        },
        approval_no="202608040849000084324",
        pending_amount=300,
        workflow_status="RUNNING",
        workflow_result="agree",
    )

    assert classification == "eligible"
    assert "全额付款" in reason


def test_date_shaped_other_approval_number_still_requires_review():
    classification, reason = classify_dingtalk_payment_event(
        {
            "comment": "202607270000000002 已付款",
            "trusted_finance": True,
        },
        approval_no="202607270000000001",
        pending_amount=60,
        workflow_status="RUNNING",
        workflow_result="agree",
    )

    assert classification == "review_required"
    assert "其他钉钉审批单号" in reason
