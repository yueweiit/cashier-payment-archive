# Daily Payables Export Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing daily-payables Excel export open as a compact, finance-friendly workbook without changing any exported values, columns, permissions, or API behavior.

**Architecture:** Keep the current Python/openpyxl write-only export pipeline and change only worksheet presentation metadata. Add focused workbook-format assertions first, then introduce shared style constants and helpers for header colors, row heights, column grouping, widths, alignment, and freeze panes; all history replay and export response code remains untouched.

**Tech Stack:** Python 3, FastAPI, openpyxl write-only workbooks, pytest, LibreOffice headless rendering, existing systemd/GitHub pull deployment.

---

## File Map

- Modify: `backend/app/daily_payables_export.py`
  - Owns the two exported worksheet layouts and cell styles.
- Modify: `backend/tests/test_payable_history.py`
  - Verifies workbook content, formatting, grouping, and regression boundaries through the public export endpoint.
- No frontend changes.
- No API signature changes.
- No database fields or migrations.

### Task 1: Add a failing workbook-readability regression test

**Files:**
- Modify: `backend/tests/test_payable_history.py:505-590`

- [ ] **Step 1: Extend the existing replay-and-format test with the approved layout contract**

In `test_daily_payables_export_replays_deduplicated_payment_history_and_formats_excel`, retain all existing value and deduplication assertions, then replace the old freeze-pane assertions and add the following formatting checks after loading the workbook:

```python
        assert summary.freeze_panes == "E2"
        assert summary.row_dimensions[1].height == 36
        assert summary.auto_filter.ref == f"A1:T{summary.max_row}"
        assert summary.sheet_properties.outlinePr.summaryRight is True

        for column in range(9, 21):
            dimension = summary.column_dimensions[get_column_letter(column)]
            assert dimension.hidden is True
            assert dimension.outline_level == 1

        assert summary.cell(1, 9).fill.fgColor.rgb == "002E7D32"
        assert summary.cell(1, 13).fill.fgColor.rgb == "002563EB"
        assert summary.cell(1, 17).fill.fgColor.rgb == "00D97706"
        assert summary.column_dimensions["A"].width == 13
        assert summary.column_dimensions["E"].width == 20

        assert detail.freeze_panes == "G2"
        assert detail.row_dimensions[1].height == 36
        assert detail.row_dimensions[2].height == 28
        assert detail.row_dimensions[3].height == 28
        assert detail.auto_filter.ref == f"A1:S{detail.max_row}"
        assert detail.column_dimensions["C"].hidden is True
        assert detail.column_dimensions["G"].width == 48
        assert detail.cell(2, 7).alignment.wrap_text is False
        assert detail.cell(2, 7).alignment.vertical == "center"
        assert detail.cell(2, 7).value == "区间去重测试"
```

Add the import used by the column assertions if it is not already present:

```python
from openpyxl.utils import get_column_letter
```

- [ ] **Step 2: Run the focused test and verify it fails for presentation reasons**

Run:

```bash
python3 -m pytest -q backend/tests/test_payable_history.py::test_daily_payables_export_replays_deduplicated_payment_history_and_formats_excel
```

Expected: FAIL because the current workbook uses `A2` freeze panes, has no grouped currency columns, has no fixed row heights, and detail cells use `wrap_text=True`.

- [ ] **Step 3: Commit the red test**

```bash
git add backend/tests/test_payable_history.py
git commit -m "test: define compact daily payable workbook layout"
```

### Task 2: Implement compact worksheet styles and currency grouping

**Files:**
- Modify: `backend/app/daily_payables_export.py:20-118`
- Modify: `backend/app/daily_payables_export.py:166-257`
- Test: `backend/tests/test_payable_history.py`

- [ ] **Step 1: Add explicit layout constants beside the existing workbook constants**

Add these constants after `EXCEL_MAX_ROWS`:

```python
DEFAULT_HEADER_FILL = "1F6F6D"
CURRENCY_HEADER_FILLS = {
    "CNY": "2E7D32",
    "USD": "2563EB",
    "MXN": "D97706",
}
HEADER_ROW_HEIGHT = 36
SUMMARY_DATA_ROW_HEIGHT = 22
DETAIL_DATA_ROW_HEIGHT = 28
SUMMARY_WIDTHS = [13, 13, 13, 13, 20, 20, 20, 20] + [18] * 12
DETAIL_WIDTHS = [13, 12, 14, 24, 24, 18, 48, 15] + [15] * 9 + [18, 18]
```

