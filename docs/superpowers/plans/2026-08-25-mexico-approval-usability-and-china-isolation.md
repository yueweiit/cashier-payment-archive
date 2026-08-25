# Mexico Approval Usability and China Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Mexico approval state visible before attachment completion, expose every current pending approver, support per-row attachment priority, prevent translated navigation from hiding destinations, and enforce China-only payable views.

**Architecture:** Keep the existing FastAPI/SQLite tracking cache, but make explicit execution region authoritative, persist active DingTalk tasks in a child table, and add an explicit state-commit marker to synchronization runs. Move attachment downloads into a claim-based shared queue so global archival and row-level priority use the same idempotent path. Render structured current tasks in the React page, use a deliberately wide scrollable table, and replace invisible navigation scrolling with a measured overflow menu.

**Tech Stack:** Python 3.9, FastAPI, SQLite, pytest, React 19, TypeScript 5.7, Vite 6, Node test runner, CSS.

---

## File map

- `backend/app/mexico_tracking.py`: region resolution, schema migrations, current-task persistence, sync-run serialization, attachment queue claims, list/detail payloads.
- `backend/app/external_expenses.py`: parse every active DingTalk task and assignee.
- `backend/app/db.py`: run the v2 region/isolation migration once.
- `backend/app/main.py`: enforce China-only scopes, mark state commits, run the attachment worker, and expose the row attachment endpoint.
- `backend/app/payable_history.py`: reuse `record_request_state` for append-only region reclassification history.
- `backend/tests/test_mexico_tracking.py`: unit and persistence coverage for regions, tasks, sync markers, and attachment claims.
- `backend/tests/test_payable_history.py`: stable China-only daily-payables regression.
- `backend/tests/test_api_workflows.py`: API-level China isolation and row-attachment workflow coverage.
- `frontend/src/api.ts`: structured current-task, attachment-status, sync marker, and endpoint types.
- `frontend/src/MexicoTrackingPage.tsx`: early refresh, current approver rendering, row-level attachment action, and attachment polling.
- `frontend/src/App.tsx`: use the extracted responsive navigation component.
- `frontend/src/AppNavigation.tsx`: measured visible navigation and accessible overflow menu.
- `frontend/src/styles.css`: wide Mexico table, current-approver chips, attachment states, and navigation menu layout.
- `frontend/src/i18n.tsx`: Chinese/Spanish labels for the new controls and fallbacks.
- `frontend/tests/mexico-tracking.test.mjs`: frontend contract coverage for early refresh, structured tasks, and row attachments.
- `frontend/tests/header-layout.test.mjs`: frontend contract coverage for measured navigation overflow.

### Task 1: Make execution region authoritative and enforce China-only scopes

**Files:**
- Modify: `backend/app/mexico_tracking.py:559-729`
- Modify: `backend/app/db.py:463-476`
- Modify: `backend/app/main.py:269-274,704-717,822-852,1160-1230,6780-6805`
- Modify: `backend/tests/test_mexico_tracking.py:94-128,408-474`
- Modify: `backend/tests/test_payable_history.py:467-513`
- Modify: `backend/tests/test_api_workflows.py:4768-4860`

- [ ] **Step 1: Replace the conflict expectations with failing execution-region precedence tests**

Change the region tests to require the external execution region even when the Sheet maps elsewhere, including the reported Yuewei case:

```python
def test_explicit_execution_region_overrides_conflicting_sheet_mapping() -> None:
    mexico = resolve_region(
        execution_region="Mexico",
        source_sheet="悦为智能 YW Tech_Ai",
    )
    china = resolve_region(
        execution_region="中国 China",
        source_sheet="YW MOLDES MX模具",
    )

    assert (mexico.region, mexico.source) == ("mexico", "execution_region")
    assert mexico.sheet_region == "china"
    assert (china.region, china.source) == ("china", "execution_region")
    assert china.sheet_region == "mexico"


def test_explicit_execution_region_supersedes_stale_admin_override() -> None:
    decision = resolve_region(
        execution_region="Mexico",
        source_sheet="悦为智能 YW Tech_Ai",
        admin_region="china",
    )

    assert (decision.region, decision.source) == ("mexico", "execution_region")
```

Update the persistence test so the stored tuple is `("mexico", "execution_region", "resolved")` for Yuewei plus Mexico.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py::test_explicit_execution_region_overrides_conflicting_sheet_mapping \
  backend/tests/test_mexico_tracking.py::test_explicit_execution_region_supersedes_stale_admin_override -q
```

Expected: both tests fail because `resolve_region()` currently returns `review` or preserves `admin_override` before considering the explicit field.

- [ ] **Step 3: Implement the minimal precedence change**

Reorder `resolve_region()` so explicit data returns first, while preserving the Sheet fact for audit:

```python
    explicit_region = _execution_region(execution_region)
    mapped_sheet_region = sheet_region(source_sheet)
    override = _normalized_token(admin_region)

    if explicit_region:
        return RegionDecision(
            region=explicit_region,
            source="execution_region",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
            conflict_reason=(
                f"execution_region={raw_region} 覆盖 Sheet 判定={mapped_sheet_region}"
                if mapped_sheet_region and mapped_sheet_region != explicit_region
                else None
            ),
        )

    if override in {"china", "mexico"}:
        return RegionDecision(
            region=override,
            source="admin_override",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
            conflict_reason=(
                f"管理员结论与 Sheet 判定={mapped_sheet_region} 不一致"
                if mapped_sheet_region and mapped_sheet_region != override
                else None
            ),
        )
