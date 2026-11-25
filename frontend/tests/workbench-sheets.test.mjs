import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const helperUrl = new URL("../src/workbenchSheets.ts", import.meta.url);
const helperSource = existsSync(helperUrl) ? readFileSync(helperUrl, "utf8") : "";
const helpers = helperSource ? await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(helperSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText).toString("base64")}`) : null;

test("workbench display groups merge raw Sheets without changing permission targets", () => {
  assert.ok(helpers, "workbench sheet helpers should exist");
  const batch = { sheet_order: ["原组甲", "原组乙"], workbench_sheet_order: ["合并组"], workbench_sheet_targets: { 合并组: "原组乙" } };
  const row = { source_sheet: "原组甲", workbench_sheet: "合并组" };
  assert.equal(helpers.requestWorkbenchSheet(row), "合并组");
  assert.deepEqual(helpers.batchWorkbenchSheetOrder(batch), ["合并组"]);
  assert.equal(helpers.workbenchSheetTarget("合并组", batch, [row]), "原组乙");
  assert.deepEqual(row, { source_sheet: "原组甲", workbench_sheet: "合并组" });
});

test("old API rows and new groups use existing raw targets before display labels", () => {
  assert.ok(helpers, "workbench sheet helpers should exist");
  assert.equal(helpers.requestWorkbenchSheet({ source_sheet: "旧分组" }), "旧分组");
  assert.deepEqual(helpers.batchWorkbenchSheetOrder({ sheet_order: ["旧分组"] }), ["旧分组"]);
  assert.equal(helpers.workbenchSheetTarget("合并组", {}, [{ source_sheet: "原始授权组", workbench_sheet: "合并组" }]), "原始授权组");
  assert.equal(helpers.workbenchSheetTarget("新组", {}, []), "新组");
  assert.deepEqual(helpers.moveToWorkbenchSheet({ source_sheet: "旧组" }, "合并组", "原始授权组"), { source_sheet: "原始授权组", workbench_sheet: "合并组" });
});

test("an equal batch reload preserves unsaved Sheet deletion while a batch switch resets it", () => {
  assert.ok(helpers, "workbench sheet helpers should exist");
  assert.equal(helpers.shouldResetWorkbenchSheets(34, 34, true), false);
  assert.equal(helpers.shouldResetWorkbenchSheets(34, 35, true), true);
  assert.equal(helpers.shouldResetWorkbenchSheets(34, 34, false), true);
});

test("display-only workbench fields never enter ordinary request writes", () => {
  assert.ok(helpers, "workbench sheet helpers should exist");
  assert.deepEqual(helpers.withoutWorkbenchSheet({ source_sheet: "原始组", workbench_sheet: "显示组", summary: "保留" }), { source_sheet: "原始组", summary: "保留" });
});

test("managed merged groups cannot rename their permission departments but custom groups can", () => {
  assert.ok(helpers, "workbench sheet helpers should exist");
  assert.equal(typeof helpers.canRenameWorkbenchSheet, "function");
  assert.equal(helpers.canRenameWorkbenchSheet("合并组", { workbench_sheet_targets: { 合并组: "原部门" } }, []), false);
  assert.equal(helpers.canRenameWorkbenchSheet("合并组", {}, [{ source_sheet: "部门甲", workbench_sheet: "合并组" }, { source_sheet: "部门乙", workbench_sheet: "合并组" }]), false);
  assert.equal(helpers.canRenameWorkbenchSheet("自定义组", {}, [{ source_sheet: "自定义组" }]), true);
});

test("historical preferences cannot hide or push the expected payment account to the far right", () => {
  assert.equal(typeof helpers.workbenchGridColumnOrder, "function");
  assert.deepEqual(helpers.workbenchGridColumnOrder(["dingding_id", "source_sheet", "summary", "amount", "expected_payment_account"]), ["dingding_id", "expected_payment_account", "summary", "amount"]);
  assert.equal(helpers.workbenchGridColumnOrder(["summary", "amount", "currency", "applicant", "payment_account", "expected_payment_account"]).indexOf("expected_payment_account"), 0);
  assert.equal(helpers.workbenchGridColumnVisible("expected_payment_account", new Set(["expected_payment_account"])), true);
  assert.equal(helpers.workbenchGridColumnVisible("source_sheet", new Set()), false);
});

test("moving to another display group sharing a raw department persists the canonical destination", () => {
  assert.deepEqual(helpers.moveToWorkbenchSheet({ source_sheet: "混写原部门", workbench_sheet: "原显示组" }, "目标显示组", "混写原部门"), { source_sheet: "目标显示组", workbench_sheet: "目标显示组" });
});

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("App.tsx", appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const functions = ["getSheetTabs", "normalizeSheetName", "rowMatchesFilters", "requestAmountCny", "requestFxRate", "currencyCode", "roundMoney", "optionalNumber", "withoutDerivedPaymentFields", "stripGridRow", "defaultGridPreference"];
const variables = ["ALL_SHEET", "GENERAL_MANAGER_EMPTY_FILTER", "gridColumns", "defaultVisibleGridColumnKeys", "calculatedRequestFields"];
const extracted = ast.statements.filter((node) => (ts.isFunctionDeclaration(node) && functions.includes(node.name?.text))
  || (ts.isVariableStatement(node) && node.declarationList.declarations.some((entry) => variables.includes(entry.name.getText(ast)))))
  .map((node) => node.getText(ast)).join("\n");
const paymentSource = readFileSync(new URL("../src/paymentStatus.ts", import.meta.url), "utf8");
const fixture = `${helperSource}\n${paymentSource}\n${extracted}\nexport { getSheetTabs, rowMatchesFilters, withoutDerivedPaymentFields, stripGridRow, defaultGridPreference };`;
const workbench = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(fixture, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText).toString("base64")}`);

