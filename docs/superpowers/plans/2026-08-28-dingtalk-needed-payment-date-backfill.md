# DingTalk Needed Payment Date Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing DingTalk metadata sync fill an empty system needed-payment date from the uniquely matched source record without ever replacing a date already stored in the system.

**Architecture:** Reuse the already-normalized date produced by the operation, purchase, and monthly-payment mappers and expose it through the metadata boundary. During the existing per-request sync transaction, accept only a valid ISO date and apply it only when the current database value is blank, before the existing `dingtalk.sync` history snapshot is recorded.

**Tech Stack:** Python 3, FastAPI, SQLite, pytest, DingTalk PostgreSQL read models, existing daily-payables history replay.

---

## File Structure

- Modify `backend/app/external_expenses.py`: centralize construction of a sync metadata item and include the mapper's canonical `needed_payment_date`.
- Modify `backend/app/main.py`: validate a source date and apply it only to a blank request date in the existing sync transaction.
- Modify `backend/tests/test_api_workflows.py`: cover the metadata boundary, blank-only backfill, manual-value preservation, invalid-source handling, history visibility, daily-payables visibility, and repeat-sync behavior.

### Task 1: Carry the Canonical Date Across the Metadata Boundary

**Files:**
- Modify: `backend/app/external_expenses.py:1167-1235`
- Test: `backend/tests/test_api_workflows.py:20-50`
- Test: `backend/tests/test_api_workflows.py` near the existing external-expense mapper tests

- [ ] **Step 1: Import the metadata helper in the workflow test module**

Add `_external_expense_metadata` to the existing import from `backend.app.external_expenses`:

```python
from backend.app.external_expenses import (
    # existing imports remain unchanged
    _external_expense_metadata,
)
```

- [ ] **Step 2: Write a failing metadata-boundary test**

Add this focused test near the mapper tests:

```python
def test_external_expense_metadata_exposes_mapped_needed_payment_date():
    mapped = {
        "approval_no": "META-DATE-1",
        "source_type": "purchase",
        "source_label": "采购支出",
        "source_id": "9778",
        "needed_payment_date": "2026-07-24",
        "request_data": {
            "raw_extra": {
                "external_source": {
                    "approval_status": "RUNNING",
                    "needed_payment_date": "stale-value",
                }
            }
        },
    }

    metadata = _external_expense_metadata(mapped)

    assert metadata["approval_no"] == "META-DATE-1"
    assert metadata["source_id"] == "9778"
    assert metadata["needed_payment_date"] == "2026-07-24"
```

- [ ] **Step 3: Run the new test and confirm RED**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py::test_external_expense_metadata_exposes_mapped_needed_payment_date
```

Expected: collection fails because `_external_expense_metadata` does not exist.

- [ ] **Step 4: Add the focused metadata constructor**

Add this helper immediately before `fetch_external_expense_metadata`:

```python
def _external_expense_metadata(mapped: Dict[str, Any]) -> Dict[str, Any]:
    external_source = mapped["request_data"]["raw_extra"]["external_source"]
    return {
        "approval_no": mapped["approval_no"],
        "source_type": mapped["source_type"],
        "source_label": mapped["source_label"],
        "source_id": mapped["source_id"],
        **external_source,
        "needed_payment_date": mapped.get("needed_payment_date"),
    }
```

The canonical mapped date is deliberately added after `external_source` so an incidental nested value cannot override it.

- [ ] **Step 5: Use the helper for both standard and monthly sources**

Replace both duplicated `metadata.append({...})` blocks with:

```python
for source_row in source_rows:
    metadata.append(
        _external_expense_metadata(map_external_expense(source_row, user_names))
    )
for monthly_row in monthly_rows:
    metadata.append(
        _external_expense_metadata(map_monthly_payment(monthly_row, user_names))
    )
```

- [ ] **Step 6: Run the focused test and confirm GREEN**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py::test_external_expense_metadata_exposes_mapped_needed_payment_date
```

Expected: `1 passed`.

- [ ] **Step 7: Commit the metadata boundary change**

```bash
git add backend/app/external_expenses.py backend/tests/test_api_workflows.py
git commit -m "fix: expose DingTalk needed payment date metadata"
```

### Task 2: Backfill Only Blank Dates Before Recording History

**Files:**
- Modify: `backend/app/main.py:5860-5945`
- Test: `backend/tests/test_api_workflows.py:3936-4025`

- [ ] **Step 1: Extend the existing sync fixture with source dates**

Change the approval numbers and metadata setup so the test covers a fill candidate, a manual value, and an invalid source date:

```python
approval_nos = (
    "SYNC-FILL-BLANK",
    "SYNC-KEEP-MANUAL",
    "SYNC-IGNORE-INVALID-DATE",
)
source_dates = ("2026-07-24", "2026-07-25", "not-a-date")
metadata = [
    {
        "approval_no": approval_no,
        "source_type": "purchase",
        "source_label": "采购支出",
        "source_id": str(index),
        "approval_status": "RUNNING" if index == 1 else "TERMINATED",
        "approval_result": "agree",
        "beneficiary": "钉钉收款人",
        "execution_region": "中国China",
        "needed_payment_date": source_dates[index - 1],
    }
    for index, approval_no in enumerate(approval_nos, start=1)
]
```

