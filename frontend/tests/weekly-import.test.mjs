import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const ast = ts.createSourceFile("api.ts", apiSource, ts.ScriptTarget.Latest, true);
const apiDeclaration = ast.statements.find((node) => ts.isVariableStatement(node)
  && node.declarationList.declarations.some((declaration) => declaration.name.getText(ast) === "api"));
assert.ok(apiDeclaration);
const compiled = ts.transpileModule(`const request = async (url, options) => ({ url, ...options });\n${apiDeclaration.getText(ast)}`, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { api } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

for (const method of ["uploadWeekly", "previewWeeklyMerge"]) {
  test(`${method} submits an explicit target Sheet with the workbook and batch`, async () => {
    const file = new File(["workbook"], "requests.xlsx");
    const result = await api[method](file, 123, "赣瑞模具");
    assert.equal(result.body.get("target_sheet"), "赣瑞模具");
    assert.equal(result.body.get("batch_id"), "123");
    assert.equal(result.body.get("file"), file);
  });

  test(`${method} preserves workbook Sheets when no target is selected`, async () => {
    const result = await api[method](new File(["workbook"], "requests.xlsx"), 123, "");
    assert.equal(result.body.has("target_sheet"), false);
  });
}
