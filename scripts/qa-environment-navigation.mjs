// Loopback-only browser integration. Environmental responses are labelled fixtures;
// bootstrap/comparison and verified provider-disabled briefs use the local service.
// Agent/external calls are blocked; a brief is never sent to an inference-enabled server.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = process.argv[2] || 'http://127.0.0.1:8046';
const origin = new URL(target).origin;
if (!['localhost', '127.0.0.1'].includes(new URL(target).hostname)) throw Error('Loopback QA only');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/playwright/environment-navigation');
mkdirSync(output, { recursive: true });
const generated = spawnSync(process.env.PYTHON || 'python', ['-c', `
import sys,os,json,socket
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
sys.path.insert(0,'artifacts/solquira/backend')
def deny(*a,**k): raise AssertionError('Network forbidden in environmental fixture generation')
socket.socket.connect=deny
import studio_geo
today=datetime.now(timezone.utc).date()
source={'timezone':'GMT','utc_offset_seconds':0,'latitude':37.1,'longitude':-4.6,
 'daily_units':{'time':'iso8601','temperature_2m_max':'°C','temperature_2m_min':'°C','precipitation_sum':'mm','et0_fao_evapotranspiration':'mm'},
 'daily':{'time':[(today+timedelta(days=i)).isoformat() for i in range(7)],'temperature_2m_max':[25.]*7,'temperature_2m_min':[12.]*7,'precipitation_sum':[2,3,0,4,0,1,1],'et0_fao_evapotranspiration':[3.2]*7}}
with patch.dict(os.environ,{'SOLQUIRA_STUDIO_GEODATA':'1'}),patch('studio_geo._fetch',return_value=studio_geo.parse_weather(source)):
 result=studio_geo.location_context({'latitude':37.1,'longitude':-4.6})
result['context_ref']=None
print(json.dumps(result))
`], { cwd: root, encoding: 'utf8', env: { ...process.env, SOLQUIRA_STUDIO_PROVIDER: 'disabled', PYTHONUTF8: '1' } });
if (generated.status !== 0) throw Error(`Offline backend fixture failed: ${generated.stderr}`);
const forecast = JSON.parse(generated.stdout);
const history = {
  schema_version: 'solquira.history/1', location: { latitude: 37.1, longitude: -4.6 },
  retrieved_at: forecast.retrieved_at,
  history: {
    state: 'ready', source: 'Open-Meteo', model: 'ERA5', kind: 'model_reanalysis', time_zone: 'UTC', units: 'mm',
    target_period: { start: '2026-09-01', end: '2026-09-30' },
    baseline: { start_year: 1991, end_year: 2020, calendar_month: 9, years_required: 30, complete_years: 30 },
    precipitation_mm: 50, baseline_mean_mm: 100, anomaly_mm: -50, anomaly_percent: -50,
    coverage: { target_days: 30, target_expected_days: 30, baseline_complete_years: 30 },
    limitations: ['Browser-only declared fixture, not a real historical observation or a diagnosis.'],
  },
};

