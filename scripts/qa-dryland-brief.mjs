// Local-only QA for the dryland field brief card. No provider, no external network.
// Usage: node scripts/qa-dryland-brief.mjs [http://127.0.0.1:8036]
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const { chromium } = createRequire(import.meta.url)("playwright");
const target = new URL(process.argv[2] || "http://127.0.0.1:8036");
if (
  !["127.0.0.1", "localhost"].includes(target.hostname) ||
  !["http:", "https:"].includes(target.protocol)
)
  throw new Error("Only a loopback Studio may be tested.");
const output = resolve(
  process.env.SOLQUIRA_QA_OUTPUT || "output/phase-b-dryland",
);
mkdirSync(output, { recursive: true });

const checks = [],
  pageErrors = [],
  external = [],
  modelRequests = [];
const check = (name, ok, detail = "") => {
  checks.push({ name, ok: Boolean(ok), detail });
};

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: {
    width: Number(process.env.SOLQUIRA_QA_WIDTH || 390),
    height: 1000,
  },
  reducedMotion: process.env.SOLQUIRA_QA_MOTION || "reduce",
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
page.on("pageerror", (error) => pageErrors.push(error.message));
await context.route("**/*", async (route) => {
  const url = new URL(route.request().url());
  if (
    url.origin !== target.origin &&
    !["data:", "blob:"].includes(url.protocol)
  ) {
    external.push(url.origin);
    await route.abort();
    return;
  }
  if (url.pathname === "/v1/field-brief")
    modelRequests.push(route.request().postDataJSON());
  await route.continue();
});

