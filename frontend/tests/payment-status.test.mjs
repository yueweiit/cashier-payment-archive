import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const helperUrl = new URL("../src/paymentStatus.ts", import.meta.url);
const helpers = existsSync(helperUrl)
  ? await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(helperUrl, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  }).outputText).toString("base64")}`)
  : null;

const refused = { payment_status: "拒绝付款", finance_review: "未付款", amount: 100, paid_amount: 0, pending_amount: 100 };

test("a refused request is complete while its financial facts remain unpaid", () => {
  assert.ok(helpers, "payment status helpers should exist");
  assert.equal(helpers.requestPaymentStatus(refused), "拒绝付款");
  assert.equal(helpers.requestIsCompleted(refused), true);
  assert.equal(helpers.requestOutstandingAmount(refused), 0);
  assert.equal(refused.paid_amount, 0);
  assert.equal(refused.pending_amount, 100);
});

test("refused requests have their own filter and do not enter payable or pending-approval filters", () => {
  assert.ok(helpers, "payment status helpers should exist");
  for (const filter of ["未付款", "部分付款", "已付款"]) {
    assert.equal(helpers.matchesPaymentStatusFilter(refused, filter), false);
  }
  assert.equal(helpers.matchesPaymentStatusFilter(refused, "拒绝付款"), true);
  assert.equal(helpers.matchesPaymentStatusFilter(refused, "已完成"), true);
  assert.equal(helpers.matchesPaymentStatusFilter(refused, ""), true);
  assert.equal(helpers.requestCanAddPayment(refused), false);
  assert.equal(helpers.requestIsCompleted({ ...refused, finance_review: "部分付款", paid_amount: 50 }), true);
});

test("completion counts include paid and refused requests without counting refusal as paid", () => {
  assert.ok(helpers, "payment status helpers should exist");
  const rows = [refused,
    { finance_review: "已付款", amount: 200, paid_amount: 200, pending_amount: 0 },
    { finance_review: "部分付款", amount: 100, paid_amount: 20, pending_amount: 80 },
    { finance_review: "未付款", amount: 100, pending_amount: 100 },
  ];
  assert.deepEqual(helpers.paymentStatusCounts(rows), { paid: 1, partial: 1, unpaid: 1, refused: 1, completed: 2, total: 4 });
  assert.equal(helpers.requestOutstandingAmount(rows[2]), 80);
  assert.equal(helpers.requestCanAddPayment(rows[2]), true);
  assert.equal(helpers.matchesPaymentStatusFilter(rows[2], "已完成"), false);
});

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const appAst = ts.createSourceFile("App.tsx", appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ["rowMatchesFilters", "requestAmountCny", "requestFxRate", "currencyCode", "roundMoney", "optionalNumber", "currencySubtotals"];
const extracted = appAst.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
  .map((node) => node.getText(appAst)).join("\n");
const fixtureCode = `${readFileSync(helperUrl, "utf8")}\nconst ALL_SHEET = "__all__";\nconst GENERAL_MANAGER_EMPTY_FILTER = "__empty_general_manager_approval__";\n${extracted}\nexport { rowMatchesFilters, currencySubtotals, requestAmountCny };`;
const workbench = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(fixtureCode, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText).toString("base64")}`);

test("workbench filters show refusal as completed and exclude it from unpaid and pending approval", () => {
  const filters = { q: "", payment_account: "", invoice_status: "", pending_amount_min: "", pending_amount_max: "", finance_review: "", general_manager_approval: "", execution_region: "" };
  assert.equal(workbench.rowMatchesFilters(refused, { ...filters, finance_review: "未付款" }, "__all__"), false);
  assert.equal(workbench.rowMatchesFilters(refused, { ...filters, finance_review: "已完成" }, "__all__"), true);
  assert.equal(workbench.rowMatchesFilters(refused, { ...filters, finance_review: "拒绝付款" }, "__all__"), true);
  assert.equal(workbench.rowMatchesFilters(refused, { ...filters, general_manager_approval: "__empty_general_manager_approval__" }, "__all__"), false);
  assert.equal(workbench.rowMatchesFilters(refused, filters, "__all__"), true);
});

test("workbench currency subtotals exclude refused outstanding amounts but retain financial facts", () => {
  const row = { ...refused, currency: "USD", fx_rate_cny_per_unit: 7, base_amount_cny: 700, paid_amount: 20 };
  assert.equal(workbench.requestAmountCny(row, "pending_amount"), 0);
  assert.equal(workbench.requestAmountCny(row, "paid_amount"), 140);
  assert.deepEqual(workbench.currencySubtotals([row]), [{ currency: "USD", amount: 100, paid_amount: 20, pending_amount: 0 }]);
});
