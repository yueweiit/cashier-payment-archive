# Expected Payment Account Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an editable “预计支付账户 / Cuenta de pago prevista” field that prefers the explicit DingTalk value, otherwise defaults from the DingTalk service subject, while protecting manual results throughout synchronization, Excel, rollover, merge, archive, and restore workflows.

**Architecture:** Add a persisted value plus an internal provenance column on `payment_requests`. Put service-subject normalization and source-aware transitions in one pure domain module, let every DingTalk mapper emit the same candidate, and keep all ordinary UI/Excel writes classified as manual at the request persistence boundary. The existing metadata-sync transaction applies only valid, unique candidates through the transition function and audits both the value and its source.

**Tech Stack:** Python 3.9, FastAPI, SQLite, PostgreSQL/psycopg read-only DingTalk source, openpyxl, pytest, React 18, TypeScript, Vite, Node test runner, systemd production deployment.

---

## File Map

- Create `backend/app/expected_payment_account.py`: source constants, alias normalization, DingTalk candidate resolution, and the source-aware synchronization transition.
- Modify `backend/app/db.py`: create and migrate the two `payment_requests` columns.
- Modify `backend/app/external_expenses.py`: parse bilingual fields and emit the canonical value/source/warning for operation, purchase, and monthly approvals.
- Modify `backend/app/main.py`: expose the business value, mark UI/Excel writes as manual, preserve trusted auto sources during DingTalk import/rollover, synchronize existing requests, audit transitions, and register the grid column.
- Modify `backend/app/excel_io.py`: add the Chinese workbench header to import/export without changing Daily Payables export.
- Verify `backend/app/snapshots.py`: its dynamic-column restore already carries both fields; add regression coverage instead of special-case code unless the test exposes a gap.
- Create `backend/tests/test_expected_payment_account.py`: pure resolver and transition tests.
- Modify `backend/tests/test_api_workflows.py`: schema, API, DingTalk import/sync, permissions, rollover, merge, snapshot, and audit integration tests.
- Modify `backend/tests/test_excel_io.py`: workbook header/value round-trip tests.
- Modify `frontend/src/api.ts`: add the public business field to request and import-preview types; do not expose source as editable state.
- Modify `frontend/src/App.tsx`: add the desktop column, column preference, editor input, and mobile display.
- Modify `frontend/src/i18n.tsx`: add the Spanish label where shared translation lookup is used.
- Create `frontend/tests/expected-payment-account.test.mjs`: frontend contract regression tests.
- Modify `README.md`: document the field and its precedence.
- Create `deploy/expected-payment-account-release.md`: production backup, migration, acceptance, and rollback checklist.
- Reference `docs/superpowers/specs/2026-09-02-expected-payment-account-design.md`: approved behavior; do not expand scope to Daily Payables or a mapping-management page.

### Task 1: Add the domain model and compatible database migration

**Files:**
- Create: `backend/app/expected_payment_account.py`
- Modify: `backend/app/db.py:160-220,405-445`
- Create: `backend/tests/test_expected_payment_account.py`
- Test: `backend/tests/test_api_workflows.py`

- [ ] **Step 1: Write failing pure-domain tests**

Cover explicit precedence, all five mapped subjects and their current aliases, unknown subjects, legacy nonblank values with no source, every transition, and explicit-field clearing. The exact alias configuration under test is:

~~~python
SERVICE_SUBJECT_ACCOUNT_ALIASES = (
    ("悦为智能公司账户", ("悦为智能", "YW Tech_Ai", "Yuewei Intelligent")),
    ("凌翔公司账户", ("凌翔", "凌翔产品&开发", "凌翔供应链及采购执行单元")),
    ("星铭公司账户", ("星铭", "星铭HR人力资源中心")),
    ("拉丁购公司账户", ("拉丁购", "Latin Buy")),
    ("YW MOLDES公司账户", ("YW MOLDES", "YW MOLDES MX模具")),
)
~~~

The core assertions are:

~~~python
@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("悦为智能 YW Tech_Ai", "悦为智能公司账户"),
        ("Yuewei Intelligent", "悦为智能公司账户"),
        ("凌翔产品&开发", "凌翔公司账户"),
        ("凌翔供应链及采购执行单元", "凌翔公司账户"),
        ("星铭HR人力资源中心", "星铭公司账户"),
        ("拉丁购", "拉丁购公司账户"),
        ("YW MOLDES MX模具", "YW MOLDES公司账户"),
    ],
)
def test_resolve_expected_payment_account_from_service_subject(subject, expected):
    candidate = resolve_expected_payment_account(None, subject)
    assert candidate.value == expected
    assert candidate.source == SOURCE_SERVICE_SUBJECT_DEFAULT
    assert candidate.warning is None


def test_explicit_value_wins_and_preserves_display_text():
    candidate = resolve_expected_payment_account("  拉丁购 USD 主账户  ", "悦为智能")
    assert candidate.value == "拉丁购 USD 主账户"
    assert candidate.source == SOURCE_DINGTALK_EXPLICIT


def test_unknown_subject_is_blank_and_warns():
    candidate = resolve_expected_payment_account(None, "新公司")
    assert candidate.value is None
    assert candidate.source is None
    assert candidate.warning == "服务主体无法匹配预计支付账户，请人工填写"
~~~

Add a transition matrix proving:

- blank → default and blank → explicit both fill;
- manual is never overwritten;
- source-less legacy nonblank is treated as manual for the overwrite decision, but its stored null source is not rewritten merely by an empty/unknown candidate;
- default → changed default and default → explicit update;
- explicit → changed explicit updates;
- explicit → empty/default retains the prior explicit result;
- empty candidate is a no-op.

- [ ] **Step 2: Run the new test and verify RED**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_expected_payment_account.py
~~~

Expected: collection fails because the new domain module does not exist.

- [ ] **Step 3: Implement the pure resolver and transition**

Create an immutable candidate and one matching normalizer. Keep mappings conservative and code-owned:

~~~python
SOURCE_DINGTALK_EXPLICIT = "dingtalk_explicit"
SOURCE_SERVICE_SUBJECT_DEFAULT = "service_subject_default"
SOURCE_MANUAL = "manual"
VALID_EXPECTED_PAYMENT_ACCOUNT_SOURCES = {
    SOURCE_DINGTALK_EXPLICIT,
    SOURCE_SERVICE_SUBJECT_DEFAULT,
    SOURCE_MANUAL,
}

@dataclass(frozen=True)
class ExpectedPaymentAccountCandidate:
    value: Optional[str]
    source: Optional[str]
    warning: Optional[str] = None


def resolve_expected_payment_account(explicit_value: Any, service_subject: Any) -> ExpectedPaymentAccountCandidate:
    explicit = clean_text(explicit_value)
    if explicit:
        return ExpectedPaymentAccountCandidate(explicit, SOURCE_DINGTALK_EXPLICIT)
    subject = clean_text(service_subject)
    if not subject:
        return ExpectedPaymentAccountCandidate(None, None)
    default = default_account_for_service_subject(subject)
    if default:
        return ExpectedPaymentAccountCandidate(default, SOURCE_SERVICE_SUBJECT_DEFAULT)
    return ExpectedPaymentAccountCandidate(
        None,
        None,
        "服务主体无法匹配预计支付账户，请人工填写",
    )
~~~

Implement `transition_synced_expected_payment_account(current_value, current_source, candidate)` as the tested state machine. A nonblank value with an absent/invalid source must be treated as manual for the overwrite decision. If it is retained, return its original stored source so an empty or unknown candidate does not create a synthetic change or audit. Return the retained or changed `(value, source)` tuple without mutating input.

- [ ] **Step 4: Add schema and migration tests, then verify RED**

In `test_api_workflows.py`, initialize a fresh test database and assert `PRAGMA table_info(payment_requests)` contains both fields. Create an ordinary request with a nonblank expected account and assert the stored source is `manual`; this API assertion should remain red until Task 3, while the schema assertion becomes green here.

