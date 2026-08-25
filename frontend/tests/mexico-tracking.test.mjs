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
    "mexicoTrackingApproverStats",
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
  assert.match(styleSource, /\.mexico-tracking-table\s*\{[^}]*min-width:\s*1400px/s);
  assert.doesNotMatch(styleSource, /\.mexico-table-wrap\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(pageSource, /mexico-request-cell/);
  assert.match(pageSource, /mexico-current-task-cell/);
  assert.match(pageSource, /className="mexico-company-clamp" title=/);
  assert.match(pageSource, /className="mexico-summary-clamp" title=/);
  assert.match(styleSource, /\.mexico-company-clamp[\s\S]*-webkit-line-clamp:\s*2/);
  assert.match(styleSource, /\.mexico-summary-clamp[\s\S]*-webkit-line-clamp:\s*2/);
  assert.match(styleSource, /\.mexico-approver-list/);
  assert.doesNotMatch(styleSource, /\.mexico-tracking-table tr\.warning-red\s*\{[^}]*background:/s);
});

test("Mexico tracking switches between the approval list and approver statistics", () => {
  assert.match(pageSource, /type MexicoPageMode = "list" \| "approvers"/);
  assert.match(pageSource, /mexico-view-tabs/);
  assert.match(pageSource, /mexico-approver-stats/);
  assert.match(pageSource, /api\.mexicoTrackingApproverStats\(\)/);
  assert.match(pageSource, /function applyApproverStat/);
  assert.match(pageSource, /setMode\("list"\)/);
  assert.match(pageSource, /\.\.\.appliedFilters,\s*approver:\s*approverName/);
  assert.doesNotMatch(
    pageSource,
    /function applyApproverStat[\s\S]*?nextFilters:\s*Filters\s*=\s*\{\s*\.\.\.emptyFilters,\s*approver:/,
  );
});

test("Mexico tracking navigation and actions include Spanish translations", () => {
  assert.match(i18nSource, /"墨西哥审批跟进":\s*"Seguimiento de aprobaciones de México"/);
  assert.match(i18nSource, /"复制双语提醒":\s*"Copiar recordatorio bilingüe"/);
});

test("user administration exposes Mexico-specific scope and DingTalk identity", () => {
  assert.match(apiSource, /export type MexicoAccessScope = "all" \| "participant" \| "none"/);
  assert.match(apiSource, /mexico_access_scope:\s*MexicoAccessScope/);
  assert.match(apiSource, /mexico_identity_name:\s*string \| null/);
  assert.match(appSource, /墨西哥审批权限/);
  assert.match(appSource, /钉钉审批姓名/);
  assert.match(appSource, /mexico_access_scope:\s*draft\.mexico_access_scope/);
  assert.match(appSource, /mexico_identity_name:\s*draft\.mexico_access_scope === "none"/);
  assert.match(appSource, /userForm\.mexico_access_scope === "participant"/);
});
