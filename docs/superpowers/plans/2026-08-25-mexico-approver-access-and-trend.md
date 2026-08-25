# Mexico Approver Access, Statistics, and Trend Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Mexico approval list, add approver workload statistics and participant-based access, provision the confirmed accounts, and keep the 14-day payable trend visible when a historical point is selected.

**Architecture:** Add Mexico-specific access fields to `users`, centralize participant visibility in `mexico_tracking.py`, and reuse that predicate for every list/detail/statistics endpoint. Add one read-only approver-statistics endpoint and a two-tab React view, while the daily trend fix remains a frontend-only separation between detail date and trend end date.

**Tech Stack:** FastAPI, SQLite, Pydantic, pytest, React 19, TypeScript, Vite, Node test runner, CSS.

---

## File Structure

- `backend/app/db.py`: add user access columns during new database creation and existing database migration.
- `backend/app/mexico_tracking.py`: centralize participant visibility, extend existing queries, and aggregate approver statistics.
- `backend/app/main.py`: validate user Mexico fields, enforce access on routes, expose statistics, and audit admin changes.
- `backend/app/provision_mexico_users.py`: idempotently bind existing full-access users and create the confirmed accounts.
- `backend/tests/test_mexico_tracking.py`: service-level visibility and statistic tests.
- `backend/tests/test_api_workflows.py`: route authorization and user-administration tests.
- `backend/tests/test_provision_mexico_users.py`: provisioning idempotency and password-preservation tests.
- `frontend/src/api.ts`: user access and approver-statistics types and API methods.
- `frontend/src/App.tsx`: navigation permission, daily trend date separation, and Mexico fields in user administration.
- `frontend/src/AppNavigation.tsx`: omit Mexico navigation when the user has no access.
- `frontend/src/MexicoTrackingPage.tsx`: internal tabs, statistics, person filtering, and six-column rows.
- `frontend/src/styles.css`: compact table, statistics buttons, and responsive styling.
- `frontend/tests/mexico-tracking.test.mjs`: structural regressions for tabs, statistics, access, and compact layout.
- `frontend/tests/header-layout.test.mjs`: navigation permission regression.
- `frontend/tests/daily-payables.test.mjs`: trend/detail date separation regression.
- `deploy/mexico-approver-access-release.md`: production backup, migration, provisioning, and acceptance checklist.

### Task 1: Add Mexico Access Fields and User Validation

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_workflows.py`

- [ ] **Step 1: Write failing schema and admin API tests**

Add tests that initialize a database and assert `users` has `mexico_access_scope` and `mexico_identity_name`. Extend the existing user CRUD workflow test with payloads like:

```python
{
    "username": "mexico-participant",
    "password": "Yuewei123",
    "role": "business",
    "display_name": "Mexico Participant",
    "mexico_access_scope": "participant",
    "mexico_identity_name": "CHONG.MARTINEZ.DAUL",
}
```

Assert login and `/api/me` return both fields, an invalid scope returns 422/400, and `participant` without an identity returns 400.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/pytest backend/tests/test_api_workflows.py -k "mexico_access or role_permissions_user_crud" -q
```

Expected: failures because the columns and API fields do not exist.

- [ ] **Step 3: Implement schema and validation**

Add fields to the table definition and migration:

```python
mexico_access_scope TEXT NOT NULL DEFAULT 'none',
mexico_identity_name TEXT
```

Use `ensure_column` for existing databases. Add constants and validation in `main.py`:

```python
MEXICO_ACCESS_SCOPES = {"all", "participant", "none"}

def validate_mexico_user_access(scope: Any, identity: Any) -> tuple[str, Optional[str]]:
    normalized_scope = str(scope or "none").strip().lower()
    normalized_identity = str(identity or "").strip() or None
    if normalized_scope not in MEXICO_ACCESS_SCOPES:
        raise HTTPException(status_code=400, detail="墨西哥权限范围无效")
    if normalized_scope == "participant" and not normalized_identity:
        raise HTTPException(status_code=400, detail="仅本人参与权限必须绑定钉钉姓名")
    return normalized_scope, normalized_identity
```

