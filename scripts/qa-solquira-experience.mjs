// Presentation checks; no inference, GPS or external browser requests.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
const { chromium } = createRequire(import.meta.url)("playwright");
const target = process.argv[2] || "http://127.0.0.1:8042";
const mode = process.argv[3] || "sample";
const origin = new URL(target).origin;
if (!["127.0.0.1", "localhost"].includes(new URL(target).hostname))
  throw Error("Loopback only");
const output = resolve(
  process.env.SOLQUIRA_QA_OUTPUT ||
    "output/solquira-rebrand-2026-10-27/browser",
);
mkdirSync(output, { recursive: true });
const browser = await chromium.launch(),
  checks = [],
  errors = [],
  external = [];
try {
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({
      viewport: { width, height: width > 1000 ? 1000 : 844 },
      reducedMotion: "reduce",
    });
    await context.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin && !["data:", "blob:"].includes(url.protocol)) {
        external.push(url.origin);
        return route.abort();
      }
      if (url.pathname.endsWith("/studio/agent"))
        throw Error("Brand QA must not call a model");
      return route.continue();
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(target, { waitUntil: "networkidle" });
    await page.getByTestId("studio-ready").waitFor();
    if (
      (await page.title()) !==
      "Solquira — Understand the land. Cultivate the future."
    )
      throw Error("Wrong page title");
    if (
      !(await page
        .getByRole("link", { name: "Solquira home", exact: true })
        .isVisible())
    )
      throw Error("Public brand missing");
    if (
      !(await page.locator('meta[name="description"]').getAttribute("content"))
    )
      throw Error("Missing page description");
    if (
      await page.locator('meta[name="author"], meta[name="generator"]').count()
    )
      throw Error("Descriptive attribution metadata was added");
    const visible = await page.locator("body").innerText();
    if (
      /Offline example|Saved rule-based explanation|Agronomic viability has not been assessed\./i.test(
        visible,
      )
    )
      throw Error(
        "Old brand or repetitive disclaimer still dominates default UI",
      );
    if (!visible.includes("Healthy land. Resilient communities."))
      throw Error("Territorial value proposition missing");
    if (mode === "sample") {
      if (
        !visible.includes("Sample workspace") ||
        !visible.includes("Put your land in context.")
      )
        throw Error("Sample mode not accurately presented");
      if (await page.getByTestId("agent-query").count())
        throw Error("Sample mode pretends to accept an AI question");
    }
    if (
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      )
    )
      throw Error(`Overflow at ${width}`);
    await page.screenshot({
      path: `${output}/${mode}-hero-${width}.png`,
      animations: "disabled",
    });
    await page.screenshot({
      path: `${output}/${mode}-full-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
    const methods = page.getByTestId("methods-version");
    await methods.locator("summary").click();
    const basis = await page.getByTestId("workspace-basis").innerText();
    if (
      mode === "sample" &&
      (!basis.includes("synthetic") ||
        !basis.includes("without requesting a live forecast or AI response"))
    )
      throw Error(
        "Necessary provenance was deleted instead of disclosed progressively",
      );
    if (
      !(await methods
        .getByRole("heading", { name: "Comparison method 1.1", exact: true })
        .isVisible())
    )
      throw Error("Method/version traceability lost");
    await methods.screenshot({
      path: `${output}/${mode}-methods-${width}.png`,
      animations: "disabled",
    });
    await page
      .getByRole("link", { name: "About Solquira", exact: true })
      .click();
    if (
      !(await page
        .locator("#qs-about")
        .evaluate(
          (element) => element.getBoundingClientRect().top < innerHeight,
        ))
    )
      throw Error("About link broken");
    if (
      !(await page.locator("#qs-about").innerText()).includes(
        "Born in Latin America",
      )
    )
      throw Error("Origin story missing");
    checks.push({
      width,
      identity: true,
      mode,
      meaningfulCopy: true,
      technicalBasisAvailable: true,
      overflow: false,
      noInference: true,
    });
    await context.close();
  }
  if (errors.length || external.length)
    throw Error(JSON.stringify({ errors, external }));
  const result = {
    status: "PASS",
    target,
    mode,
    checks,
    errors,
    externalRequests: external,
  };
  writeFileSync(
    `${output}/${mode}-brand-checks.json`,
    JSON.stringify(result, null, 2),
  );
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
}
