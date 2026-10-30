import test from "node:test";
import assert from "node:assert/strict";
import { wrapRequestImage } from "./rasterLoad.ts";

test("notifies once after the first resolved raster", async () => {
  let calls = 0;
  const wrapped = wrapRequestImage(async () => ({ id: "tile" }), {
    isActive: () => true,
    onFirstRaster: () => { calls += 1; },
  });
  assert.deepEqual(await wrapped(), { id: "tile" });
  assert.deepEqual(await wrapped(), { id: "tile" });
  assert.equal(calls, 1);
});

test("propagates rejection without claiming a raster", async () => {
  let calls = 0;
  const wrapped = wrapRequestImage(
    async () => { throw new Error("tile failed"); },
    { isActive: () => true, onFirstRaster: () => { calls += 1; } },
  );
  await assert.rejects(wrapped(), /tile failed/);
  assert.equal(calls, 0);
});

test("preserves the undefined throttled/deferred return", () => {
  let calls = 0;
  const wrapped = wrapRequestImage(
    () => undefined,
    { isActive: () => true, onFirstRaster: () => { calls += 1; } },
  );
  assert.equal(wrapped(), undefined);
  assert.equal(calls, 0);
});

test("does not notify after disposal", async () => {
  let calls = 0;
  const wrapped = wrapRequestImage(async () => ({ id: "tile" }), {
    isActive: () => false,
    onFirstRaster: () => { calls += 1; },
  });
  assert.deepEqual(await wrapped(), { id: "tile" });
  assert.equal(calls, 0);
});