Extend `UserIn`, `UserPatch`, `user_public`, create, and update handling. Preserve Mexico fields in audit values.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/main.py backend/tests/test_api_workflows.py
git commit -m "feat: add mexico-specific user access"
```

### Task 2: Centralize Participant Visibility and Enforce It Everywhere

**Files:**
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_mexico_tracking.py`
- Test: `backend/tests/test_api_workflows.py`

- [ ] **Step 1: Write failing service visibility tests**

Create pending/history cases where the bound name appears only as applicant, current task approver, historical operator, or CC operator. Add an unrelated case. Call list, summary, filter options, and detail with `participant_name="Ana"`; assert the four matching routes are visible and the unrelated case is absent. Assert direct detail access to the unrelated ID raises `PermissionError`.

- [ ] **Step 2: Write failing route authorization tests**

Create users with `all`, `participant`, and `none`. Assert:

- `all` sees every Mexico record.
- `participant` sees only matching records and receives 404 for an unrelated detail ID.
- `none` receives 403 for list, summary, filter options, detail, one-row attachment sync, and attachment content.
- Admin remains full access even if the stored field is `none`.

- [ ] **Step 3: Run visibility tests and verify RED**

```bash
.venv/bin/pytest backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py -k "participant_visibility or mexico_route_access" -q
```

Expected: failures because Mexico access is still Sheet-based.

- [ ] **Step 4: Implement a shared visibility predicate**

Extend `_mexico_tracking_where` with `participant_name`. The predicate must use exact trimmed matching:

```sql
TRIM(COALESCE(applicant_name, '')) = ?
OR EXISTS (
  SELECT 1 FROM mexico_approval_current_tasks task
  WHERE task.approval_no = mexico_approval_tracking.approval_no
    AND TRIM(COALESCE(task.approver_name, '')) = ?
)
OR EXISTS (
  SELECT 1 FROM mexico_approval_events event
  WHERE event.approval_no = mexico_approval_tracking.approval_no
    AND TRIM(COALESCE(event.operator_name, '')) = ?
)
```

Pass `participant_name` through list, summary, filter options, and detail. Keep default arguments full-access for service callers and existing tests. Add indexes for tracking applicant and event operator.

In `main.py`, replace Mexico Sheet authorization with one helper:

```python
def mexico_access_for_user(user: Dict[str, Any]) -> Optional[str]:
    if user["role"] == ROLE_ADMIN:
        return None
    scope, identity = validate_mexico_user_access(
        user.get("mexico_access_scope"), user.get("mexico_identity_name")
    )
    if scope == "none":
        raise HTTPException(status_code=403, detail="无权访问墨西哥审批")
    return identity if scope == "participant" else None
```

Every Mexico route must call this helper. Convert service `PermissionError` for a hidden detail to HTTP 404.

- [ ] **Step 5: Run visibility tests and verify GREEN**

Run the Step 3 command. Expected: selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/mexico_tracking.py backend/app/main.py backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py
git commit -m "feat: isolate mexico approvals by participant"
```

### Task 3: Add Approver Workload Statistics

**Files:**
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/api.ts`
- Test: `backend/tests/test_mexico_tracking.py`
- Test: `backend/tests/test_api_workflows.py`

- [ ] **Step 1: Write failing aggregation tests**

Insert a red workflow with Ana and Bruno, a yellow workflow with Ana, and a red workflow containing two current tasks for Ana. Assert:

```python
stats == [
    {"approver_name": "Ana", "pending": 3, "overdue": 3, "severe": 2},
    {"approver_name": "Bruno", "pending": 1, "overdue": 1, "severe": 1},
]
```

Add a participant visibility assertion so unrelated workflow counts are not exposed.

- [ ] **Step 2: Run aggregation tests and verify RED**

```bash
.venv/bin/pytest backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py -k "approver_stat" -q
```

Expected: failure because no aggregation function or route exists.

- [ ] **Step 3: Implement service and API**

Add `summarize_mexico_approvers(...)`. Load all visible pending rows, attach current tasks, deduplicate names per approval, and aggregate warning levels using the existing settings and `warning_level`. Sort by `(-severe, -overdue, -pending, approver_name)`.

Expose:

