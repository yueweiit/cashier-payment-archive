import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("App.tsx", appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const component = (name) => ast.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name);
function findNode(parent, predicate) {
  let found;
  function visit(node) {
    if (found) return;
    if (predicate(node)) found = node;
    else ts.forEachChild(node, visit);
  }
  visit(parent);
  assert.ok(found);
  return found;
}
const importPanel = component("TopbarImportActions");
const targetVariables = importPanel.body.statements.filter((node) => ts.isVariableStatement(node)
  && node.declarationList.declarations.some((declaration) => ["weeklyTargetOptions", "importTargetGroup", "importTargetSheet"].includes(declaration.name.getText(ast))));
const targetLabel = findNode(importPanel, (node) => ts.isJsxElement(node) && node.openingElement.attributes.properties.some((attribute) => ts.isJsxAttribute(attribute) && attribute.name.text === "className" && attribute.initializer?.text === "weekly-target-sheet"));
const mergeGroupCell = findNode(component("WeeklyMergeDialog"), (node) => ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === "td" && node.getText(ast).includes("row.source_sheet"));
const mergeChangeCell = findNode(component("WeeklyMergeDialog"), (node) => ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === "td" && /row\.changes|WeeklyMergeChangeList/.test(node.getText(ast)));
const historicalGroupLabel = findNode(component("HistoricalCurrencyRestoreDialog"), (node) => ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === "span" && node.getText(ast).includes("row.source_sheet"));
const workbench = readFileSync(new URL("../src/workbenchSheets.ts", import.meta.url), "utf8");
const helperUrl = new URL("../src/weeklyImportSheets.ts", import.meta.url);
const helper = existsSync(helperUrl) ? readFileSync(helperUrl, "utf8").replace(/^import .* from "\.\/workbenchSheets";\s*/m, "") : "";
const fixture = `${workbench}\n${helper}
const React = { createElement(type, props, ...children) { return { type, props: { ...props, children } }; } };
${component("mergeDisplayValue").getText(ast)}
${component("WeeklyMergeChangeList")?.getText(ast) || ""}
export function importTarget(selectedBatch, weeklyTargetSheet) {
  const busyAction = null;
  const setWeeklyTargetSheet = () => {};
  ${targetVariables.map((node) => node.getText(ast)).join("\n")}
  return { target: importTargetSheet, control: ${targetLabel.getText(ast)} };
}
export function mergeGroup(row) { return ${mergeGroupCell.getText(ast)}; }
export function historicalGroup(row) { return ${historicalGroupLabel.getText(ast)}; }
export function mergeChanges(changes) { const row = { changes }; return ${mergeChangeCell.getText(ast)}; }
`;
const subject = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(fixture, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
}).outputText).toString("base64")}`);
function find(element, type) {
  if (!element || typeof element !== "object") return undefined;
  if (element.type === type) return element;
  return (element.props?.children || []).flat(Infinity).map((child) => find(child, type)).find(Boolean);
}
function textContent(element) {
  if (!element || typeof element === "boolean") return "";
  if (typeof element !== "object") return String(element);
  if (typeof element.type === "function") return textContent(element.type(element.props));
  return (element.props?.children || []).flat(Infinity).map(textContent).join("");
}
const batch = {
  sheet_order: ["凌翔产品&开发", "原始授权部门"],
  workbench_sheet_order: ["广州凌翔", "自定义组"],
  workbench_sheet_targets: { 广州凌翔: "凌翔产品&开发", 自定义组: "原始授权部门" },
  workbench_sheet_options: ["广州凌翔"],
};

test("weekly target dropdown displays projected groups and sends canonical fixed-group destinations", () => {
  const result = subject.importTarget(batch, "广州凌翔");
  const select = find(result.control, "select");
  assert.equal(select.props.value, "广州凌翔");
  assert.deepEqual(select.props.children.flat(Infinity).map((option) => option.props.value), ["", "广州凌翔", "自定义组"]);
  assert.equal(result.target, "广州凌翔");
});

test("weekly targets retain custom raw mappings and reject stale or arbitrary groups", () => {
  assert.equal(subject.importTarget(batch, "自定义组").target, "原始授权部门");
  assert.equal(subject.importTarget(batch, "任意新组").target, "");
  assert.equal(subject.importTarget({ sheet_order: ["旧原始组"] }, "旧原始组").target, "旧原始组");
  assert.equal(subject.importTarget(null, "广州凌翔").target, "");
});

test("weekly merge and history correction labels use projected groups with old API fallback", () => {
  for (const render of [subject.mergeGroup, subject.historicalGroup]) {
    const row = { request_id: 1, applicant: "申请人", source_sheet: "凌翔产品&开发", workbench_sheet: "广州凌翔" };
    const text = textContent(render(row));
    assert.match(text, /广州凌翔/);
    assert.doesNotMatch(text, /凌翔产品&开发/);
    assert.match(textContent(render({ source_sheet: "旧分组" })), /旧分组/);
    assert.equal(row.source_sheet, "凌翔产品&开发");
  }
});

test("weekly merge differences display projected values and suppress equal display groups", () => {
  const change = { field: "source_sheet", label: "所属 Sheet", old: "凌翔产品&开发", new: "星铭FC财务中心", display_old: "广州凌翔", display_new: "星铭 财务" };
  const rendered = textContent(subject.mergeChanges([change]));
  assert.match(rendered, /广州凌翔 → 星铭 财务/);
  assert.doesNotMatch(rendered, /凌翔产品&开发|星铭FC财务中心/);
  const hiddenChange = { ...change, display_new: "广州凌翔" };
  assert.equal(textContent(subject.mergeChanges([hiddenChange])), "无字段变化");
  assert.match(textContent(subject.mergeChanges([{ field: "summary", label: "摘要", old: "原说明", new: "新说明" }])), /原说明 → 新说明/);
  const visible = Array.from({ length: 5 }, (_, index) => ({ field: `field-${index}`, label: "修改", old: "原", new: "新" }));
  assert.doesNotMatch(textContent(subject.mergeChanges([hiddenChange, ...visible])), /另有/);
  assert.equal(hiddenChange.old, "凌翔产品&开发", "display filtering must retain raw merge data");
});
