import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("App.tsx", appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const editor = ast.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "RequestEditor");
let renderField;
let fields;
function visit(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "renderField") renderField = node;
  if (ts.isVariableStatement(node) && node.declarationList.declarations.some((item) => item.name.getText(ast) === "fields")) fields = node;
  ts.forEachChild(node, visit);
}
visit(editor);
const workbenchSource = readFileSync(new URL("../src/workbenchSheets.ts", import.meta.url), "utf8");
const helperUrl = new URL("../src/requestEditorSheets.ts", import.meta.url);
const helperSource = existsSync(helperUrl) ? readFileSync(helperUrl, "utf8").replace(/^import .* from "\.\/workbenchSheets";\s*/m, "") : "";
const dirtyFunctions = ast.statements.filter((node) => ts.isFunctionDeclaration(node) && ["requestFormDirty", "normalizeFormValue"].includes(node.name?.text)).map((node) => node.getText(ast)).join("\n");
const fixture = `
${workbenchSource}
${helperSource}
${dirtyFunctions}
const React = { createElement(type, props, ...children) { return { type, props: { ...props, children } }; } };
export function renderSheet(context) {
  const { form, request = form, batch, user = { role: "finance" }, canEditField = () => true, setForm = () => {} } = context;
  const isErpLockedRequestField = (row) => row.erp_payment_owner === "deeplinkerp";
  const requestDingTalkTerminated = () => false;
  const requestIsPaymentRefused = () => false;
  const generalManagerControlledFields = new Set();
  const fieldLabels = { source_sheet: "所属 Sheet" };
  const moneyFields = new Set();
  const selectOptionsForField = () => null;
  ${renderField.getText(ast)}
  return renderField("source_sheet");
}
export function editorDraftIsDirty(original, draft) {
  ${fields.getText(ast)}
  return requestFormDirty(original, draft, fields);
}
`;
const subject = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(fixture, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
}).outputText).toString("base64")}`);

const batch = {
  workbench_sheet_order: ["广州凌翔", "星铭 财务", "LATINGO", "自定义组"],
  workbench_sheet_targets: { 广州凌翔: "凌翔产品&开发", "星铭 财务": "凌翔/星铭供应链及职能中心", LATINGO: "拉丁购LATINGO", 自定义组: "原始授权组" },
  workbench_sheet_options: ["广州凌翔", "星铭 财务", "LATINGO", "拉丁购"],
};

function find(element, type) {
  if (!element || typeof element !== "object") return undefined;
  if (element.type === type) return element;
  return (element.props?.children || []).flat(Infinity).map((child) => find(child, type)).find(Boolean);
}

test("readonly request drawer displays its projected group instead of the raw department", () => {
  const element = subject.renderSheet({ batch, form: { source_sheet: "凌翔产品&开发", workbench_sheet: "广州凌翔" }, canEditField: () => false });
  assert.deepEqual(find(element, "strong").props.children, ["广州凌翔"]);
});

test("editable request drawer selects an exact existing group and maps an explicit move", () => {
  let changed;
  const form = { source_sheet: "凌翔产品&开发", workbench_sheet: "广州凌翔", summary: "保留" };
  const element = subject.renderSheet({ batch, form, setForm: (value) => { changed = value; } });
  const select = find(element, "select");
  assert.ok(select, "所属 Sheet should offer existing workbench groups");
  assert.equal(select.props.value, "广州凌翔");
  assert.deepEqual(select.props.children.flat(Infinity).map((option) => option.props.value), batch.workbench_sheet_order);
  select.props.onChange({ target: { value: "星铭 财务" } });
  assert.deepEqual(changed, { ...form, source_sheet: "星铭 财务", workbench_sheet: "星铭 财务" });
});

test("business and ERP request drawers keep the projected group readonly", () => {
  for (const options of [{ user: { role: "business" } }, { form: { source_sheet: "凌翔产品&开发", workbench_sheet: "广州凌翔", erp_payment_owner: "deeplinkerp" } }]) {
    const element = subject.renderSheet({ batch, form: { source_sheet: "凌翔产品&开发", workbench_sheet: "广州凌翔" }, ...options });
    assert.equal(find(element, "select"), undefined);
    assert.equal(find(element, "input"), undefined);
    assert.deepEqual(find(element, "strong").props.children, ["广州凌翔"]);
  }
});

test("drawer choices do not introduce options outside the current batch groups", () => {
  assert.equal(typeof subject.requestEditorSheetChoices, "function");
  assert.deepEqual(subject.requestEditorSheetChoices(batch), batch.workbench_sheet_order);
  const row = { source_sheet: "历史原部门", workbench_sheet: "历史显示组" };
  const element = subject.renderSheet({ batch, form: row });
  const select = find(element, "select");
  assert.equal(select.props.value, "历史显示组");
  assert.deepEqual(select.props.children.flat(Infinity).map((option) => option.props.value), ["历史显示组", ...batch.workbench_sheet_order]);
  assert.equal(select.props.children.flat(Infinity)[0].props.disabled, true);
});

test("leaving the group unchanged or returning to it preserves the exact original raw identity", () => {
  assert.equal(typeof subject.selectRequestEditorSheet, "function");
  const original = { id: 12, source_sheet: "凌翔供应链及采购执行单元", workbench_sheet: "广州凌翔", summary: "原说明" };
  const draft = { ...original, summary: "只改说明" };
  assert.deepEqual(subject.selectRequestEditorSheet(original, draft, "广州凌翔", batch, true), draft);
  const moved = subject.selectRequestEditorSheet(original, draft, "星铭 财务", batch, true);
  assert.deepEqual(subject.selectRequestEditorSheet(original, moved, "广州凌翔", batch, true), draft);
  assert.equal(original.source_sheet, "凌翔供应链及采购执行单元");
});

test("an explicit fixed-group move persists even when the destination raw source already matches", () => {
  assert.equal(typeof subject.selectRequestEditorSheet, "function");
  const original = { id: 12, source_sheet: "LATINGO", workbench_sheet: "拉丁购" };
  const moved = subject.selectRequestEditorSheet(original, original, "LATINGO", batch, true);
  assert.deepEqual(moved, { id: 12, source_sheet: "LATINGO", workbench_sheet: "LATINGO" });
  assert.equal(subject.editorDraftIsDirty(original, moved), true);
});

test("custom destinations keep exact server targets and disallowed moves keep the draft", () => {
  assert.equal(typeof subject.selectRequestEditorSheet, "function");
  const row = { source_sheet: "凌翔产品&开发", workbench_sheet: "广州凌翔" };
  assert.deepEqual(subject.selectRequestEditorSheet(row, row, "自定义组", batch, true), { source_sheet: "原始授权组", workbench_sheet: "自定义组" });
  assert.equal(subject.selectRequestEditorSheet(row, row, "拉丁购", batch, true), row, "fixed options alone must not add a batch group");
  assert.equal(subject.selectRequestEditorSheet(row, row, "任意新组", batch, true), row);
  assert.equal(subject.selectRequestEditorSheet(row, row, "LATINGO", batch, false), row);
  const owned = { ...row, erp_payment_owner: "deeplinkerp" };
  assert.equal(subject.selectRequestEditorSheet(owned, owned, "LATINGO", batch, true), owned);
});

test("create and old API drawers preserve their initial target until an explicit existing choice", () => {
  assert.equal(typeof subject.selectRequestEditorSheet, "function");
  const row = { source_sheet: "凌翔产品&开发", workbench_sheet: "广州凌翔" };
  assert.deepEqual(subject.selectRequestEditorSheet(row, row, "广州凌翔", batch, true), row);
  assert.deepEqual(subject.selectRequestEditorSheet(row, row, "LATINGO", batch, true), { source_sheet: "LATINGO", workbench_sheet: "LATINGO" });
  const legacyBatch = { sheet_order: ["旧部门甲", "旧部门乙"] };
  assert.deepEqual(subject.requestEditorSheetChoices(legacyBatch), ["旧部门甲", "旧部门乙"]);
  assert.deepEqual(subject.selectRequestEditorSheet({ source_sheet: "旧部门甲" }, { source_sheet: "旧部门甲", remark: "保留" }, "旧部门乙", legacyBatch, true), { source_sheet: "旧部门乙", workbench_sheet: "旧部门乙", remark: "保留" });
});
