# DingTalk Invoice and Project Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate blank account nature and project fields from the new DingTalk invoice/project form components during both import and later workflow synchronization, without overwriting existing values.

**Architecture:** Extend the existing shared DingTalk form mapper in `external_expenses.py` so every supported source emits normalized `payment_account` and `project` values. Expose those values through the existing metadata interface, then let the current transactional synchronization in `main.py` fill only blank request fields. Reuse the existing request-state history snapshot for auditability and add regression tests around import, synchronization, conflicts, manual preservation, and idempotence.

**Tech Stack:** Python 3.9, FastAPI, SQLite, PostgreSQL read-only source gateway, pytest, React/TypeScript (verification only), Vite.

---

## File Map

- Modify `backend/app/external_expenses.py`: recognize the bilingual DingTalk components, normalize invoice choices, map project/account fields, and expose them as metadata.
- Modify `backend/app/main.py`: validate metadata values and fill blank `payment_account` and `project` fields in the existing synchronization transaction.
- Modify `backend/tests/test_api_workflows.py`: add mapping/import/synchronization/manual-preservation/idempotence/history regression coverage.
- Reference `docs/superpowers/specs/2026-08-31-dingtalk-invoice-project-fields-design.md`: approved behavioral contract; no further product changes are part of this plan.

### Task 1: Parse and expose DingTalk invoice/project fields

**Files:**
- Modify: `backend/app/external_expenses.py:24-72`
- Modify: `backend/app/external_expenses.py:700-725`
- Modify: `backend/app/external_expenses.py:944-1020`
- Modify: `backend/app/external_expenses.py:1167-1177`
- Modify: `backend/app/external_expenses.py:1883-2055`
- Test: `backend/tests/test_api_workflows.py:59-80`
- Test: `backend/tests/test_api_workflows.py:3238-3300`

- [ ] **Step 1: Write failing parser and metadata tests**

Add a parameterized test that builds an operation-source row with the bilingual components and verifies the exact account mapping:

```python
@pytest.mark.parametrize(
    ("invoice_value", "expected_account"),
    [
        ("是", "公户"),
        ("有发票", "公户"),
        ("Sí", "公户"),
        ("Si", "公户"),
        ("Yes", "公户"),
        ("否", "私户"),
        ("无发票", "私户"),
        ("No", "私户"),
        ("待定", None),
        ("", None),
    ],
)
def test_external_expense_maps_invoice_choice_and_project(invoice_value, expected_account):
    mapped = map_external_expense({
        "source_type": "operation",
        "source_id": "invoice-project-1",
        "effective_date": "2026-08-31",
        "approval_no": "INV-PROJECT-1",
        "creator_name": "user-1",
        "applicant_department": "悦为智能",
        "approval_status": "RUNNING",
        "approval_result": "agree",
        "execution_region": "中国China",
        "beneficiary": "测试收款人",
        "expense_type": "采购",
        "summary": "测试新字段",
        "source_currency": "CNY",
        "source_amount": 100,
        "base_currency_amount": 100,
        "raw_data": {
            "formComponentValues": [
                {"name": "是否有发票¿Existe factura?", "value": invoice_value},
                {"name": "项目归属Pertenencia del proyecto", "value": "墨西哥新工厂"},
            ]
        },
    }, {"user-1": "测试申请人"})

    assert mapped["request_data"]["payment_account"] == expected_account
    assert mapped["request_data"]["project"] == "墨西哥新工厂"
    metadata = _external_expense_metadata(mapped)
    assert metadata["payment_account"] == expected_account
    assert metadata["project"] == "墨西哥新工厂"
```

Extend the monthly mapping fixture with invoice and project components and assert that the invoice-derived `公户` value takes precedence when it is present. Keep the existing `付款账户类型` assertion in a second case where the new invoice component is absent, proving the legacy fallback remains intact.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k 'external_expense_maps_invoice_choice_and_project or monthly_payment_mapping_aggregates'
```

Expected: FAIL because `payment_account` is absent for standard rows, the project form component is ignored, and `_external_expense_metadata()` does not expose either field.

- [ ] **Step 3: Add bilingual component constants and conservative normalization**

Add constants beside the existing component prefix constants:

```python
INVOICE_COMPONENT_PREFIXES = ("是否有发票", "existe factura")
PROJECT_COMPONENT_PREFIXES = ("项目归属", "pertenencia del proyecto")
LEGACY_PAYMENT_ACCOUNT_PREFIXES = ("付款账户类型", "付款账户")
```

Add a small normalizer beside `_form_component_value()`:

```python
def _fold_choice(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value) or "").casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[\s/|,，;；:：?¿._-]+", "", text)