test("actual workbench tabs and filters group rows by the projected Sheet", () => {
  const rows = [{ source_sheet: "原组甲", workbench_sheet: "合并组" }, { source_sheet: "原组乙", workbench_sheet: "合并组" }];
  assert.deepEqual(workbench.getSheetTabs(rows, ["合并组"]).map((tab) => [tab.key, tab.count]), [["__all__", 2], ["合并组", 2]]);
  const filters = { q: "", payment_account: "", invoice_status: "", pending_amount_min: "", pending_amount_max: "", finance_review: "", general_manager_approval: "", execution_region: "" };
  assert.equal(workbench.rowMatchesFilters(rows[1], filters, "合并组"), true);
});

test("actual editor and new-row writes exclude projected fields and retain raw Sheet", () => {
  const row = { source_sheet: "原始授权组", workbench_sheet: "显示组", summary: "保留" };
  assert.deepEqual(workbench.withoutDerivedPaymentFields(row), { source_sheet: "原始授权组", summary: "保留" });
  assert.deepEqual(workbench.stripGridRow(row), { source_sheet: "原始授权组", summary: "保留" });
});

test("the default grid hides company Sheet and retains expected payment account", () => {
  const preference = workbench.defaultGridPreference();
  assert.equal(preference.hidden.includes("source_sheet"), true);
  assert.equal(preference.hidden.includes("expected_payment_account"), false);
});

const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const apiModule = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(
  apiSource.replace('import { translateKnownError } from "./i18n";', "const translateKnownError = (value) => value;"),
  { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } },
).outputText).toString("base64")}`);

test("workbench sorting marks projected groups while the old sorting API stays unchanged", async () => {
  const previousFetch = globalThis.fetch;
  const bodies = [];
  globalThis.fetch = async (url, options) => {
    bodies.push(JSON.parse(options.body));
    return new Response(JSON.stringify({ batch: { id: 34 } }), { status: 200 });
  };
  try {
    await apiModule.api.updateSheetOrder(34, ["合并组"], 8, true);
    await apiModule.api.updateSheetOrder(34, ["原始组"], 8);
    assert.deepEqual(bodies, [
      { sheet_order: ["合并组"], expected_batch_version: 8, workbench_groups: true },
      { sheet_order: ["原始组"], expected_batch_version: 8 },
    ]);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("all workspace sorting calls mark projected groups and export uses the display group", () => {
  const calls = [];
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(ast) === "api.updateSheetOrder") calls.push(node);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.equal(calls.length, 4);
  assert.ok(calls.every((call) => call.arguments[3]?.kind === ts.SyntaxKind.TrueKeyword));
  assert.match(appSource, /params\.set\("workbench_sheet", activeSheet\)/);
  assert.doesNotMatch(appSource, /params\.set\("source_sheet", activeSheet\)/);
});
