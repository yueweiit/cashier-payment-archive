# DingTalk Payee and Manager Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate payee fields from DingTalk `beneficiario`, derive blank general-manager approvals from the latest CEO/general-manager workflow decision, and rename the Chinese UI label to “收款人”.

**Architecture:** Keep source interpretation in `backend/app/external_expenses.py`, where DingTalk form values and workflow events are already normalized. Apply only fill-if-empty updates inside the existing metadata-sync transaction in `backend/app/main.py`, so manual values win and the update remains atomic and versioned. Keep the database/API field `payee_name` unchanged and alter only user-facing Chinese labels.

**Tech Stack:** Python 3.9, FastAPI, SQLite, pytest, React 19, TypeScript, Node test runner, Vite.

---

## File Structure

- `backend/app/external_expenses.py`: normalize `beneficiario` into import data and source metadata; recognize the latest CEO/general-manager decision from normalized workflow events.
- `backend/app/main.py`: fill blank payee and manager-approval fields during the existing transactional DingTalk sync.
- `backend/tests/test_api_workflows.py`: backend unit and API regression coverage for import mapping, workflow recognition, fill-if-empty behavior, and manual-value preservation.
- `frontend/src/App.tsx`: change the Chinese grid label from “收款人名称” to “收款人”.
- `frontend/src/i18n.tsx`: change the Chinese translation key while preserving the Spanish translation.
- `frontend/tests/payee-label.test.mjs`: protect the Chinese label and Spanish translation.

### Task 1: Correct the DingTalk beneficiary mapping

**Files:**
- Modify: `backend/tests/test_api_workflows.py`
- Modify: `backend/app/external_expenses.py`

- [ ] **Step 1: Extend the existing purchase mapping test with failing expectations**

Add these assertions to `test_external_expense_mapping_uses_base_currency_and_purchase_form_values` after the existing beneficiary assertion:

```python
assert purchase["request_data"]["payee_name"] == purchase["beneficiary"]
assert purchase["request_data"]["payee_account"] == purchase["beneficiary"]
assert (
    purchase["request_data"]["raw_extra"]["external_source"]["beneficiary"]
    == purchase["beneficiary"]
)
```

- [ ] **Step 2: Run the test and verify it fails for the missing payee name**

Run:

```bash
python3 -m pytest backend/tests/test_api_workflows.py::test_external_expense_mapping_uses_base_currency_and_purchase_form_values -q
```

Expected: FAIL because `request_data["payee_name"]` does not exist.

- [ ] **Step 3: Add the minimal canonical mapping**

In `map_external_expense`, include the normalized beneficiary in both request fields and in `external_source`:

```python
"payee_name": beneficiary or None,
"payee_account": beneficiary or None,
```

Add this entry to the nested `request_data["raw_extra"]["external_source"]` mapping:

```python
"beneficiary": beneficiary or None,
```

- [ ] **Step 4: Run the mapping test and verify it passes**

Run:

```bash
python3 -m pytest backend/tests/test_api_workflows.py::test_external_expense_mapping_uses_base_currency_and_purchase_form_values -q
```

Expected: 1 passed.

- [ ] **Step 5: Commit the beneficiary mapping**

```bash
git add backend/app/external_expenses.py backend/tests/test_api_workflows.py
git commit -m "fix: map DingTalk beneficiary to payee fields"
```

### Task 2: Recognize the latest CEO/general-manager decision

**Files:**
- Modify: `backend/tests/test_api_workflows.py`
- Modify: `backend/app/external_expenses.py`

- [ ] **Step 1: Write failing unit tests for manager-level workflow recognition**

Import `general_manager_approval_from_workflow_events` from `backend.app.external_expenses`, then add:

```python
@pytest.mark.parametrize(
    "stage_name",
    ["悦为智能 CEO 审批", "总经理审批", "Gerente General", "Dirección General"],
)
def test_general_manager_approval_uses_latest_explicit_manager_node(stage_name):
    result = general_manager_approval_from_workflow_events([
        {
            "event_type": "EXECUTE_TASK_NORMAL",
            "stage_name": stage_name,
            "result": "AGREE",
            "event_time": "2026-08-24T16:20:00+08:00",
            "sequence_index": 3,
        }
    ])
    assert result == ("同意付款", "2026-08-24")


def test_general_manager_approval_uses_latest_decision_and_ignores_department_manager():
    assert general_manager_approval_from_workflow_events([
        {
            "event_type": "EXECUTE_TASK_NORMAL",
            "stage_name": "采购经理审批",
            "result": "AGREE",
            "event_time": "2026-08-24T09:00:00+08:00",
            "sequence_index": 1,
        }
    ]) is None

    assert general_manager_approval_from_workflow_events([
        {
            "event_type": "EXECUTE_TASK_NORMAL",
            "stage_name": "CEO 审批",
            "result": "AGREE",
            "event_time": "2026-08-23T09:00:00+08:00",
            "sequence_index": 1,
        },
        {
            "event_type": "EXECUTE_TASK_NORMAL",
            "stage_name": "CEO 审批",
            "result": "REFUSE",
            "event_time": "2026-08-24T09:00:00+08:00",
            "sequence_index": 2,
        },
    ]) is None
```

- [ ] **Step 2: Run the new tests and verify they fail because the helper is missing**

Run:

```bash
python3 -m pytest backend/tests/test_api_workflows.py -q -k 'general_manager_approval_uses_latest'
```

Expected: collection error or FAIL because `general_manager_approval_from_workflow_events` is not defined.

- [ ] **Step 3: Implement strict manager-stage recognition**

Add a strict regular expression and helper in `backend/app/external_expenses.py`:

```python
GENERAL_MANAGER_STAGE_RE = re.compile(
    r"(?:\bCEO\b|总经理|gerente\s+general|direcci[oó]n\s+general)",
    re.IGNORECASE,
)


def general_manager_approval_from_workflow_events(
    events: Iterable[Dict[str, Any]],
) -> Optional[tuple[str, str]]:
    candidates = [
        event
        for event in events
        if str(event.get("event_type") or "").upper() == "EXECUTE_TASK_NORMAL"
        and GENERAL_MANAGER_STAGE_RE.search(str(event.get("stage_name") or ""))
    ]
    if not candidates:
        return None
    latest = max(
        candidates,
        key=lambda event: (
            str(event.get("event_time") or ""),
            int(event.get("sequence_index") or 0),
        ),
    )
    event_time = str(latest.get("event_time") or "").strip()
    if str(latest.get("result") or "").upper() != "AGREE" or not event_time:
        return None
    return "同意付款", event_time[:10]
```

- [ ] **Step 4: Run the focused helper tests**

Run:

```bash
python3 -m pytest backend/tests/test_api_workflows.py -q -k 'general_manager_approval_uses_latest'
```

Expected: 5 passed.

- [ ] **Step 5: Commit the workflow recognizer**

```bash
git add backend/app/external_expenses.py backend/tests/test_api_workflows.py
git commit -m "feat: derive manager approval from DingTalk workflow"
```

### Task 3: Fill blank fields during DingTalk synchronization

