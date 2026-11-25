import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const helperUrl = new URL("../src/externalExpenseGroups.ts", import.meta.url);
const helpers = existsSync(helperUrl) ? await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(helperUrl, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText).toString("base64")}`) : null;
const options = ["悦为智能", "广州凌翔", "星铭 HR"];
const automatic = { source_type: "operation", source_id: "1", importable: true, workbench_sheet: "悦为智能" };
const uncertain = { source_type: "purchase", source_id: "1", importable: true, needs_workbench_sheet: true, workbench_sheet: null };

test("only uncertain source records send a user-selected existing group", () => {
  assert.ok(helpers, "external expense group helpers should exist");
  const plan = helpers.externalExpenseImportPlan([automatic, uncertain], new Set(["operation:1", "purchase:1"]), {
    "operation:1": "星铭 HR", "purchase:1": "广州凌翔",
  }, options);
  assert.deepEqual(plan, { items: [
    { source_type: "operation", source_id: "1" },
    { source_type: "purchase", source_id: "1", workbench_sheet: "广州凌翔" },
  ], missingKeys: [], unavailableKeys: [] });
});

test("uncertain selected rows block submission until assigned an offered group", () => {
  assert.ok(helpers, "external expense group helpers should exist");
  for (const choice of [undefined, "", "新部门", "待归组"]) {
    const plan = helpers.externalExpenseImportPlan([uncertain], new Set(["purchase:1"]), { "purchase:1": choice }, options);
    assert.deepEqual(plan.items, []);
    assert.deepEqual(plan.missingKeys, ["purchase:1"]);
  }
});

test("selection and group choices survive page changes and source ids do not collide", () => {
  assert.ok(helpers, "external expense group helpers should exist");
  const pageTwo = { ...uncertain, source_type: "monthly" };
  let selected = helpers.toggleExternalExpenseKeys(new Set(), ["purchase:1"]);
  selected = helpers.toggleExternalExpenseKeys(selected, ["monthly:1"]);
  const choices = { "purchase:1": "广州凌翔", "monthly:1": "星铭 HR" };
  const plan = helpers.externalExpenseImportPlan([uncertain, pageTwo], selected, choices, options);
  assert.equal(plan.items.length, 2);
  assert.deepEqual(plan.items.map((item) => item.workbench_sheet), ["广州凌翔", "星铭 HR"]);
  assert.deepEqual([...helpers.toggleExternalExpenseKeys(selected, ["monthly:1"])], ["purchase:1"]);
  assert.deepEqual(choices, { "purchase:1": "广州凌翔", "monthly:1": "星铭 HR" });
});

test("bulk group assignment affects only selected uncertain records", () => {
  assert.ok(helpers, "external expense group helpers should exist");
  const unselected = { ...uncertain, source_id: "2" };
  const original = { "purchase:2": "星铭 HR" };
  const assigned = helpers.assignExternalExpenseGroup([automatic, uncertain, unselected], new Set(["operation:1", "purchase:1"]), original, "广州凌翔", options);
  assert.deepEqual(assigned, { "purchase:1": "广州凌翔", "purchase:2": "星铭 HR" });
  assert.deepEqual(original, { "purchase:2": "星铭 HR" });
  assert.throws(() => helpers.assignExternalExpenseGroup([uncertain], new Set(["purchase:1"]), {}, "新部门", options));
});

test("stale selections are reported and old rows retain their original import payload", () => {
  assert.ok(helpers, "external expense group helpers should exist");
  const legacy = { source_type: "operation", source_id: "9", importable: true };
  assert.deepEqual(helpers.externalExpenseImportPlan([legacy], new Set(["operation:9"]), {}, []).items, [{ source_type: "operation", source_id: "9" }]);
  const plan = helpers.externalExpenseImportPlan([{ ...uncertain, importable: false }], new Set(["purchase:1", "purchase:2"]), {}, options);
  assert.deepEqual(plan.unavailableKeys, ["purchase:1", "purchase:2"]);
});

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("App.tsx", appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function extractFunction(name) {
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(found, `${name} should exist`);
  return found;
}
const uiCode = `
export async function submit(context) {
  const { busy, selectedKeys, importPlan, setError, setImporting, setResult, api, batch, setSelectedKeys, setGroupAssignments, setBulkGroup, onImported, queryPreview, setMessage } = context;
  ${extractFunction("importSelected")}
  await importSelected();
}
export async function query(context) {
  const { importing, validateFilters, previewQueryRef, setLoading, setError, setResult, setSelectedKeys, setGroupAssignments, setBulkGroup, batch, dateFrom, dateTo, sourceTypes, approvalNo, applicantIds, resultFilter, api, setPreview, setPreviewPage, setResultFilter } = context;
  ${extractFunction("queryPreview")}
  await queryPreview(true);
}`;
const ui = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(uiCode, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText).toString("base64")}`);

