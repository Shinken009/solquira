import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const { chromium } = createRequire(import.meta.url)('playwright');
const target = process.argv[2] || 'http://127.0.0.1:8036';
const mode = process.argv[3] || 'api';
const parsed = new URL(target);
if (!['127.0.0.1', 'localhost'].includes(parsed.hostname) || !['http:', 'https:'].includes(parsed.protocol)) throw new Error('QA is restricted to a loopback target.');
const output = resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/playwright/studio-2026-10-25');
mkdirSync(output, { recursive: true });
const errors = [], external = [], checks = [];
const browser = await chromium.launch();
try {
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: width > 1000 ? 1000 : 844 }, reducedMotion: 'reduce' });
    await context.route('**/*', async route => {
      const u = new URL(route.request().url());
      if (u.origin !== parsed.origin && !['data:', 'blob:'].includes(u.protocol)) {
        external.push(u.origin); await route.abort(); return;
      }
      await route.continue();
    });
    const requests = [];
    context.on('request', request => { if (request.url().includes('/studio/')) requests.push({ method: request.method(), path: new URL(request.url()).pathname }); });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.getByTestId('studio-ready').waitFor();
    if (await page.locator('html').getAttribute('lang') !== 'en') throw new Error('Document language is not English');
    await page.screenshot({ path: `${output}/${mode}-${width}.png`, fullPage: true, animations: 'disabled' });
    await page.screenshot({ path: `${output}/${mode}-${width}-viewport.png`, fullPage: false, animations: 'disabled' });
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error(`Horizontal overflow at ${width}px`);
    if (!await page.getByTestId('connection-mode').isVisible()) throw new Error('Data/connection provenance is not visible');
    await page.getByTestId('connection-panel').waitFor();
    const oversizedIcons = await page.locator('.qs-studio svg.lucide').evaluateAll(nodes => nodes.filter(node => { const box = node.getBoundingClientRect(); return box.width > 64 || box.height > 64; }).length);
    if (oversizedIcons) throw new Error('An illustration sizing rule leaked into interface icons');
    const actionContrast = await page.locator('.qs-hero-cta, .qs-primary').evaluateAll(nodes => {
      const luminance = color => {
        const channels = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
        return channels.reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
      };
      return nodes.map(node => { const style = getComputedStyle(node), a = luminance(style.color), b = luminance(style.backgroundColor); return { action: node.textContent.trim(), ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) }; });
    });
    if (actionContrast.some(value => value.ratio < 4.5)) throw new Error(`Insufficient primary action contrast: ${JSON.stringify(actionContrast)}`);
    for (const id of ['connection-engine', 'connection-tools', 'connection-ai']) {
      if (!await page.getByTestId(id).isVisible()) throw new Error(`Missing useful connection state: ${id}`);
    }
    if (await page.getByTestId('tool-checks').count()) throw new Error('Completed tool checks appeared before an explanation was requested');
    await page.getByTestId('connection-panel').screenshot({ path: `${output}/${mode}-connections-${width}.png`, animations: 'disabled' });
    await page.getByRole('tab', { name: 'Evidence', exact: true }).click();
    await page.getByTestId('evidence-panel').waitFor();
    await page.getByRole('tab', { name: 'Evidence', exact: true }).press('ArrowRight');
    await page.getByTestId('decision-panel').waitFor();
    await page.getByRole('tab', { name: 'Soil & cover', exact: true }).click();
    await page.getByTestId('land-panel').waitFor();
    // Native labels and test IDs are shared across layouts.
    await page.getByTestId('history-select').selectOption('leguminosa');
    await page.getByTestId('water-input').fill('40');
    if (await page.getByTestId('plan-row').count()) throw new Error('Old comparison remained visible after inputs changed');
    await page.getByTestId('compare-button').click();
    await page.getByTestId('fit-count').filter({ hasText: /0/ }).waitFor();
    if (await page.getByTestId('plan-row').count() !== 3) throw new Error('Expected three actual plans');
    if (mode === 'api') {
      await page.getByTestId('agent-query').fill('Explain the water limits and what soil evidence is missing.');
      await page.getByTestId('agent-submit').click();
      await page.getByTestId('agent-result').waitFor();
      const text = await page.getByTestId('agent-result').innerText();
      if (!text.trim()) throw new Error('Empty assistant result');
      if (/nebius_live|live model connected/i.test(text)) throw new Error('Offline QA must not claim live inference');
      const steps = page.getByTestId('tool-step-link');
      if (await steps.count() !== 5) throw new Error('Expected five navigable checks from the actual tool trace');
      await steps.nth(2).click();
      await page.getByTestId('evidence-panel').waitFor();
      await steps.nth(3).click();
      await page.getByTestId('land-panel').waitFor();
      await steps.nth(4).click();
      await page.getByTestId('decision-panel').waitFor();
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error(`Tool guidance overflow at ${width}px`);
      await page.getByTestId('agent-result').screenshot({ path: `${output}/${mode}-explanation-${width}.png`, animations: 'disabled' });
      await page.getByTestId('connection-panel').screenshot({ path: `${output}/${mode}-connections-returned-${width}.png`, animations: 'disabled' });
      if (!requests.some(r => r.method === 'POST' && r.path.endsWith('/studio/agent'))) throw new Error('Assistant did not reach the real local API');
    } else {
      if (await page.getByTestId('agent-query').count()) throw new Error('Static example must not pretend to accept an AI query');
      await page.getByTestId('offline-explanation').waitFor();
      if (requests.length) throw new Error('Static example unexpectedly requested a Studio API');
    }
    const duplicates = await page.locator('[id]').evaluateAll(nodes => {
      const ids = nodes.map(n => n.id); return ids.length - new Set(ids).size;
    });
    if (duplicates) throw new Error('Duplicate document IDs');
    const focusable = page.getByTestId('compare-button');
    await focusable.focus();
    // :focus-visible follows keyboard modality, not a programmatic mouse-era focus.
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    if (!await focusable.evaluate(el => el === document.activeElement)) throw new Error('Primary action cannot receive keyboard focus');
    if (await focusable.evaluate(el => getComputedStyle(el).outlineStyle === 'none')) throw new Error('Primary action lacks a visible focus outline');
    // Saved result is never silently presented as a new API/AI result after disconnect.
    if (mode === 'api') {
      await context.setOffline(true);
      await page.getByTestId('water-input').fill('100');
      await page.getByTestId('compare-button').click();
      await page.getByRole('alert').first().waitFor();
      await context.setOffline(false);
    }
    await page.getByRole('link', { name: 'About Solquira', exact: true }).click();
    if (!await page.locator('#qs-about').evaluate(el => { const box = el.getBoundingClientRect(); return box.top < window.innerHeight && box.bottom > 0; })) throw new Error('About navigation did not reach the project story');
    checks.push({ width, scenario: 'valle:40:leguminosa', plans: 3, expectedFits: 0, language: 'en', overflow: false, actionContrast, connectionStates: 3, navigableToolChecks: mode === 'api' ? 5 : 0, keyboardTabs: true, staleComparisonCleared: true, mode, studioRequests: requests });
    await context.close();
  }
  if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }));
  writeFileSync(`${output}/${mode}-checks.json`, JSON.stringify({ target, checks, errors, externalRequests: external }, null, 2));
  console.log(JSON.stringify({ status: 'PASS', target, output, checks, errors, externalRequests: external }, null, 2));
} catch (error) {
  writeFileSync(`${output}/${mode}-failure.json`, JSON.stringify({ error: String(error), errors, external }, null, 2));
  console.error(error); process.exitCode = 1;
} finally { await browser.close(); }