try {
  await page.goto(target.href, { waitUntil: "domcontentloaded" });
  // --- The start is neutral: no place is claimed on the person's behalf ------
  const workspace = page.getByTestId("dryland-workspace");
  await workspace.scrollIntoViewIfNeeded();
  await workspace.waitFor();
  check(
    "no place is preselected for the visitor",
    (await workspace.locator(".qd-example").innerText()).trim() ===
      "Choose a reference point",
    await workspace.locator(".qd-example").innerText(),
  );
  check(
    "the brief offers the four prepared references",
    (await page.getByTestId("dryland-reference-picker").isVisible()) &&
      (await page
        .getByTestId("dryland-reference-picker")
        .locator("button")
        .count()) === 4,
  );
  check(
    "the brief cannot build without a reference",
    await page.getByTestId("dryland-build").isDisabled(),
  );
  await page.getByTestId("dryland-reference-ouagadougou").click();
  await page
    .getByTestId("dryland-reference-picker")
    .waitFor({ state: "hidden" });
  check(
    "choosing a reference names it in the brief",
    (await workspace.locator(".qd-example").innerText()).trim() ===
      "Sahel reference",
  );
  await page
    .waitForFunction(
      () => {
        const lat = document.querySelector('[data-testid="geo-lat"]');
        const lon = document.querySelector('[data-testid="geo-lon"]');
        return (
          lat instanceof HTMLInputElement &&
          lon instanceof HTMLInputElement &&
          lat.value === "12.371" &&
          lon.value === "-1.519"
        );
      },
      undefined,
      { timeout: 10000 },
    )
    .catch(() => undefined);
  check(
    "choosing a reference moves the map to its point",
    (await page.getByTestId("geo-lat").inputValue()) === "12.371" &&
      (await page.getByTestId("geo-lon").inputValue()) === "-1.519",
    `${await page.getByTestId("geo-lat").inputValue()},${await page.getByTestId("geo-lon").inputValue()}`,
  );
  check("workspace mounts", await workspace.isVisible());
  check(
    "no model request before an explicit build",
    modelRequests.length === 0,
    `${modelRequests.length} early requests`,
  );
  check(
    "card absent before build",
    (await page.getByTestId("dryland-result").count()) === 0,
  );

  // --- Dry week: the deterministic brief -----------------------------------
  await page.getByTestId("dryland-build").click();
  const result = page.getByTestId("dryland-result");
  await result.waitFor();
  check("dry brief renders", await result.isVisible());
  const source = await page.getByTestId("dryland-source").innerText();
  check(
    "provenance names both owners",
    source.includes("Numbers · Python") && source.includes("Field rules"),
    source,
  );
  check(
    "dry dataset is fallback",
    (await result.getAttribute("data-source")) === "fallback",
  );
  check(
    "soil band Weak",
    (await page.getByTestId("vital-soil").innerText()) === "Weak",
  );
  check(
    "water band Fair",
    (await page.getByTestId("vital-water").innerText()) === "Fair",
  );
  check(
    "rain outlook matches the payload",
    (await page.getByTestId("vital-rain").innerText()) ===
      "Dry week ahead — prepare water pockets",
  );
  const actions = await page
    .getByTestId("dryland-checklist")
    .locator("li[data-action]")
    .evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-action")),
    );
  check(
    "dry action order",
    actions.join(",") === "zai_pits,delay_sowing,living_cover,biocrust_check",
    actions.join(","),
  );
  const pathways = await page
    .getByTestId("dryland-pathways")
    .locator("article")
    .evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-pathway")),
    );
  check(
    "three approved pathways",
    pathways.join(",") === "sorghum_cowpea,millet_cowpea,amaranth",
    pathways.join(","),
  );

  // --- Checklist interaction ----------------------------------------------
  const first = page.getByTestId("dryland-checklist").locator("li").first();
  await first.locator("input[type=checkbox]").check();
  check(
    "checkbox marks the step",
    (await first.getAttribute("data-checked")) === "true",
  );
  check(
    "counter reads one of four",
    (await result.locator(".qd-check-heading").innerText()).includes("1 of 4"),
  );

  // --- Receipt -------------------------------------------------------------
  await page.getByTestId("dryland-receipt").locator("summary").click();
  const receipt = await page.getByTestId("dryland-receipt").innerText();
  for (const line of [
    "Python · soil and water bands",
    "Field rules composed this brief",
    "Field rules only",
    "Sahel reference · reference table",
  ]) {
    check(
      `receipt shows: ${line}`,
      receipt.includes(line),
      receipt.replace(/\s+/g, " ").slice(0, 160),
    );
  }
  check(
    "receipt hides model limits when none ran",
    !receipt.includes("350 output tokens") &&
      !receipt.includes("3-second model deadline"),
    receipt.replace(/\s+/g, " ").slice(0, 160),
  );
  check(
    "receipt never names a model when none ran",
    !receipt.includes("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"),
  );

  // --- Counterfactual: same bands, different work order -------------------
  await page.getByTestId("dryland-rain").click();
  check(
    "scenario flip clears the old card",
    (await page.getByTestId("dryland-result").count()) === 0,
  );
  await page.getByTestId("dryland-build").click();
  await page.getByTestId("dryland-result").waitFor();
  check(
    "bands unchanged after flip",
    (await page.getByTestId("vital-soil").innerText()) === "Weak" &&
      (await page.getByTestId("vital-water").innerText()) === "Fair",
  );
  check(
    "rain outlook changed",
    (await page.getByTestId("vital-rain").innerText()) ===
      "Rain may return — inspect water pathways",
  );
  const rainActions = await page
    .getByTestId("dryland-checklist")
    .locator("li[data-action]")
    .evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-action")),
    );
  check(
    "wet week reorders the work",
    rainActions.join(",") === "living_cover,biocrust_check,half_moons",
    rainActions.join(","),
  );
  check(
    "marks reset per scenario",
    (await page
      .getByTestId("dryland-checklist")
      .locator("input:checked")
      .count()) === 0,
  );

  await page
    .locator('[data-testid="dryland-result"][data-phase="final"]')
    .waitFor();
  const metrics = await workspace.evaluate((root) => {
    const luminance = (rgb) => {
      const a = rgb.slice(0, 3).map((v) => {
        v /= 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
    };
    const color = (text) => (text.match(/[\d.]+/g) || []).map(Number);
    const findings = [];
    for (const node of root.querySelectorAll("*")) {
      if (
        ![...node.childNodes].some(
          (n) => n.nodeType === 3 && n.textContent.trim(),
        )
      )
        continue;
      const r = node.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const css = getComputedStyle(node);
      let parent = node,
        bg = [];
      while (parent) {
        bg = color(getComputedStyle(parent).backgroundColor);
        if (bg.length === 3 || bg[3] === 1) break;
        parent = parent.parentElement;
      }
      const fg = color(css.color),
        size = parseFloat(css.fontSize),
        a = luminance(fg),
        b = luminance(bg.length ? bg : [255, 255, 255]);
      const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (size < 12 || ratio < (size === 12 ? 8 : 7))
        findings.push({ text: node.textContent.slice(0, 35), size, ratio });
    }
    return findings;
  });
  check(
    "rendered type floor and contrast",
    metrics.length === 0,
    JSON.stringify(metrics),
  );
  check(
    "no horizontal overflow",
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  for (const [name, soil, water] of [
    ["Dryland valley", "Weak", "Low"],
    ["Mediterranean farmland", "Building", "Fair"],
    ["East African plateau", "Strong", "Good"],
  ]) {
    await page
      .locator(".qg-examples")
      .getByRole("button", { name, exact: true })
      .click();
    await page.getByTestId("dryland-result").waitFor({ state: "hidden" });
    check(
      "map change clears previous brief " + name,
      (await page.getByTestId("dryland-result").count()) === 0,
    );
    await page.getByTestId("dryland-build").click();
    await page
      .locator('[data-testid="dryland-result"][data-phase="final"]')
      .waitFor();
    check(
      "reference bands " + name,
      (await page.getByTestId("vital-soil").innerText()) === soil &&
        (await page.getByTestId("vital-water").innerText()) === water,
    );
  }
  // --- Layout --------------------------------------------------------------
  const box = await page.getByTestId("field-vital-signs").boundingBox();
  check(
    "vitals fit a 390px viewport",
    box !== null && box.width <= Number(process.env.SOLQUIRA_QA_WIDTH || 390),
    JSON.stringify(box),
  );
  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));
  check("no external origins", external.length === 0, external.join(","));
  check(
    "payload was server-built",
    modelRequests.every((body) =>
      [
        [12.371, -1.519],
        [-30.03, -70.71],
        [37.1, -4.6],
        [-0.5, 36.9],
      ].some(([lat, lon]) => body.lat === lat && body.lon === lon),
    ),
  );
  await page.screenshot({
    path: resolve(output, "dryland-390.png"),
    fullPage: false,
  });
  await result.scrollIntoViewIfNeeded();
  await result.screenshot({ path: resolve(output, "dryland-card-390.png") });

  // --- A real location adapts map and forecast, but never invents a soil profile
  const located = await browser.newContext({
    viewport: {
      width: Number(process.env.SOLQUIRA_QA_WIDTH || 390),
      height: 1000,
    },
    reducedMotion: process.env.SOLQUIRA_QA_MOTION || "reduce",
    geolocation: { latitude: -33.4489, longitude: -70.6693 },
    permissions: ["geolocation"],
  });
  const locatedPage = await located.newPage();
  locatedPage.setDefaultTimeout(20000);
  const locatedErrors = [];
  const locatedModelRequests = [];
  locatedPage.on("pageerror", (error) => locatedErrors.push(error.message));
  await located.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.origin !== target.origin &&
      !["data:", "blob:"].includes(url.protocol)
    ) {
      await route.abort();
      return;
    }
    if (url.pathname === "/v1/field-brief")
      locatedModelRequests.push(route.request().postDataJSON());
    await route.continue();
  });
  await locatedPage.goto(target.href, { waitUntil: "domcontentloaded" });
  await locatedPage
    .waitForFunction(
      () =>
        document.querySelector('[data-testid="geo-lat"]')?.value === "-33.449",
      undefined,
      { timeout: 15000 },
    )
    .catch(() => undefined);
  check(
    "an already-granted location moves the map",
    (await locatedPage.getByTestId("geo-lat").inputValue()) === "-33.449" &&
      (await locatedPage.getByTestId("geo-lon").inputValue()) === "-70.669",
    `${await locatedPage.getByTestId("geo-lat").inputValue()},${await locatedPage.getByTestId("geo-lon").inputValue()}`,
  );
  check(
    "the map says the point has no prepared profile",
    await locatedPage.getByTestId("geo-location-state").isVisible(),
  );
  const locatedWorkspace = locatedPage.getByTestId("dryland-workspace");
  await locatedWorkspace.scrollIntoViewIfNeeded();
  check(
    "the brief names the real point without a soil profile",
    (await locatedWorkspace.locator(".qd-example").innerText()).trim() ===
      "Your point · no soil profile",
    await locatedWorkspace.locator(".qd-example").innerText(),
  );
  check(
    "the brief still offers the four references",
    (await locatedPage
      .getByTestId("dryland-reference-ouagadougou")
      .isVisible()) &&
      (await locatedPage.getByTestId("dryland-build").isDisabled()),
  );
  check(
    "no nearest reference is assigned to a real point",
    locatedModelRequests.length === 0,
    `${locatedModelRequests.length} requests`,
  );
  check(
    "no page errors with a real location",
    locatedErrors.length === 0,
    locatedErrors.join(" | "),
  );
  await locatedPage.screenshot({
    path: resolve(output, "dryland-location-390.png"),
    fullPage: false,
  });
  await located.close();
} finally {
  await browser.close();
}

const failed = checks.filter((item) => !item.ok);
console.log(
  JSON.stringify(
    {
      target: target.origin,
      passed: checks.length - failed.length,
      failed: failed.length,
      checks,
    },
    null,
    2,
  ),
);
if (failed.length) process.exitCode = 1;
