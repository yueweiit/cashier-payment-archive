import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../src/i18n.tsx", import.meta.url), "utf8");

test("payee name uses the concise Chinese label and keeps its Spanish translation", () => {
  assert.match(
    appSource,
    /key:\s*"payee_name",\s*labelZh:\s*"收款人",\s*labelEs:\s*"Nombre del beneficiario"/,
  );
  assert.match(i18nSource, /"收款人":\s*"Nombre del beneficiario"/);
  assert.doesNotMatch(appSource, /收款人名称/);
  assert.doesNotMatch(i18nSource, /收款人名称/);
});
