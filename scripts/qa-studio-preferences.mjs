// Loopback-only UI regression. Declared device-location fixture; no real GPS,
// source acquisition, history or inference. Preferences never send requests.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const target = process.argv[2] || 'http://127.0.0.1:5179';
const origin = new URL(target).origin;
if (!['127.0.0.1', 'localhost'].includes(new URL(target).hostname)) throw Error('Loopback QA only');
const output = resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/playwright/studio-preferences-20261102');
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ args: ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const checks = [], errors = [];
let activePage;

// Measures visible text with solid/composited ancestor backgrounds. It does
// not claim physical-device, image-background or full WCAG conformance.
function textContrast() {
  const rgba = value => {
    const m = value.match(/[\d.]+/g)?.map(Number) || [];
    return [m[0] || 0, m[1] || 0, m[2] || 0, m[3] ?? 1];
  };
  const over = (fg, bg) => fg.slice(0, 3).map((v, i) => v * fg[3] + bg[i] * (1 - fg[3])).concat(1);
  const background = el => {
    const chain = []; for (let node = el; node; node = node.parentElement) chain.unshift(node);
    return chain.reduce((bg, node) => over(rgba(getComputedStyle(node).backgroundColor), bg), [255, 255, 255, 1]);
  };
  const luminance = color => color.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const results = [];
  for (const el of document.querySelectorAll('.qs-studio :is(h1,h2,h3,h4,p,span,small,strong,label,legend,button,summary,a,dt,dd,li)')) {
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) || !el.getClientRects().length) continue;
    if (![...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim())) continue;
    if (el.closest('.cesium-credit-logoContainer')) continue;
    const style = getComputedStyle(el), bg = background(el), fg = over(rgba(style.color), bg);
    const l = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
    const ratio = (l[0] + .05) / (l[1] + .05);
    const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700);
    results.push({ text: el.textContent.trim().replace(/\s+/g, ' ').slice(0, 100), className: el.className, ratio: Number(ratio.toFixed(2)), required: large ? 3 : 4.5 });
  }
  return { measured: results.length, failures: results.filter(row => row.ratio + .01 < row.required) };
}