Keep the workflow generator aligned with all three approval numbers.

- [ ] **Step 2: Give only the manual request an existing date and add the invalid-date request**

Add this field to the manual request payload:

```python
"needed_payment_date": "2026-08-20",
```

Create the invalid-source request after the manual request:

```python
invalid_source = client.post(
    f"/api/batches/{batch['id']}/requests",
    json={
        "dingding_id": approval_nos[2],
        "source_sheet": "悦为智能",
        "amount": 300,
    },
).json()["request"]
```

- [ ] **Step 3: Assert the backfill, manual preservation, invalid-date no-op, and history snapshot**

Before leaving `TestClient`, make the first sync, assert request values, inspect the history row, verify daily-payables visibility, run a second sync, and assert values again:

```python
response = client.post(f"/api/batches/{batch['id']}/external-expenses/sync-metadata")
assert response.status_code == 200

rows = client.get(
    f"/api/batches/{batch['id']}/requests",
    params={"dingtalk_lifecycle": "all"},
).json()["requests"]
by_id = {row["id"]: row for row in rows}
assert by_id[blank["id"]]["needed_payment_date"] == "2026-07-24"
assert by_id[manual["id"]]["needed_payment_date"] == "2026-08-20"
assert by_id[invalid_source["id"]]["needed_payment_date"] is None

with connect() as conn:
    history = conn.execute(
        """
        SELECT needed_payment_date
        FROM payable_history_versions
        WHERE request_id = ? AND event_type = 'dingtalk.sync'
        ORDER BY id DESC
        LIMIT 1
        """,
        (blank["id"],),
    ).fetchone()
assert history["needed_payment_date"] == "2026-07-24"

details = client.get(
    "/api/daily-payables/details",
    params={"date": date.today().isoformat()},
)
assert details.status_code == 200
assert any(
    item["dingding_id"] == "SYNC-FILL-BLANK"
    for item in details.json()["items"]
)

second = client.post(f"/api/batches/{batch['id']}/external-expenses/sync-metadata")
assert second.status_code == 200
second_rows = client.get(
    f"/api/batches/{batch['id']}/requests",
    params={"dingtalk_lifecycle": "all"},
).json()["requests"]
second_by_id = {row["id"]: row for row in second_rows}
assert second_by_id[blank["id"]]["needed_payment_date"] == "2026-07-24"
assert second_by_id[manual["id"]]["needed_payment_date"] == "2026-08-20"
assert second_by_id[invalid_source["id"]]["needed_payment_date"] is None
```

- [ ] **Step 4: Run the sync test and confirm RED**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py::test_dingtalk_sync_fills_blank_payee_and_manager_fields_without_overwriting_manual_values
```

Expected: failure because the blank request still has no `needed_payment_date`.

- [ ] **Step 5: Add a defensive ISO-date normalizer for sync metadata**

Add this helper near the DingTalk sync endpoint helpers in `backend/app/main.py`:

```python
def external_needed_payment_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None
```

This helper intentionally rejects malformed or non-ISO source values without failing the whole sync.

- [ ] **Step 6: Compute the blank-only date inside the existing request loop**

Immediately before the request `UPDATE`, add:

```python
needed_payment_date = row["needed_payment_date"]
if not str(needed_payment_date or "").strip() and approval_no in matched:
    needed_payment_date = (
        external_needed_payment_date(
            metadata_by_approval[approval_no][0].get("needed_payment_date")
        )
        or needed_payment_date
    )
```

- [ ] **Step 7: Persist the date in the same transaction and before history recording**

Change the existing SQL and parameter tuple to include the computed value:

```python
UPDATE payment_requests
SET raw_extra_json = ?, payee_name = ?, payee_account = ?,
    needed_payment_date = ?,
    general_manager_approval = ?, general_manager_approval_date = ?,
    updated_by = ?, updated_at = ?, version = version + 1
WHERE id = ? AND batch_id = ?
```

```python
(
    json.dumps(raw_extra, ensure_ascii=False, default=str),
    payee_name,
    payee_account,
    needed_payment_date,
    manager_approval,
    manager_approval_date,
    user["id"],
    timestamp,
    request_id,
    batch_id,
)
```

Do not move `record_request_state`; its existing position after region and payment refresh guarantees that the new date is captured in the `dingtalk.sync` history event.

- [ ] **Step 8: Run the sync and metadata tests and confirm GREEN**

Run:

```bash
python3 -m pytest -q \
  backend/tests/test_api_workflows.py::test_external_expense_metadata_exposes_mapped_needed_payment_date \
  backend/tests/test_api_workflows.py::test_dingtalk_sync_fills_blank_payee_and_manager_fields_without_overwriting_manual_values
```

Expected: `2 passed`.

- [ ] **Step 9: Run the wider DingTalk-sync regression group**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k 'dingtalk_sync or external_expense_metadata_sync'
```