- [ ] **Step 2: Make header colors column-aware and ordinary data non-wrapping**

Replace `_header_cells` and adjust `_data_cells` to use the following implementations. Preserve the existing illegal-character and formula-injection handling exactly as shown:

```python
def _header_cells(
    worksheet,
    headers: list[str],
    *,
    column_fills: dict[int, str] | None = None,
) -> list[WriteOnlyCell]:
    fills = column_fills or {}
    cells: list[WriteOnlyCell] = []
    for column, value in enumerate(headers, start=1):
        cell = WriteOnlyCell(worksheet, value=value)
        cell.fill = PatternFill("solid", fgColor=fills.get(column, DEFAULT_HEADER_FILL))
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cells.append(cell)
    return cells


def _data_cells(
    worksheet,
    values: list[Any],
    *,
    date_columns: set[int],
    money_columns: set[int],
) -> list[WriteOnlyCell]:
    cells: list[WriteOnlyCell] = []
    for column, value in enumerate(values, start=1):
        if isinstance(value, str):
            value = ILLEGAL_CHARACTERS_RE.sub("", value)
            if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
                value = f"'{value}"
        cell = WriteOnlyCell(worksheet, value=value)
        cell.alignment = Alignment(vertical="center", wrap_text=False)
        if column in date_columns and value is not None:
            cell.number_format = "yyyy-mm-dd"
        elif column in money_columns:
            cell.number_format = "#,##0.00"
        cells.append(cell)
    return cells
```

- [ ] **Step 3: Add small helpers for summary header fills and the native Excel column outline**

Add these functions after `_set_column_widths`:

```python
def _summary_header_fills() -> dict[int, str]:
    fills: dict[int, str] = {}
    start_column = 9
    for currency in SUPPORTED_CURRENCIES:
        color = CURRENCY_HEADER_FILLS[currency]
        for column in range(start_column, start_column + 4):
            fills[column] = color
        start_column += 4
    return fills


def _collapse_summary_currency_columns(worksheet) -> None:
    worksheet.sheet_properties.outlinePr.summaryRight = True
    for column in range(9, len(SUMMARY_HEADERS) + 1):
        dimension = worksheet.column_dimensions[get_column_letter(column)]
        dimension.hidden = True
        dimension.outlineLevel = 1
```

- [ ] **Step 4: Apply the approved freeze panes, widths, heights, colors, and grouping**

In `export_daily_payables_workbook`, replace the current worksheet setup with:

```python
        summary.freeze_panes = "E2"
        detail.freeze_panes = "G2"
        summary.sheet_view.showGridLines = False
        detail.sheet_view.showGridLines = False
        summary.row_dimensions[1].height = HEADER_ROW_HEIGHT
        detail.row_dimensions[1].height = HEADER_ROW_HEIGHT
        _set_column_widths(summary, SUMMARY_WIDTHS)
        _set_column_widths(detail, DETAIL_WIDTHS)
        _collapse_summary_currency_columns(summary)
        detail.column_dimensions["C"].hidden = True
        summary.append(
            _header_cells(
                summary,
                SUMMARY_HEADERS,
                column_fills=_summary_header_fills(),
            )
        )
        detail.append(_header_cells(detail, DETAIL_HEADERS))
```

Immediately before appending each summary row, set its fixed height:

```python
            summary.row_dimensions[summary_rows + 1].height = SUMMARY_DATA_ROW_HEIGHT
```

Immediately before appending each detail row, set its fixed height:

```python
                detail.row_dimensions[detail_rows + 1].height = DETAIL_DATA_ROW_HEIGHT
```

Do not change `summary_values`, `detail_values`, number formats, row-limit checks, auto-filter ranges, workbook saving, file-size limits, or cleanup behavior.

- [ ] **Step 5: Run the focused test and verify the green result**

Run:

```bash
python3 -m pytest -q backend/tests/test_payable_history.py::test_daily_payables_export_replays_deduplicated_payment_history_and_formats_excel
```

Expected: `1 passed`.