**Files:**
- Modify: `backend/tests/test_api_workflows.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add a failing API test for transactional fill-if-empty behavior**

Add this focused test, which uses two matched approvals and verifies both the blank and manual cases:

```python
def test_dingtalk_sync_fills_blank_payee_and_manager_fields_without_overwriting_manual_values(monkeypatch):
    approval_nos = ("SYNC-FILL-BLANK", "SYNC-KEEP-MANUAL")
    metadata = [
        {
            "approval_no": approval_no,
            "source_type": "purchase",
            "source_label": "采购支出",
            "source_id": str(index),
            "approval_status": "RUNNING",
            "approval_result": "agree",
            "beneficiary": "钉钉收款人",
        }
        for index, approval_no in enumerate(approval_nos, start=1)
    ]
    workflows = [
        {
            "approval_no": approval_no,
            "process_instance_id": f"process-{index}",
            "status": "RUNNING",
            "result": "agree",
            "events": [
                {
                    "event_key": f"manager-{index}",
                    "process_instance_id": f"process-{index}",
                    "activity_id": "ceo-node",
                    "event_type": "EXECUTE_TASK_NORMAL",
                    "stage_name": "悦为智能 CEO 审批",
                    "result": "AGREE",
                    "operator_id": "ceo-user",
                    "operator_name": "CEO",
                    "event_time": "2026-08-24T16:20:00+08:00",
                    "sequence_index": 3,
                    "comment": None,
                    "images": [],
                    "attachments": [],
                    "trusted_finance": False,
                    "current": False,
                }
            ],
            "current_tasks": [],
        }
        for index, approval_no in enumerate(approval_nos, start=1)
    ]
    monkeypatch.setattr(main_module, "fetch_external_expense_metadata", lambda values: metadata)
    monkeypatch.setattr(main_module, "fetch_dingtalk_workflows", lambda values: workflows)
    monkeypatch.setattr(main_module, "fetch_external_expense_attachments", lambda values: [])

    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "sync-fill-fields"}).json()["batch"]
        blank = client.post(
            f"/api/batches/{batch['id']}/requests",
            json={"dingding_id": approval_nos[0], "source_sheet": "悦为智能", "amount": 100},
        ).json()["request"]
        manual = client.post(
            f"/api/batches/{batch['id']}/requests",
            json={
                "dingding_id": approval_nos[1],
                "source_sheet": "悦为智能",
                "amount": 200,
                "payee_name": "人工收款人",
                "payee_account": "人工账号",
                "general_manager_approval": "存在争议",
                "general_manager_approval_date": "2026-08-20",
            },
        ).json()["request"]

        response = client.post(f"/api/batches/{batch['id']}/external-expenses/sync-metadata")
        assert response.status_code == 200
        rows = client.get(f"/api/batches/{batch['id']}/requests").json()["requests"]
        by_id = {row["id"]: row for row in rows}

    blank_row = by_id[blank["id"]]
    assert blank_row["payee_name"] == "钉钉收款人"
    assert blank_row["payee_account"] == "钉钉收款人"
    assert blank_row["general_manager_approval"] == "同意付款"
    assert blank_row["general_manager_approval_date"] == "2026-08-24"

    manual_row = by_id[manual["id"]]
    assert manual_row["payee_name"] == "人工收款人"
    assert manual_row["payee_account"] == "人工账号"
    assert manual_row["general_manager_approval"] == "存在争议"
    assert manual_row["general_manager_approval_date"] == "2026-08-20"
```

- [ ] **Step 2: Run the focused API test and verify it fails on blank fields**

Run:

```bash
python3 -m pytest backend/tests/test_api_workflows.py::test_dingtalk_sync_fills_blank_payee_and_manager_fields_without_overwriting_manual_values -q
```

Expected: FAIL because sync currently updates only `raw_extra_json`.

- [ ] **Step 3: Apply fill-if-empty values inside the existing sync transaction**

Import `general_manager_approval_from_workflow_events` in `backend/app/main.py`. Before the first `UPDATE payment_requests` in the per-request sync loop, derive values without overwriting existing data:

```python
beneficiary = (
    str(external_source.get("beneficiary") or "").strip()
    if approval_no in matched
    else ""
)
payee_name = str(row["payee_name"] or "").strip() or beneficiary or None
payee_account = str(row["payee_account"] or "").strip() or beneficiary or None
manager_approval = row["general_manager_approval"]
manager_approval_date = row["general_manager_approval_date"]
if not str(manager_approval or "").strip() and len(request_workflows) == 1:
    derived_manager = general_manager_approval_from_workflow_events(
        request_workflows[0].get("events") or []
    )
    if derived_manager:
        manager_approval = derived_manager[0]
        manager_approval_date = manager_approval_date or derived_manager[1]
