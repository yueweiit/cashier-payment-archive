# China Intermediate Import Region Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Mexico execution-region expenses from appearing in or being imported through the China workbench's DingTalk intermediate-table pull while preserving Mexico tracking and legacy blank-region China compatibility.

**Architecture:** Keep the shared China/Mexico expense mappers unchanged, and add a China-workbench-specific region boundary around preview and import. Standard PostgreSQL queries reject explicit Mexico rows early; one reusable region predicate then covers monthly payloads, blank-region Mexico Sheets, applicant options, and direct import requests using the existing authoritative `resolve_region` logic.

**Tech Stack:** Python 3, FastAPI, SQLite, PostgreSQL/psycopg source reads, pytest, React/Vite production build, systemd deployment.

---

## File Map

- Modify: `backend/app/external_expenses.py`
  - Owns China-workbench region predicates, standard preview SQL, monthly payload filtering, applicant-option filtering, and shared external-expense mapping.
- Modify: `backend/app/main.py`
  - Applies the server-side region guard after re-fetching selected source records and before the import candidate split.
- Modify: `backend/tests/test_api_workflows.py`
  - Defines SQL, region-predicate, monthly-preview, applicant-option, and direct-import regression coverage.
- Verify only: `frontend/`
  - No frontend source change is required because the backend response shape remains unchanged.
- No database schema or migration changes.

### Task 1: Define failing region-boundary tests

**Files:**
- Modify: `backend/tests/test_api_workflows.py:26-43`
- Modify: `backend/tests/test_api_workflows.py:1586-1638`
- Modify: `backend/tests/test_api_workflows.py:3341-3355`

- [ ] **Step 1: Extend the test helper with source-region fields**

Add optional `execution_region` and `source_sheet` arguments to `external_expense_test_row`, use `source_sheet` for the department and request Sheet, and persist `execution_region` in `raw_extra.external_source`:

```python
def external_expense_test_row(
    approval_no: str,
    source_id: str,
    *,
    source_type: str = "operation",
    status: str = "RUNNING",
    amount: float = 123.45,
    beneficiary: str = "测试收款信息",
    warnings=None,
    execution_region: str = "",
    source_sheet: str = "测试部门",
) -> dict:
    source_label = {
        "operation": "运营支出",
        "purchase": "采购支出",
        "monthly": "月结付款",
    }[source_type]
    request_data = {
        "dingding_id": approval_no,
        "expense_type": "测试支出",
        "summary": "中间表测试",
        "amount": amount,
        "currency": "CNY",
        "payee_account": beneficiary or None,
        "source_sheet": source_sheet,
        "raw_extra": {
            "external_source": {
                "system": "dingtalk_expense_database",
                "table": f"approval_expense_{source_type}",
                "record_id": source_id,
                "approval_no": approval_no,
                "approval_status": status,
                "applicant_id": "test-user-id",
                "applicant": "测试申请人",
                "applicant_department": source_sheet,
                "application_date": "2026-07-15",
                "execution_region": execution_region,
            }
        },
    }
    return {
        "source_type": source_type,
        "source_label": source_label,
        "source_id": source_id,
        "application_date": "2026-07-15",
        "approval_no": approval_no,
        "applicant_id": "test-user-id",
        "applicant": "测试申请人",
        "applicant_department": source_sheet,
        "approval_status": status,
        "approval_result": "agree",
        "summary": "中间表测试",
        "amount": amount,
        "beneficiary": beneficiary,
        "needed_payment_date": "2026-07-20",
        "warnings": warnings or [],
        "errors": [],
        "source_conflict": False,
        "request_data": request_data,
    }
```

The existing default values must preserve every prior fixture's behavior.

- [ ] **Step 2: Change the SQL contract test to require China-only preview parameters**

In `test_external_expense_exact_approval_number_ignores_dates`, change the expected regex parameter to:

```python
    assert exact_params[-3] == r"(中国|china)"
```

Keep the blank-region SQL assertion so legacy records remain eligible for the later Sheet fallback.

- [ ] **Step 3: Add a pure region-boundary regression test**

