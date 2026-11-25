import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const appAst = ts.createSourceFile("App.tsx", appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const topbar = appAst.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "TopbarImportActions");
let upload;
let fallbackSelect;
function visit(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "uploadDingTalk") upload = node;
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(appAst) === "select"
    && node.openingElement.attributes.properties.some((attribute) => ts.isJsxAttribute(attribute) && attribute.name.getText(appAst) === "aria-label" && attribute.initializer?.text === "未识别记录归入")) fallbackSelect = node;
  ts.forEachChild(node, visit);
}
visit(topbar);
const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const apiAst = ts.createSourceFile("api.ts", apiSource, ts.ScriptTarget.Latest, true);
const apiDeclaration = apiAst.statements.find((node) => ts.isVariableStatement(node)
  && node.declarationList.declarations.some((declaration) => declaration.name.getText(apiAst) === "api"));
const source = `
const request = async (url, options) => ({ url, ...options });
${apiDeclaration.getText(apiAst)}
const React = { createElement(type, props, ...children) { return { type, props: { ...props, children } }; } };
export async function submit(context, withMapping) {
  const { dingtalkFile = new File(["data"], "dingtalk.xlsx"), selectedBatch = { id: 34 }, mapping = { source_sheet: "申请人部门" }, busyAction = null, mappingGroup = "", mappingGroupOptions = ["广州凌翔", "LATINGO"] } = context;
  const state = { calls: [], group: mappingGroup, open: true, error: "", refreshed: false };
  const api = { async uploadDingTalk(...args) { state.calls.push(args); if (context.error) throw new Error(context.error); return context.result || { status: "imported" }; } };
  const setBusyAction = (value) => { state.busy = value; };
  const setHeaders = (value) => { state.headers = value; };
  const setMapping = (value) => { state.mapping = value; };
  const setMappingGroupOptions = (value) => { state.options = value; };
  const setMappingGroup = (value) => { state.group = value; };
  const setMappingError = (value) => { state.error = value; };
  const setMappingOpen = (value) => { state.open = value; };
  const setMessage = (value) => { state.message = value; };
  const setDingtalkFile = (value) => { state.file = value; };
  const setDingtalkInputKey = () => {};
  const refreshAfterImport = async () => { state.refreshed = true; };
  ${upload.getText(appAst)}
  await uploadDingTalk(withMapping);
  return state;
}
export function renderFallback(mappingGroupOptions, mappingGroup, busyAction) {
  const setMappingGroup = () => {};
  const setMappingError = () => {};
  return ${fallbackSelect?.getText(appAst) || "null"};
}
`;
const subject = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
}).outputText).toString("base64")}`);

test("DingTalk file API sends an optional fallback group and preserves old requests", async () => {
  const file = new File(["file"], "dingtalk.xlsx");
  const result = await subject.api.uploadDingTalk(file, 34, { source_sheet: "部门" }, "LATINGO");
  assert.equal(result.body.get("workbench_sheet"), "LATINGO");
  assert.equal(result.body.get("mapping_json"), JSON.stringify({ source_sheet: "部门" }));
  assert.equal((await subject.api.uploadDingTalk(file, 34)).body.has("workbench_sheet"), false);
});

test("file mapping displays only offered fallback groups and freezes them during import", () => {
  const select = subject.renderFallback(["广州凌翔", "LATINGO"], "LATINGO", "dingtalk");
  assert.ok(select, "mapping popup should offer an unknown-row group");
  assert.equal(select.props.value, "LATINGO");
  assert.equal(select.props.disabled, true);
  assert.deepEqual(select.props.children.flat(Infinity).map((option) => option.props.value), ["", "广州凌翔", "LATINGO"]);
});

test("file identification takes server groups and clears any previous fallback", async () => {
  const result = await subject.submit({ mappingGroup: "LATINGO", result: { status: "needs_mapping", headers: ["部门"], suggested_mapping: { source_sheet: "部门" }, workbench_sheet_options: ["广州凌翔", "LATINGO"] } }, false);
  assert.deepEqual(result.options, ["广州凌翔", "LATINGO"]);
  assert.equal(result.group, "");
  assert.equal(result.open, true);
  assert.equal(result.calls[0][3], undefined, "identification must not send a stale choice");
});

test("mapping submission sends only a valid explicit fallback and keeps rejected input open", async () => {
  const result = await subject.submit({ mappingGroup: "LATINGO", error: "有记录无法识别，请选择归入分组" }, true);
  assert.equal(result.calls[0][3], "LATINGO");
  assert.equal(result.open, true);
  assert.equal(result.group, "LATINGO");
  assert.match(result.error, /无法识别/);
  assert.equal(result.refreshed, false);
  const automatic = await subject.submit({}, true);
  assert.equal(automatic.calls[0][3], undefined);
  assert.equal(automatic.refreshed, true);
});

test("busy or unavailable file-group choices cannot submit a stale mapping", async () => {
  assert.equal((await subject.submit({ busyAction: "dingtalk" }, true)).calls.length, 0);
  const result = await subject.submit({ mappingGroup: "任意新组" }, true);
  assert.equal(result.calls.length, 0);
  assert.match(result.error, /分组/);
});