def payment_account_from_invoice(value: Any) -> Optional[str]:
    normalized = _fold_choice(value)
    if normalized in {"是", "有", "有发票", "si", "yes"}:
        return "公户"
    if normalized in {"否", "无", "无发票", "no"}:
        return "私户"
    return None
```

The exact-match sets are intentional: values such as `待定`, `不是`, or arbitrary comments must not be guessed.

- [ ] **Step 4: Map the fields once for every source type**

At the beginning of `map_external_expense()`, after `form_values` is available, derive:

```python
invoice_value = _form_component_value(form_values, *INVOICE_COMPONENT_PREFIXES)
form_project = _form_component_value(form_values, *PROJECT_COMPONENT_PREFIXES)
payment_account = payment_account_from_invoice(invoice_value)
project = form_project or _text(row.get("project"))
```

Add `payment_account` and `project` to `request_data`, the top-level mapped result, and `raw_extra.external_source`. Preserve the raw invoice display value in `external_source["invoice_value"]` for diagnosis.

For monthly approvals, retain the existing explicit account-type fallback only when no recognized invoice value was found:

```python
if not mapped["request_data"].get("payment_account"):
    mapped["request_data"]["payment_account"] = _monthly_component(
        form_values,
        *LEGACY_PAYMENT_ACCOUNT_PREFIXES,
    )
```

Expose the canonical values from `_external_expense_metadata()` rather than trusting stale values nested in prior raw metadata:

```python
return {
    "approval_no": mapped["approval_no"],
    "source_type": mapped["source_type"],
    "source_label": mapped["source_label"],
    "source_id": mapped["source_id"],
    **external_source,
    "needed_payment_date": mapped.get("needed_payment_date"),
    "payment_account": mapped.get("payment_account"),
    "project": mapped.get("project"),
}
```

- [ ] **Step 5: Run parser/import tests and verify GREEN**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k 'external_expense_metadata or external_expense_maps_invoice_choice_and_project or monthly_payment_mapping_aggregates'
```

Expected: PASS. The legacy monthly test must still prove `付款账户类型` works when the new invoice field is missing.

- [ ] **Step 6: Commit the shared mapping change**

```bash
git add backend/app/external_expenses.py backend/tests/test_api_workflows.py
git commit -m "feat: map DingTalk invoice and project fields"
```

### Task 2: Fill only blank fields during DingTalk synchronization

**Files:**
- Modify: `backend/app/main.py:5557-5565`
- Modify: `backend/app/main.py:5890-5955`
- Test: `backend/tests/test_api_workflows.py:3970-4125`

- [ ] **Step 1: Extend the existing sync test with blank/manual/invalid cases**

Add canonical metadata to the blank item and conflicting values to the manual item:

```python
metadata[0].update({
    "payment_account": "公户",
    "project": "钉钉项目 A",
})
metadata[1].update({
    "payment_account": "私户",
    "project": "钉钉项目 B",
})
metadata[2].update({
    "payment_account": "未知账户",
    "project": "",
})
```

Create the manual request with explicit values:

```python
"payment_account": "人工账户",
"project": "人工项目",
```

After synchronization, assert:

```python
assert by_id[blank["id"]]["payment_account"] == "公户"
assert by_id[blank["id"]]["project"] == "钉钉项目 A"
assert by_id[manual["id"]]["payment_account"] == "人工账户"
assert by_id[manual["id"]]["project"] == "人工项目"
assert by_id[invalid_source["id"]]["payment_account"] is None
assert by_id[invalid_source["id"]]["project"] is None
```

Extend the `payable_history_versions` query and assertion:

```python
SELECT needed_payment_date, payment_account, project
FROM payable_history_versions
WHERE source_request_id = ? AND event_type = 'dingtalk.sync'
ORDER BY id DESC
LIMIT 1
```

```python
assert history["payment_account"] == "公户"
assert history["project"] == "钉钉项目 A"
```

Repeat the same business-field assertions after the second sync to prove idempotence. Add a conflict-source request to `test_external_expense_metadata_sync_statuses_conflicts_and_atomic_failure()` with empty fields and assert that conflict metadata does not populate them.

