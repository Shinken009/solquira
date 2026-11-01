// Browser-only fault injection. No real forecast, GPS or model requests.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.argv[2] || 'http://127.0.0.1:8036');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || !['http:', 'https:'].includes(target.protocol)) throw new Error('Only a loopback Studio may be tested.');
const output = resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/release-2026-10-27/browser/recovery');
mkdirSync(output, { recursive: true });
const checks = [], errors = [], external = [], agentRequests = [];
let bootstraps = 0, locationRequests = 0, outcome = 'provider_error', nextStatus = 0;
const now = new Date().toISOString(), day = index => new Date(Date.parse(now.slice(0, 10)) + index * 86400000).toISOString().slice(0, 10);
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on('pageerror', error => errors.push(error.message));
await context.route('**/*', async route => {
  const url = new URL(route.request().url());
  if (url.origin !== target.origin && !['data:', 'blob:'].includes(url.protocol)) { external.push(url.origin); await route.abort(); return; }
  await route.continue();
});

function fixtureContext(point) {
  const ref = `slq-context-${String(locationRequests).padStart(32, 'A')}`;
  return { schema_version: 'solquira.location/1', context_ref: ref, location: { ...point, precision_degrees: .001 }, retrieved_at: now,
    weather: { state: 'ready', source: 'Open-Meteo', kind: 'model_forecast', time_zone: 'UTC', days: Array.from({ length: 7 }, (_, i) => ({ date: day(i), temperature_max_c: 28, temperature_min_c: 12, precipitation_mm: 0, reference_et0_mm: 4 })) },
    imagery: { state: 'disabled', source: 'NASA GIBS', date: day(0), layer: 'MODIS_Terra_CorrectedReflectance_TrueColor', projection: 'EPSG:3857', tile_matrix_set: 'GoogleMapsCompatible_Level9', max_level: 9,
      url_template: `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${day(0)}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg` } };
}
let attachedFixture;
function fixtureBrief(reference) {
  return { schema_version: 'solquira.field-brief/1', context_ref: reference, focus: 'resilience', location: attachedFixture.location, created_at: now,
    summary: 'Browser test fixture: compare forecast dates with field observations.', pattern: 'rain_below_reference_et0',
    forecast: { source: 'Open-Meteo', kind: 'model_forecast', time_zone: 'UTC', retrieved_at: now, period_start: day(0), period_end: day(6), rain_mm: 0, reference_et0_mm: 28, temperature_min_c: 12, temperature_max_c: 28, days_without_rain: 7 },
    sources: [{ id: 'forecast-7d', label: 'Browser fixture forecast', kind: 'model_forecast', url: 'https://open-meteo.com/' }, { id: 'field-inputs', label: 'Missing field inputs', kind: 'missing_inputs' }],
    actions: [{ id: 'cover', title: 'Review field cover', detail: 'Browser fixture only.', evidence_ids: ['field-inputs'], destination: 'soil' }, { id: 'weather', title: 'Review weather dates', detail: 'Browser fixture only.', evidence_ids: ['forecast-7d'], destination: 'weather' }, { id: 'inputs', title: 'Bring field observations', detail: 'Browser fixture only.', evidence_ids: ['field-inputs'], destination: 'question' }],
    missing_inputs: ['Observed soil moisture'], external_actions_executed: false };
}