- [ ] **Step 5: Add the database columns**

Add to the `CREATE TABLE payment_requests` definition immediately after `payment_account`:

~~~sql
expected_payment_account TEXT,
expected_payment_account_source TEXT,
~~~

Add idempotent migrations in `migrate_schema()`:

~~~python
ensure_column(conn, "payment_requests", "expected_payment_account", "TEXT")
ensure_column(conn, "payment_requests", "expected_payment_account_source", "TEXT")
~~~

Do not backfill historical rows during migration.

- [ ] **Step 6: Run domain and schema tests and commit**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_expected_payment_account.py
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'expected_payment_account_schema'
git add backend/app/expected_payment_account.py backend/app/db.py backend/tests/test_expected_payment_account.py backend/tests/test_api_workflows.py
git commit -m "feat: add expected payment account domain model"
~~~

Expected: pure resolver/transition tests and schema migration tests pass. The later API-source test may stay excluded until Task 3.

### Task 2: Parse and map DingTalk fields for every source type

**Files:**
- Modify: `backend/app/external_expenses.py:45-75,681-725,940-1035,1174-1190,1890-2080`
- Modify: `backend/tests/test_api_workflows.py:59-215,3130-3490`

- [ ] **Step 1: Write failing mapper tests**

Extend the existing invoice/project fixtures with these form components:

~~~python
{"name": "预计支付账户Cuenta de pago prevista", "value": "悦为智能 6221 主账户"},
{"name": "服务主体Sujeto de servicio", "value": "悦为智能 YW Tech_Ai"},
~~~

Assert the top-level result, `request_data`, `raw_extra.external_source`, and `_external_expense_metadata()` all contain exactly:

~~~python
assert mapped["expected_payment_account"] == "悦为智能 6221 主账户"
assert mapped["expected_payment_account_source"] == "dingtalk_explicit"
assert mapped["request_data"]["expected_payment_account"] == "悦为智能 6221 主账户"
assert mapped["request_data"]["expected_payment_account_source"] == "dingtalk_explicit"
assert mapped["request_data"]["raw_extra"]["external_source"]["service_subject"] == "悦为智能 YW Tech_Ai"
~~~

Add cases for JSON-encoded selection objects using `name` and `label`, fallback to each service-subject alias, an unknown subject warning, and absence of both fields. Repeat representative explicit/default cases for purchase and monthly mappings. Continue asserting invoice only controls `payment_account` (`公户`/`私户`).

- [ ] **Step 2: Run mapper tests and verify RED**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'expected_payment_account_mapping or monthly_payment_mapping'
~~~

Expected: failures because mapped records do not yet expose the new value/source.

- [ ] **Step 3: Add bilingual prefixes and shared candidate resolution**

Add constants beside invoice/project prefixes:

~~~python
EXPECTED_PAYMENT_ACCOUNT_COMPONENT_PREFIXES = (
    "预计支付账户",
    "cuenta de pago prevista",
)
SERVICE_SUBJECT_COMPONENT_PREFIXES = (
    "服务主体",
    "sujeto de servicio",
)
~~~

After `form_values` is available in `map_external_expense()`, use the existing decoded display-value helper:

~~~python
explicit_expected_account = _form_component_value(
    form_values,
    *EXPECTED_PAYMENT_ACCOUNT_COMPONENT_PREFIXES,
)
service_subject = _form_component_value(form_values, *SERVICE_SUBJECT_COMPONENT_PREFIXES)
expected_candidate = resolve_expected_payment_account(explicit_expected_account, service_subject)
if expected_candidate.warning:
    warnings.append(expected_candidate.warning)
~~~

The warning append must occur after `warnings` is initialized. Add candidate value/source and raw inputs to top-level, `request_data`, and `raw_extra.external_source`. `_external_expense_metadata()` must expose canonical top-level value/source rather than relying on stale nested metadata.

Because `map_monthly_payment()` already forwards the raw form payload through `map_external_expense()`, do not build a second resolver. Verify its later invoice-account fallback does not alter the expected-account candidate.