const checks = [], errors = [];
let activePage;
const browser = await chromium.launch({ args: ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
try {
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: width === 1440 ? 1000 : 900 }, reducedMotion: 'reduce' });
    // Do not request the person's location or synthesize a GPS observation.
    await context.addInitScript(() => {
      window.__qaGPS = [];
      Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition(success, error) { window.__qaGPS.push({success,error}); } } });
    });
    const calls = { location: 0, history: 0, model: 0, deterministicBrief: 0, blockedExternal: 0 };
    const locationSequence = [];
    let forecastMode = 'ready', historyMode = 'ready';
    let verifiedProviderDisabled = false;
    let releasePendingHistory, markPendingHistoryStarted;
    const pendingHistoryStarted = new Promise(resolve => { markPendingHistoryStarted = resolve; });
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin && !['data:', 'blob:'].includes(url.protocol)) { calls.blockedExternal++; return route.abort(); }
      if (url.pathname.endsWith('/studio/agent')) { calls.model++; return route.abort(); }
      if (url.pathname.endsWith('/v1/field-brief')) {
        if (!verifiedProviderDisabled) { calls.model++; return route.abort(); }
        calls.deterministicBrief++;
        return route.continue();
      }
      if (url.pathname.endsWith('/studio/bootstrap')) {
        const response = await route.fetch(), value = await response.json();
        verifiedProviderDisabled = value.inference?.mode === 'disabled' && value.inference?.available === false;
        // Enable fixture-backed controls only in the browser. The actual local
        // server remains provider/geodata disabled; CSRF/session stay genuine.
        value.geodata = { enabled: true };
        return route.fulfill({ response, json: value });
      }
      if (url.pathname.endsWith('/studio/location')) {
        calls.location++;
        const value = structuredClone(forecast), point = route.request().postDataJSON();
        locationSequence.push({ point, fixture: forecastMode });
        value.location = { ...point, precision_degrees: .001 };
        if (forecastMode === 'invalid') value.anticipation.units.precipitation = 'cm';
        if (forecastMode === 'legacy') delete value.anticipation;
        return route.fulfill({ status: 200, contentType: 'application/json', json: value });
      }
      if (url.pathname.endsWith('/studio/history')) {
        calls.history++;
        if (historyMode === 'pending') {
          markPendingHistoryStarted();
          await new Promise(resolve => { releasePendingHistory = resolve; });
          const value = structuredClone(history); value.location = route.request().postDataJSON();
          // The intentionally obsolete request may already be aborted.
          await route.fulfill({ status: 200, contentType: 'application/json', json: value }).catch(() => {});
          return;
        }
        if (historyMode === 'error') return route.fulfill({ status: 503, contentType: 'application/json', json: { detail: 'Fixture unavailable' } });
        const value = structuredClone(history);
        value.location = route.request().postDataJSON();
        if (historyMode === 'unavailable') value.history = {
          state: 'unavailable', source: 'Open-Meteo', model: 'ERA5', kind: 'model_reanalysis', time_zone: 'UTC', units: 'mm',
          note: 'A complete historical comparison is unavailable; no anomaly has been invented.',
        };
        return route.fulfill({ status: 200, contentType: 'application/json', json: value });
      }
      return route.continue();
    });
    const page = await context.newPage(); activePage = page;
    page.setDefaultTimeout(25000);
    page.on('pageerror', e => errors.push({ width, message: e.message }));
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.getByTestId('studio-ready').waitFor();
    const tabs = page.getByRole('tablist', { name: 'Workspace areas', exact: true });
    assert.equal(await tabs.getByRole('tab').count(), 4);
    assert.equal(calls.location, 0, 'No forecast before selecting a point');
    assert.equal(calls.history, 0, 'History must be explicit opt-in');

    const territory = page.getByTestId('area-tab-territory');
    await territory.focus(); await territory.press('ArrowRight');
    await page.locator('.qs-studio[data-active-area="anticipation"]').waitFor();
    assert(await page.getByTestId('area-tab-anticipation').evaluate(el => el === document.activeElement));
    assert.equal(await page.getByTestId('area-tab-anticipation').getAttribute('tabindex'), '0');
    assert.equal(await territory.getAttribute('tabindex'), '-1');
    await page.getByTestId('anticipation-unavailable').waitFor();
    await page.getByTestId('area-tab-anticipation').press('End');
    await page.locator('.qs-studio[data-active-area="evidence"]').waitFor();
    assert(await page.getByTestId('area-tab-evidence').evaluate(el => el === document.activeElement));
    await page.getByTestId('area-tab-evidence').press('Home');
    await page.locator('.qs-studio[data-active-area="territory"]').waitFor();
    assert(await territory.evaluate(el => el === document.activeElement));
    assert.notEqual(await territory.evaluate(el => getComputedStyle(el).outlineStyle), 'none', 'Keyboard focus must remain visible');
    assert.equal(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), true);
    assert.notEqual(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior), 'smooth');

    // Declared permission/error fixtures, never the user's device location.
    await page.getByTestId('geo-use-location').click();
    await page.evaluate(() => window.__qaGPS[0].error({code:1,message:'Denied fixture'}));
    assert.equal(calls.location, 0);
    await page.getByTestId('geo-use-location').click(); // deliberately late GPS
    await page.getByTestId('geo-manual').locator('summary').click();
    await page.getByTestId('geo-lat').fill('37.1');
    await page.getByTestId('geo-lon').fill('-4.6');
    await page.getByTestId('geo-load-context').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state') === 'ready');
    await page.waitForFunction(() => ['ready', 'unavailable'].includes(document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state')));
    const mapState = await page.getByTestId('geo-map-state').getAttribute('data-state');
    const mapNode = await page.getByTestId('geo-map-state').elementHandle();
    const canvas = await page.locator('.qg-map canvas').first().elementHandle();
    assert.equal(calls.location, 1, 'A manual forecast load must not double-fetch');
    await page.evaluate(() => window.__qaGPS[1].success({coords:{latitude:-33.45,longitude:-70.66,accuracy:10},timestamp:Date.now()}));
    assert.match(await page.getByTestId('geo-selected-point').innerText(), /37\.100, -4\.600/);
    assert.equal(calls.location, 1, 'Late GPS must not replace the manual point or load data');
    await page.getByTestId('area-tab-anticipation').click();
    await page.getByTestId('anticipation-indicators').waitFor();
    assert.match(await page.getByTestId('anticipation-difference').innerText(), /-11\.4/);
    assert.match(await page.getByTestId('anticipation-low-days').innerText(), /2/);
    assert.match(await page.getByTestId('anticipation-episodes').innerText(), /3/);
    assert.equal(calls.history, 0);
    await page.getByTestId('history-load').click();
    await page.getByTestId('history-result').waitFor();
    assert.match(await page.getByTestId('history-result').innerText(), /1991–2020/);
    assert.match(await page.getByTestId('history-result').innerText(), /-50\.0/);
    assert.equal(calls.history, 1);
    await page.screenshot({ path: `${output}/anticipation-fixture-${width}.png`, fullPage: true, animations: 'disabled' });
    await page.screenshot({ path: `${output}/anticipation-viewport-${width}.png`, animations: 'disabled' });

    await page.getByTestId('area-tab-guidance').click();
    await page.getByTestId('profile-select').selectOption('secano');
    await page.getByTestId('history-select').selectOption('leguminosa');
    await page.getByTestId('water-input').fill('70');
    await page.getByTestId('compare-button').click();
    await page.getByTestId('fit-count').filter({ hasText: /0/ }).waitFor();
    assert.equal(await page.getByTestId('plan-row').count(), 3);
    const resultText = await page.getByTestId('fit-count').innerText();
    await page.getByTestId('land-record-start').click();
    await page.getByTestId('land-record-cover').selectOption('mostly_bare');
    await page.getByTestId('land-record-note').fill('Synthetic QA note: revisit the same cover gaps.');
    await page.getByTestId('land-record-next-check').fill('Compare the same patch next visit.');
    assert.equal(verifiedProviderDisabled, true, 'Only a locally verified provider-disabled service may build a brief');
    await page.getByTestId('dryland-build').click();
    await page.locator('[data-testid="dryland-result"][data-phase="final"]').waitFor();
    assert.equal(await page.getByTestId('dryland-result').getAttribute('data-source'), 'fallback');
    const dryBands = [await page.getByTestId('vital-soil').innerText(), await page.getByTestId('vital-water').innerText()];
    const dryActions = await page.getByTestId('dryland-checklist').locator('li[data-action]').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-action')));
    assert(dryActions.includes('delay_sowing'));
    assert.equal(dryActions[0], 'zai_pits');
    await page.getByTestId('dryland-checklist').locator('input[type="checkbox"]').first().check();
    const beforeNavigation = { ...calls };
    for (const area of [...['territory', 'anticipation', 'evidence', 'guidance'], ...['territory', 'anticipation', 'evidence', 'guidance'], 'territory', 'guidance']) {
      await page.getByTestId(`area-tab-${area}`).click();
      await page.locator(`.qs-studio[data-active-area="${area}"]`).waitFor();
      assert.equal(await tabs.getByRole('tab', { selected: true }).count(), 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `No horizontal overflow at ${width}/${area}`);
    }
    assert.equal(calls.location, beforeNavigation.location, 'Navigation must not repeat forecast requests');
    assert.equal(calls.history, beforeNavigation.history, 'Navigation must not repeat historical requests');
    assert.equal(calls.deterministicBrief, beforeNavigation.deterministicBrief, 'Navigation must not rebuild the existing brief');
    assert.equal(await page.getByTestId('profile-select').inputValue(), 'secano');
    assert.equal(await page.getByTestId('history-select').inputValue(), 'leguminosa');
    assert.equal(await page.getByTestId('water-input').inputValue(), '70');
    assert.equal(await page.getByTestId('fit-count').innerText(), resultText);
    assert.equal(await page.getByTestId('land-record-note').inputValue(), 'Synthetic QA note: revisit the same cover gaps.');
    assert.equal(await page.getByTestId('land-record-cover').inputValue(), 'mostly_bare');
    assert.equal(await page.getByTestId('dryland-checklist').locator('input[type="checkbox"]').first().isChecked(), true, 'The brief checklist must survive area navigation');
    assert(await mapNode.evaluate(el => el.isConnected && el === document.querySelector('[data-testid="geo-map-state"]')));
    if (canvas) assert(await canvas.evaluate(el => el.isConnected && el === document.querySelector('.qg-map canvas')), 'Cesium canvas must not remount');
    await page.screenshot({ path: `${output}/guidance-state-${width}.png`, fullPage: true, animations: 'disabled' });
    await page.getByTestId('dryland-rain').click();
    assert.equal(await page.getByTestId('dryland-result').count(), 0, 'A changed brief scenario must clear the previous card');
    await page.getByTestId('dryland-build').click();
    await page.locator('[data-testid="dryland-result"][data-phase="final"]').waitFor();
    assert.equal(await page.getByTestId('dryland-result').getAttribute('data-source'), 'fallback');
    assert.deepEqual([await page.getByTestId('vital-soil').innerText(), await page.getByTestId('vital-water').innerText()], dryBands);
    const rainActions = await page.getByTestId('dryland-checklist').locator('li[data-action]').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-action')));
    assert(!rainActions.includes('delay_sowing'));
    assert.notDeepEqual(rainActions, dryActions);
    assert.equal(await page.getByTestId('dryland-checklist').locator('input[type="checkbox"]:checked').count(), 0);
    await page.getByTestId('area-tab-anticipation').click();
    await page.getByTestId('area-tab-guidance').click();
    assert.equal(await page.getByTestId('dryland-rain').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.getByTestId('dryland-result').getAttribute('data-source'), 'fallback');
    assert.equal(calls.deterministicBrief, 2);
    await page.getByTestId('area-tab-evidence').click();
    await page.getByRole('tab', { name: 'Evidence', exact: true }).click();
    await page.getByTestId('evidence-panel').waitFor();
    await page.getByRole('tab', { name: 'Evidence', exact: true }).press('ArrowRight');
    await page.getByTestId('decision-panel').waitFor();
    await page.getByTestId('area-tab-anticipation').click();
    await page.getByTestId('history-result').waitFor();

    historyMode = 'pending';
    await page.getByTestId('history-load').click();
    await pendingHistoryStarted;
    await page.getByTestId('area-tab-territory').click();
    await page.getByTestId('geo-lat').fill('37.2');
    await page.getByTestId('geo-load-context').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state') === 'ready');
    releasePendingHistory();
    await page.getByTestId('area-tab-anticipation').click();
    assert.equal(await page.getByTestId('history-result').count(), 0, 'New point clears the previous historical comparison');
    assert.equal(await page.getByTestId('historical-context').getByRole('alert').count(), 0, 'An aborted old history request must not show an error for the new point');
    assert.equal(await page.getByTestId('history-load').isEnabled(), true);
    historyMode = 'unavailable';
    await page.getByTestId('history-load').click();
    await page.getByTestId('history-unavailable').waitFor();
    assert.equal(await page.getByTestId('history-result').count(), 0, 'Missing history is not a zero anomaly');
    historyMode = 'error';
    await page.getByTestId('history-load').click();
    await page.getByTestId('historical-context').getByRole('alert').waitFor();
    assert.equal(await page.getByTestId('history-result').count(), 0);

    forecastMode = 'invalid';
    await page.getByTestId('area-tab-territory').click();
    await page.getByTestId('geo-lat').fill('37.3');
    await page.getByTestId('geo-load-context').click();
    await page.getByRole('alert').filter({ hasText: 'anticipation units' }).waitFor();
    await page.getByTestId('area-tab-anticipation').click();
    await page.getByTestId('anticipation-unavailable').waitFor();
    assert.equal(await page.getByTestId('anticipation-indicators').count(), 0, 'Invalid indicator units must not render numbers');
    assert.equal(calls.location, 3, 'Exactly three explicit loads, no duplicate forecast requests');
    forecastMode = 'ready';
    await page.getByTestId('area-tab-territory').click();
    for (const sceneMode of ['2d','3d']) {
      if (await page.getByTestId('geo-map-state').getAttribute('data-scene-mode') !== sceneMode) {
        await page.getByRole('button',{name:sceneMode === '3d' ? 'Switch to 3D view' : 'Switch to 2D view'}).click();
        await page.waitForFunction(mode => document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-scene-mode') === mode && document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-morphing') === 'false', sceneMode);
      }
      await page.getByTestId('geo-select-map').click();
      const beforePick = calls.location;
      const canvasBox = await page.locator('.qg-map canvas').first().boundingBox();
      await page.locator('.qg-map canvas').first().click({position:{x:canvasBox.width/2,y:canvasBox.height/2}});
      await page.locator('[data-testid="geo-point-review"][data-confirmed="false"]').waitFor();
      assert.equal(calls.location,beforePick,'A map draft must not request forecast');
      await page.getByTestId('geo-confirm-point').click();
      await page.waitForFunction(() => document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state') === 'ready');
      assert.equal(calls.location,beforePick+1,'Map confirmation requests one forecast');
      assert.equal(await page.getByTestId('geo-selected-point').getAttribute('data-source'),'map');
    }
    const beforeGPS = calls.location;
    await page.getByTestId('geo-use-location').click();
    await page.evaluate(() => window.__qaGPS.at(-1).success({coords:{latitude:-33.45,longitude:-70.66,accuracy:10},timestamp:Date.now()}));
    assert.match(await page.getByTestId('geo-point-review').innerText(), /-33\.45000, -70\.66000/);
    assert.equal(calls.location,beforeGPS,'GPS draft requires confirmation');
    await page.getByTestId('geo-confirm-point').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state') === 'ready');
    assert.equal(calls.location,beforeGPS+1);
    await page.screenshot({path:`${output}/territorial-journey-${width}.png`,fullPage:true,animations:'disabled'});
    assert.equal(calls.history,4,'Selection and GPS must not load history');
    assert.equal(calls.model, 0, 'No model endpoint should be requested');
    checks.push({ width, primaryAreas: 4, keyboardRoving: true, focusVisible: true, reducedMotion: true, preservedScenarioResultAndRecord: true,
      gpsDenied:true, lateGpsPreservesManual:true, gpsRequiresConfirmation:true, map2DAnd3DPicking:true, threeExplicitLoadsExactlyThreeRequests:true, mapState, sameMapNode: true, sameCesiumCanvas: Boolean(canvas), navigationRefetches: 0, historyExplicit: true,
      realDisabledProviderBriefs: calls.deterministicBrief, dryRainBriefPreserved: true, checklistPreservedAcrossAreas: true,
      staleHistoryRequestIgnored: true, missingHistoryNotZero: true, historyErrorHandled: true, invalidAnticipationRejected: true, modelRequests: calls.model, fixtureLocationRequests: calls.location,
      fixtureHistoryRequests: calls.history, blockedExternalRequests: calls.blockedExternal, horizontalOverflow: false });
    checks[checks.length - 1].fixtureLocationSequence = locationSequence;
    await context.close();
  }
  assert.equal(errors.length, 0, JSON.stringify(errors));
  const result = { status: 'PASS', target, scope: 'Declared browser-only environmental fixtures; real local bootstrap/comparison and provider-disabled briefs. No field measurements, real external sources, GPS or model requests.', checks, errors };
  writeFileSync(`${output}/checks.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  if (activePage && !activePage.isClosed()) await activePage.screenshot({ path: `${output}/failure.png`, fullPage: true, animations: 'disabled' }).catch(() => {});
  writeFileSync(`${output}/failure.json`, JSON.stringify({ error: String(error), checks, errors }, null, 2));
  console.error(error); process.exitCode = 1;
} finally { await browser.close(); }