```

Delete the branch that returns `source="conflict"` for explicit region versus Sheet.

- [ ] **Step 4: Add failing migration and China-only API tests**

Add a v2 backfill test that starts with an existing false setting and an incorrectly classified Yuewei request, then asserts a new append-only history row:

```python
def test_region_v2_backfill_reclassifies_yuewei_mexico_and_appends_history(isolated_db) -> None:
    with isolated_db.connect() as conn:
        request_id = _insert_region_request(
            conn,
            source_sheet="悦为智能 YW Tech_Ai",
            execution_region="Mexico",
            resolved_region="review",
            resolution_source="conflict",
            review_status="pending",
        )
        record_request_state(
            conn,
            request_id,
            event_type="baseline",
            event_key=f"baseline:{request_id}",
        )

        result = backfill_request_regions(
            conn,
            append_history=True,
            event_key_prefix="mexico-request-region-v2",
        )

        stored = conn.execute(
            "SELECT resolved_region, region_review_status FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        history = conn.execute(
            "SELECT event_type, resolved_region FROM payable_history_versions "
            "WHERE source_request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
        assert tuple(stored) == ("mexico", "resolved")
        assert [tuple(row) for row in history][-1] == (
            "request.region_reclassified",
            "mexico",
        )
        assert result["reclassified"] == 1
```

In `test_daily_payables_can_exclude_mexico_and_region_review_history`, replace the fixed `2026-08-24` with `selected = date.today()` for both request due dates and snapshot queries so the regression does not fail when the calendar day changes.

Update `test_region_backfill_counts_and_history_snapshot` to include `"reclassified": 2` in the exact count assertion: its China and Mexico fixtures move from the default review classification, while the unknown Sheet remains review.

Extend `test_china_region_isolation_filters_workbench_totals_sheets_and_export` to set `china_region_isolation_enabled=false` before calling the APIs and still expect only China rows.

- [ ] **Step 5: Run the new migration and API tests and verify RED**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py::test_region_v2_backfill_reclassifies_yuewei_mexico_and_appends_history \
  backend/tests/test_payable_history.py::test_daily_payables_can_exclude_mexico_and_region_review_history \
  backend/tests/test_api_workflows.py::test_china_region_isolation_filters_workbench_totals_sheets_and_export -q
```

Expected: the new backfill signature is missing and the API test includes non-China rows while the stored flag is false.

- [ ] **Step 6: Implement the v2 migration and unconditional China scope**

Extend `backfill_request_regions()` without changing existing callers:

```python
def backfill_request_regions(
    conn: sqlite3.Connection,
    *,
    append_history: bool = False,
    event_key_prefix: str = "mexico-request-region-backfill",
) -> Dict[str, int]:
    from .payable_history import record_request_state

    counts = {
        "china": 0,
        "mexico": 0,
        "review": 0,
        "preserved_override": 0,
        "reclassified": 0,
    }
    rows = conn.execute(
        "SELECT id, resolved_region, region_resolution_source, region_review_status "
        "FROM payment_requests ORDER BY id"
    ).fetchall()
    for row in rows:
        previous = (str(row["resolved_region"] or ""), str(row["region_review_status"] or ""))
        decision = persist_request_region(conn, int(row["id"]), actor_id=None)
        counts[decision.region] += 1
        current = (decision.region, "pending" if decision.region == "review" else "resolved")
        if current != previous:
            counts["reclassified"] += 1
            if append_history:
                record_request_state(
                    conn,
                    int(row["id"]),
                    event_type="request.region_reclassified",
                    event_key=f"{event_key_prefix}:{row['id']}:{decision.region}",
                )
    return counts
```

Keep `preserved_override` counting for cases where execution region is absent and the admin override remains authoritative.

In `backend/app/db.py`, add a new one-time migration after v1:

```python
    isolation_key = "mexico_request_region_and_china_isolation_v2"
    if not conn.execute(
        "SELECT 1 FROM schema_migrations WHERE key = ?", (isolation_key,)
    ).fetchone():
        backfill_request_regions(
            conn,
            append_history=True,
            event_key_prefix=isolation_key,
        )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES "
            "('china_region_isolation_enabled', 'true', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = 'true', updated_at = excluded.updated_at",
            (now_iso(),),
        )
        conn.execute(
            "INSERT INTO schema_migrations (key, applied_at) VALUES (?, ?)",
            (isolation_key, now_iso()),
        )
```

Make the China scope unconditional:

```python
def china_region_isolation_enabled(conn) -> bool:
    del conn
    return True


def china_workbench_scope(conn, column_prefix: str = "") -> str:
    del conn
    prefix = f"{column_prefix}." if column_prefix else ""
    return (
        f"LOWER(TRIM(COALESCE({prefix}resolved_region, ''))) = 'china' "
        f"AND LOWER(TRIM(COALESCE({prefix}region_review_status, ''))) = 'resolved'"
    )
```

Always pass `china_only=True` to the three daily-payables calls. Keep the compatibility setting in responses, but force it to `True` in `get_mexico_tracking_settings()` and ignore attempts to set it false in `update_mexico_tracking_settings()`.

- [ ] **Step 7: Run focused tests and commit**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py -q \
  backend/tests/test_payable_history.py::test_daily_payables_can_exclude_mexico_and_region_review_history \
  backend/tests/test_api_workflows.py::test_china_region_isolation_filters_workbench_totals_sheets_and_export -q
```

Expected: PASS.

Commit:

```bash
git add backend/app/mexico_tracking.py backend/app/db.py backend/app/main.py \
  backend/tests/test_mexico_tracking.py backend/tests/test_payable_history.py \
  backend/tests/test_api_workflows.py
git commit -m "fix: enforce china payable region scope"
```

### Task 2: Persist every current pending DingTalk approver

**Files:**
- Modify: `backend/app/mexico_tracking.py:276-438,885-1135,1530-1845`
- Modify: `backend/app/external_expenses.py:1477-1570`
- Modify: `backend/tests/test_mexico_tracking.py:819-999,1318-1379`

- [ ] **Step 1: Write failing parser tests for parallel tasks and multiple assignees**

Replace the single-latest-task contract with structured current tasks:

```python
def test_workflow_snapshot_keeps_all_current_tasks_and_assignees() -> None:
    instance = {
        "approval_no": "202608240100",
        "process_instance_id": "process-100",
        "status": "RUNNING",
        "result": "",
        "operation_records": [],
        "tasks": [
            {
                "taskId": "finance-task",
                "activityId": "finance-node",
                "activityName": "财务审批",
                "assigneeUserIds": ["finance-a", "finance-b"],
                "status": "RUNNING",
                "createTime": "2026-08-24T01:00:00Z",
            },
            {
                "taskId": "legal-task",
                "activityId": "legal-node",
                "activityName": "法务会签",
                "userId": "legal-a",
                "status": "PENDING",
                "createTime": "2026-08-24T00:30:00Z",
            },
        ],
    }

    snapshot = parse_dingtalk_workflow_instance(
        instance,
        {"finance-a": "Ana", "finance-b": "Bruno", "legal-a": "Carla"},
    )

    assert [(task["node_name"], task["approver_name"]) for task in snapshot["current_tasks"]] == [
        ("法务会签", "Carla"),
        ("财务审批", "Ana"),
        ("财务审批", "Bruno"),
    ]
    assert snapshot["current_node_name"] == "法务会签、财务审批"
    assert snapshot["current_approver_name"] == "Carla、Ana、Bruno"
    assert snapshot["current_node_entered_at"] == "2026-08-24T08:30:00+08:00"
```

Update the unknown-person test to assert `approver_name == "未识别人员（unknown-user-id）"` inside `current_tasks`.

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py::test_workflow_snapshot_keeps_all_current_tasks_and_assignees \
  backend/tests/test_mexico_tracking.py::test_workflow_snapshot_keeps_unknown_current_approver_id -q
```

Expected: FAIL because `current_tasks` does not exist and only the first assignee is returned.

- [ ] **Step 3: Parse structured active tasks**

Replace `_current_workflow_task()` with `_current_workflow_tasks()` that emits one record per assignee:

```python
def _current_workflow_tasks(
    process_instance_id: str,
    tasks: list[Dict[str, Any]],
    events: list[Dict[str, Any]],
    user_names: Dict[str, str],
) -> list[Dict[str, Optional[str]]]:
    current: list[Dict[str, Optional[str]]] = []
    for index, task in enumerate(tasks):
        status = (_text(task.get("status")) or "").upper()
        if status not in {"RUNNING", "PROCESSING", "PENDING"}:
            continue
        activity_id = _text(task.get("activityId")) or ""
        task_id = _text(task.get("taskId") or task.get("id")) or f"{activity_id}:{index}"
        entered_at = _workflow_event_time(
            task.get("startTime") or task.get("createTime")
            or task.get("createdAt") or task.get("updatedAt")
        )
        node_name = (
            _text(task.get("activityName")) or _text(task.get("showName"))
            or _text(task.get("name"))
            or next((
                _text(event.get("stage_name"))
                for event in reversed(events)
                if activity_id and _text(event.get("activity_id")) == activity_id
            ), None)
            or "待审批节点"
        )
        assignee_ids = _task_assignee_ids(task) or [""]
        for approver_id in assignee_ids:
            approver_name = (
                user_names.get(approver_id) or f"未识别人员（{approver_id}）"
                if approver_id else "待识别待办人"
            )
            current.append({
                "task_key": hashlib.sha256(
                    f"{process_instance_id}|{task_id}|{approver_id}".encode("utf-8")
                ).hexdigest(),
                "task_id": task_id,
                "activity_id": activity_id or None,
                "node_name": node_name,
                "approver_id": approver_id or None,
                "approver_name": approver_name,
                "entered_at": entered_at,
            })
    return sorted(current, key=lambda item: (
        item.get("entered_at") or "", item.get("node_name") or "", item.get("approver_name") or ""
    ))
```

In `parse_dingtalk_workflow_instance()`, derive compatible scalar summaries from this list with order-preserving de-duplication and the earliest non-empty `entered_at`.

- [ ] **Step 4: Add failing persistence/list/detail tests**

Extend `test_workflow_cache_is_idempotent_tracks_history_and_rebuilds_request_links` with three current tasks, sync twice, then sync with one task removed:

```python
        workflow["current_tasks"] = [
            {"task_key": "t1:a", "task_id": "t1", "activity_id": "n1", "node_name": "Finance", "approver_id": "a", "approver_name": "Ana", "entered_at": "2026-08-24T08:00:00+08:00"},
            {"task_key": "t1:b", "task_id": "t1", "activity_id": "n1", "node_name": "Finance", "approver_id": "b", "approver_name": "Bruno", "entered_at": "2026-08-24T08:00:00+08:00"},
            {"task_key": "t2:c", "task_id": "t2", "activity_id": "n2", "node_name": "Legal", "approver_id": "c", "approver_name": "Carla", "entered_at": "2026-08-24T07:00:00+08:00"},
        ]
```

Assert the table contains three rows after idempotent replay, two after removing one, the list supports `approver="Bruno"`, and detail returns `current_tasks`, `current_approvers`, and `current_nodes`.

- [ ] **Step 5: Run persistence tests and verify RED**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py::test_workflow_cache_is_idempotent_tracks_history_and_rebuilds_request_links \
  backend/tests/test_mexico_tracking.py::test_mexico_tracking_detail_returns_timeline_attachments_and_links -q
```

Expected: FAIL because `mexico_approval_current_tasks` and structured payload fields do not exist.

- [ ] **Step 6: Add the current-task schema and transactional replacement**

Add the child table in `ensure_mexico_tracking_schema()`:

```sql
CREATE TABLE IF NOT EXISTS mexico_approval_current_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_no TEXT NOT NULL REFERENCES mexico_approval_tracking(approval_no)
        ON DELETE CASCADE,
    task_key TEXT NOT NULL,
    task_id TEXT,
    activity_id TEXT,
    node_name TEXT,
    approver_id TEXT,
    approver_name TEXT,
    entered_at TEXT,
    synced_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(approval_no, task_key)
);
CREATE INDEX IF NOT EXISTS idx_mexico_current_tasks_approver
ON mexico_approval_current_tasks(approver_id, approver_name);
CREATE INDEX IF NOT EXISTS idx_mexico_current_tasks_node
ON mexico_approval_current_tasks(node_name, entered_at);
```

Inside `cache_mexico_workflow_snapshots()`, compare desired task keys with stored keys, delete stale rows, upsert desired rows, and include task changes in `workflow_changed`. Do this inside the function's existing transaction.

Update `_mexico_tracking_where()` approver and node clauses to use `EXISTS` against the child table. Add a helper that loads tasks for a page of approval numbers in one query and populates:

```python
payload["current_tasks"] = tasks_by_approval.get(payload["approval_no"], [])
payload["current_approvers"] = list(dict.fromkeys(
    task["approver_name"] for task in payload["current_tasks"] if task["approver_name"]
))
payload["current_nodes"] = list(dict.fromkeys(
    task["node_name"] for task in payload["current_tasks"] if task["node_name"]
))
```

Use the same helper for `get_mexico_tracking_detail()` and derive filter options from the child table.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest backend/tests/test_mexico_tracking.py -q
```