- [ ] **Step 4: Run mapping regressions and commit**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'external_expense_maps or external_expense_metadata_exposes or monthly_payment_mapping'
git add backend/app/external_expenses.py backend/tests/test_api_workflows.py
git commit -m "feat: map DingTalk expected payment account"
~~~

Expected: operation, purchase, and monthly cases pass; unknown subjects warn without blocking import; invoice/account-nature assertions remain unchanged.

### Task 3: Persist trusted automatic sources and classify manual writes

**Files:**
- Modify: `backend/app/main.py:235-285,436-535,1384-1485,4570-4615,4860-4890,8390-8665`
- Modify: `backend/tests/test_api_workflows.py`

- [ ] **Step 1: Write failing API/import/manual-clear tests**

Add integration cases proving:

1. UI create with `expected_payment_account="人工账户"` stores source `manual`.
2. UI patch to another nonblank value stays `manual`.
3. UI patch to `""` stores blank value and a null source.
4. Bulk grid create/update applies the same rules.
5. External import preserves `dingtalk_explicit` or `service_subject_default` supplied by the trusted mapper.
6. A client-supplied `expected_payment_account_source` in a bulk dictionary cannot spoof an automatic source.
7. A business user may edit the field only on an accessible Sheet and cannot use the new field to bypass existing Sheet visibility or mutation permissions.

- [ ] **Step 2: Run the focused tests and verify RED**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'expected_payment_account_manual or expected_payment_account_external_import'
~~~

Expected: failures because request models and write-field normalization omit the new field/source.

- [ ] **Step 3: Add the public business field and internal write field**

Add only this field to `RequestIn`:

~~~python
expected_payment_account: Optional[str] = None
~~~

Add both database columns to `REQUEST_WRITE_FIELDS`, but never add `expected_payment_account_source` to the Pydantic request model or frontend payload type. Add `expected_payment_account` to `REQUEST_FIELD_LABELS`.

- [ ] **Step 4: Centralize manual classification at persistence boundaries**

Add a helper that trims values and never trusts a public source marker:

~~~python
def apply_expected_payment_account_write_source(
    data: Dict[str, Any],
    *,
    preserve_trusted_source: bool,
) -> Dict[str, Any]:
    result = dict(data)
    if "expected_payment_account" not in result:
        result.pop("expected_payment_account_source", None)
        return result
    value = str(result.get("expected_payment_account") or "").strip()
    result["expected_payment_account"] = value or None
    if not value:
        result["expected_payment_account_source"] = None
    elif preserve_trusted_source and result.get("expected_payment_account_source") in VALID_EXPECTED_PAYMENT_ACCOUNT_SOURCES:
        pass
    else:
        result["expected_payment_account_source"] = SOURCE_MANUAL
    return result
~~~

Call it before `normalize_request_payload()` in `insert_request()`. Add keyword-only `preserve_expected_payment_account_source: bool = False`. For `update_request_row()`, always apply manual classification when the business field is present and discard a standalone submitted source marker.

Pass `preserve_expected_payment_account_source=True` only from:

- the unique DingTalk external-import `insert_request()` call;
- rollover, where the already stored value/source must be copied exactly.

Weekly Excel import, merge create/update, single create/update, and bulk grid saves use the default manual behavior.

- [ ] **Step 5: Run API/import tests and commit**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'expected_payment_account_manual or expected_payment_account_external_import or bulk_save_requests'
git add backend/app/main.py backend/tests/test_api_workflows.py
git commit -m "feat: preserve expected account provenance"
~~~

Expected: trusted DingTalk candidates retain automatic sources; every UI/Excel-style write is manual; clear removes the marker; spoofed source input is ignored.

### Task 4: Synchronize existing records with manual-result protection

**Files:**
- Modify: `backend/app/main.py:5890-6005`
- Modify: `backend/tests/test_api_workflows.py:3890-4290`

- [ ] **Step 1: Extend existing metadata-sync tests with the full transition matrix**