Import `CHINA_WORKBENCH_REGION_ERROR`, `china_workbench_external_expense_allowed`, and `mark_china_workbench_external_expense` from `backend.app.external_expenses`, then add:

```python
def test_china_workbench_external_expense_region_boundary():
    explicit_china = external_expense_test_row(
        "REGION-CN",
        "9101",
        execution_region="中国China",
        source_sheet="YW MOLDES MX模具",
    )
    explicit_mexico = external_expense_test_row(
        "REGION-MX",
        "9102",
        execution_region="墨西哥México",
        source_sheet="悦为智能 YW Tech_Ai",
    )
    mexico_sheet_fallback = external_expense_test_row(
        "REGION-MX-SHEET",
        "9103",
        source_sheet="YW MOLDES MX模具",
    )
    unknown_legacy = external_expense_test_row("REGION-LEGACY", "9104")

    assert china_workbench_external_expense_allowed(explicit_china)
    assert not china_workbench_external_expense_allowed(explicit_mexico)
    assert not china_workbench_external_expense_allowed(mexico_sheet_fallback)
    assert china_workbench_external_expense_allowed(unknown_legacy)

    mark_china_workbench_external_expense(explicit_mexico)
    mark_china_workbench_external_expense(explicit_mexico)
    assert explicit_mexico["errors"].count(CHINA_WORKBENCH_REGION_ERROR) == 1
```

This locks the precedence rule: explicit execution region overrides Sheet mapping, known Mexico fallback is excluded, and unknown blank-region legacy rows remain compatible.

- [ ] **Step 4: Run the red tests**

Run:

```bash
python3 -m pytest -q \
  backend/tests/test_api_workflows.py::test_external_expense_exact_approval_number_ignores_dates \
  backend/tests/test_api_workflows.py::test_china_workbench_external_expense_region_boundary
```

Expected: FAIL because preview SQL still uses the China-or-Mexico regex and the China-workbench helper symbols do not exist.

- [ ] **Step 5: Commit the red tests**

```bash
git add backend/tests/test_api_workflows.py
git commit -m "test: define China intermediate region boundary"
```

### Task 2: Implement the shared China-workbench predicate and preview filtering

**Files:**
- Modify: `backend/app/external_expenses.py:25-48`
- Modify: `backend/app/external_expenses.py:622-690`
- Modify: `backend/app/external_expenses.py:1003-1105`
- Test: `backend/tests/test_api_workflows.py`

- [ ] **Step 1: Add the China-only query pattern and import error constant**

Keep `ALLOWED_EXECUTION_REGION_PATTERN` unchanged for the shared mapper, and add:

```python
CHINA_WORKBENCH_EXECUTION_REGION_PATTERN = r"(中国|china)"
CHINA_WORKBENCH_REGION_ERROR = "执行地区为墨西哥，不允许导入中国请款工作台"
```

- [ ] **Step 2: Add one reusable region predicate and idempotent import marker**

Add these functions after `execution_region_is_allowed`:

```python
def china_workbench_source_allowed(
    execution_region: Any,
    source_sheet: Any,
) -> bool:
    decision = resolve_region(
        execution_region=_text(execution_region),
        source_sheet=_text(source_sheet),
    )
    return decision.region != "mexico"


def china_workbench_external_expense_allowed(row: Dict[str, Any]) -> bool:
    request_data = row.get("request_data") if isinstance(row.get("request_data"), dict) else {}
    raw_extra = request_data.get("raw_extra") if isinstance(request_data.get("raw_extra"), dict) else {}
    external_source = raw_extra.get("external_source") if isinstance(raw_extra.get("external_source"), dict) else {}
    return china_workbench_source_allowed(
        external_source.get("execution_region"),
        row.get("applicant_department") or request_data.get("source_sheet"),
    )


def mark_china_workbench_external_expense(row: Dict[str, Any]) -> None:
    if china_workbench_external_expense_allowed(row):
        return
    errors = row.setdefault("errors", [])
    if CHINA_WORKBENCH_REGION_ERROR not in errors:
        errors.append(CHINA_WORKBENCH_REGION_ERROR)
```