Expected: PASS.

Commit:

```bash
git add backend/app/external_expenses.py backend/app/mexico_tracking.py \
  backend/tests/test_mexico_tracking.py
git commit -m "feat: track all current mexico approvers"
```

### Task 3: Refresh approval state as soon as it commits

**Files:**
- Modify: `backend/app/mexico_tracking.py:60-240,276-430`
- Modify: `backend/app/main.py:6328-6475`
- Modify: `backend/tests/test_mexico_tracking.py:999-1098`
- Modify: `frontend/src/api.ts:127-145`
- Modify: `frontend/src/MexicoTrackingPage.tsx:90-230`
- Modify: `frontend/tests/mexico-tracking.test.mjs:39-47`

- [ ] **Step 1: Write a failing sync-run state marker test**

Add:

```python
def test_mexico_sync_run_records_state_commit_before_completion(isolated_db) -> None:
    with isolated_db.connect() as conn:
        run, _ = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="manual",
        )
        committed = update_mexico_sync_run(
            conn,
            run["id"],
            phase="querying_attachments",
            processed_count=2,
            total_count=2,
            state_committed=True,
        )
        assert committed["status"] == "running"
        assert committed["state_committed_at"]
        same_marker = update_mexico_sync_run(
            conn,
            run["id"],
            phase="syncing_attachments",
            state_committed=True,
        )
        assert same_marker["state_committed_at"] == committed["state_committed_at"]
```