- [ ] **Step 6: Run all daily-payables export tests**

Run:

```bash
python3 -m pytest -q backend/tests/test_payable_history.py -k daily_payables_export
```

Expected: all selected tests pass, including range validation, duplicate DingTalk history, payment reconciliation, Sheet permissions, China-region isolation, row limits, concurrency, and response cleanup.

- [ ] **Step 7: Commit the implementation**

```bash
git add backend/app/daily_payables_export.py backend/tests/test_payable_history.py
git commit -m "fix: make daily payable exports readable"
```

### Task 3: Generate and visually inspect a real workbook sample

**Files:**
- Read: `data/app.db`
- Create temporarily: `output/daily-payables-readability-qa/`
- Do not commit generated `.xlsx`, `.pdf`, or `.png` files.

- [ ] **Step 1: Generate a short-range workbook with the application export code**

Use the local production-shaped database without writing to it. The command creates one QA workbook and prints its path:

```bash
mkdir -p output/daily-payables-readability-qa
PAYMENT_DB_PATH="$PWD/data/app.db" python3 -c "from datetime import date, timedelta; from pathlib import Path; from shutil import copy2; from backend.app.db import connect; from backend.app.daily_payables import iter_daily_snapshots; from backend.app.daily_payables_export import export_daily_payables_workbook; destination=Path('output/daily-payables-readability-qa/daily-payables-readability.xlsx'); connection=connect(); history_start=date.fromisoformat(connection.execute(\"SELECT value FROM app_settings WHERE key='daily_payables_history_start_date'\").fetchone()[0]); end=date.today(); start=max(history_start, end-timedelta(days=6)); source=export_daily_payables_workbook(iter_daily_snapshots(connection, start, end, allowed_sheets=None, include_details=True, china_only=True)); connection.close(); copy2(source, destination); source.unlink(); print(destination.resolve())"
```

Expected: `output/daily-payables-readability-qa/daily-payables-readability.xlsx` exists and the temporary `daily-payables-*.xlsx` source file is removed.

- [ ] **Step 2: Parse the QA workbook and reconcile structure and totals**

Run:

```bash
python3 -c "from openpyxl import load_workbook; path='output/daily-payables-readability-qa/daily-payables-readability.xlsx'; workbook=load_workbook(path, read_only=False, data_only=True); summary=workbook['每日汇总']; detail=workbook['逐日明细']; keys=[(row[0].value,row[2].value) for row in detail.iter_rows(min_row=2)]; print('sheets', workbook.sheetnames); print('summary_rows', summary.max_row); print('detail_rows', detail.max_row-1); print('detail_unique', len(set(keys))); print('last_pending_cny', summary.cell(summary.max_row,7).value); print('freeze', summary.freeze_panes, detail.freeze_panes); print('hidden_currency_columns', all(summary.column_dimensions[column].hidden for column in 'IJKLMNOPQRST')); print('detail_row_height', detail.row_dimensions[2].height); print('detail_wrap', detail.cell(2,7).alignment.wrap_text); workbook.close()"
```

Expected:

- Sheets are exactly `['每日汇总', '逐日明细']`.
- `detail_rows == detail_unique`.
- Freeze panes are `E2` and `G2`.
- All I–T columns are hidden by default.
- Detail row height is `28.0` and detail summary wrapping is false.
- Last-day pending total matches the same date in the application UI.

- [ ] **Step 3: Render both sheets through LibreOffice**

Run:

```bash
mkdir -p output/daily-payables-readability-qa/rendered
soffice --headless --convert-to pdf --outdir output/daily-payables-readability-qa/rendered output/daily-payables-readability-qa/daily-payables-readability.xlsx
pdftoppm -png -r 130 output/daily-payables-readability-qa/rendered/daily-payables-readability.pdf output/daily-payables-readability-qa/rendered/page
```

Expected: PDF conversion succeeds and PNG pages are created.

- [ ] **Step 4: Inspect every rendered page**

Open the PNG files and verify:

- No detail row expands vertically because of a long summary.
- Default summary view shows the eight core columns without the twelve currency columns.
- Header text is readable at 100% scale.
- Dates and amounts are not clipped.
- Long summaries remain present in the workbook when inspecting the underlying cell value.