```

Extend the existing update statement so all values commit atomically:

```sql
UPDATE payment_requests
SET raw_extra_json = ?, payee_name = ?, payee_account = ?,
    general_manager_approval = ?, general_manager_approval_date = ?,
    updated_by = ?, updated_at = ?, version = version + 1
WHERE id = ? AND batch_id = ?
```

- [ ] **Step 4: Run the focused API test and metadata-sync regression test**

Run:

```bash
python3 -m pytest \
  backend/tests/test_api_workflows.py::test_dingtalk_sync_fills_blank_payee_and_manager_fields_without_overwriting_manual_values \
  backend/tests/test_api_workflows.py::test_external_expense_metadata_sync_statuses_conflicts_and_atomic_failure \
  -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit the transactional sync update**

```bash
git add backend/app/main.py backend/tests/test_api_workflows.py
git commit -m "fix: fill payee and manager fields during DingTalk sync"
```

### Task 4: Rename the Chinese payee label

**Files:**
- Create: `frontend/tests/payee-label.test.mjs`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/i18n.tsx`

- [ ] **Step 1: Write a failing frontend source-contract test**

Create `frontend/tests/payee-label.test.mjs`:

```javascript
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../src/i18n.tsx", import.meta.url), "utf8");

test("payee name uses the concise Chinese label and keeps its Spanish translation", () => {
  assert.match(
    appSource,
    /key:\s*"payee_name",\s*labelZh:\s*"收款人",\s*labelEs:\s*"Nombre del beneficiario"/,
  );
  assert.match(i18nSource, /"收款人":\s*"Nombre del beneficiario"/);
  assert.doesNotMatch(appSource, /收款人名称/);
  assert.doesNotMatch(i18nSource, /收款人名称/);
});
```

- [ ] **Step 2: Run the frontend test and verify it fails on the old label**

Run:

```bash
node --test frontend/tests/payee-label.test.mjs
```

Expected: FAIL because `App.tsx` and `i18n.tsx` still contain “收款人名称”.

- [ ] **Step 3: Change only the user-facing Chinese labels**

In `frontend/src/App.tsx`, set the `payee_name` column label to:

```typescript
{ key: "payee_name", labelZh: "收款人", labelEs: "Nombre del beneficiario", width: 200 },
```

In `frontend/src/i18n.tsx`, replace the old key with:

```typescript
"收款人": "Nombre del beneficiario",
```

- [ ] **Step 4: Run the focused test and production type/build check**

Run:

```bash
node --test frontend/tests/payee-label.test.mjs
npm run build
```

Expected: 1 test passed; TypeScript and Vite build pass.

- [ ] **Step 5: Commit the label change**

```bash
git add frontend/src/App.tsx frontend/src/i18n.tsx frontend/tests/payee-label.test.mjs
git commit -m "fix: rename payee label"
```

### Task 5: Complete regression verification

**Files:**
- Verify only; no production file changes expected.

- [ ] **Step 1: Run the complete backend suite**

```bash
python3 -m pytest backend/tests -q
```

Expected: all backend tests pass with only the repository's existing deprecation warnings.

- [ ] **Step 2: Run the complete frontend suite**

```bash
npm run test:frontend
```

Expected: all frontend tests pass.

- [ ] **Step 3: Build the production frontend**

```bash
npm run build
```

Expected: TypeScript checking and Vite production build pass.

- [ ] **Step 4: Check the final diff and worktree state**

```bash
git diff --check
git status --short
git log --oneline --decorate -6
```

Expected: no whitespace errors; only the intended commits are ahead of `main`; worktree has no uncommitted production or test changes.