- [ ] **Step 2: Run the backend test and verify RED**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py::test_mexico_sync_run_records_state_commit_before_completion -q
```

Expected: FAIL because the column and `state_committed` argument do not exist.

- [ ] **Step 3: Add the state commit marker**

Add `state_committed_at TEXT` to `mexico_sync_runs` creation and `_ensure_column()` migration. Extend `update_mexico_sync_run()`:

```python
def update_mexico_sync_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    phase: str,
    processed_count: int = 0,
    total_count: int = 0,
    attachment_processed_count: int = 0,
    attachment_total_count: int = 0,
    stage_timings: Optional[Dict[str, Any]] = None,
    state_committed: bool = False,
    lease_seconds: int = 1800,
) -> Dict[str, Any]:
```

Set the column with `state_committed_at = CASE WHEN ? THEN COALESCE(state_committed_at, ?) ELSE state_committed_at END`.

Immediately after the state transaction in `_run_mexico_tracking_sync()`, call:

```python
update_mexico_sync_run(
    conn,
    run_id,
    phase="querying_attachments",
    processed_count=len(workflows),
    total_count=len(workflow_approval_nos),
    stage_timings=timings,
    state_committed=True,
)
```

- [ ] **Step 4: Add a failing frontend contract test for one-time early refresh**

Extend `frontend/tests/mexico-tracking.test.mjs`:

```javascript
test("tracking refreshes once when approval state commits before attachments finish", () => {
  assert.match(apiSource, /state_committed_at\?: string \| null/);
  assert.match(pageSource, /lastRefreshedStateCommit/);
  assert.match(pageSource, /response\.run\.state_committed_at/);
  assert.match(pageSource, /await refreshAll\(\)/);
  assert.match(pageSource, /\u5ba1批状态已更新/);
  assert.match(pageSource, /\u9644件正在后台处理/);
});
```

- [ ] **Step 5: Run the frontend test and verify RED**

Run:

```bash
npm run test:frontend -- --test-name-pattern="state commits"
```

Expected: FAIL because the type, ref, and separate messages do not exist.

- [ ] **Step 6: Implement the one-time early refresh**

Add to `MexicoSyncRun`:

```ts
state_committed_at?: string | null;
```

Add a ref in `MexicoTrackingPage`:

```tsx
const lastRefreshedStateCommit = useRef<string | null>(null);
```

In the poll callback, before terminal-state handling:

```tsx
const stateCommit = response.run.state_committed_at || null;
if (stateCommit && stateCommit !== lastRefreshedStateCommit.current) {
  lastRefreshedStateCommit.current = stateCommit;
  await refreshAll();
  if (selectedId !== null) await reloadDetail(selectedId);
  setMessage(t(
    "审批状态已更新，附件继续在后台处理",
    "El estado ya está actualizado; los archivos continúan en segundo plano",
  ));
}
```

Extract the existing detail request into `reloadDetail(id)` so both `openDetail()` and the early refresh use exactly one implementation. Change the sync banner text during attachment phases to `附件正在后台处理 x/y`.

- [ ] **Step 7: Run tests, build, and commit**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py::test_mexico_sync_run_records_state_commit_before_completion -q
npm run test:frontend
npm run build
```

Expected: all commands PASS.

Commit:

```bash
git add backend/app/mexico_tracking.py backend/app/main.py \
  backend/tests/test_mexico_tracking.py frontend/src/api.ts \
  frontend/src/MexicoTrackingPage.tsx frontend/tests/mexico-tracking.test.mjs
git commit -m "fix: reveal mexico approval state before attachments"
```

### Task 4: Add an idempotent priority attachment queue and row API

**Files:**
- Modify: `backend/app/mexico_tracking.py:276-430,1248-1435,1793-1877`
- Modify: `backend/app/main.py:90-145,6328-6638,6713-6943`
- Modify: `backend/tests/test_mexico_tracking.py:1412-1545`
- Modify: `backend/tests/test_api_workflows.py:4677-4860`

- [ ] **Step 1: Write failing claim and priority tests**

Add imports for `claim_next_mexico_attachment`, `prioritize_mexico_attachments`, and `summarize_mexico_attachments`, then add:

```python
def test_mexico_attachment_claim_prefers_requested_row_and_is_exclusive(isolated_db) -> None:
    with isolated_db.connect() as conn:
        _insert_mexico_tracking_row(conn, "MX-NORMAL")
        _insert_mexico_tracking_row(conn, "MX-PRIORITY")
        upsert_mexico_attachment_candidates(conn, [
            {"approval_no": "MX-NORMAL", "source_file_id": "normal", "file_name": "normal.pdf"},
            {"approval_no": "MX-PRIORITY", "source_file_id": "priority", "file_name": "priority.pdf"},
        ])
        prioritize_mexico_attachments(conn, "MX-PRIORITY", requested_at="2026-08-25T09:00:00+08:00")

        claimed = claim_next_mexico_attachment(conn, claim_token="worker-a")
        duplicate = claim_next_mexico_attachment(
            conn,
            claim_token="worker-b",
            approval_nos=["MX-PRIORITY"],
        )

        assert claimed["approval_no"] == "MX-PRIORITY"
        assert claimed["claim_token"] == "worker-a"
        assert duplicate is None


def test_mexico_attachment_summary_reports_queue_states(isolated_db) -> None:
    with isolated_db.connect() as conn:
        _insert_mexico_tracking_row(conn, "MX-SUMMARY")
        upsert_mexico_attachment_candidates(conn, [
            {"approval_no": "MX-SUMMARY", "source_file_id": "one"},
            {"approval_no": "MX-SUMMARY", "source_file_id": "two"},
        ])
        claim_next_mexico_attachment(conn, claim_token="worker")
        summary = summarize_mexico_attachments(conn, "MX-SUMMARY")
        assert summary == {
            "total": 2,
            "ready": 0,
            "queued": 1,
            "downloading": 1,
            "failed": 0,
            "complete": False,
        }
```

