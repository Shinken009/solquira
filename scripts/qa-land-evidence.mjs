// Loopback-only integration of the real inventory API and its manual UI.
// Missing inventories come from the local backend. Error/stale/unavailable and
// malformed responses below are explicitly declared transport fixtures, not data.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = process.argv[2] || 'http://127.0.0.1:8048';
const origin = new URL(target).origin;
if (!['127.0.0.1', 'localhost'].includes(new URL(target).hostname)) throw Error('Loopback QA only');
const output = resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/playwright/land-evidence');
mkdirSync(output, { recursive: true });
const ids = ['forecast_context', 'climate_baseline', 'land_cover_change', 'land_productivity', 'soil_organic_carbon', 'terrain', 'field_observations'];
const browser = await chromium.launch({ args: ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const checks = [], errors = [];
let activePage;

try {
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: width === 1440 ? 1000 : 900 }, reducedMotion: 'reduce' });
    await context.addInitScript(() => {
      window.__evidenceQAGps = 0;
      Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition(_success, error) {
        window.__evidenceQAGps++;
        error({ code: 1, message: 'Declared permission-denied fixture' });
      } } });
    });
    const calls = { inventory: 0, forecast: 0, history: 0, model: 0, blockedExternal: 0 };
    let verifiedLocal = false, mode = 'real', lastRealInventory;
    let markPending, releasePending;
    const pendingStarted = new Promise(resolve => { markPending = resolve; });
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin && !['blob:', 'data:'].includes(url.protocol)) { calls.blockedExternal++; return route.abort(); }
      if (url.pathname.endsWith('/studio/agent') || url.pathname.endsWith('/v1/field-brief')) { calls.model++; return route.abort(); }
      if (url.pathname.endsWith('/studio/history')) { calls.history++; return route.abort(); }
      if (url.pathname.endsWith('/studio/location')) { calls.forecast++; return route.abort(); }
      if (url.pathname.endsWith('/studio/bootstrap')) {
        const response = await route.fetch(), value = await response.json();
        verifiedLocal = value.inference?.mode === 'disabled' && value.inference?.available === false && value.geodata?.enabled === false;
        // Bootstrap stays genuine. No fake enablement, credentials or tokens.
        return route.fulfill({ response });
      }
      if (url.pathname.endsWith('/studio/evidence')) {
        calls.inventory++;
        assert(verifiedLocal, 'Only a verified provider/source-disabled service may run this test');
        if (mode === 'error') return route.fulfill({ status: 503, contentType: 'application/json', json: { detail: 'Declared inventory transport fixture unavailable.' } });
        const response = await route.fetch(), value = await response.json();
        assert.equal(response.status(), 200, 'The genuine inventory API must admit the session and CSRF');
        assert.equal(value.schema_version, 'solquira.land-evidence/1');
        assert.equal(value.status, 'inventory_only');
        assert.equal(value.scientific_validation, 'not_validated');
        assert.equal(value.is_medalus_r, false);
        assert.deepEqual(value.items.map(item => item.id), ids);
        assert(value.items.every(item => item.state === 'missing'), 'A source-disabled, reference-free inventory must not pretend to know environmental conditions');
        lastRealInventory = value;
        if (mode === 'pending') {
          markPending();
          await new Promise(resolve => { releasePending = resolve; });
          await route.fulfill({ response, json: value }).catch(() => {});
          return;
        }
        if (mode === 'stale' || mode === 'unavailable') {
          value.items[0].state = mode;
          value.items[0].reason = `Declared browser ${mode} fixture; not a source result.`;
        }
        if (mode === 'malformed') value.items[0].state = 'validated_for_everywhere';
        if (mode === 'mismatched') value.location.latitude = -1;
        return route.fulfill({ response, json: value });
      }
      return route.continue();
    });
    const page = await context.newPage(); activePage = page; page.setDefaultTimeout(25000);
    page.on('pageerror', error => errors.push({ width, message: error.message }));
    page.on('console', message => {
      if (message.type() === 'error' && /same key|unique.*key|duplicate.*key/i.test(message.text())) errors.push({ width, message: message.text() });
    });
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.getByTestId('studio-ready').waitFor();
    assert(verifiedLocal, 'Bootstrap must declare both model and geographic source disabled');
    assert.equal(await page.evaluate(() => window.__evidenceQAGps), 0);
    const panel = page.getByTestId('land-evidence-panel');
    const load = page.getByTestId('land-evidence-load');
    const result = page.getByTestId('land-evidence-result');
    const error = page.getByTestId('land-evidence-error');
    await page.getByTestId('area-tab-guidance').click();
    await page.getByTestId('land-evidence-empty').waitFor();
    assert.equal(await load.isDisabled(), true, 'A geographic point is required');
    assert.equal(calls.inventory, 0);
    await page.getByTestId('area-tab-territory').click();
    await page.getByTestId('geo-manual').locator('summary').click();
    await page.getByTestId('geo-lat').fill('52.520008');
    await page.getByTestId('geo-lon').fill('13.404954');
    await page.getByTestId('geo-load-context').click();
    await page.locator('[data-testid="geo-point-review"][data-confirmed="true"]').waitFor();
    await page.waitForFunction(() => document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state') === 'ready');
    const canvas = await page.locator('.qg-map canvas').first().elementHandle();
    assert(canvas, 'The persistent Cesium canvas must exist');
    await page.getByTestId('area-tab-guidance').click();
    assert.equal(calls.inventory, 0, 'Point confirmation and opening the panel must not load inventory');
    await load.focus(); await load.press('Enter');
    await result.waitFor();
    assert.equal(calls.inventory, 1);
    assert.equal(lastRealInventory.location.latitude, 52.520008, 'Inventory retains original coordinates, not forecast rounding');
    assert.equal(lastRealInventory.location.longitude, 13.404954);
    for (const id of ['forecast_context', 'land_cover_change', 'soil_organic_carbon']) {
      await page.getByTestId(`evidence-slot-${id}`).waitFor();
      assert.match(await page.getByTestId(`evidence-state-${id}`).innerText(), /missing|not included|not available|not yet/i);
    }
    const panelText = await panel.innerText();
    assert.match(panelText, /not.*diagnos|not.*validated|not.*susceptib|no.*index|not.*MEDALUS/i, 'Scientific limits must be visible, not only hidden in metadata');
    assert.doesNotMatch(panelText, /1970-01-01|Invalid Date|\b0\s*(?:mm|%|m³)/, 'Null metadata must not become zero measurements or epoch dates');
    const otherGaps = panel.locator('.le-other');
    await otherGaps.locator(':scope > summary').click();
    for (const id of ids) {
      const card = page.getByTestId(`evidence-slot-${id}`);
      assert.equal(await card.getAttribute('data-state'), 'missing');
      await card.locator('.le-metadata > summary').click();
      const metadataText = await card.locator('.le-metadata').innerText();
      assert.match(metadataText, /No source included|No source/i);
      assert.match(metadataText, /Not supplied|No period supplied/i);
      assert.doesNotMatch(metadataText, /1970-01-01|Invalid Date|\b0\s*(?:mm|%|m³)/);
      await card.locator('.le-metadata > summary').click();
    }
    await otherGaps.locator(':scope > summary').click();
    const beforeNavigation = { ...calls };
    for (const area of ['territory', 'anticipation', 'evidence', 'guidance', 'territory', 'guidance', 'anticipation', 'guidance', 'territory', 'guidance']) {
      await page.getByTestId(`area-tab-${area}`).click();
      await page.locator(`.qs-studio[data-active-area="${area}"]`).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `No overflow at ${width}/${area}`);
    }
    assert.equal(calls.inventory, beforeNavigation.inventory, 'Navigation must not refetch inventory');
    await result.waitFor();
    assert(await canvas.evaluate(el => el.isConnected && el === document.querySelector('.qg-map canvas')), 'Cesium must not remount');
    await panel.scrollIntoViewIfNeeded();
    await panel.screenshot({ path: `${output}/inventory-${width}.png`, animations: 'disabled' });
    await page.screenshot({ path: `${output}/inventory-viewport-${width}.png`, animations: 'disabled' });

    mode = 'stale'; await load.click(); await result.waitFor();
    await page.locator('[data-testid="evidence-slot-forecast_context"][data-state="stale"]').waitFor();
    assert.match(await page.getByTestId('evidence-state-forecast_context').innerText(), /stale|expired|out of date|fresh check/i);
    assert.doesNotMatch(await result.innerText(), /1970-01-01|Invalid Date/);
    mode = 'unavailable'; await load.click(); await result.waitFor();
    await page.getByTestId('evidence-state-forecast_context').filter({ hasText: /unavailable|could not|not available/i }).waitFor();
    mode = 'error'; await load.click(); await error.waitFor();
    assert.equal(await result.count(), 0, 'Transport failure must not leave a successful previous inventory');
    mode = 'malformed'; await load.click(); await error.waitFor();
    assert.equal(await result.count(), 0, 'An unknown status cannot render an authoritative inventory');
    mode = 'mismatched'; await load.click(); await error.waitFor();
    assert.equal(await result.count(), 0, 'Inventory belonging to another point must be rejected');
    mode = 'pending'; await load.click(); await pendingStarted;
    await page.getByTestId('area-tab-territory').click();
    await page.getByTestId('geo-lat').fill('-1.286389');
    releasePending();
    await page.getByTestId('area-tab-guidance').click();
    await page.getByTestId('land-evidence-empty').waitFor();
    assert.equal(await result.count(), 0);
    assert.equal(await error.count(), 0, 'An obsolete aborted request must not report an error on the new point');
    assert.equal(await load.isDisabled(), true);
    await page.getByTestId('area-tab-territory').click();
    await page.getByTestId('geo-lon').fill('36.817223');
    await page.getByTestId('geo-load-context').click();
    await page.locator('[data-testid="geo-point-review"][data-confirmed="true"]').waitFor();
    await page.getByTestId('area-tab-guidance').click();
    assert.equal(await result.count(), 0, 'Newly confirmed point requires another explicit inventory request');
    mode = 'real'; await load.click(); await result.waitFor();
    assert.equal(lastRealInventory.location.latitude, -1.286389);
    assert.equal(lastRealInventory.location.longitude, 36.817223);
    assert.equal(calls.inventory, 8, 'Exactly eight explicit inventory requests, never a hidden retry');
    assert.equal(calls.forecast, 0, 'Inventory must not activate geographic acquisition');
    assert.equal(calls.history, 0);
    assert.equal(calls.model, 0);
    assert.equal(await page.evaluate(() => window.__evidenceQAGps), 0);
    checks.push({ width, realSessionAndInventory: true, pointRequired: true, rawCoordinatesPreserved: true, manualOnly: true,
      sevenMissingSlotsHonest: true, nullsNotMeasurements: true, scientificLimitsVisible: true, staleAndUnavailableShown: true,
      transportAndMalformedRejected: true, mismatchedPointRejected: true, obsoleteRequestIgnored: true, sameCesiumCanvas: true,
      tenAreaChangesNoRefetch: true, noOverflow: true, keyboardAction: true, ...calls });
    await context.close();
  }
  assert.equal(errors.length, 0, JSON.stringify(errors));
  const result = { status: 'PASS', target, scope: 'Genuine loopback session/CSRF and reference-free inventories; declared response fixtures for error/stale/unavailable/malformed/late transport. No external acquisition, observations, model, history, GPS or Kobo.', checks, errors };
  writeFileSync(`${output}/checks.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  if (activePage && !activePage.isClosed()) await activePage.screenshot({ path: `${output}/failure.png`, fullPage: true, animations: 'disabled' }).catch(() => {});
  writeFileSync(`${output}/failure.json`, JSON.stringify({ error: String(error), checks, errors }, null, 2));
  console.error(error); process.exitCode = 1;
} finally { await browser.close(); }