- [ ] **Step 2: Run the sync tests and verify RED**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k 'dingtalk_sync_fills_blank_payee_and_manager_fields or external_expense_metadata_sync_statuses_conflicts'
```

Expected: FAIL because the synchronization update does not yet set `payment_account` or `project`.

- [ ] **Step 3: Add strict metadata validators**

Beside `external_needed_payment_date()`, add:

```python
def external_payment_account(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text if text in {"公户", "私户"} else None


def external_project(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None
```

These validators protect the local database if a malformed or older source record bypasses the shared mapper.

- [ ] **Step 4: Fill blank fields inside the existing transaction**

In `_sync_external_expense_metadata_blocking()`, reuse the already unique `matched` branch:

```python
payment_account = row["payment_account"]
project = row["project"]
if approval_no in matched:
    metadata = metadata_by_approval[approval_no][0]
    if not str(payment_account or "").strip():
        payment_account = external_payment_account(metadata.get("payment_account")) or payment_account
    if not str(project or "").strip():
        project = external_project(metadata.get("project")) or project
```

Add both values to the existing `UPDATE payment_requests` statement and parameter list:

```sql
SET raw_extra_json = ?, payee_name = ?, payee_account = ?,
    payment_account = ?, project = ?, needed_payment_date = ?,
    general_manager_approval = ?, general_manager_approval_date = ?,
    updated_by = ?, updated_at = ?, version = version + 1
```

Do not add another transaction or audit mechanism. The existing `record_request_state(..., event_type="dingtalk.sync")` call must run after the update so its snapshot contains both filled fields.

- [ ] **Step 5: Run sync tests and verify GREEN**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k 'dingtalk_sync_fills_blank_payee_and_manager_fields or external_expense_metadata_sync_statuses_conflicts'
```

Expected: PASS with blank fields filled, manual fields unchanged, conflicts untouched, history updated, and the second sync unchanged.

- [ ] **Step 6: Commit the synchronization change**

```bash
git add backend/app/main.py backend/tests/test_api_workflows.py
git commit -m "feat: backfill DingTalk account and project fields"
```

### Task 3: Regression verification and documentation consistency

**Files:**
- Verify: `backend/app/external_expenses.py`
- Verify: `backend/app/main.py`
- Verify: `backend/tests/test_api_workflows.py`
- Verify: `frontend/src/App.tsx`
- Verify: `docs/superpowers/specs/2026-08-31-dingtalk-invoice-project-fields-design.md`

- [ ] **Step 1: Run the focused backend suite**

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k 'external_expense or dingtalk_sync'
```

Expected: all selected tests PASS.

- [ ] **Step 2: Run the complete backend suite**

```bash
python3 -m pytest -q backend/tests
```

Expected: all tests PASS. If a known date-sensitive test is unrelated, document its exact name and independently verify that the new focused tests pass; do not silently ignore a new failure.

- [ ] **Step 3: Run frontend tests and production build**

```bash
npm run test:frontend
npm run build
```

Expected: frontend tests PASS; TypeScript checking and Vite production build succeed.

- [ ] **Step 4: Review the final diff and spec coverage**

```bash
git diff --check
git diff main...HEAD -- backend/app/external_expenses.py backend/app/main.py backend/tests/test_api_workflows.py docs/superpowers/specs/2026-08-31-dingtalk-invoice-project-fields-design.md
```

Confirm every approved rule is represented: bilingual labels, conservative invoice mapping, direct project text, new-import behavior, blank-only sync, manual preservation, conflicts, idempotence, history, and no schema/frontend change.

- [ ] **Step 5: Commit any verification-only corrections**

If verification requires a test or documentation correction, stage only the affected files and commit:

```bash
git add backend/tests/test_api_workflows.py docs/superpowers/specs/2026-08-31-dingtalk-invoice-project-fields-design.md
git commit -m "test: cover DingTalk invoice and project synchronization"
```

If no correction is required, do not create an empty commit.

### Task 4: Merge and deploy to production

**Files:**
- Deploy from repository root: `/Users/smk/Documents/出纳请款`
- Production checkout: `/www/wwwroot/cashier-payment-archive`
- Production database: `/www/wwwroot/cashier-payment-archive/data/app.db`
- Backup root: `/data/cashier-payment/backups`

- [ ] **Step 1: Merge the verified branch into local `main` and push**

From the primary checkout, verify no unrelated tracked changes, fast-forward `main`, and push:

```bash
git status --short --branch
git switch main
git merge --ff-only codex/dingtalk-invoice-project-fields
git push origin main
```

Expected: `main` and `origin/main` point to the verified feature commit. Preserve the existing untracked `.superpowers/` and `output/` directories.

- [ ] **Step 2: Record pre-deploy production state**

Using the user's authenticated external Chrome and Aliyun Workbench, run in `/www/wwwroot/cashier-payment-archive`:

```bash
git rev-parse HEAD
systemctl is-active cashier-payment
```

Expected: the old commit is recorded and the service is `active` before the maintenance window.

- [ ] **Step 3: Stop the service and back up the database**

Create a unique directory under `/data/cashier-payment/backups`, stop the service, copy `data/app.db`, and verify the copy. Keep the returned `backup_dir` value in the same Workbench terminal session for the later commands:

```bash
backup_dir="$(mktemp -d /data/cashier-payment/backups/20260831-invoice-project-fields-XXXXXX)"
printf '%s\n' "$backup_dir"
systemctl stop cashier-payment
cp /www/wwwroot/cashier-payment-archive/data/app.db "$backup_dir/app.db"
sha256sum "$backup_dir/app.db"
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('$backup_dir/app.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0])"
```

Expected: the backup has a nonzero size, SHA-256 is recorded, and `PRAGMA integrity_check` prints `ok`.

- [ ] **Step 4: Deploy and start the service**

```bash
deploy_started="$(date '+%Y-%m-%d %H:%M:%S')"
git pull --ff-only origin main
npm run build
systemctl start cashier-payment
systemctl is-active cashier-payment
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8011/
```

Expected: build succeeds, service is `active`, and local HTTP status is `200`.

- [ ] **Step 5: Trigger one online DingTalk synchronization and validate data**

Before syncing, save every current account/project value for the newest draft batch into the deployment backup directory:

```bash
BACKFILL_SNAPSHOT="$backup_dir/pre-sync-invoice-project.json" .venv/bin/python - <<'PY'
import json
import os
import sqlite3
from pathlib import Path

connection = sqlite3.connect("data/app.db")
connection.row_factory = sqlite3.Row
batch = connection.execute(
    "SELECT id FROM request_batches WHERE status = 'draft' ORDER BY id DESC LIMIT 1"
).fetchone()
assert batch is not None, "没有可用于线上验收的草稿批次"
rows = connection.execute(
    """
    SELECT id, dingding_id, payment_account, project
    FROM payment_requests
    WHERE batch_id = ?
    ORDER BY id
    """,
    (batch["id"],),
).fetchall()
payload = {"batch_id": batch["id"], "rows": [dict(row) for row in rows]}
Path(os.environ["BACKFILL_SNAPSHOT"]).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps({"batch_id": batch["id"], "row_count": len(rows)}, ensure_ascii=False))
PY
```

In the production UI, open the recorded draft batch, click “同步钉钉流程” once, and wait for completion. Then compare the database with the saved snapshot:

```bash
BACKFILL_SNAPSHOT="$backup_dir/pre-sync-invoice-project.json" .venv/bin/python - <<'PY'
import json
import os
import sqlite3
from pathlib import Path

before_payload = json.loads(Path(os.environ["BACKFILL_SNAPSHOT"]).read_text(encoding="utf-8"))
before = {int(row["id"]): row for row in before_payload["rows"]}
connection = sqlite3.connect("data/app.db")
connection.row_factory = sqlite3.Row
after_rows = connection.execute(
    """
    SELECT id, dingding_id, payment_account, project
    FROM payment_requests
    WHERE batch_id = ?
    ORDER BY id
    """,
    (before_payload["batch_id"],),
).fetchall()
after = {int(row["id"]): dict(row) for row in after_rows}

changed_existing = []
new_accounts = []
new_projects = []
for request_id, old in before.items():
    current = after[request_id]
    for field in ("payment_account", "project"):
        old_value = str(old.get(field) or "").strip()
        new_value = str(current.get(field) or "").strip()
        if old_value and new_value != old_value:
            changed_existing.append({
                "id": request_id,
                "field": field,
                "before": old_value,
                "after": new_value,
            })
    if not str(old.get("payment_account") or "").strip() and str(current.get("payment_account") or "").strip():
        new_accounts.append({"id": request_id, "value": current["payment_account"]})
    if not str(old.get("project") or "").strip() and str(current.get("project") or "").strip():
        new_projects.append({"id": request_id, "value": current["project"]})

assert changed_existing == [], changed_existing
assert {row["value"] for row in new_accounts} <= {"公户", "私户"}, new_accounts
assert all(str(row["value"]).strip() for row in new_projects)
print(json.dumps({
    "changed_existing": changed_existing,
    "new_accounts": new_accounts,
    "new_projects": new_projects,
}, ensure_ascii=False))
PY
```

Also open representative newly filled rows in the grid and confirm the displayed “账户性质”和“项目归属” match the corresponding DingTalk approval.

- [ ] **Step 6: Complete production health checks**

```bash
git rev-parse --short=12 HEAD
journalctl -u cashier-payment --since "$deploy_started" -p warning --no-pager
```

From the local machine:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://8.135.70.130:8011/
```

Expected: deployed commit matches `origin/main`, no new service warnings are present, and public HTTP status is `200`.

- [ ] **Step 7: Clean the temporary worktree and feature branch**

Only after production verification and after confirming the worktree is clean:

```bash
git worktree remove /Users/smk/.config/superpowers/worktrees/出纳请款/dingtalk-invoice-project-fields
git branch -d codex/dingtalk-invoice-project-fields
```

Expected: the temporary worktree and already-merged feature branch are removed; the production backup remains available.
