import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/gridClipboard.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const helpers = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("Excel TSV retains multiline cells, tabs, escaped quotes and source identifiers", () => {
  const text = '00001234567890123456789\t"first line\r\nsecond\tline with ""quotes"""\tUS$45,567.18\tUSD\r\n00002\tordinary\tMX$2,000.50\tMXN\r\n';
  assert.deepEqual(helpers.parseClipboardTable(text), [
    ["00001234567890123456789", 'first line\nsecond\tline with "quotes"', "US$45,567.18", "USD"],
    ["00002", "ordinary", "MX$2,000.50", "MXN"],
  ]);
});

test("TSV preserves empty cells and strips only the terminal record separator", () => {
  assert.deepEqual(helpers.parseClipboardTable("a\t\r\nb\t\r\n"), [["a", ""], ["b", ""]]);
  assert.deepEqual(helpers.parseClipboardTable('"only\ncell"'), [["only\ncell"]]);
});

test("formatted currency input is the original amount without currency conversion", () => {
  const amount = { key: "amount", type: "number" };
  assert.equal(helpers.normalizeCellValue(amount, "US$45,567.18"), 45567.18);
  assert.equal(helpers.normalizeCellValue(amount, "MX$2,000.50"), 2000.5);
  assert.equal(helpers.normalizeCellValue(amount, "￥ 1,234.50"), 1234.5);
  assert.equal(helpers.normalizeCellValue(amount, ""), undefined);
});

test("date cells normalize unambiguous year-first slash and ISO date text", () => {
  const date = { key: "needed_payment_date", type: "date" };
  assert.equal(helpers.normalizeCellValue(date, "2026/9/17"), "2026-09-17");
  assert.equal(helpers.normalizeCellValue(date, "2026-09-17 00:00:00"), "2026-09-17");
  assert.equal(helpers.normalizeCellValue(date, "2026-09-17"), "2026-09-17");
  assert.equal(helpers.normalizeCellValue(date, ""), "");
});

test("text identifiers remain strings with leading zeroes and all digits", () => {
  assert.equal(helpers.normalizeCellValue({ key: "dingding_id" }, "00001234567890123456789"), "00001234567890123456789");
  assert.equal(helpers.normalizeCellValue({ key: "payee_account" }, "001234567890123456789"), "001234567890123456789");
});

test("invalid numbers and ambiguous or nonexistent dates fail instead of becoming empty values", () => {
  assert.throws(() => helpers.normalizeCellValue({ key: "amount", type: "number" }, "US$45,56.18"));
  assert.throws(() => helpers.normalizeCellValue({ key: "amount", type: "number" }, "not an amount"));
  assert.throws(() => helpers.normalizeCellValue({ key: "needed_payment_date", type: "date" }, "09/10/2026"));
  assert.throws(() => helpers.normalizeCellValue({ key: "needed_payment_date", type: "date" }, "2026/2/30"));
});

test("new foreign rows allow original currency and amount while saved rows require correction", () => {
  const newRow = { __isNew: true, currency: "USD" };
  assert.equal(helpers.canDirectlyEditGridField?.(newRow, "currency"), true);
  assert.equal(helpers.canDirectlyEditGridField?.(newRow, "amount"), true);
  assert.equal(helpers.canDirectlyEditGridField?.({ id: 42, currency: "USD" }, "currency"), false);
  assert.equal(helpers.canDirectlyEditGridField?.({ id: 42, currency: "USD" }, "amount"), false);
  assert.equal(helpers.canDirectlyEditGridField?.({ id: 42, currency: "CNY" }, "amount"), true);
  assert.equal(helpers.canDirectlyEditGridField?.({ __deleted: true }, "summary"), false);
});
