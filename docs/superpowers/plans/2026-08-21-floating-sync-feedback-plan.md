# Floating Sync Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the application navigation stable by rendering global synchronization feedback in an auto-dismissing floating toast outside the header.

**Architecture:** `Shell` remains the owner of the existing `message` and `accountNotice` state. A small `GlobalFeedback` component renders those messages after the header in a fixed viewport, owns the dismiss timer, and reports dismissal through the existing state setters. CSS positions and constrains the viewport without changing business APIs or database behavior.

**Tech Stack:** React 19, TypeScript, Vite, CSS, Node source-level regression test.

---

### Task 1: Add a source-level regression test

**Files:**
- Create: `frontend/tests/header-feedback.test.mjs`
- Modify: `package.json`

- [ ] **Step 1: Write a failing Node test**

Create a test that reads `frontend/src/App.tsx` and asserts that the `app-userbar` block contains no `toast`, while `GlobalFeedback` is rendered after `</header>`.

- [ ] **Step 2: Add the test command**

Add `"test:frontend": "node --test frontend/tests/*.test.mjs"` to `package.json`.

- [ ] **Step 3: Run the test and verify failure**

Run: `npm run test:frontend`

Expected: FAIL because the current toast is still nested in `.app-userbar`.

### Task 2: Move feedback out of the header

**Files:**
- Modify: `frontend/src/App.tsx:390-470`
- Modify: `frontend/src/styles.css:180-250,500-515,3597-3640`

- [ ] **Step 1: Add `GlobalFeedback`**

Implement a typed component accepting `message`, `accountNotice`, `onDismissMessage`, and `onDismissAccountNotice`. Use a five-second effect timer, `aria-live="polite"`, and a close button.

- [ ] **Step 2: Remove toasts from `.app-userbar`**

Keep only language, account, and logout controls in the user bar. Render `GlobalFeedback` immediately after `</header>`.

- [ ] **Step 3: Add fixed toast styles**

Add `.global-feedback-viewport`, `.global-feedback-toast`, and close-button styles. Set the viewport below the header, cap width at `min(520px, calc(100vw - 32px))`, allow wrapping, add a shadow, and keep the element above drawers without affecting layout.

- [ ] **Step 4: Stabilize the header**

Set desktop `.app-userbar` to `flex-wrap: nowrap`. Preserve the existing mobile header wrapping, while keeping feedback outside it.

- [ ] **Step 5: Run the regression test**

Run: `npm run test:frontend`

Expected: PASS.

### Task 3: Verify and publish the local change

**Files:**
- Verify: `frontend/src/App.tsx`
- Verify: `frontend/src/styles.css`
- Verify: `frontend/tests/header-feedback.test.mjs`

- [ ] **Step 1: Run the production build**

Run: `npm run build`

Expected: TypeScript checking and Vite production build complete successfully.

- [ ] **Step 2: Verify in the local browser**

Log in with `codex-ui-test` / `local-ui-test`, trigger a long message, and verify desktop plus 390px layouts remain stable and the toast disappears.

- [ ] **Step 3: Commit only intended files**

Stage the specification, plan, App/CSS changes, test, and `package.json`. Do not stage `.superpowers/` or `output/`.

- [ ] **Step 4: Push the current branch**

Run `git push -u origin codex/account-applicant`, then verify the remote commit hash. Do not deploy to the 8011 server.