```python
@app.get("/api/mexico-tracking/approver-stats")
def get_mexico_approver_stats(...):
    participant_name = mexico_access_for_user(user)
    with connect() as conn:
        return {"items": summarize_mexico_approvers(conn, participant_name=participant_name)}
```

Add `MexicoApproverStat` and `api.mexicoTrackingApproverStats()` in `frontend/src/api.ts`.

- [ ] **Step 4: Run aggregation tests and verify GREEN**

Run the Step 2 command. Expected: selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/mexico_tracking.py backend/app/main.py backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py frontend/src/api.ts
git commit -m "feat: summarize mexico approvals by approver"
```

### Task 4: Provision Confirmed Mexico Accounts Idempotently

**Files:**
- Create: `backend/app/provision_mexico_users.py`
- Create: `backend/tests/test_provision_mexico_users.py`

- [ ] **Step 1: Write failing provisioning tests**

Create existing Tiffany and 施鸣坤 users with known password hashes. Assert the provisioner:

- binds them to `all` without changing role or password hash;
- creates the nine confirmed missing users with `Yuewei123`;
- creates Nelly and Angelica as `finance`, others as `business`;
- grants no Sheet permissions to new business users;
- creates no “未识别人员” account;
- produces no duplicates on a second run;
- writes `user.create` / `user.update` audit entries without exposing password material;
- raises a conflict if Tiffany or 施鸣坤 matches multiple existing rows.

- [ ] **Step 2: Run the test and verify RED**

```bash
.venv/bin/pytest backend/tests/test_provision_mexico_users.py -q
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement the idempotent module**

Define immutable account specs and a callable `provision_mexico_users(conn, actor_id)`. Match protected existing users by normalized username or an explicit display-name alias set. Never update existing password hashes or roles. For create-if-missing accounts, hash `Yuewei123` with the existing security helper. Write sanitized user audit entries with the selected active administrator as actor. Return counts and conflicts; the CLI must exit nonzero before mutation if protected matching is ambiguous.

Support production execution with:

```bash
.venv/bin/python -m backend.app.provision_mexico_users --actor-username admin
```

- [ ] **Step 4: Run the test and verify GREEN**

Run the Step 2 command. Expected: all provisioning tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/provision_mexico_users.py backend/tests/test_provision_mexico_users.py
git commit -m "feat: provision mexico approval accounts"
```

### Task 5: Expose Mexico Access in Navigation and User Administration

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/AppNavigation.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/header-layout.test.mjs`
- Modify: `frontend/tests/mexico-tracking.test.mjs`

- [ ] **Step 1: Write failing frontend tests**

Assert `User` includes `mexico_access_scope` and `mexico_identity_name`, `AppNavigation` accepts `canMexico`, and the Mexico item is conditional. Assert AdminView renders permission scope and identity controls and submits the fields.

- [ ] **Step 2: Run frontend tests and verify RED**

```bash
npm run test:frontend
```

Expected: new assertions fail.

- [ ] **Step 3: Implement frontend user access**

Add:

```ts
export type MexicoAccessScope = "all" | "participant" | "none";
```

Pass `canMexico={user.role === "admin" || user.mexico_access_scope !== "none"}` to navigation. If an access update removes permission while the Mexico tab is active, return to the workspace on the next user reload. Add AdminView selectors for the three scopes and an identity input enabled for `participant` and optionally populated for `all`.

- [ ] **Step 4: Run frontend tests and build**

```bash
npm run test:frontend
npm run build
```

