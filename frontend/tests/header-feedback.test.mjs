import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("global feedback is rendered outside the navigation header", () => {
  const userbarMatch = appSource.match(
    /<div className="app-userbar">([\s\S]*?)<\/div>\s*<\/header>/,
  );

  assert.ok(userbarMatch, "expected the application user bar to exist");
  assert.doesNotMatch(
    userbarMatch[1],
    /className="toast/,
    "global feedback must not participate in the header flex layout",
  );
  assert.match(
    appSource,
    /<\/header>\s*<GlobalFeedback/,
    "global feedback should render immediately after the header",
  );
});

test("feedback viewport is fixed and width constrained", () => {
  assert.match(styleSource, /\.global-feedback-viewport\s*\{[^}]*position:\s*fixed/s);
  assert.match(styleSource, /\.global-feedback-toast\s*\{[^}]*max-width:/s);
});