Create requests representing blank, manual, default, explicit, and source-less legacy states. Feed unique metadata candidates in successive syncs and assert:

- blank fills;
- manual and legacy nonblank values stay unchanged;
- default follows a new default and upgrades to explicit;
- explicit follows a changed explicit value;
- explicit survives later DingTalk clearing;
- unknown/unmatched/conflict records do not change;
- manual clear through the API allows the next sync to refill;
- repeated identical sync creates no second `metadata_sync.request_fields` audit.

Assert audits include all four keys:

~~~python
assert json.loads(audit["old_value_json"]) == {
    "expected_payment_account": None,
    "expected_payment_account_source": None,
}
assert json.loads(audit["new_value_json"]) == {
    "expected_payment_account": "悦为智能公司账户",
    "expected_payment_account_source": "service_subject_default",
}
~~~

- [ ] **Step 2: Run the sync tests and verify RED**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'expected_payment_account_sync'
~~~

Expected: failures because metadata synchronization ignores the new candidate/source.

- [ ] **Step 3: Validate metadata and apply the pure transition in the existing transaction**

For uniquely matched approvals only, construct a candidate from trimmed metadata if its source is valid. Reject a default-source value not produced by the configured mapping. Then call the pure transition:

~~~python
next_expected_value, next_expected_source = transition_synced_expected_payment_account(
    row["expected_payment_account"],
    row["expected_payment_account_source"],
    metadata_candidate,
)
~~~

Add both fields to the existing `UPDATE payment_requests`. Extend `external_expenses.metadata_sync.request_fields` audit only when a business value/source actually changes; include old/new source alongside the value. Do not create a field audit for conflicts, unmatched records, invalid candidates, or idempotent reruns.

Keep `payment_account` (account nature), project, payee, approval workflow, attachments, and Daily Payables behavior untouched.

- [ ] **Step 4: Run synchronization regressions and commit**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'expected_payment_account_sync or dingtalk_sync_fills_blank or external_expense_metadata_sync_statuses'
git add backend/app/main.py backend/tests/test_api_workflows.py
git commit -m "feat: sync expected account without overwriting manual values"
~~~

Expected: all transition, conflict, atomic-failure, audit, and idempotence assertions pass.

### Task 5: Carry the field through Excel and lifecycle operations

**Files:**
- Modify: `backend/app/excel_io.py:18-130,500-610,1190-1260`
- Verify: `backend/app/snapshots.py:150-240`
- Modify: `backend/app/main.py:1384-1485,3618-3680`
- Modify: `backend/tests/test_excel_io.py`
- Modify: `backend/tests/test_api_workflows.py:998-1068,1802-1970`

- [ ] **Step 1: Write failing Excel and lifecycle tests**

Add tests that:

- export “预计支付账户” immediately after “付款账户” on every workbench request sheet;
- import that header and classify a nonblank value as `manual`;
- merge updates it as `manual`, including a clear;
- rollover preserves both value and source without reclassifying;
- a baseline snapshot restore returns both fields to their snapshotted values;
- archived correction preserves normal reason/audit behavior;
- Daily Payables page/export headers do not gain this field.

- [ ] **Step 2: Run focused tests and verify RED**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_excel_io.py backend/tests/test_api_workflows.py -k 'expected_payment_account_excel or expected_payment_account_rollover or expected_payment_account_snapshot'
~~~

Expected: header/value assertions fail.

- [ ] **Step 3: Add the workbench Excel field**

Add to `CORE_FIELDS` and `KNOWN_HEADER_ALIASES`:

~~~python
"expected_payment_account": "预计支付账户",
"expected_payment_account": ["预计支付账户", "计划支付账户", "Cuenta de pago prevista"],
~~~

During `SHEET_HEADERS` normalization, insert “预计支付账户” immediately after “付款账户” if absent. Parse it in `row_from_sheet()`, include it in `values_for_headers()`, and give it a readable text-column width. Do not add it to `PAYMENT_DETAIL_HEADERS` or `daily_payables_export.py`.

