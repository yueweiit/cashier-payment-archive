import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const navigationSource = readFileSync(new URL("../src/AppNavigation.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("translated navigation cannot overlap the account controls", () => {
  assert.match(
    appSource,
    /<header className="app-header" data-language=\{language\}>/,
    "the header should expose the active language for responsive layout rules",
  );
  assert.match(
    styleSource,
    /\.app-userbar\s*\{[^}]*flex:\s*0 0 auto;[^}]*min-width:\s*max-content/s,
    "account controls must keep their intrinsic width instead of overlapping navigation",
  );
});

test("translated navigation exposes hidden destinations through an overflow menu", () => {
  assert.match(appSource, /<AppNavigation/);
  assert.match(navigationSource, /ResizeObserver/);
  assert.match(navigationSource, /app-nav-measure/);
  assert.match(navigationSource, /app-nav-more/);
  assert.match(navigationSource, /aria-expanded=\{menuOpen\}/);
  assert.match(navigationSource, /t\("更多", "Más"\)/);
  assert.match(navigationSource, /event\.key === "Escape"/);
  assert.doesNotMatch(styleSource, /\.app-nav\s*\{[^}]*overflow-x:\s*auto/s);
});
