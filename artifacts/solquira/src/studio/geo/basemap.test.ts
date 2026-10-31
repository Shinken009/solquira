import test from "node:test";
import assert from "node:assert/strict";
import { resolveBasemapConfig } from "./basemap.ts";

test("defaults to the public OpenStreetMap tile service", () => {
  const config = resolveBasemapConfig({});
  assert.equal(config.provider, "osm");
  assert.equal(config.publicOsm, true);
  assert.equal(
    config.urlTemplate,
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  );
  assert.match(config.attributionHtml, /OpenStreetMap contributors/);
  assert.equal(config.maxLevel, 18);
});

test("uses MapTiler when a key is configured", () => {
  const config = resolveBasemapConfig({
    VITE_BASEMAP_PROVIDER: "maptiler",
    VITE_BASEMAP_API_KEY: "abc 123",
  });
  assert.equal(config.provider, "maptiler");
  assert.equal(config.publicOsm, false);
  assert.match(config.urlTemplate, /api\.maptiler\.com/);
  assert.match(config.urlTemplate, /key=abc%20123/);
  assert.match(config.attributionHtml, /MapTiler/);
});

test("falls back to OpenStreetMap when MapTiler has no key", () => {
  const config = resolveBasemapConfig({ VITE_BASEMAP_PROVIDER: "maptiler" });
  assert.equal(config.provider, "osm");
  assert.equal(config.publicOsm, true);
  assert.match(config.warning ?? "", /MapTiler/);
});

test("accepts a custom HTTPS XYZ provider and keeps its attribution", () => {
  const config = resolveBasemapConfig({
    VITE_BASEMAP_PROVIDER: "custom",
    VITE_BASEMAP_URL: "https://tiles.example.test/{z}/{x}/{y}.png",
    VITE_BASEMAP_ATTRIBUTION: "© Example Maps",
    VITE_BASEMAP_MAX_LEVEL: "17",
  });
  assert.equal(config.provider, "custom");
  assert.equal(
    config.urlTemplate,
    "https://tiles.example.test/{z}/{x}/{y}.png",
  );
  assert.equal(config.attributionHtml, "© Example Maps");
  assert.equal(config.maxLevel, 17);
  assert.equal(config.publicOsm, false);
});

test("rejects an incomplete custom URL instead of silently breaking the map", () => {
  const config = resolveBasemapConfig({
    VITE_BASEMAP_PROVIDER: "custom",
    VITE_BASEMAP_URL: "https://tiles.example.test/{z}/{x}.png",
  });
  assert.equal(config.provider, "osm");
  assert.match(config.warning ?? "", /custom basemap/i);
});