- [ ] **Step 2: Run queue tests and verify RED**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py::test_mexico_attachment_claim_prefers_requested_row_and_is_exclusive \
  backend/tests/test_mexico_tracking.py::test_mexico_attachment_summary_reports_queue_states -q
```

Expected: collection fails because the new queue functions do not exist.

- [ ] **Step 3: Add queue columns and atomic helpers**

Add these columns to table creation and `_ensure_column()` migration:

```sql
priority INTEGER NOT NULL DEFAULT 0,
requested_at TEXT,
claim_token TEXT,
claimed_at TEXT
```

Create an index on `(status, priority DESC, requested_at, id)`.

Implement priority and summary:

```python
def prioritize_mexico_attachments(
    conn: sqlite3.Connection,
    approval_no: str,
    *,
    priority: int = 100,
    requested_at: Optional[str] = None,
) -> Dict[str, int]:
    timestamp = requested_at or _now_iso()
    conn.execute(
        "UPDATE mexico_approval_attachments SET priority = MAX(priority, ?), "
        "requested_at = ?, status = CASE WHEN status = 'failed' THEN 'pending' ELSE status END, "
        "last_error = CASE WHEN status = 'failed' THEN NULL ELSE last_error END, updated_at = ? "
        "WHERE approval_no = ? AND status <> 'ready'",
        (priority, timestamp, timestamp, approval_no),
    )
    conn.commit()
    return summarize_mexico_attachments(conn, approval_no)
```

Implement `claim_next_mexico_attachment()` with `BEGIN IMMEDIATE`: reset stale downloading rows to `pending`, select one `pending` row ordered by priority, conditionally update it to `downloading` with the supplied token and timestamp, increment attempts, commit, and return the joined tracking metadata. Do not automatically select a fresh `failed` row in the same worker run; `prioritize_mexico_attachments()` is the explicit retry action that resets failed rows to pending. A second caller must not be able to claim the same row.

Implement both summary helpers from the same grouped-status query. `summarize_mexico_attachments(conn, approval_no)` limits by approval number and returns the six-field row contract used by the detail API. `summarize_mexico_attachment_queue(conn)` has no approval filter and returns aggregate `total`, `ready`, `queued`, `downloading`, and `failed` counts for the attachment run result. Treat `pending` as queued; keep `failed` separate.

Add `complete_mexico_attachment_run_if_empty(conn, run_id) -> bool`. It must use `BEGIN IMMEDIATE`, recheck that no `pending` or retryable stale `downloading` row exists, and update the run to completed in that same transaction only when the queue is still empty. This closes the race where a row request enqueues work just as the worker is about to exit.

Change `mark_mexico_attachment_ready()` and `mark_mexico_attachment_failed()` to accept `claim_token`; when supplied, include `AND claim_token = ?` and clear claim fields on completion.

- [ ] **Step 4: Add a failing row endpoint test**

Add an API test that inserts one authorized and one unauthorized tracking row, monkeypatches `fetch_dingtalk_workflows`, `fetch_external_expense_attachments`, and `_submit_mexico_attachment_task`, then calls:

```python
response = client.post(f"/api/mexico-tracking/{tracking_id}/attachments/sync")
assert response.status_code == 202
payload = response.json()
assert payload["attachment_status"]["queued"] == 1
assert payload["run"]["kind"] == "mexico-attachments"
assert client.post(
    f"/api/mexico-tracking/{forbidden_id}/attachments/sync"
).status_code == 403
```

Call the authorized endpoint twice and assert one attachment row and one active attachment run remain.

- [ ] **Step 5: Run the endpoint test and verify RED**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_api_workflows.py -k "mexico_tracking_row_attachment_sync" -q
```

Expected: FAIL with HTTP 405/404 because the endpoint is missing.

- [ ] **Step 6: Generalize sync leases and implement the queue worker**

Extend `acquire_or_reuse_mexico_sync_run()` with `kind: str = "mexico-tracking"` and use the parameter in its stale, active, latest, and insert queries. Keep `_latest_mexico_source_cursors()` restricted to `kind='mexico-tracking'`.

Import `claim_next_mexico_attachment`, `complete_mexico_attachment_run_if_empty`, `prioritize_mexico_attachments`, and `summarize_mexico_attachment_queue` into `backend/app/main.py` together with the existing Mexico tracking helpers.

Add a dedicated executor and in-memory future map in `backend/app/main.py`:

```python
_MEXICO_ATTACHMENT_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="mexico-attachments",
)
_MEXICO_ATTACHMENT_FUTURES: Dict[str, Future[Any]] = {}
_MEXICO_ATTACHMENT_FUTURES_LOCK = threading.Lock()
```

Implement `_run_mexico_attachment_queue(run_id)` as a loop:

```python
while True:
    claim_token = uuid.uuid4().hex
    with connect() as conn:
        candidate = claim_next_mexico_attachment(conn, claim_token=claim_token)
    if candidate is None:
        break
    try:
        downloaded, failed, errors = download_dingtalk_attachment_candidates(
            [candidate], max_workers=1
        )
        if failed or not downloaded:
            raise RuntimeError(errors[0]["message"] if errors else "附件下载失败")
        with connect() as conn:
            file_object = register_file_object(conn, downloaded[0])
            mark_mexico_attachment_ready(
                conn,
                int(candidate["attachment_id"]),
                file_object_id=int(file_object["id"]),
                claim_token=claim_token,
            )
    except Exception as exc:
        with connect() as conn:
            mark_mexico_attachment_failed(
                conn,
                int(candidate["attachment_id"]),
                str(exc),
                claim_token=claim_token,
            )
```

Track processed/total counts on a `kind="mexico-attachments"` run. Global Mexico sync should upsert attachment inventory, acquire/reuse the attachment run, submit the worker, and then complete the state sync without waiting for downloads.

