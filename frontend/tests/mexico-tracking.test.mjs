import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../src/i18n.tsx", import.meta.url), "utf8");
const helperSource = readFileSync(new URL("../src/mexicoTracking.ts", import.meta.url), "utf8");
const pageSource = readFileSync(new URL("../src/MexicoTrackingPage.tsx", import.meta.url), "utf8");

test("Mexico approval tracking has a stable navigation destination", () => {
  assert.match(appSource, /type Tab =[\s\S]*"mexico-tracking"/);
  assert.match(appSource, /data-page="mexico-tracking"/);
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

test("Mexico tracking navigation and actions include Spanish translations", () => {
  assert.match(i18nSource, /"墨西哥审批跟进":\s*"Seguimiento de aprobaciones de México"/);
  assert.match(i18nSource, /"复制双语提醒":\s*"Copiar recordatorio bilingüe"/);
});
