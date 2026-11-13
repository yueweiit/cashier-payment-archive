# Logistics expense source implementation plan

Goal: add the approved logistics process to the existing cashier pull and tracking pipeline.

Architecture: parameterize the existing monthly raw-instance path by a fixed process-code registry; reuse financial mapping, attachments, region guards and import transactions. No new database schema or API.

## Tasks

- [x] Query read-only source records to verify process ID and published form fields. Check the current production SHA before release.
- [x] Add failing `backend/tests/test_logistics_expenses.py` cases for mapping, money validation, preview/refetch/metadata/attachments and incremental discovery. Run `python -m pytest backend/tests/test_logistics_expenses.py -q`; observe missing logistics support.
- [x] Extend `backend/app/external_expenses.py`: registry, shared process query/mapping and attachment identity; exact current-payment amounts and mismatch rejection. Replace private monthly-only helpers and preserve the public monthly entry point. Parameterize the existing API dedupe/rollback scenario rather than copying it.
- [x] Extend `frontend/src/api.ts`, `frontend/src/App.tsx`, and `frontend/src/mexicoTracking.ts`: logistics source type, selected checkbox and bilingual label.
- [ ] Run API/Mexico/logistics regressions, frontend tests, build, and diff checks. Review source filters and retained month-end behavior.
- [ ] Commit and publish on top of the production ancestor; make an online SQLite backup, build assets before cutover and verify service health.
- [ ] In the logged-in pull dialog query only logistics over 2026-10-25 through 2026-11-13; compare 6 source rows and sample amounts/dates. Check exact-number search and duplicate detection without importing live financial rows.
- [ ] Report additions/modifications/deletions, reuse, code/test counts, validation evidence and remaining debt.