When `claim_next_mexico_attachment()` returns `None`, finalize only through the atomic empty-queue helper:

```python
with connect() as conn:
    if complete_mexico_attachment_run_if_empty(conn, run_id):
        break
continue
```

The helper stores `{"attachments": summarize_mexico_attachment_queue(conn)}` in `result_json`, clears the lease, and sets `completed_at` before committing. Wrap the worker body in `try/except BaseException`; on an unhandled worker error call `fail_mexico_sync_run(conn, run_id, str(exc))`. Per-file download exceptions remain handled inside the loop and produce a failed attachment row rather than failing the whole queue.

- [ ] **Step 7: Implement the row endpoint**

Add:

```python
@app.post("/api/mexico-tracking/{tracking_id}/attachments/sync")
def sync_mexico_tracking_row_attachments(
    tracking_id: int,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> JSONResponse:
    with connect() as conn:
        detail = get_mexico_tracking_detail(
            conn,
            tracking_id,
            allowed_sheets=mexico_tracking_allowed_sheets(conn, user),
            allow_review=user["role"] == ROLE_ADMIN,
        )
    approval_no = str(detail["approval_no"])
    workflows = fetch_dingtalk_workflows([approval_no])
    source_rows = fetch_external_expense_attachments([approval_no])
    candidates = collect_mexico_attachment_candidates(workflows, source_rows)
    with connect() as conn:
        upsert_mexico_attachment_candidates(conn, candidates)
        attachment_status = prioritize_mexico_attachments(conn, approval_no)
        run, reused = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=user.get("id"),
            trigger_type="manual",
            kind="mexico-attachments",
        )
    _submit_mexico_attachment_task(str(run["id"]))
    return JSONResponse(
        status_code=202,
        content={"run": run, "reused": reused, "attachment_status": attachment_status},
    )
```

Translate `KeyError` to 404 and `PermissionError` to 403 exactly like the detail endpoint. Add `attachment_status` to `get_mexico_tracking_detail()` using `summarize_mexico_attachments()`.

- [ ] **Step 8: Run tests and commit**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest \
  backend/tests/test_mexico_tracking.py \
  backend/tests/test_api_workflows.py -k "mexico or attachment" -q
```

Expected: PASS.

Commit:

```bash
git add backend/app/mexico_tracking.py backend/app/main.py \
  backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py
