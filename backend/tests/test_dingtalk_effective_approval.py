from copy import deepcopy

import pytest

from backend.app import external_expenses as expenses


def stale_source():
    return {
        "approval_no": "EFFECTIVE-APPROVAL-1",
        "process_instance_id": "workflow-instance-1",
        "approval_status": "RUNNING",
        "approval_result": "agree",
        "source_id": "123",
        "amount": 100,
    }


def completed_workflow(result="refuse", **changes):
    return {
        "approval_no": "EFFECTIVE-APPROVAL-1",
        "process_instance_id": "workflow-instance-1",
        "status": "COMPLETED",
        "result": result,
        "events": [{"result": "AGREE"}],
        **changes,
    }


def original_state(source):
    return {key: source.get(key) for key in ("approval_status", "approval_result")}


def test_live_completed_refusal_overrides_stale_expense_approval():
    source = stale_source()
    workflow = completed_workflow()
    before_source, before_workflow = deepcopy(source), deepcopy(workflow)

    resolved = expenses.resolve_effective_dingtalk_approval(source, workflow)

    assert resolved == {"approval_status": "COMPLETED", "approval_result": "refuse"}
    assert source == before_source
    assert workflow == before_workflow


@pytest.mark.parametrize("result", ["refuse", "refused", "reject", "rejected", "disagree", "disagreed"])
def test_explicit_terminal_refusal_aliases_are_canonicalized(result):
    assert result in expenses.DINGTALK_EXPLICIT_REFUSAL_RESULTS
    resolved = expenses.resolve_effective_dingtalk_approval(
        stale_source(), completed_workflow("  " + result.upper() + "  ", status=" completed "),
    )

    assert resolved == {"approval_status": "COMPLETED", "approval_result": "refuse"}


@pytest.mark.parametrize("status", ["RUNNING", "TERMINATED", "CANCELLED", "CANCELED", "FINISHED", "UNKNOWN", "", None])
def test_refusal_result_requires_known_completed_workflow_status(status):
    source = stale_source()

    resolved = expenses.resolve_effective_dingtalk_approval(source, completed_workflow(status=status))

    assert resolved == original_state(source)


@pytest.mark.parametrize("result", [
    "agree", "agreed", "cancel", "cancelled", "withdrawn", "terminated", "abort",
    "no", "false", "not_refused", "refuse_pending", "", None,
])
def test_completed_non_refusal_keeps_original_lifecycle_even_with_rejected_nodes(result):
    source = stale_source()
    workflow = completed_workflow(result, events=[{"event_type": "EXECUTE_TASK_NORMAL", "result": "REFUSE"}])

    resolved = expenses.resolve_effective_dingtalk_approval(source, workflow)

    assert resolved == original_state(source)


@pytest.mark.parametrize("source", [
    {"approval_status": "TERMINATED", "approval_result": None},
    {"approval_status": "COMPLETED", "approval_result": "refuse"},
    {"approval_status": " running ", "approval_result": " agreed "},
    {},
])
@pytest.mark.parametrize("workflow", [None, {"status": "RUNNING", "result": "agree"}])
def test_missing_or_non_decisive_workflow_preserves_source_values(source, workflow):
    assert expenses.resolve_effective_dingtalk_approval(source, workflow) == original_state(source)


@pytest.mark.parametrize(("source_changes", "workflow_changes"), [
    ({}, {"approval_no": "DIFFERENT-APPROVAL"}),
    ({}, {"process_instance_id": "different-instance"}),
    ({"process_instance_id": None, "workflow_process_instance_id": "earlier-instance"}, {}),
    ({"corp_id": "source-corp"}, {"corp_id": "different-corp"}),
])
def test_workflow_identity_conflicts_cannot_override_source(source_changes, workflow_changes):
    source = {**stale_source(), **source_changes}

    resolved = expenses.resolve_effective_dingtalk_approval(source, completed_workflow(**workflow_changes))

    assert resolved == original_state(source)


def test_absent_optional_workflow_identity_leaves_identity_checks_to_caller():
    source = {"approval_status": "RUNNING", "approval_result": "agree"}

    resolved = expenses.resolve_effective_dingtalk_approval(source, {"status": "COMPLETED", "result": "refuse"})

    assert resolved == {"approval_status": "COMPLETED", "approval_result": "refuse"}