Add `expected_payment_account` to `MERGE_REQUEST_FIELDS`. Rollover already starts from `SELECT *`; the trusted-source flag from Task 3 must preserve both columns. Snapshot restore is dynamic via `table_columns()`; leave production code unchanged if the new regression test passes.

- [ ] **Step 4: Run Excel/lifecycle regressions and commit**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_excel_io.py backend/tests/test_api_workflows.py -k 'excel or merge or rollover or snapshot or expected_payment_account'
git add backend/app/excel_io.py backend/app/main.py backend/tests/test_excel_io.py backend/tests/test_api_workflows.py
git commit -m "feat: carry expected account through workbench lifecycle"
~~~

Expected: Excel round-trip, manual classification, source-preserving rollover, snapshot restore, and unchanged Daily Payables tests pass.

### Task 6: Add the responsive bilingual workbench UI

**Files:**
- Modify: `frontend/src/api.ts:265-300,620-740`
- Modify: `frontend/src/App.tsx:80-270,2050-2080,4140-4245,4620-4995`
- Modify: `frontend/src/i18n.tsx`
- Create: `frontend/tests/expected-payment-account.test.mjs`
- Modify: `backend/app/main.py:436-485,1088-1148`
- Modify: `backend/tests/test_api_workflows.py:1038-1068`

- [ ] **Step 1: Write failing frontend and preference tests**

The Node source-contract test must assert:

- `PaymentRequest` has `expected_payment_account?: string` but no editable source property;
- the desktop column follows `payment_account` and uses Spanish `Cuenta de pago prevista`;
- it is in `defaultVisibleGridColumnKeys` and `wrappableColumnKeys`;
- editor dirty-field lists and form layout include it after account nature;
- mobile cards display both account nature and expected payment account;
- Daily Payables source files do not reference it.

Extend backend grid-preference coverage so a saved old preference missing the new key gets it appended to order and not hidden, while explicit later hiding still works.

- [ ] **Step 2: Run tests and verify RED**

~~~bash
npm run test:frontend
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'request_grid_preferences'
~~~

Expected: new column and preference assertions fail.

- [ ] **Step 3: Implement the UI contract**

Add `expected_payment_account` after `payment_account` in backend `REQUEST_GRID_COLUMN_KEYS` and `REQUEST_GRID_DEFAULT_VISIBLE`. The current preference normalizer appends missing allowed keys, so keep version 1 unless a test proves migration needs a bump.

Add this grid definition:

~~~typescript
{ key: "expected_payment_account", labelZh: "预计支付账户", labelEs: "Cuenta de pago prevista", width: 220 },
~~~

Add the field to `fieldLabels`, `defaultVisibleGridColumnKeys`, `wrappableColumnKeys`, request-editor dirty fields, and the “基本信息” editor section immediately after `payment_account`. Add account-nature and expected-account entries to mobile-card context using bilingual labels. Leave `emptyRequest` blank so manually created requests are not guessed from Sheet names.

- [ ] **Step 4: Run frontend tests/build and commit**

~~~bash
npm run test:frontend
npm run build
.venv/bin/python -m pytest -q backend/tests/test_api_workflows.py -k 'request_grid_preferences'
git add backend/app/main.py backend/tests/test_api_workflows.py frontend/src/api.ts frontend/src/App.tsx frontend/src/i18n.tsx frontend/tests/expected-payment-account.test.mjs
git commit -m "feat: show expected payment account in workbench"
~~~

Expected: Node tests, TypeScript checking, Vite build, and preference tests pass.

### Task 7: Full verification, review, and documentation

**Files:**
- Modify: `README.md`
- Create: `deploy/expected-payment-account-release.md`
- Verify all files listed above.

- [ ] **Step 1: Document business rules and release safety**

In `README.md`, document explicit DingTalk value > service-subject default, manual protection, and the distinction from account nature. In the release guide, include real DB path discovery, SQLite Backup API, migration verification, source-aware sync checks, rollback, and public health checks.

- [ ] **Step 2: Run focused backend tests**