git commit -m "feat: prioritize mexico attachments by approval"
```

### Task 5: Render current approvers and row-level attachment loading

**Files:**
- Modify: `frontend/src/api.ts:20-160,1009-1042`
- Modify: `frontend/src/MexicoTrackingPage.tsx:1-620`
- Modify: `frontend/src/i18n.tsx:12-365`
- Modify: `frontend/tests/mexico-tracking.test.mjs:18-55`

- [ ] **Step 1: Write failing frontend contract tests**

Add:

```javascript
test("tracking renders every current approver and supports one-row attachments", () => {
  assert.match(apiSource, /export type MexicoCurrentTask/);
  assert.match(apiSource, /current_tasks: MexicoCurrentTask\[\]/);
  assert.match(apiSource, /current_approvers: string\[\]/);
  assert.match(apiSource, /attachment_status: MexicoAttachmentStatus/);
  assert.match(apiSource, /syncMexicoTrackingAttachments:/);
  assert.match(pageSource, /mexico-approver-list/);
  assert.match(pageSource, /current_approvers\.map/);
  assert.match(pageSource, /加载此单附件/);
  assert.match(pageSource, /attachmentRun/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
npm run test:frontend -- --test-name-pattern="current approver"
```

Expected: FAIL because the types, endpoint method, and UI controls are missing.

- [ ] **Step 3: Add exact API types and method**

Add:

```ts
export type MexicoCurrentTask = {
  id?: number;
  task_key: string;
  task_id?: string | null;
  activity_id?: string | null;
  node_name: string;
  approver_id?: string | null;
  approver_name: string;
  entered_at?: string | null;
};

export type MexicoAttachmentStatus = {
  total: number;
  ready: number;
  queued: number;
  downloading: number;
  failed: number;
  complete: boolean;
};
```

Add `current_tasks`, `current_approvers`, and `current_nodes` to `MexicoTrackingItem`, and `attachment_status` to `MexicoTrackingDetail`.

Add the client call:

```ts
syncMexicoTrackingAttachments: (trackingId: number) =>
  request<{ run: MexicoSyncRun; reused: boolean; attachment_status: MexicoAttachmentStatus }>(
    `/api/mexico-tracking/${trackingId}/attachments/sync`,
    { method: "POST" },
  ),
```

- [ ] **Step 4: Render structured nodes and approvers**

Add small rendering helpers:

```tsx
function CurrentApprovers({ item }: { item: MexicoTrackingItem }) {
  const names = item.current_approvers?.length
    ? item.current_approvers
    : [item.current_approver_name || "待识别待办人"];
  return <div className="mexico-approver-list">{names.map((name) => (
    <span key={name}>{name}</span>
  ))}</div>;
}

function CurrentNodes({ item }: { item: MexicoTrackingItem }) {
  const nodes = item.current_nodes?.length
    ? item.current_nodes
    : [item.current_node_name || "待审批节点"];
  return <div className="mexico-node-list">{nodes.map((node) => (
    <span key={node}>{node}</span>
  ))}</div>;
}
```

Use these helpers in the desktop row, mobile card, and detail overview. Change the main row action label to `t("查看审批", "Ver aprobación")`.

- [ ] **Step 5: Implement row attachment loading and polling**

In `MexicoDetailDrawer`, add `attachmentRun`, `attachmentStarting`, and an in-flight polling ref. The button handler calls `api.syncMexicoTrackingAttachments(item.id)`, stores the run, and reports whether it reused existing work.

Poll `api.mexicoTrackingSyncRun(attachmentRun.id)` every 1200ms. When terminal, call an `onReload` prop that reloads only the selected detail. Render exact status text from `item.attachment_status`:

```tsx
const attachmentStatusText = item.attachment_status.complete
  ? t("附件已完成", "Archivos completos")
  : item.attachment_status.failed > 0
    ? t("部分附件失败，可重试", "Algunos archivos fallaron; puede reintentar")
    : item.attachment_status.downloading > 0
      ? t("附件加载中", "Cargando archivos")
      : item.attachment_status.queued > 0
        ? t("附件已排队", "Archivos en cola")
        : t("附件尚未加载", "Archivos aún no cargados");
```

Place `加载此单附件 / Cargar archivos de esta solicitud` beside the status. Disable it only while the request itself is being submitted; repeated use after a failure remains available.

- [ ] **Step 6: Add translations, run tests/build, and commit**

Add exact dictionary entries for `查看审批`, `待审批节点`, `待识别待办人`, `加载此单附件`, and all five attachment state labels.

Run:

```bash
npm run test:frontend
npm run build
```

Expected: PASS.

Commit:

```bash
git add frontend/src/api.ts frontend/src/MexicoTrackingPage.tsx \
  frontend/src/i18n.tsx frontend/tests/mexico-tracking.test.mjs
git commit -m "feat: load mexico attachments per approval"
```

### Task 6: Stop the Mexico desktop table from squeezing content

**Files:**
- Modify: `frontend/src/MexicoTrackingPage.tsx:330-430`
- Modify: `frontend/src/styles.css:5255-5341,5641-5661`
- Modify: `frontend/tests/mexico-tracking.test.mjs:39-65`

- [ ] **Step 1: Write a failing table-layout contract test**

Read `styles.css` in `frontend/tests/mexico-tracking.test.mjs`, then add:

```javascript
test("Mexico approval desktop table keeps readable columns and visible overflow", () => {
  assert.match(styleSource, /\.mexico-table-wrap\s*\{[^}]*overflow-x:\s*auto/s);
  assert.match(styleSource, /\.mexico-tracking-table\s*\{[^}]*min-width:\s*1720px/s);
  assert.doesNotMatch(styleSource, /\.mexico-table-wrap\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(styleSource, /\.mexico-tracking-table th:nth-child\(5\)\s*\{[^}]*width:\s*320px/s);
  assert.match(styleSource, /\.mexico-tracking-table th:nth-child\(7\)\s*\{[^}]*width:\s*240px/s);
  assert.match(styleSource, /\.mexico-approver-list/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
npm run test:frontend -- --test-name-pattern="readable columns"
```

Expected: FAIL because the wrapper hides overflow and the table lacks the required width.

- [ ] **Step 3: Implement the wide table and readable cells**

Change the desktop rules to:

```css
.mexico-table-wrap {
  position: relative;
  min-height: 150px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-gutter: stable;
}

.mexico-tracking-table {
  width: 100%;
  min-width: 1720px;
  border-collapse: collapse;
  table-layout: fixed;
}

.mexico-tracking-table th:nth-child(1) { width: 90px; }
.mexico-tracking-table th:nth-child(2) { width: 210px; }
.mexico-tracking-table th:nth-child(3) { width: 240px; }
.mexico-tracking-table th:nth-child(4) { width: 180px; }
.mexico-tracking-table th:nth-child(5) { width: 320px; }
.mexico-tracking-table th:nth-child(6) { width: 200px; }
.mexico-tracking-table th:nth-child(7) { width: 240px; }
.mexico-tracking-table th:nth-child(8) { width: 110px; }
.mexico-tracking-table th:nth-child(9) { width: 130px; }

.mexico-tracking-table td:nth-child(2),
.mexico-tracking-table td:nth-child(8),
.mexico-row-actions { white-space: nowrap; }

.mexico-tracking-table td:nth-child(3),
.mexico-tracking-table td:nth-child(4),
.mexico-tracking-table td:nth-child(5) { overflow-wrap: anywhere; }

.mexico-approver-list,
.mexico-node-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.mexico-approver-list > span,
.mexico-node-list > span {
  display: inline-flex;
  max-width: 100%;
  padding: 3px 7px;
  border-radius: 999px;
  overflow-wrap: anywhere;
  background: #e9f4f1;
}
```

Remove the conditional `min-width: 1260px` rule from the 1280px media query; the base desktop rule now owns width. Preserve the card switch at 980px.

- [ ] **Step 4: Run tests/build and commit**

Run:

```bash
npm run test:frontend
npm run build
```

Expected: PASS.

Commit:

```bash
git add frontend/src/MexicoTrackingPage.tsx frontend/src/styles.css \
  frontend/tests/mexico-tracking.test.mjs
git commit -m "fix: keep mexico approval table readable"
```

### Task 7: Replace invisible translated navigation scrolling with an overflow menu

**Files:**
- Create: `frontend/src/AppNavigation.tsx`
- Modify: `frontend/src/App.tsx:78,473-515`
- Modify: `frontend/src/styles.css:183-250,3692-3715`
- Modify: `frontend/src/i18n.tsx:12-30`
- Modify: `frontend/tests/header-layout.test.mjs:1-40`

- [ ] **Step 1: Write a failing navigation contract test**

Extend `frontend/tests/header-layout.test.mjs` to read `AppNavigation.tsx` and assert measured overflow rather than hidden scrolling:

```javascript
test("translated navigation exposes hidden destinations through an overflow menu", () => {
  assert.match(appSource, /<AppNavigation/);
  assert.match(navigationSource, /ResizeObserver/);
  assert.match(navigationSource, /app-nav-measure/);
  assert.match(navigationSource, /app-nav-more/);
  assert.match(navigationSource, /aria-expanded=\{menuOpen\}/);
  assert.match(navigationSource, /t\("更多", "Más"\)/);
  assert.match(navigationSource, /event\.key === "Escape"/);
  assert.doesNotMatch(styleSource, /\.app-nav\s*\{[^}]*overflow-x:\s*auto/s);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
npm run test:frontend -- --test-name-pattern="overflow menu"
```

Expected: FAIL because navigation is inline in `App.tsx` and still uses invisible horizontal scrolling.

- [ ] **Step 3: Create the measured navigation component**

Create `AppNavigation.tsx` with this exact public type boundary:

```tsx
export type AppTab = "workspace" | "daily-payables" | "mexico-tracking" | "archive" | "admin";

export type AppNavigationProps = {
  tab: AppTab;
  canAdmin: boolean;
  onSelect: (tab: AppTab) => void;
};
```

Implement `export function AppNavigation({ tab, canAdmin, onSelect }: AppNavigationProps)` in the same file. Define items from `FileSpreadsheet`, `CalendarDays`, `MapPinned`, `Archive`, and `Users`, with labels from `t()`.

Render a visually hidden `.app-nav-measure` containing every item and a More button. In `useLayoutEffect`, observe the visible nav root and compute:

```tsx
const available = rootRef.current?.clientWidth || 0;
const widths = measureItemRefs.current.map((node) => node?.offsetWidth || 0);
const allWidth = widths.reduce((sum, width) => sum + width, 0);
if (allWidth <= available) {
  setVisibleCount(widths.length);
  return;
}
let used = moreMeasureRef.current?.offsetWidth || 0;
let count = 0;
for (const width of widths) {
  if (count > 0 && used + width > available) break;
  used += width;
  count += 1;
}
setVisibleCount(Math.max(1, Math.min(count, widths.length - 1)));
```

Render `items.slice(0, visibleCount)` directly and `items.slice(visibleCount)` inside an anchored menu. The More button must have `aria-haspopup="menu"`, `aria-expanded`, and active state when the selected tab is in overflow. Close on outside pointer-down, Escape, and selection. Use roving focus or direct ArrowDown/ArrowUp focus movement inside the menu.

- [ ] **Step 4: Replace inline navigation and style the menu**

Export/use `AppTab` as the `Tab` type in `App.tsx`, and replace the inline `<nav>` with:

```tsx
<AppNavigation
  tab={tab}
  canAdmin={isPrivilegedRole(user.role)}
  onSelect={setTab}
/>
```

Remove `overflow-x: auto`, hidden scrollbar rules, and the mobile horizontal-scroll override from `.app-nav`. Add:

```css
.app-nav { position: relative; flex: 1 1 auto; min-width: 0; }
.app-nav-list { display: flex; align-items: center; min-width: 0; }
.app-nav-measure { position: fixed; left: -10000px; top: -10000px; display: flex; visibility: hidden; }
.app-nav-more-wrap { position: relative; flex: 0 0 auto; }
.app-nav-menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 8px);
  right: 0;
  min-width: 210px;
  padding: 6px;
  border: 1px solid #49616b;
  border-radius: 8px;
  box-shadow: 0 12px 28px rgba(12, 29, 38, 0.24);
  background: #20343f;
}
.app-nav-menu button { width: 100%; justify-content: flex-start; }
```

At the mobile breakpoint, keep the nav on its own full-width row but let the same measurement logic decide overflow.

- [ ] **Step 5: Run tests/build and commit**

Run:

```bash
npm run test:frontend
npm run build
```

Expected: PASS with no TypeScript or Vite errors.

Commit:

```bash
git add frontend/src/AppNavigation.tsx frontend/src/App.tsx \
  frontend/src/styles.css frontend/src/i18n.tsx frontend/tests/header-layout.test.mjs
git commit -m "fix: expose translated navigation overflow"
```

### Task 8: Full regression, visual QA, and release notes

**Files:**
- Modify: `README.md:55-78`
- Create: `deploy/mexico-approval-usability-release.md`

- [ ] **Step 1: Run the complete automated regression suite**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest backend/tests -q
npm run test:frontend
npm run build
git diff --check
```

Expected: all pytest and Node tests pass, the production bundle builds, and `git diff --check` emits no output.

- [ ] **Step 2: Start an isolated local server for browser verification**

Use a temporary data directory so local development data is not changed:

```bash
PAYMENT_APP_DATA_DIR="$(mktemp -d)" \
PAYMENT_APP_ADMIN_PASSWORD=admin123 \
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/uvicorn backend.app.main:app \
  --host 127.0.0.1 --port 8012
```

In a second terminal run `npm run dev -- --host 127.0.0.1 --port 5174` with `VITE_API_PROXY_TARGET=http://127.0.0.1:8012` if the Vite config requires an explicit proxy target.

- [ ] **Step 3: Perform Chinese and Spanish visual QA**

At 1280, 1512, and 1920 CSS pixels:

1. Switch between Chinese and Spanish.
2. Verify every destination is either directly visible or reachable from `更多 / Más`.
3. Select a destination inside the menu and verify the More button shows an active state.
4. Populate or fixture a Mexico row with a long bilingual company, long Spanish summary, two nodes, and three current approvers.
5. Verify the desktop table has a visible horizontal scrollbar, no overlapping text, readable approver chips, and usable action buttons.
6. Open the detail, verify current approvers appear before attachments, click `加载此单附件`, and verify only the detail attachment state polls.
7. Begin a global sync with delayed attachment mocks and verify approval state refreshes at `state_committed_at` before attachments complete.

Expected: all seven checks pass in both languages.

- [ ] **Step 4: Document the release and migration checks**

Add to `README.md`:

```markdown
- 中国工作台、每日应付和默认导出固定只包含已确认执行地区为中国的请款；执行地区优先于 Sheet 映射。
- 墨西哥审批状态先于附件显示，支持全部当前待办人和单行附件优先加载。
```

Create `deploy/mexico-approval-usability-release.md` with these exact operator checks:

```markdown
1. 停止服务并使用 SQLite Backup API 备份主库。
2. 启动新版本，确认 `schema_migrations` 包含 `mexico_request_region_and_china_isolation_v2`。
3. 确认 `app_settings.china_region_isolation_enabled=true`。
4. 核对“悦为智能”中执行地区为墨西哥的记录已从中国工作台和每日应付排除。
5. 触发墨西哥同步，确认审批状态先显示，附件队列继续后台运行。
```

- [ ] **Step 5: Run final verification after documentation and commit**

Run:

```bash
PYTHONPATH=. /Users/smk/Documents/出纳请款/.venv/bin/pytest backend/tests -q
npm run test:frontend
npm run build
git diff --check
git status --short
```

Expected: all checks pass; status contains only the intended README and release-note changes before commit.

Commit:

```bash
git add README.md deploy/mexico-approval-usability-release.md
git commit -m "docs: add mexico usability release checks"
```

## Final acceptance checklist

- [ ] Mexico rows with explicit execution region override conflicting Sheet mappings.
- [ ] China workbench, daily payables, statistics, and default export always exclude Mexico/review rows.
- [ ] Region v2 migration is append-only for payable history.
- [ ] Every active task and assignee is returned, filterable, and included in reminders.
- [ ] Approval state refreshes when `state_committed_at` appears, before attachment completion.
- [ ] Global and row attachment work share one exclusive priority queue.
- [ ] Repeated row attachment requests remain idempotent and retry failures safely.
- [ ] Mexico desktop rows remain readable with visible horizontal scrolling.
- [ ] Spanish navigation never hides destinations without a visible `Más` entry.
- [ ] Backend tests, frontend tests, TypeScript, Vite build, and visual QA all pass.
