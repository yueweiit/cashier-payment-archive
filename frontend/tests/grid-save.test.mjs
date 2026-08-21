import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const sourceUrl = new URL("../src/gridSave.ts", import.meta.url);
const source = readFileSync(sourceUrl, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const helpers = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("existing grid rows only submit fields that were actually edited", () => {
  const row = {
    __localId: "row-9",
    applicant: "原申请人",
    source_sheet: "目标 Sheet",
    summary: "原摘要",
  };
  const payload = helpers.buildDirtyGridPayload(
    row,
    new Set(["row-9:source_sheet"]),
    new Set(),
  );

  assert.deepEqual(payload, { source_sheet: "目标 Sheet" });
});

test("clearing a dirty field is preserved instead of omitted", () => {
  const row = {
    __localId: "row-10",
    summary: "",
    needed_payment_date: undefined,
  };
  const payload = helpers.buildDirtyGridPayload(
    row,
    new Set(["row-10:summary", "row-10:needed_payment_date"]),
    new Set(),
  );

  assert.deepEqual(payload, { summary: "", needed_payment_date: null });
});

test("sheet orders are compared after normalization", () => {
  assert.equal(helpers.sameSheetOrder(["甲", "乙"], ["甲", "乙"]), true);
  assert.equal(helpers.sameSheetOrder(["甲", "乙"], ["乙", "甲"]), false);
});