test("the actual submit handler prevents busy or unassigned records from reaching import", async () => {
  const sent = [];
  const errors = [];
  const choices = { "purchase:1": "广州凌翔" };
  const selectedKeys = new Set(["operation:1", "purchase:1"]);
  const context = {
    busy: false, selectedKeys,
    importPlan: helpers.externalExpenseImportPlan([automatic, uncertain], selectedKeys, {}, options),
    setError: (value) => errors.push(value), setImporting: () => {}, setResult: () => {}, batch: { id: 34 },
    api: { importExternalExpenses: async (id, items) => { sent.push({ id, items }); return { imported_rows: 2, duplicate_rows: 0, invalid_rows: 0 }; } },
    setSelectedKeys: () => {}, setGroupAssignments: () => {}, setBulkGroup: () => {}, onImported: async () => {}, queryPreview: async () => {}, setMessage: () => {},
  };
  await ui.submit(context);
  assert.equal(sent.length, 0);
  assert.match(errors.at(-1), /选择导入归组/);
  context.importPlan = helpers.externalExpenseImportPlan([automatic, uncertain], selectedKeys, choices, options);
  await ui.submit({ ...context, busy: true });
  assert.equal(sent.length, 0);
  await ui.submit(context);
  assert.deepEqual(sent, [{ id: 34, items: [
    { source_type: "operation", source_id: "1" },
    { source_type: "purchase", source_id: "1", workbench_sheet: "广州凌翔" },
  ] }]);
});

test("a new query clears stale choices immediately and late old responses cannot replace the preview", async () => {
  const resolvers = [];
  const previews = [];
  const selections = [];
  const assignments = [];
  const context = {
    importing: false, validateFilters: () => "", previewQueryRef: { current: 0 }, setLoading: () => {}, setError: () => {}, setResult: () => {},
    setSelectedKeys: (value) => selections.push(value), setGroupAssignments: (value) => assignments.push(value), setBulkGroup: () => {},
    batch: { id: 34 }, dateFrom: "2026-11-01", dateTo: "2026-11-25", sourceTypes: ["operation"], approvalNo: "", applicantIds: [], resultFilter: "importable",
    api: { previewExternalExpenses: () => new Promise((resolve) => resolvers.push(resolve)) },
    setPreview: (value) => previews.push(value), setPreviewPage: () => {}, setResultFilter: () => {},
  };
  const first = ui.query(context);
  assert.equal(selections[0].size, 0);
  assert.deepEqual(assignments[0], {});
  const second = ui.query(context);
  resolvers[1]({ marker: "new" });
  await second;
  resolvers[0]({ marker: "old" });
  await first;
  assert.deepEqual(previews, [{ marker: "new" }]);
});

test("the import API preserves optional group assignments and legacy two-field items", async () => {
  const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
  const module = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(
    apiSource.replace('import { translateKnownError } from "./i18n";', "const translateKnownError = (value) => value;"),
    { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } },
  ).outputText).toString("base64")}`);
  const previous = globalThis.fetch;
  let body;
  globalThis.fetch = async (url, request) => {
    body = JSON.parse(request.body);
    return new Response(JSON.stringify({ imported_rows: 2 }), { status: 200 });
  };
  try {
    const items = [{ source_type: "operation", source_id: "1" }, { source_type: "purchase", source_id: "1", workbench_sheet: "广州凌翔" }];
    await module.api.importExternalExpenses(34, items);
    assert.deepEqual(body, { items });
  } finally {
    globalThis.fetch = previous;
  }
});