- [ ] **Step 3: Tighten standard preview SQL without changing the shared mapper**

In `_preview_conditions`, replace only the preview parameter:

```python
        CHINA_WORKBENCH_EXECUTION_REGION_PATTERN,
```

Retain the SQL shape `(blank OR region matches pattern)` so empty legacy regions reach the source-Sheet fallback.

- [ ] **Step 4: Filter standard and monthly applicant options with the same rule**

Include `execution_region` in the standard option query's select and group-by clauses:

```python
        option_query = f"""
            {SOURCE_ROWS_CTE}
            SELECT creator_name, applicant_department, approval_title,
                   execution_region, COUNT(*) AS count
            FROM source_rows
            WHERE {option_sql}
              AND creator_name IS NOT NULL
              AND BTRIM(creator_name) <> ''
            GROUP BY creator_name, applicant_department, approval_title,
                     execution_region
            ORDER BY creator_name, count DESC
        """
```

Add the monthly-instance predicate beside `_monthly_payment_query`:

```python
def _monthly_payment_allowed_in_china_workbench(instance: Dict[str, Any]) -> bool:
    raw_payload = _json_object(instance.get("raw_payload"))
    execution_region = _form_component_value(
        _form_values(raw_payload),
        *EXECUTION_REGION_COMPONENT_PREFIXES,
    )
    return china_workbench_source_allowed(
        execution_region,
        raw_payload.get("originatorDeptName"),
    )
```

Then filter the option and monthly input collections before fetching names or mapping:

```python
    applicant_rows = [
        row
        for row in applicant_rows
        if china_workbench_source_allowed(
            row.get("execution_region"),
            row.get("applicant_department"),
        )
    ]
    monthly_rows = [
        row for row in monthly_rows
        if _monthly_payment_allowed_in_china_workbench(row)
    ]
    monthly_option_rows = [
        row for row in monthly_option_rows
        if _monthly_payment_allowed_in_china_workbench(row)
    ]
```

When converting each monthly option into `applicant_rows`, also carry the extracted `execution_region`; otherwise the following common filter would incorrectly fall back to the Sheet and discard an explicit China record whose Sheet name is mapped to Mexico.

- [ ] **Step 5: Apply a final mapped-row filter**

After mapping all standard and monthly preview rows, retain only rows for which `china_workbench_external_expense_allowed(row)` is true. This final pass is required even when SQL already filtered standard sources because it covers blank-region Mexico Sheets and source format differences.

```python
    mapped_rows = [
        row
        for row in mapped_rows
        if china_workbench_external_expense_allowed(row)
    ]
```

- [ ] **Step 6: Add a monthly preview and applicant-option regression test**

Import `backend.app.external_expenses as external_expenses_module`, then add this test. It uses actual raw payload form components for `执行地区`, so applicant filtering exercises monthly extraction independently of the mapped-row predicate:

```python
def test_external_expense_preview_excludes_mexico_monthly_rows_and_applicants(monkeypatch):
    def instance(source_id: str, approval_no: str, user_id: str, department: str, region: str) -> dict:
        return {
            "source_id": source_id,
            "status": "RUNNING",
            "result": "agree",
            "title": f"{user_id} submitted monthly payment",
            "raw_payload": {
                "businessId": approval_no,
                "originatorUserId": user_id,
                "originatorDeptName": department,
                "formComponentValues": [{"name": "执行地区", "value": region}],
            },
        }

    china_instance = instance("9301", "MONTHLY-CN", "china-user", "YW MOLDES MX模具", "中国China")
    mexico_instance = instance("9302", "MONTHLY-MX", "mexico-user", "YW MOLDES MX模具", "墨西哥México")

    monkeypatch.setattr(
        external_expenses_module,
        "_monthly_payment_query",
        lambda **kwargs: [china_instance, mexico_instance],
    )
    monkeypatch.setattr(
        external_expenses_module,
        "fetch_dingtalk_user_names",
        lambda user_ids: {"china-user": "中国申请人", "mexico-user": "Mexico Applicant"},
    )

    def fake_map_monthly(row, user_names):
        raw_payload = row["raw_payload"]
        region = raw_payload["formComponentValues"][0]["value"]
        return external_expense_test_row(
            raw_payload["businessId"],
            row["source_id"],
            source_type="monthly",
            execution_region=region,
            source_sheet=raw_payload["originatorDeptName"],
        ) | {
            "applicant_id": raw_payload["originatorUserId"],
            "applicant": user_names[raw_payload["originatorUserId"]],
        }

    monkeypatch.setattr(external_expenses_module, "map_monthly_payment", fake_map_monthly)

    result = external_expenses_module.preview_external_expenses(
        date_from=date(2026, 8, 25),
        date_to=date(2026, 8, 25),
        source_types=["monthly"],
    )

    assert [row["approval_no"] for row in result["rows"]] == ["MONTHLY-CN"]
    assert result["applicant_options"] == [{
        "id": "china-user",
        "name": "中国申请人",
        "department": "YW MOLDES MX模具",
        "count": 1,
    }]
```

