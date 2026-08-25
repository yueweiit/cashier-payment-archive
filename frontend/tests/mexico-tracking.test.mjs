import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const navigationSource = readFileSync(new URL("../src/AppNavigation.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../src/i18n.tsx", import.meta.url), "utf8");
const helperSource = readFileSync(new URL("../src/mexicoTracking.ts", import.meta.url), "utf8");
const pageSource = readFileSync(new URL("../src/MexicoTrackingPage.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("Mexico approval tracking has a stable navigation destination", () => {
  assert.match(navigationSource, /type AppTab =[\s\S]*"mexico-tracking"/);
  assert.match(navigationSource, /data-page=\{item\.tab === "mexico-tracking"/);
  assert.match(appSource, /<MexicoTrackingPage/);
  assert.match(appSource, /tab !== "mexico-tracking" && \(\s*<header className="topbar">/);
});

test("Mexico tracking API exposes list, detail, sync, settings and resolution operations", () => {
  for (const method of [
    "mexicoTrackingList",
    "mexicoTrackingDetail",
    "mexicoTrackingSummary",
    "mexicoTrackingFilterOptions",
    "startMexicoTrackingSync",
    "mexicoTrackingSyncRun",
    "mexicoTrackingSettings",
    "updateMexicoTrackingSettings",
    "resolveMexicoTrackingRegion",
  ]) {
    assert.match(apiSource, new RegExp(`${method}:`), `missing ${method}`);
  }
});

test("reminder copy supports secure clipboard and HTTP fallback", () => {
  assert.match(helperSource, /navigator\.clipboard\.writeText/);
  assert.match(helperSource, /document\.execCommand\("copy"\)/);
});

test("tracking page is lightweight, filterable and task-aware", () => {
  assert.match(pageSource, /mexicoTrackingList/);
  assert.match(pageSource, /startMexicoTrackingSync/);
  assert.match(pageSource, /syncing_attachments/);
  assert.match(pageSource, /mexico-tracking-card/);
  assert.match(pageSource, /syncPollInFlight/);
  assert.match(pageSource, /selectView\("history"\)/);
  assert.match(pageSource, /selectView\("review"\)/);
});

test("tracking refreshes once when approval state commits before attachments finish", () => {
  assert.match(apiSource, /state_committed_at\?: string \| null/);
  assert.match(pageSource, /lastRefreshedStateCommit/);
  assert.match(pageSource, /response\.run\.state_committed_at/);
  assert.match(pageSource, /await refreshAll\(\)/);
  assert.match(pageSource, /审批状态已更新/);
  assert.match(pageSource, /附件正在后台处理/);
});

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

test("Mexico approval desktop table keeps readable columns and visible overflow", () => {
  assert.match(styleSource, /\.mexico-table-wrap\s*\{[^}]*overflow-x:\s*auto/s);
  assert.match(styleSource, /\.mexico-tracking-table\s*\{[^}]*min-width:\s*1720px/s);
  assert.doesNotMatch(styleSource, /\.mexico-table-wrap\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(styleSource, /\.mexico-tracking-table th:nth-child\(5\)\s*\{[^}]*width:\s*320px/s);
  assert.match(styleSource, /\.mexico-tracking-table th:nth-child\(7\)\s*\{[^}]*width:\s*240px/s);
  assert.match(styleSource, /\.mexico-approver-list/);
});

test("Mexico tracking navigation and actions include Spanish translations", () => {
  assert.match(i18nSource, /"墨西哥审批跟进":\s*"Seguimiento de aprobaciones de México"/);
  assert.match(i18nSource, /"复制双语提醒":\s*"Copiar recordatorio bilingüe"/);
});