try {
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, locale: 'es-CL', reducedMotion: 'reduce' });
    await context.addInitScript(() => {
      window.__fixtureGpsCalls = 0;
      Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition(success) {
        window.__fixtureGpsCalls++;
        success({ coords: { latitude: -30.123456, longitude: -71.123456, accuracy: 12 }, timestamp: Date.now() });
      } } });
    });
    const calls = { bootstrap: 0, evaluate: 0, forecast: 0, inventory: 0, history: 0, model: 0, blockedExternal: 0 };
    await context.route('**/*', async route => {
      const url = new URL(route.request().url()), path = url.pathname;
      if (url.origin !== origin && !['blob:', 'data:'].includes(url.protocol)) { calls.blockedExternal++; return route.abort(); }
      if (path.endsWith('/studio/agent') || path.endsWith('/v1/field-brief')) { calls.model++; return route.abort(); }
      if (path.endsWith('/studio/location')) { calls.forecast++; return route.abort(); }
      if (path.endsWith('/studio/history')) { calls.history++; return route.abort(); }
      if (path.endsWith('/studio/evidence')) { calls.inventory++; return route.abort(); }
      if (path.endsWith('/studio/evaluate')) calls.evaluate++;
      if (path.endsWith('/studio/bootstrap')) {
        calls.bootstrap++;
        const response = await route.fetch(), boot = await response.json();
        assert.equal(boot.inference.available, false); assert.equal(boot.inference.mode, 'disabled');
        assert.equal(boot.geodata.enabled, false);
        return route.fulfill({ response });
      }
      return route.continue();
    });
    const page = await context.newPage(); activePage = page; page.setDefaultTimeout(30000);
    page.on('pageerror', e => errors.push({ width, message: e.message }));
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.getByTestId('studio-ready').waitFor();
    await page.waitForFunction(() => document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state') === 'ready');
    assert.equal(await page.locator('html').getAttribute('lang'), 'es');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    assert.equal(await page.getByTestId('area-tab-territory').locator('strong').innerText(), 'Territorio');
    assert.doesNotMatch(await page.getByTestId('connection-mode').innerText(), /demo|prepared examples/i);
    const canvas = await page.locator('.qg-map canvas').first().elementHandle(); assert(canvas);
    await page.getByTestId('geo-use-location').click();
    await page.locator('[data-testid="geo-point-review"][data-confirmed="false"]').waitFor();
    const point = { latitude: await page.getByTestId('geo-lat').inputValue(), longitude: await page.getByTestId('geo-lon').inputValue() };
    assert.deepEqual(point, { latitude: '-30.123456', longitude: '-71.123456' });
    assert.equal(calls.forecast, 0);
    const before = { ...calls };
    const combinations = [];
    for (const [locale, theme] of [['es', 'light'], ['es', 'dark'], ['en', 'dark'], ['en', 'light']]) {
      await page.getByTestId(`locale-${locale}`).click();
      await page.getByTestId(`theme-${theme}`).click();
      assert.equal(await page.locator('html').getAttribute('lang'), locale);
      assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
      assert.equal(await page.evaluate(() => document.documentElement.style.colorScheme), theme);
      const labels = locale === 'es' ? ['Territorio', 'Anticipación', 'Orientación', 'Evidencia'] : ['Territory', 'Anticipation', 'Guidance', 'Evidence'];
      const contrast = [];
      for (const [i, area] of ['territory', 'anticipation', 'guidance', 'evidence'].entries()) {
        await page.getByTestId(`area-tab-${area}`).click();
        assert.equal(await page.getByTestId(`area-tab-${area}`).locator('strong').innerText(), labels[i]);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${width}/${locale}/${theme}/${area} overflow`);
        const measured = await page.evaluate(textContrast); contrast.push({ area, ...measured });
        await page.screenshot({ path: `${output}/${width}-${locale}-${theme}-${area}.png`, animations: 'disabled' });
      }
      await page.getByTestId('area-tab-territory').click();
      assert(await canvas.evaluate(node => node.isConnected && node === document.querySelector('.qg-map canvas')), 'Preferences and area navigation must preserve Cesium canvas');
      assert.equal(await page.getByTestId('geo-lat').inputValue(), point.latitude);
      assert.equal(await page.getByTestId('geo-lon').inputValue(), point.longitude);
      assert.equal(await page.getByTestId('geo-point-review').getAttribute('data-confirmed'), 'false');
      assert.equal(await page.getByTestId('geo-methods').getAttribute('open'), null);
      assert.equal(await page.getByTestId('geo-map-layers').getAttribute('open'), null);
      assert(await page.getByTestId('geo-map-attribution').isVisible());
      assert.equal(await page.getByTestId('geo-nasa-toggle').count(), 0);
      // The declared GPS draft can still be rendering blocked reference-map
      // tiles. Assert application acquisitions separately from raster retries.
      for (const key of ['bootstrap', 'evaluate', 'forecast', 'inventory', 'history', 'model'])
        assert.equal(calls[key], before[key], `${key}: preferences/navigation never acquire application data or call a model`);
      combinations.push({ locale, theme, contrast });
    }
    // Explicit example links remain reachable even when examples start collapsed.
    await page.getByRole('link', { name: 'Try a crop example', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid="prepared-comparison-examples"]')?.open);
    assert.equal(await page.getByTestId('prepared-comparison-examples').getAttribute('open'), '');
    await page.getByTestId('profile-select').waitFor({ state: 'visible' });
    await page.getByTestId('profile-select').selectOption('secano');
    await page.getByTestId('area-tab-territory').click();
    await page.getByTestId('locale-es').click(); await page.getByTestId('theme-dark').click();
    assert.equal(await page.getByTestId('profile-select').inputValue(), 'secano');
    assert.equal(await page.getByTestId('geo-lat').inputValue(), point.latitude);
    await page.reload({ waitUntil: 'networkidle' }); await page.getByTestId('studio-ready').waitFor();
    assert.equal(await page.locator('html').getAttribute('lang'), 'es');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    assert.deepEqual(await page.evaluate(() => Object.keys(localStorage).sort()), ['solquira.locale', 'solquira.theme']);
    assert.equal(await page.evaluate(() => window.__fixtureGpsCalls), 0, 'Reload never reacquires location');
    for (const key of ['forecast', 'inventory', 'history', 'model']) assert.equal(calls[key], 0);
    checks.push({ width, navigatorSpanishDefault: true, explicitLightDefault: true, draftPreserved: true, sameCanvas: true, preferencesPersistAfterReload: true, noFieldDataPersisted: true, explicitExampleLink: true, calls, combinations });
    await context.close();
  }
  assert.deepEqual(errors, []);
  const failedContrast = checks.flatMap(c => c.combinations.flatMap(combo => combo.contrast.flatMap(area => area.failures.map(f => ({ width: c.width, locale: combo.locale, theme: combo.theme, area: area.area, ...f })))));
  writeFileSync(`${output}/contrast.json`, JSON.stringify(failedContrast, null, 2));
  assert.deepEqual(failedContrast, [], 'Visible measured text must meet AA contrast');
  const result = { status: 'PASS', target, scope: 'Loopback real provider-disabled bootstrap; declared device-location fixture, external requests blocked; no actual GPS, source acquisition or inference.', checks, errors };
  writeFileSync(`${output}/checks.json`, JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2));
} catch (error) {
  if (activePage && !activePage.isClosed()) await activePage.screenshot({ path: `${output}/failure.png`, fullPage: true, animations: 'disabled' }).catch(() => {});
  writeFileSync(`${output}/failure.json`, JSON.stringify({ error: String(error), checks, errors }, null, 2)); console.error(error); process.exitCode = 1;
} finally { await browser.close(); }