await page.route('**/solquira-api/studio/bootstrap', async route => {
  const upstream = await route.fetch({ maxRedirects: 0 });
  if (!upstream.ok()) throw new Error(`Local bootstrap failed: ${upstream.status()}`);
  const data = await upstream.json(); bootstraps++;
  // UI injection only: no provider is installed or contacted by this test.
  data.inference = { available: true, mode: 'nebius_live', provider: 'nebius', model: 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B' };
  data.geodata = { enabled: true };
  await route.fulfill({ response: upstream, json: data });
});
await page.route('**/solquira-api/studio/location', async route => {
  locationRequests++; attachedFixture = fixtureContext(route.request().postDataJSON());
  await route.fulfill({ json: attachedFixture });
});
await page.route('**/solquira-api/studio/agent', async route => {
  const request = route.request().postDataJSON(); agentRequests.push(request);
  if (nextStatus) { const status = nextStatus; nextStatus = 0; await route.fulfill({ status, json: { detail: 'Browser-injected session admission failure' } }); return; }
  // Obtain an actual local deterministic comparison, never invoke /agent upstream.
  const upstream = await route.fetch({ url: new URL('/solquira-api/studio/evaluate', target).href, method: 'POST', postData: JSON.stringify(request.scenario), maxRedirects: 0 });
  if (!upstream.ok()) throw new Error(`Local comparison failed: ${upstream.status()}`);
  const data = await upstream.json();
  data.run = { inference_mode: 'local_fallback', outcome, model: null, trace: [{ tool: 'read_landscape_context', status: 'executed' }] };
  if (request.context_ref) data.report.field_brief = fixtureBrief(request.context_ref);
  await route.fulfill({ response: upstream, json: data });
});

async function attach() {
  await page.getByTestId('geo-load-context').click();
  await page.getByTestId('geo-attach').waitFor();
  await page.getByTestId('geo-attach').click();
  await page.getByTestId('attached-context').waitFor();
}
async function submit() {
  const response = page.waitForResponse(value => value.url().endsWith('/studio/agent'));
  await page.getByTestId('agent-submit').click(); await response;
}
try {
  await page.goto(target.href, { waitUntil: 'networkidle' });
  await page.getByTestId('studio-ready').waitFor();
  if (!(await page.getByTestId('connection-mode').innerText()).includes('Demo · prepared examples')) throw new Error('Hosted-safe Studio status missing.');
  await attach();
  await page.getByTestId('water-input').fill('70');
  if (await page.getByTestId('plan-row').count()) throw new Error('Changed example retained a comparison.');
  if (!(await page.getByTestId('agent-query').isEnabled())) throw new Error('Attached forecast incorrectly depends on completed example comparison.');
  await page.getByTestId('agent-query').fill('What should I check about soil cover?');
  await submit(); await page.getByTestId('field-brief').waitFor();
  if (agentRequests.at(-1).scenario.water_percent !== 70 || agentRequests.at(-1).context_ref !== attachedFixture.context_ref) throw new Error('Field question lost current scenario or attached context.');
  checks.push('Attached forecast remains actionable after editing separate crop example');
  for (const [code, label] of [['provider_error', 'AI response unavailable'], ['timeout', 'AI response timed out'], ['trial_limit', 'AI request limit reached'], ['budget_exceeded', 'AI request limit reached']]) {
    outcome = code; await submit();
    await page.getByTestId('agent-fallback-notice').filter({ hasText: label }).waitFor();
    if (await page.getByTestId('connection-ai').getAttribute('data-state') !== 'unavailable') throw new Error(`Stale model availability for ${code}`);
    if (!(await page.getByTestId('connection-ai').innerText()).includes(label)) throw new Error(`Missing outcome status for ${code}`);
    if (!(await page.getByTestId('field-brief').isVisible())) throw new Error('Useful field checks disappeared with provider failure.');
  }
  checks.push('Provider failure, timeout and request/tool limits replace stale bootstrap availability while preserving field checks');
  for (const status of [401, 403]) {
    nextStatus = status; await submit();
    await page.getByTestId('agent-reconnect').waitFor();
    const previous = bootstraps;
    const renewed = page.waitForResponse(value => value.url().endsWith('/studio/bootstrap'));
    await page.getByTestId('agent-reconnect').click();
    await renewed;
    await page.getByTestId('studio-ready').waitFor();
    if (bootstraps <= previous) throw new Error('Recovery did not renew bootstrap.');
    if (await page.getByTestId('attached-context').count() || await page.getByTestId('field-brief').count()) throw new Error('Recovery retained stale context or answer.');
    await attach(); outcome = 'provider_error'; await submit();
    await page.getByTestId('field-brief').waitFor();
    if (agentRequests.at(-1).context_ref !== attachedFixture.context_ref) throw new Error('New session reused old forecast reference.');
  }
  checks.push('401 and 403 offer inline session recovery, clear stale context and permit explicit reload/reattach');
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error('Recovery UI overflows at 390px.');
  if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }));
  const report = { status: 'PASS', scope: 'Browser-only provider/status/session fault injection and synthetic forecast fixtures; actual local comparison API; zero upstream agent/model, forecast or GPS calls', checks, bootstraps, locationFixtures: locationRequests, agentInterceptions: agentRequests.length, pageErrors: errors, externalRequests: external };
  writeFileSync(resolve(output, 'recovery.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  writeFileSync(resolve(output, 'recovery.json'), JSON.stringify({ status: 'FAIL', error: String(error), checks, pageErrors: errors, externalRequests: external }, null, 2));
  throw error;
} finally { await context.close(); await browser.close(); }
