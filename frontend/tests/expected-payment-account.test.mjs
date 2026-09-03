import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../src/i18n.tsx", import.meta.url), "utf8");

test("expected payment account is a first-class editable request field", () => {
  assert.match(apiSource, /expected_payment_account\?:\s*string/);
  assert.match(appSource, /expected_payment_account:\s*"预计支付账户"/);
  assert.match(
    appSource,
    /key:\s*"payment_account"[\s\S]*?key:\s*"expected_payment_account",\s*labelZh:\s*"预计支付账户",\s*labelEs:\s*"Cuenta de pago prevista"/,
  );
  assert.match(appSource, /"payment_account",\s*\n\s*"expected_payment_account",/);
  assert.match(appSource, /renderField\("payment_account"\)\}\s*\n\s*\{renderField\("expected_payment_account"\)\}/);
});

test("expected payment account is visible on mobile and translated", () => {
  assert.match(i18nSource, /"预计支付账户":\s*"Cuenta de pago prevista"/);
  assert.match(appSource, /expectedPaymentAccount:\s*"Cuenta de pago prevista"/);
  assert.match(appSource, /expectedPaymentAccount:\s*"预计支付账户"/);
  assert.match(appSource, /labels\.expectedPaymentAccount[\s\S]*?row\.expected_payment_account/);
});