~~~bash
.venv/bin/python -m pytest -q backend/tests/test_expected_payment_account.py
.venv/bin/python -m pytest -q backend/tests/test_excel_io.py backend/tests/test_api_workflows.py -k 'expected_payment_account or external_expense_maps or monthly_payment_mapping or request_grid_preferences or rollover or snapshot'
~~~

Expected: all selected tests pass.

- [ ] **Step 3: Run complete regression and production build**

~~~bash
.venv/bin/python -m pytest -q backend/tests
npm run test:frontend
npm run build
git diff --check
~~~

Expected: all tests pass, Vite production build succeeds, and `git diff --check` emits no output. If a pre-existing date-sensitive test fails, reproduce it on the pre-feature commit and document the exact result; never waive a new or feature-related failure.

- [ ] **Step 4: Request code review and resolve findings**

Use the required `superpowers:requesting-code-review` skill. Review schema compatibility, source spoofing, manual clear/refill, unknown-subject safety, conflict isolation, Excel round-trip, and proof that Daily Payables did not change. Apply valid findings test-first, rerun the relevant suites, and commit each correction.

- [ ] **Step 5: Verify spec coverage and commit documentation**

~~~bash
git diff main...HEAD --stat
git diff main...HEAD -- backend/app backend/tests frontend/src frontend/tests README.md deploy/expected-payment-account-release.md
git status --short --branch
git add README.md deploy/expected-payment-account-release.md
git commit -m "docs: add expected account release runbook"
~~~

Expected: only intended tracked files differ; user-owned `.superpowers/` and `output/` remain untouched.

### Task 8: Merge local main and deploy online

**Files / locations:**
- Local checkout: `/Users/smk/Documents/出纳请款`
- Production checkout: `/www/wwwroot/cashier-payment-archive`
- Production service: `cashier-payment`
- Backup root: `/data/cashier-payment/backups`

- [ ] **Step 1: Finish the branch, fast-forward local `main`, and push**

Use `superpowers:finishing-a-development-branch`, select local integration, then run from the primary checkout:

~~~bash
git status --short --branch
git switch main
git merge --ff-only codex/expected-payment-account
git push origin main
git rev-parse HEAD
git rev-parse origin/main
~~~

Expected: local `main` and `origin/main` are identical. Preserve untracked `.superpowers/` and `output/`.

- [ ] **Step 2: Capture the live database path and old revision**

In the authenticated external Chrome/Aliyun Workbench terminal, follow `deploy/expected-payment-account-release.md`. From `/www/wwwroot/cashier-payment-archive`, confirm a clean tracked checkout and capture the real database path from the running service; do not assume the default path:

~~~bash
cd /www/wwwroot/cashier-payment-archive
git status --short --branch
sudo systemctl status cashier-payment --no-pager
service_pid="$(sudo systemctl show cashier-payment --property=MainPID --value)"
release_env_file=/run/cashier-payment-expected-account.env
sudo env SERVICE_PID="$service_pid" RELEASE_ENV_FILE="$release_env_file" .venv/bin/python - <<'PY'
import os
import shlex
from pathlib import Path

service_pid = int(os.environ["SERVICE_PID"])
if service_pid <= 0:
    raise SystemExit("cashier-payment 服务没有正在运行的 MainPID")
entries = Path(f"/proc/{service_pid}/environ").read_bytes().split(b"\0")
service_env = {}
for entry in entries:
    if b"=" in entry:
        key, value = entry.split(b"=", 1)
        service_env[key.decode()] = value.decode()
working_dir = Path(f"/proc/{service_pid}/cwd").resolve()
data_dir = Path(service_env.get("PAYMENT_APP_DATA_DIR", working_dir / "data")).resolve()
db_path = Path(service_env.get("PAYMENT_APP_DB", data_dir / "app.db")).resolve()
if not db_path.is_file():
    raise SystemExit(f"解析的生产数据库不存在: {db_path}")