- [ ] **Step 7: Run focused tests**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k \
  'external_expense_exact_approval_number or china_workbench_external_expense_region_boundary or external_expense_preview_excludes_mexico_monthly_rows_and_applicants'
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit preview isolation**

```bash
git add backend/app/external_expenses.py backend/tests/test_api_workflows.py
git commit -m "fix: isolate China intermediate expense preview"
```

### Task 3: Enforce the region boundary again during import

**Files:**
- Modify: `backend/app/main.py:69-102`
- Modify: `backend/app/main.py:5264-5285`
- Modify: `backend/tests/test_api_workflows.py:3379-3510`

- [ ] **Step 1: Add a failing direct-import regression test**

Create a Mexico row, monkeypatch the post-selection source fetch, and submit its key directly to the import endpoint. Assert the response remains HTTP 200 with partial-import semantics:

```python
def test_external_expense_import_rejects_mexico_after_source_refetch(monkeypatch):
    mexico_row = external_expense_test_row(
        "IMPORT-MX-GUARD",
        "9201",
        execution_region="墨西哥México",
        source_sheet="YW MOLDES MX模具",
    )
    monkeypatch.setattr(
        main_module,
        "fetch_external_expenses",
        lambda items: [mexico_row],
    )

    with TestClient(app) as client:
        login(client)
        batch = client.post(
            "/api/batches",
            json={
                "name": "Mexico import guard",
                "start_date": "2026-08-25",
                "end_date": "2026-08-25",
            },
        ).json()["batch"]
        response = client.post(
            f"/api/batches/{batch['id']}/imports/external-expenses",
            json={"items": [{"source_type": "operation", "source_id": "9201"}]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["imported_rows"] == 0
        assert payload["invalid_rows"] == 1
        assert payload["job_id"] is None
        assert payload["errors"] == [{
            "source_type": "operation",
            "source_id": "9201",
            "messages": [CHINA_WORKBENCH_REGION_ERROR],
        }]
        assert client.get(
            f"/api/batches/{batch['id']}/requests"
        ).json()["requests"] == []
```

- [ ] **Step 2: Run the direct-import test and verify red**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py::test_external_expense_import_rejects_mexico_after_source_refetch
```

Expected: FAIL because the import endpoint currently treats the re-fetched Mexico row as a valid candidate.

- [ ] **Step 3: Add the import guard**

Import `mark_china_workbench_external_expense` into `backend/app/main.py`. Immediately after `fetch_external_expenses(keys)` returns, apply it to every source row before computing `invalid_rows` and `candidate_rows`:

```python
    for source_row in source_rows:
        mark_china_workbench_external_expense(source_row)
