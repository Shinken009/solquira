// Local user notes only. No forecast, GPS or model calls are allowed.
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const { chromium } = createRequire(import.meta.url)('playwright');
const target = process.argv[2] || 'http://127.0.0.1:8041/';
const origin = new URL(target).origin;
if (!['127.0.0.1', 'localhost'].includes(new URL(target).hostname)) throw Error('Loopback QA only');
const output = resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/release-2026-10-27/browser/land-record');
mkdirSync(output, { recursive: true });
const browser = await chromium.launch(), checks = [], errors = [];
try {
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, acceptDownloads: true, reducedMotion: 'reduce' });
    await context.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.origin !== origin && !['blob:', 'data:'].includes(u.protocol)) return route.abort();
      return route.continue();
    });
    const page = await context.newPage(), requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.url().includes('/studio/')) requests.push(request.url()); });
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.getByTestId('studio-ready').waitFor();
    const before = requests.length;
    await page.getByTestId('land-record-start').click();
    if (await page.getByTestId('land-record-download').isEnabled()) throw Error('Incomplete record can be downloaded');
    await page.getByTestId('land-record-cover').selectOption('mostly_bare');
    await page.getByTestId('land-record-runoff').selectOption('channels');
    await page.getByTestId('land-record-constraint').selectOption('cost');
    await page.getByTestId('land-record-support').selectOption('grower_group');
    await page.getByTestId('land-record-note').fill('Repeat the same boundary walk with the grower group.');
    await page.getByTestId('land-record-next-check').fill('Compare cover gaps at the next visit.');
    const observed = await page.getByTestId('land-record-observed').inputValue();
    await page.getByTestId('land-record-followup').fill(observed);
    const questions = await page.getByTestId('land-record-questions').innerText();
    if (!questions.includes('bare ground') || !questions.includes('cost')) throw Error('Discussion prompts are not linked to chosen entries');
    const downloaded = page.waitForEvent('download');
    await page.getByTestId('land-record-download').click();
    const download = await downloaded, path = await download.path(), text = readFileSync(path, 'utf8');
    for (const expected of ['Mostly bare ground', 'Small channels or rills', 'Grower group', 'Compare cover gaps', 'Location: not included', 'https://www.unccd.int/', 'https://www.ipcc.ch/']) {
      if (!text.includes(expected)) throw Error(`Missing record content: ${expected}`);
    }
    if (requests.length !== before) throw Error('The local record triggered API work');
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw Error(`Record overflow at${width}`);
    await page.getByTestId('land-care-record').screenshot({ path: `${output}/land-record-${width}.png`, animations: 'disabled' });
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByTestId('studio-ready').waitFor();
    await page.getByTestId('land-record-start').click();
    if (await page.getByTestId('land-record-note').inputValue()) throw Error('Notes persisted without an explicit persistence contract');
    checks.push({ width, tailoredQuestions: true, download: true, extraApiRequests: 0, notesPersisted: false, overflow: false });
    await context.close();
  }
  if (errors.length) throw Error(errors.join(';'));
  const result = { status: 'PASS', target, checks, errors, scope: 'Local-only record; synthetic notes, no real field observations or model requests' };
  writeFileSync(`${output}/checks.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
} finally { await browser.close(); }
