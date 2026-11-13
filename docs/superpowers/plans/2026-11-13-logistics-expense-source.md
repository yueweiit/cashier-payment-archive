# Logistics expense source implementation plan

Goal: add the approved logistics process to the existing cashier pull and tracking pipeline.

Architecture: parameterize the existing monthly raw-instance path by a fixed process-code registry; reuse financial mapping, attachments, region guards and import transactions. No new database schema or API.

## Tasks

- [x] Query read-only source records to verify process ID and published form fields. Check the current production SHA before release.
- [x] Add failing `backend/tests/test_logistics_expenses.py` cases for mapping, money validation, preview/refetch/metadata/attachments and incremental discovery. Run `python -m pytest backend/tests/test_logistics_expenses.py -q`; observe missing logistics support.
- [x] Extend `backend/app/external_expenses.py`: registry, shared process query/mapping and attachment identity; exact current-payment amounts and mismatch rejection. Replace private monthly-only helpers and preserve the public monthly entry point. Parameterize the existing API dedupe/rollback scenario rather than copying it.
- [x] Extend `frontend/src/api.ts`, `frontend/src/App.tsx`, and `frontend/src/mexicoTracking.ts`: logistics source type, selected checkbox and bilingual label.
- [x] Run API/Mexico/logistics regressions, frontend tests, build, and diff checks. Review source filters and retained month-end behavior.
- [x] Commit and publish on top of the production ancestor; make an online SQLite backup, build assets before cutover and verify service health.
- [x] Query all 6 logistics rows for 2026-10-25 through 2026-11-13 with the server read-only pipeline. Verify the logged-in dialog default source and current-date 60 CNY record; exact-number lookup retrieves the historical 67 CNY record. Dedupe/rollback is verified in isolated API tests, with no live financial import.
- [x] Report additions/modifications/deletions, reuse, code/test counts, validation evidence and remaining debt.

## Verified release

- Code commit: `d8530f2b27c131f6a8e3e565ed41e1a43beb7a66`, based on the existing production `e4dea8b` ancestor.
- Backend: 399 passed; frontend: 41 passed; TypeScript/Vite production build passed. Existing FastAPI/Pydantic deprecation warnings remain.
- Read-only live preview/refetch/metadata checks: 6 logistics rows, 11 attachment references, exact-number search passed. No financial rows imported by the acceptance scripts.
- Deployed service active and public HTTP 200. Backup: `/data/cashier-payment/backups/20261113-145428-logistics-source`.
- New tests: 5 independent groups expanded to 11 cases, plus one logistics parameter added to the existing import/dedupe/rollback test. Business code net +13 lines; test code net +161 lines; no new duplicate pipeline or schema, replaced private monthly helpers removed.
- Browser proof: `output/logistics-source/online.jpg` in the primary local workspace.