Expected: tests and TypeScript/Vite build pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/App.tsx frontend/src/AppNavigation.tsx frontend/src/styles.css frontend/tests/header-layout.test.mjs frontend/tests/mexico-tracking.test.mjs
git commit -m "feat: manage mexico approval access"
```

### Task 6: Build the Approver Statistics Tab and Compact Task Table

**Files:**
- Modify: `frontend/src/MexicoTrackingPage.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/mexico-tracking.test.mjs`

- [ ] **Step 1: Write failing statistics and layout tests**

Assert the page contains `mexico-view-tabs`, `mexico-approver-stats`, the approver-stat API call, and a handler that applies an approver then returns to the list. Replace the legacy nine-column CSS assertions with a six-column table, approximately `1400px` minimum width, two-line applicant/company and summary clamping, and an untruncated current-task area.

- [ ] **Step 2: Run frontend tests and verify RED**

```bash
npm run test:frontend
```

Expected: new assertions fail against the nine-column page.

- [ ] **Step 3: Implement the two tabs and statistics buttons**

Add `mode: "list" | "approvers"`, loading/error state for statistics, and refresh it with overview/sync completion. Render a “全部审批人” button and one button per statistic. Clicking a person must set both form and applied approver filters, reset page to one, and set mode to list.

Update warning KPI handlers to preserve the applied approver when setting yellow/red. Do not preserve unrelated temporary filters when using KPI shortcuts.

- [ ] **Step 4: Implement the six-column row and CSS**

Group applicant and company into `mexico-request-cell`; group nodes and all approvers into `mexico-current-task-cell`. Use `title` plus two-line clamps for company and summary. Set the table minimum width around 1400px, add a warning accent on the first cell, and remove the full red row background. Keep the mobile cards unchanged except for the shared all-approver rendering.

- [ ] **Step 5: Run tests and production build**

```bash
npm run test:frontend
npm run build
```

Expected: tests and build pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/MexicoTrackingPage.tsx frontend/src/styles.css frontend/tests/mexico-tracking.test.mjs
git commit -m "feat: add mexico approver workload view"
```

### Task 7: Decouple Daily Detail Date from the Trend Window

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/tests/daily-payables.test.mjs`

- [ ] **Step 1: Write the failing trend interaction regression**

Add structural assertions that DailyPayablesView defines `trendEndDate`, trend API range uses it, the date input updates both dates, and `DailyPayablesTrendChart.onSelectDate` updates only `selectedDate`.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
node --test frontend/tests/daily-payables.test.mjs
```

Expected: failure because only `selectedDate` exists.

- [ ] **Step 3: Implement the minimal state separation**

Add:

```ts
const [selectedDate, setSelectedDate] = useState(localIsoDate(new Date()));
const [trendEndDate, setTrendEndDate] = useState(localIsoDate(new Date()));
```

Derive the trend start from `trendEndDate` and request `api.dailyPayablesTrend(start, trendEndDate)`. The top date input calls a handler that updates both states. The chart continues to receive `onSelectDate={setSelectedDate}`.

- [ ] **Step 4: Run focused and full frontend verification**

```bash
node --test frontend/tests/daily-payables.test.mjs
npm run test:frontend
npm run build
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/tests/daily-payables.test.mjs
git commit -m "fix: preserve future daily payable trend points"
```

### Task 8: Release Documentation and Full Verification

**Files:**
- Create: `deploy/mexico-approver-access-release.md`
- Modify: `README.md`

- [ ] **Step 1: Write deployment and rollback instructions**

Document exact steps to stop the service, create a SQLite backup using the backup API, deploy the commit, install requirements, build frontend assets, start once to initialize columns/indexes, run the provisioning module, restart, and validate with three account scopes. Include rollback using the backup plus previous Git commit.

- [ ] **Step 2: Run the complete verification suite**

```bash
.venv/bin/pytest -q
npm run test:frontend
npm run build
git diff --check
```

Expected: all backend and frontend tests pass, build exits zero, and no whitespace errors are reported.

- [ ] **Step 3: Run browser acceptance locally**

Start the local app with an isolated database containing full, participant, and none users. Verify:

1. Compact list has all six columns without horizontal scroll at approximately 1512 CSS pixels.
2. Approver statistics switch and person buttons filter the list.
3. Participant users see applicant/current/historical/CC matches only.
4. None users have no Mexico navigation and receive 403 directly.
5. Clicking 8/24 keeps the 8/25 trend node visible while changing daily details.
6. Chinese, Spanish, and mobile card layouts remain usable.

- [ ] **Step 4: Commit release documentation**

```bash
git add deploy/mexico-approver-access-release.md README.md
git commit -m "docs: add mexico access release checks"
```

- [ ] **Step 5: Review branch before integration**

Run:

```bash
git status --short --branch
git log --oneline main..HEAD
git diff --stat main...HEAD
```

Expected: clean worktree with only the intended commits. Then use the finishing-a-development-branch skill for local integration and deployment approval.
