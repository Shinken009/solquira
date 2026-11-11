import assert from "node:assert/strict";
import test from "node:test";
import { emptyRotationInputs, evaluateRotation } from "../artifacts/solquira/src/studio/rotation-checks.ts";

const evaluate = (data = {}, previous, candidate) => evaluateRotation({ ...emptyRotationInputs, ...data }, previous, candidate);

test("unknown data keep all checks actionable and never become zero", () => {
  const result = evaluate();
  assert.equal(result.next, "history");
  assert.equal(result.volume, null);
  assert.ok(Object.values(result.statuses).every(value => value === "check"));
});

test("local window includes both ends and supports a season across New Year", () => {
  const window = { start: "2026-12-15", end: "2027-01-20" };
  for (const planned of [window.start, "2027-01-02", window.end]) assert.equal(evaluate({ ...window, planned }).window, "inside");
  assert.equal(evaluate({ ...window, planned: "2027-01-21" }).window, "outside");
  assert.equal(evaluate({ ...window, planned: "2026-02-30" }).window, "invalid");
  assert.equal(evaluate({ ...window, start: "2027-01-21" }).window, "invalid");
  assert.equal(evaluate({ start: window.start }).window, "unknown");
  assert.equal(evaluate(window).window, "needDate");
});

test("family comparison requires two identified taxa; a different family is not a pass", () => {
  assert.equal(evaluate({}, "Solanaceae").history, "unknown");
  assert.equal(evaluate({}, "Solanaceae", "Solanaceae").statuses.history, "attention");
  assert.equal(evaluate({}, "Solanaceae", "Poaceae").statuses.history, "reference");
});

test("water comparison uses delivery volume for one explicitly comparable turn", () => {
  const turn = { water: "turn", flowLpm: "12,5", minutes: "80", requiredM3: "1.2" };
  assert.equal(evaluate(turn).volume, 1);
  assert.equal(evaluate(turn).water, "volume");
  assert.equal(evaluate({ ...turn, sameBasis: true }).water, "below");
  assert.equal(evaluate({ ...turn, sameBasis: true, requiredM3: "1" }).water, "meets");
  assert.equal(evaluate({ ...turn, sameBasis: true, requiredM3: "0" }).water, "volume");
  assert.equal(evaluate({ ...turn, sameBasis: true, flowLpm: "0" }).water, "below");
  for (const flowLpm of ["", "-1", "1e3", "NaN", "12,5.1"]) assert.equal(evaluate({ ...turn, flowLpm }).volume, null);
  assert.equal(evaluate({ ...turn, water: "rain", sameBasis: true }).volume, null);
  assert.equal(evaluate({ ...turn, water: "rain", sameBasis: true }).water, "rain");
});

test("ponding is a field signal; absence in one visit never confirms drainage", () => {
  assert.equal(evaluate({ ponding: "seen" }).soil, "matchGuide");
  assert.equal(evaluate({ drainage: "required" }).soil, "observe");
  assert.equal(evaluate({ drainage: "required", ponding: "seen" }).statuses.soil, "attention");
  assert.equal(evaluate({ drainage: "required", ponding: "notSeen" }).soil, "notSeen");
});

test("a concrete discrepancy takes priority over unknowns", () => {
  assert.equal(evaluate({ drainage: "required", ponding: "seen" }).next, "soil");
  assert.equal(evaluate({ drainage: "required", ponding: "seen" }, "Poaceae", "Poaceae").next, "history");
});
