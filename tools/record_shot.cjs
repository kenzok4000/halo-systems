// Screen-record one shot of the live dashboard with headless Chromium, for the demo video.
// The dashboard must already be running (`swarmlab serve`); this only drives the page and
// films it, so every frame is the real dashboard flying a real simulation.
//
//   node tools/record_shot.cjs --scenario forest_trail --config original+fixed --seed 1000 \
//        --speed 2 --seconds 20 --view top --out video/shot2.webm
//
// Options: --n (drones, blank = the scenario's own), --url (default http://127.0.0.1:8765, where swarmlab serve listens), --tab fly|mission|results|about,
// --inject gust@6,motor_failure@12 (seconds after the start), --mission "text" (Mission tab:
// compile, then fly it), --scroll (scroll the side panel while recording).
// Needs Playwright (`npm install playwright`, or a global install with NODE_PATH set).
const { chromium } = require("playwright");
const { mkdirSync, renameSync } = require("node:fs");
const { dirname } = require("node:path");

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "1"]);
  return acc;
}, []));
const url = args.url || "http://127.0.0.1:8765";
const out = args.out || "video/shot.webm";
const seconds = Number(args.seconds || 15);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  mkdirSync(dirname(out), { recursive: true });
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: dirname(out), size: { width: 1920, height: 1080 } } });
  const page = await context.newPage();
  page.on("pageerror", (e) => console.error("page error:", e.message));
  await page.goto(url);
  await page.waitForFunction(() => document.querySelectorAll("#scenario option").length > 0);
  await page.waitForFunction(() => !/checking/.test(document.querySelector("#ai-pill").textContent));

  if (args.tab && args.tab !== "fly") {
    await page.click(`nav.tabs button[data-tab="${args.tab}"]`);
  }
  if (args.mission) {
    await page.click('nav.tabs button[data-tab="mission"]');
    await page.fill("#mission-text", args.mission);
    await sleep(1500);
    await page.click("#compile");
    await page.waitForFunction(() => document.querySelector("#compile-out").textContent.trim().length > 0, null, { timeout: 120000 });
    await sleep(4000);
    if (!args["no-fly"] && await page.isEnabled("#fly-mission")) {
      await page.click("#fly-mission");
    }
  } else if (args.scenario) {
    await page.selectOption("#scenario", args.scenario);
    if (args.config) await page.selectOption("#config", args.config);
    if (args.seed) await page.fill("#seed", String(args.seed));
    if (args.n) await page.fill("#drones", String(args.n));
    await page.click("#start");
  }
  if (args.speed && args.speed !== "1") {
    await page.click(`#speed button[data-speed="${args.speed}"]`);
  }
  if (args.view === "top") await page.click("#view-top");

  const injects = (args.inject || "").split(",").filter(Boolean).map((s) => {
    const [kind, at] = s.split("@");
    return { kind, at: Number(at || 0) };
  }).sort((a, b) => a.at - b.at);
  const t0 = Date.now();
  for (const inj of injects) {
    await sleep(Math.max(0, inj.at * 1000 - (Date.now() - t0)));
    await page.click(`button[data-inject="${inj.kind}"]`);
  }
  if (args.scroll) {
    const steps = Math.max(20, Math.round(seconds * 5));
    for (let i = 0; i < steps; i++) {
      await page.evaluate((f) => { const el = document.querySelector("aside"); el.scrollTop = f * (el.scrollHeight - el.clientHeight); }, i / (steps - 1));
      await sleep((seconds * 1000) / steps);
    }
  }
  await sleep(Math.max(0, seconds * 1000 - (Date.now() - t0)));
  const video = page.video();
  await context.close();
  await browser.close();
  renameSync(await video.path(), out);
  console.log(out);
})().catch((e) => { console.error(e); process.exit(1); });