Path(os.environ["RELEASE_ENV_FILE"]).write_text(
    f"PAYMENT_APP_DATA_DIR={shlex.quote(str(data_dir))}\n"
    f"PAYMENT_APP_DB={shlex.quote(str(db_path))}\n",
    encoding="utf-8",
)
print(db_path)
PY
sudo chown "$(id -u):$(id -g)" "$release_env_file"
chmod 600 "$release_env_file"
. "$release_env_file"
git rev-parse HEAD
~~~

Expected: tracked files are clean, the service is active, the resolved DB exists, and the old commit is recorded.

- [ ] **Step 3: Stop service and create a unique consistent backup**

Create a non-overwriting directory:

~~~bash
release_backup_dir="$(mktemp -d /data/cashier-payment/backups/20260902-expected-account-XXXXXX)"
git rev-parse HEAD > "$release_backup_dir/previous-commit.txt"
sudo systemctl stop cashier-payment
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  PAYMENT_RELEASE_BACKUP_PATH="$release_backup_dir/app.db" \
  .venv/bin/python -c 'import json, os; from backend.app.db import backup_database; print(json.dumps(backup_database(os.environ["PAYMENT_RELEASE_BACKUP_PATH"]), ensure_ascii=False))'
~~~

Expected: backup output reports a nonzero file, SHA-256, and integrity `ok`. If backup fails, restart the old service and stop the release.

- [ ] **Step 4: Deploy, migrate, and verify schema**

~~~bash
deploy_started="$(date '+%Y-%m-%d %H:%M:%S')"
git fetch origin
git switch main
git pull --ff-only origin main
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
sudo systemctl start cashier-payment
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/)" = 200
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  .venv/bin/python -c 'import sqlite3; from backend.app.db import DB_PATH; c=sqlite3.connect(DB_PATH); cols={r[1] for r in c.execute("PRAGMA table_info(payment_requests)")}; assert {"expected_payment_account", "expected_payment_account_source"} <= cols; print(DB_PATH)'
~~~

Expected: build succeeds, HTTP is 200, and both columns exist.

- [ ] **Step 5: Snapshot account-related fields before the first online sync**

Using the resolved DB, save latest-draft values for `payment_account`, `expected_payment_account`, and source into `$release_backup_dir/pre-sync-accounts.json`. This snapshot must be read-only and include request id, DingTalk id, source Sheet, and values. It is used to prove account nature does not change and any pre-existing manual expected account is protected.

- [ ] **Step 6: Trigger one DingTalk synchronization and validate online behavior**

In the production UI, open the latest draft batch and run “同步钉钉流程” once. Wait for success, then compare the database with the snapshot and assert:

1. every pre-sync `payment_account` is unchanged;
2. every pre-sync source `manual` value is unchanged;
3. auto-populated rows have source only `dingtalk_explicit` or `service_subject_default`;
4. default-source values belong to the five configured defaults;
5. explicit-source values are nonblank;
6. unknown subjects remain blank rather than guessed;
7. no conflict/unmatched request received an expected-account field audit;
8. a second identical sync adds no new expected-account field audit.

Open one explicit row and one service-default row in Chinese and Spanish UI, confirm column order, editor value, mobile/card rendering, and manual editability. Export the workbench once and confirm “预计支付账户” follows “付款账户”. Confirm Daily Payables UI/export remains unchanged.

- [ ] **Step 7: Complete health checks and retain rollback assets**

~~~bash
git rev-parse --short=12 HEAD
git rev-parse --short=12 origin/main
sudo systemctl is-active cashier-payment
sudo journalctl -u cashier-payment --since "$deploy_started" -p warning --no-pager
curl -sS -o /dev/null -w '%{http_code}\n' http://8.135.70.130:8011/
~~~

Expected: deployed and remote commits match, service is active, public HTTP is 200, and there are no new application errors. Keep the production backup and previous commit file.

- [ ] **Step 8: Roll back only if acceptance fails**

Stop the service, preserve the failed-release DB separately, restore the old Git commit and verified backup from the unique release directory, remove only the restored DB’s WAL/SHM sidecars, rebuild, restart, and recheck HTTP 200. Never delete the release backup.
