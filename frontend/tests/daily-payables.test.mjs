import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("selecting a historical daily-payables point keeps the later trend window", () => {
  assert.match(appSource, /const \[trendEndDate, setTrendEndDate\] = useState/);
  assert.match(appSource, /shiftIsoDate\(trendEndDate, -13\)/);
  assert.match(appSource, /api\.dailyPayablesTrend\(start, trendEndDate\)/);
  assert.match(appSource, /function selectQueryDate\(value: string\)[\s\S]*setSelectedDate\(value\)[\s\S]*setTrendEndDate\(value\)/);
  assert.match(appSource, /onChange=\{\(event\) => selectQueryDate\(event\.target\.value\)\}/);
  assert.match(appSource, /<DailyPayablesTrendChart[\s\S]*onSelectDate=\{setSelectedDate\}/);
  assert.doesNotMatch(appSource, /onSelectDate=\{setTrendEndDate\}/);
});