```

This preserves the current response model and bulk partial-success behavior.

- [ ] **Step 4: Run import and external-expense regressions**

Run:

```bash
python3 -m pytest -q backend/tests/test_api_workflows.py -k external_expense
```

Expected: all selected tests pass, including Mexico mapper tests that prove the shared mapper remains multi-region.

- [ ] **Step 5: Commit import defense**

```bash
git add backend/app/main.py backend/tests/test_api_workflows.py
git commit -m "fix: reject Mexico rows at China import boundary"
```

### Task 4: Full verification, review, merge, and production deployment

**Files:**
- Verify: all changed files
- Production application: `/www/wwwroot/cashier-payment-archive`
- Production service: `cashier-payment`
- Production backup directory: `/data/cashier-payment/backups`

- [ ] **Step 1: Run all relevant and full local checks**

Run:

```bash
python3 -m pytest -q backend/tests -k 'not test_mexico_legacy_approver_and_node_fallbacks_match_stat_filters'
npm run test:frontend
npm run build
git diff --check
git status --short
```

Expected: all selected backend tests pass with exactly the documented date-sensitive legacy test deselected; 26 frontend tests pass; the Vite build succeeds; no whitespace error or unexpected tracked file exists.

- [ ] **Step 2: Review the focused diff**

Verify that:

- `ALLOWED_EXECUTION_REGION_PATTERN` and the shared mappers still accept Mexico for Mexico tracking.
- Preview SQL uses the new China-workbench-only pattern.
- explicit execution region remains authoritative over Sheet mapping.
- applicant options and monthly rows use the same predicate as visible preview rows.
- direct import cannot bypass the filter.
- unknown blank-region legacy rows remain eligible.

Resolve every correctness or security finding and rerun Step 1.

- [ ] **Step 3: Fast-forward merge and push `main`**

In the original repository, preserve user-owned untracked `.superpowers/` and `output/`, then run:

```bash
git switch main
git fetch origin main
git merge --ff-only codex/china-intermediate-region-isolation
python3 -m pytest -q backend/tests/test_api_workflows.py -k external_expense
npm run test:frontend
npm run build
git push origin main
```

Expected: local `main`, `origin/main`, and the verified implementation commit match.

- [ ] **Step 4: Capture the production database path and create a consistent backup**

On `root@172.19.49.227`, enter `/www/wwwroot/cashier-payment-archive`, verify the tracked worktree is clean, capture the active systemd process's `PAYMENT_APP_DATA_DIR` and `PAYMENT_APP_DB`, record the current commit, stop `cashier-payment`, and create this non-overwriting backup using `backend.app.db.backup_database`:

```text
/data/cashier-payment/backups/app-before-china-intermediate-region-isolation-20260828.db
```

Expected: the backup reports SQLite `integrity_check=ok`, has a non-zero size and SHA-256 digest, and the service remains stopped until the build succeeds.

- [ ] **Step 5: Deploy the verified commit**

In `/www/wwwroot/cashier-payment-archive`, preserve untracked historical backups and run:

```bash
git fetch origin main
git merge --ff-only origin/main
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
systemctl start cashier-payment
```

Wait up to 60 seconds for `http://127.0.0.1:8011/` to return HTTP 200. If the update, dependency install, build, or health check fails, return to the recorded old commit, rebuild, and restart; restore the database backup only if application startup changed the database.

- [ ] **Step 6: Perform online acceptance**

Using an authenticated finance/admin session:

1. Open “从钉钉支出中间表拉取” with a date range containing the screenshot's Mexico record.
2. Confirm `YW MOLDES MX模具`, `GREGORIO.GUIA.MIRSA`, and other explicit Mexico execution-region rows are absent from matched rows.
3. Confirm a known China record remains visible and importable.
4. Confirm applicant options contain no person whose only matched records are Mexico rows.
5. Submit a direct API import for the known Mexico source key only in a non-production-mutating check if an existing safe draft/test batch is available; otherwise rely on the automated guard test and do not create production data solely for acceptance.
6. Confirm `systemctl is-active cashier-payment`, public `http://8.135.70.130:8011/` returns HTTP 200, and recent logs contain no new `ERROR`, `Traceback`, or `Exception`.

- [ ] **Step 7: Clean up the merged worktree and branch**

After successful acceptance, remove only the clean worktree at `/Users/smk/.config/superpowers/worktrees/出纳请款/china-intermediate-region-isolation` and delete the merged local implementation branch. Do not modify the user's `.superpowers/` or `output/` directories in the original repository.