Expected: all selected tests pass.

- [ ] **Step 10: Commit the transactional backfill**

```bash
git add backend/app/main.py backend/tests/test_api_workflows.py
git commit -m "fix: backfill blank needed payment dates from DingTalk"
```

### Task 3: Full Verification, Main Integration, and Production Deployment

**Files:**
- Verify: `backend/app/external_expenses.py`
- Verify: `backend/app/main.py`
- Verify: `backend/tests/test_api_workflows.py`
- Verify: `frontend/`
- Production backup: `/data/cashier-payment/backups/app-before-needed-date-backfill-20260828-<timestamp>.db`

- [ ] **Step 1: Inspect the final diff and protect scope**

Run:

```bash
git status --short
git diff e3a7273 -- backend/app/external_expenses.py backend/app/main.py backend/tests/test_api_workflows.py
```

Expected: only the planned metadata, blank-only sync, and test changes are present beyond the committed design and implementation plan.

- [ ] **Step 2: Run the full backend suite with the recorded unrelated time-boundary test deselected**

Run:

```bash
python3 -m pytest -q backend/tests \
  --deselect backend/tests/test_mexico_tracking.py::test_mexico_legacy_approver_and_node_fallbacks_match_stat_filters
```

Expected: all selected backend tests pass. The deselected test is the known unrelated fixed-timestamp Mexico timeout case documented before this change.

- [ ] **Step 3: Run frontend tests and production build**

Run:

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Expected: all frontend tests pass and the production build completes successfully.

- [ ] **Step 4: Review the change against the approved specification**

Confirm all of the following from code and tests:

```text
- Standard and monthly mapped dates cross the metadata boundary.
- Only a uniquely matched, valid ISO source date can fill a blank request date.
- Existing dates, invalid dates, unmatched rows, and conflicts are unchanged.
- The update and dingtalk.sync history event share one transaction.
- No schema, startup scan, or daily-payables business-rule change was added.
```

- [ ] **Step 5: Merge the verified branch into local `main`**

Run from the primary checkout:

```bash
git switch main
git merge --ff-only codex/dingtalk-payment-date-backfill
```

Expected: fast-forward succeeds while the user's existing untracked `.superpowers/` and `output/` directories remain untouched.

- [ ] **Step 6: Re-run focused verification on merged `main` and push**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k 'dingtalk_sync or external_expense_metadata_sync'
npm --prefix frontend run build
git push origin main
```

Expected: tests and build pass, then `origin/main` advances to the verified commit.

- [ ] **Step 7: Capture the pre-sync set of existing nonblank production dates**

Using the running production container's actual database path, execute a read-only ordered query over the current batch:

```sql
SELECT id, needed_payment_date
FROM payment_requests
WHERE batch_id = :current_batch_id
  AND TRIM(COALESCE(needed_payment_date, '')) <> ''
ORDER BY id;
```

Save the exact ordered `id -> needed_payment_date` output in the deployment log. This is the protected set: every listed row must retain the same date after synchronization.

- [ ] **Step 8: Stop the service and make a unique full production backup**

Resolve the production database path from the running service configuration. Stop the service, then call the repository's `backup_database()` against that database with a unique timestamped destination under:

```text
/data/cashier-payment/backups/app-before-needed-date-backfill-20260828-<timestamp>.db
```

Expected: the backup file is newly created, non-empty, and passes SQLite integrity check; no existing backup is overwritten.

- [ ] **Step 9: Deploy the pushed `main` and wait for health**

Use the established production deployment procedure to update the application to the pushed commit and restart it. Verify:

```text
- service reports running/healthy;
- public application returns HTTP 200;
- startup logs contain no migration, SQLite, or import errors.
```

- [ ] **Step 10: Trigger one metadata sync for the current draft batch**

Using the already authenticated administrator/finance production session, run the existing “同步钉钉流程” action once with the existing attachment behavior unchanged. Wait for the sync result to finish successfully.

- [ ] **Step 11: Verify the target request and manual-date preservation**

Confirm production request `202607221050000501984` now has:

```text
需求付款日期: 2026-07-24
已付: 14,500
待付: 14,500
执行地区: 中国（已核定）
```

Re-run the ordered nonblank-date query from Step 7. Expected: every pre-sync `id -> needed_payment_date` pair is still present and identical, while the target formerly blank row is newly present with `2026-07-24`. No other date change is allowed.

- [ ] **Step 12: Verify Daily Payables and production logs**

Open Daily Payables for `2026-08-28` and confirm request `202607221050000501984` appears with pending CNY `14,500`. Confirm the two future-dated requests remain absent for August 28, then check the service logs for errors from the sync and daily-payables requests.

- [ ] **Step 13: Remove the completed worktree after deployment verification**

From the primary checkout, run:

```bash
git worktree remove /Users/smk/.config/superpowers/worktrees/出纳请款/dingtalk-payment-date-backfill
git branch -d codex/dingtalk-payment-date-backfill
```

Expected: the verified commits remain on `main`; only the temporary worktree and merged feature branch are removed.
