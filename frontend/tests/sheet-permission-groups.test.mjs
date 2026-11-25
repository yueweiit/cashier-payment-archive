import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const helperUrl = new URL("../src/sheetPermissionGroups.ts", import.meta.url);
const source = existsSync(helperUrl) ? readFileSync(helperUrl, "utf8") : "";
const helpers = source ? await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText).toString("base64")}`) : null;

test("legacy partial group access is not promoted to a full grant on display", () => {
  assert.ok(helpers, "sheet permission group helpers should exist");
  const states = { 广州凌翔: "partial", 拉丁购: "partial", LATINGO: "none" };
  assert.equal(helpers.sheetGroupPermissionState("广州凌翔", states, {}), "partial");
  assert.equal(helpers.sheetGroupPermissionState("LATINGO", states, {}), "none");
  assert.equal(helpers.sheetGroupPermissionState("未知组", states, {}), "none");
});

test("saving other user fields does not rewrite any original raw permissions", () => {
  assert.ok(helpers);
  const original = ["凌翔产品&开发", "LatínGo拉丁购", "原手工自定义组"];
  assert.deepEqual(helpers.sheetPermissionPatch(original, [...original], {}), {});
  assert.deepEqual(original, ["凌翔产品&开发", "LatínGo拉丁购", "原手工自定义组"]);
});

test("an explicit full grant affects only the clicked group", () => {
  assert.ok(helpers);
  const changes = helpers.changeSheetGroupPermission({}, "拉丁购", true);
  assert.deepEqual(changes, { 拉丁购: true });
  assert.equal(helpers.sheetGroupPermissionState("拉丁购", { 拉丁购: "partial" }, changes), "all");
  assert.equal(helpers.sheetGroupPermissionState("LATINGO", { LATINGO: "partial" }, changes), "partial");
  assert.deepEqual(helpers.sheetPermissionPatch(["LatínGo拉丁购"], ["LatínGo拉丁购"], changes), {
    sheet_group_changes: { 拉丁购: true },
  });
});

test("revoking one mixed-source group leaves other group limits and raw grants intact", () => {
  assert.ok(helpers);
  const states = { 拉丁购: "partial", LATINGO: "partial" };
  const changes = helpers.changeSheetGroupPermission({}, "拉丁购", false);
  assert.equal(helpers.sheetGroupPermissionState("拉丁购", states, changes), "none");
  assert.equal(helpers.sheetGroupPermissionState("LATINGO", states, changes), "partial");
  assert.deepEqual(helpers.sheetPermissionPatch(["LatínGo拉丁购"], ["LatínGo拉丁购"], changes), {
    sheet_group_changes: { 拉丁购: false },
  });
});

test("explicit group changes never mutate the saved server state", () => {
  assert.ok(helpers);
  const before = { 广州凌翔: true };
  const after = helpers.changeSheetGroupPermission(before, "广州凌翔", false);
  assert.deepEqual(before, { 广州凌翔: true });
  assert.deepEqual(after, { 广州凌翔: false });
});

test("only explicit legacy removal submits raw permissions and preserves exact spelling", () => {
  assert.ok(helpers);
  const original = ["LatínGo拉丁购", "志威模具 (7月前)", "自定义旧组"];
  const changed = helpers.changeLegacySheetPermission(original, original, "自定义旧组", false);
  assert.deepEqual(helpers.sheetPermissionPatch(original, changed, {}), {
    sheet_permissions: ["LatínGo拉丁购", "志威模具 (7月前)"],
  });
  assert.deepEqual(original, ["LatínGo拉丁购", "志威模具 (7月前)", "自定义旧组"]);
});

test("restoring an unsaved legacy removal restores its exact prior scope", () => {
  assert.ok(helpers);
  const original = ["旧组乙", "旧组甲"];
  const removed = helpers.changeLegacySheetPermission(original, original, "旧组乙", false);
  const restored = helpers.changeLegacySheetPermission(original, removed, "旧组乙", true);
  assert.deepEqual(restored, original);
  assert.deepEqual(helpers.sheetPermissionPatch(original, restored, {}), {});
});

test("the legacy control cannot create a new raw permission", () => {
  assert.ok(helpers);
  const original = ["原权限"];
  assert.deepEqual(helpers.changeLegacySheetPermission(original, original, "新原始部门", true), original);
  assert.deepEqual(helpers.sheetPermissionPatch(original, [...original, "新原始部门"], {}), {});
});

test("reordering raw values alone does not rewrite legacy permissions", () => {
  assert.ok(helpers);
  assert.deepEqual(helpers.sheetPermissionPatch(["乙", "甲"], ["甲", "乙"], {}), {});
});

test("group and explicit legacy changes can be sent together without alias conversion", () => {
  assert.ok(helpers);
  assert.deepEqual(helpers.sheetPermissionPatch(["凌翔产品&开发", "原自定义组"], ["凌翔产品&开发"], { 广州凌翔: true }), {
    sheet_permissions: ["凌翔产品&开发"],
    sheet_group_changes: { 广州凌翔: true },
  });
});