If rendering reveals a width or height defect, adjust only the relevant constant, add or update the exact format assertion, rerun Task 2 tests, and repeat the render once.

- [ ] **Step 5: Verify no generated QA artifacts are staged**

Run:

```bash
git status --short
```

Expected: `output/` may remain untracked as an existing user directory, but no QA workbook, PDF, or PNG is staged or committed.

### Task 4: Full regression, review, merge, and production deployment

**Files:**
- Verify: all changed files
- Production application: `/www/wwwroot/cashier-payment-archive`
- Production database: `/www/wwwroot/cashier-payment-archive/data/app.db`
- Production backup directory: `/data/cashier-payment/backups`

- [ ] **Step 1: Run the full backend suite with the documented date-sensitive legacy exclusion**

Run:

```bash
python3 -m pytest -q backend/tests -k 'not test_mexico_legacy_approver_and_node_fallbacks_match_stat_filters'
```

Expected: all selected tests pass. Record the single deselected legacy test explicitly; do not change unrelated Mexico timeout behavior in this work.

- [ ] **Step 2: Run frontend regression and production build**

Run:

```bash
npm run test:frontend
npm run build
git diff --check
```

Expected: all frontend tests pass, Vite production build succeeds, and `git diff --check` reports no errors.

- [ ] **Step 3: Request a focused code review**

Ask the reviewer to verify:

- Workbook values and column order are unchanged.
- Formula-injection and illegal-character sanitization remain intact.
- Write-only row-height and column-outline metadata survive save/reload.
- Column grouping is compatible with the two-sheet contract.
- Tests would fail if wrapping, freeze panes, grouping, or fixed row heights regress.

Resolve every Critical or Important finding, rerun affected tests, and commit fixes separately.

- [ ] **Step 4: Merge locally into `main` and push**

After final verification, fast-forward the confirmed implementation branch into `main`, preserving user-owned untracked `.superpowers/` and `output/` content:

```bash
git switch main
git fetch origin main
git merge --ff-only codex/daily-payables-export-readability
python3 -m pytest -q backend/tests/test_payable_history.py -k daily_payables_export
npm run test:frontend
npm run build
git push origin main
```

Expected: local and remote `main` resolve to the same verified commit.

- [ ] **Step 5: Back up the real production database before deployment**

On the ECS server, confirm the tracked production tree is clean, capture the current commit, stop `cashier-payment`, and create a new non-overwriting SQLite backup with the SQLite Backup API. Verify the backup with `PRAGMA integrity_check` before changing code.

Use an explicit path of this form:

```text
/data/cashier-payment/backups/app-before-daily-export-readability-20260827.db
```

Expected: integrity check returns `ok`, backup size is non-zero, and the service remains stopped until the build succeeds.

- [ ] **Step 6: Deploy the verified commit and restart**

In `/www/wwwroot/cashier-payment-archive`, preserve all historical untracked backup files and run:

```bash
git fetch origin main
git merge --ff-only origin/main
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
systemctl start cashier-payment
```

Wait up to 60 seconds for `http://127.0.0.1:8011/` to return HTTP 200. If code update, dependency installation, build, or health check fails, return to the captured old commit, rebuild, start the service, and restore the database backup only if the database was modified.

- [ ] **Step 7: Perform online export acceptance**

Using the logged-in administrator UI:

1. Open “每日应付” and export a short range that includes a long-summary record.
2. Confirm the request returns HTTP 200 and the UTF-8 filename remains `每日应付_YYYYMMDD-YYYYMMDD.xlsx`.
3. Open both sheets and confirm the approved compact layout.
4. Expand I–T and verify CNY/USD/MXN values and header colors.
5. Compare last-day pending CNY total with the page.
6. Confirm `(statistics date, logical request id)` remains unique in details.
7. Confirm `/tmp` contains no `daily-payables-*.xlsx` after the response completes.
8. Confirm `systemctl is-active cashier-payment`, public HTTP 200, and no new `ERROR`, `Traceback`, or `Exception` entries in the service log.

- [ ] **Step 8: Clean up the merged implementation worktree and branch**

After successful production acceptance, stop local test servers, remove only temporary files created for this implementation, remove the clean merged worktree, and delete the merged implementation branch. Do not delete or modify user-owned `.superpowers/` or `output/` content.
