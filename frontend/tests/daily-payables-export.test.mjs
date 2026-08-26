import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

async function importTypeScript(sourceUrl) {
  if (!existsSync(sourceUrl)) return null;
  return importTypeScriptSource(readFileSync(sourceUrl, "utf8"));
}

async function importTypeScriptSource(source) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
}

const helperUrl = new URL("../src/dailyPayablesExport.ts", import.meta.url);
const helpers = await importTypeScript(helperUrl);
const apiUrl = new URL("../src/api.ts", import.meta.url);
const apiSource = readFileSync(apiUrl, "utf8");
const apiModule = await importTypeScriptSource(
  apiSource.replace('import { translateKnownError } from "./i18n";', "const translateKnownError = (value) => value;"),
);
const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../src/i18n.tsx", import.meta.url), "utf8");

test("daily-payables export defaults to the latest available 30 days", () => {
  assert.ok(helpers, "dailyPayablesExport.ts should exist");
  assert.deepEqual(
    helpers.defaultDailyPayablesExportRange("2026-08-25", "2026-08-21", "2026-08-26"),
    { start: "2026-08-21", end: "2026-08-25" },
  );
  assert.deepEqual(
    helpers.defaultDailyPayablesExportRange("2026-09-10", "2026-01-01", "2026-08-26"),
    { start: "2026-07-28", end: "2026-08-26" },
  );
});

test("daily-payables export validates history, ordering, future dates and six calendar months", () => {
  assert.ok(helpers, "dailyPayablesExport.ts should exist");
  assert.equal(helpers.validateDailyPayablesExportRange("2026-02-26", "2026-08-25", "2026-02-26", "2026-08-26"), null);
  assert.equal(helpers.validateDailyPayablesExportRange("2026-02-26", "2026-08-26", "2026-02-26", "2026-08-26"), "EXPORT_RANGE_TOO_LARGE");
  assert.equal(helpers.validateDailyPayablesExportRange("2026-08-26", "2026-08-27", "2026-02-26", "2026-08-26"), "FUTURE_EXPORT_DATE");
  assert.equal(helpers.validateDailyPayablesExportRange("2026-08-26", "2026-08-25", "2026-02-26", "2026-08-26"), "INVALID_EXPORT_RANGE");
  assert.equal(helpers.validateDailyPayablesExportRange("2026-02-25", "2026-02-26", "2026-02-26", "2026-08-26"), "HISTORY_NOT_AVAILABLE");
});

test("daily-payables API downloads an authenticated blob and preserves the server filename", async () => {
  assert.match(apiSource, /dailyPayablesExport\s*:\s*async/);
  assert.match(apiSource, /\/api\/daily-payables\/export\.xlsx/);
  assert.match(apiSource, /credentials:\s*["']include["']/);
  assert.match(apiSource, /response\.blob\(\)/);
  assert.match(apiSource, /content-disposition/i);
  assert.match(apiSource, /filename\\\*/);

  const originalFetch = globalThis.fetch;
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response("xlsx-content", {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": "attachment; filename*=UTF-8''%E6%AF%8F%E6%97%A5%E5%BA%94%E4%BB%98_20260801-20260826.xlsx",
      },
    });
  };
  try {
    const result = await apiModule.api.dailyPayablesExport("2026-08-01", "2026-08-26");
    assert.equal(request.url, "/api/daily-payables/export.xlsx?start=2026-08-01&end=2026-08-26");
    assert.equal(request.options.credentials, "include");
    assert.equal(result.filename, "每日应付_20260801-20260826.xlsx");
    assert.equal(await result.blob.text(), "xlsx-content");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("daily-payables API preserves structured backend export errors", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: { code: "EXPORT_RANGE_TOO_LARGE", message: "日期区间最多为六个自然月" },
  }), { status: 400, headers: { "Content-Type": "application/json" } });
  try {
    await assert.rejects(
      apiModule.api.dailyPayablesExport("2026-02-26", "2026-08-26"),
      (error) => error.code === "EXPORT_RANGE_TOO_LARGE" && error.status === 400,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("daily-payables page exposes a bilingual responsive export dialog", () => {
  assert.match(appSource, /defaultDailyPayablesExportRange/);
  assert.match(appSource, /validateDailyPayablesExportRange/);
  assert.match(appSource, /api\.dailyPayablesExport/);
  assert.match(appSource, /t\("导出数据", "Exportar datos"\)/);
  assert.match(appSource, /className="daily-export-modal"/);
  assert.match(appSource, /t\("导出 Excel", "Exportar Excel"\)/);
  assert.match(i18nSource, /"导出数据": "Exportar datos"/);
  assert.match(stylesSource, /\.daily-export-modal/);
  assert.match(stylesSource, /@media \(max-width: 560px\)[\s\S]*\.daily-export-form/);
});
