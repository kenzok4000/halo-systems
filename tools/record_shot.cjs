// Screen-record one shot of the live dashboard with headless Chromium, for the demo video.
// The dashboard must already be running (`halo serve`); this only drives the page and
// films it, so every frame is the real dashboard flying a real simulation.
//
//   node tools/record_shot.cjs --scenario forest_trail --config original+fixed --seed 1000 \
//        --speed 2 --seconds 20 --view top --out video/shot2.webm
//
// Options: --n (drones, blank = the scenario's own), --url (default http://127.0.0.1:8765, where halo serve listens), --tab fly|mission|results|about,
// --inject gust@6,motor_failure@12 (kinds gust, motor_failure or blackout; seconds after the start),
// --mission "text" (Mission tab: compile, then fly it; --no-fly stops after compiling),
// --speed 1|2|5 (the page's speed buttons; any other value has no button, so the click times out),
// --view follow|chase|orbit|ride|top (the page's view buttons), --seconds (default 15), --out (default video/shot.webm),
// --scroll (scroll the side panel while recording).
// The video starts when the page opens, so it also holds the page load and set-up before the shot.
//
// --film [--fps 25] renders the shot frame by frame instead of screen-recording it, for a smooth video on a
// machine without a GPU: the page (opened with ?film=1) buffers the run as it streams, then steps through it
// one video frame at a time, and each frame is a screenshot of the whole page. --speed is then the playback
// speed (simulated seconds per video second, any value), --inject times are simulated seconds, and --out is
// an .mp4 made with ffmpeg. The run itself is the same live run of the real simulator. In the wildfire
// valley, --approve 0@40,1@44 approves those evacuation alerts at those simulated seconds, through the same
// action as the page's Approve button (alert 0 is the first one drafted).
// Needs Playwright (`npm install playwright && npx playwright install chromium`, or a global install
// with NODE_PATH set).
const { chromium } = require("playwright");
const { mkdirSync, renameSync, rmSync } = require("node:fs");
const { dirname, join } = require("node:path");
const { execFileSync } = require("node:child_process");

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "1"]);
  return acc;
}, []));
const url = args.url || "http://127.0.0.1:8765";
const out = args.out || (args.film ? "video/shot.mp4" : "video/shot.webm");
const seconds = Number(args.seconds || 15);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  mkdirSync(dirname(out), { recursive: true });
  if (args.film) return film();
  if (args.approve) console.warn("--approve needs --film; ignored");
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
  if (args.view) await page.click(`#view-${args.view === "follow" ? "reset" : args.view}`);

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

async function film() {
  const fps = Number(args.fps || 25), playback = Number(args.speed || 1);
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on("pageerror", (e) => console.error("page error:", e.message));
  await page.goto(`${url}/?film=1`);
  await page.waitForFunction(() => document.querySelectorAll("#scenario option").length > 0);
  await page.waitForFunction(() => !/checking/.test(document.querySelector("#ai-pill").textContent));
  if (args.mission) {
    await page.click('nav.tabs button[data-tab="mission"]');
    await page.fill("#mission-text", args.mission);
    await page.click("#compile");
    await page.waitForFunction(() => { const t = document.querySelector("#compile-out").textContent.trim(); return t && !/^Compiling/.test(t); }, null, { timeout: 120000 });
    if (!(await page.isEnabled("#fly-mission"))) throw new Error(`mission not flyable: ${await page.textContent("#compile-out")}`);
    await page.click("#fly-mission");
  } else {
    await page.selectOption("#scenario", args.scenario);
    if (args.config) await page.selectOption("#config", args.config);
    if (args.seed) await page.fill("#seed", String(args.seed));
    if (args.n) await page.fill("#drones", String(args.n));
    await page.click("#start");
  }
  // The run exists, and the drone model has loaded (or failed, and simple drones are drawn).
  await page.waitForFunction(() => { const s = window.haloFilm.status(); return s !== null && s.model !== "loading"; }, null, { timeout: 120000 });
  await page.click('#speed button[data-speed="5"]');   // buffer as fast as the server's pacing allows
  if (args.view) await page.click(`#view-${args.view === "follow" ? "reset" : args.view}`);
  // The page buffers at 5x, but the video plays at --speed: show that speed on the buttons (display only).
  await page.evaluate((sp) => document.querySelectorAll("#speed button").forEach((b) => b.classList.toggle("active", Number(b.dataset.speed) === sp)), playback);
  const need = seconds * playback;
  const injects = [
    ...(args.inject || "").split(",").filter(Boolean).map((s) => s.split("@")).map(([kind, at]) => ({ kind, at: Number(at || 0) })),
    ...(args.approve || "").split(",").filter(Boolean).map((s) => s.split("@")).map(([id, at]) => ({ kind: "approve", id: Number(id), at: Number(at || 0) })),
  ].sort((a, b) => a.at - b.at);
  const bad = injects.find((i) => i.kind === "approve" && !Number.isInteger(i.id));
  if (bad) throw new Error(`--approve takes alert numbers, as in 0@40,1@44`);
  const buffered = () => page.evaluate(() => window.haloFilm.status());
  for (const inj of injects) {
    await page.waitForFunction((t) => { const s = window.haloFilm.status(); return s.ended || s.buffered_s >= t; }, inj.at, { polling: 50, timeout: 0 });
    if ((await buffered()).ended) { console.warn(`run ended before ${inj.kind}@${inj.at}; skipped`); break; }
    if (inj.kind === "approve") {
      const r = await page.evaluate((id) => window.haloApprove(id), inj.id);
      if (!r || !r.ok) console.warn(`approving alert ${inj.id} at ${inj.at} s failed: ${r && r.reason}`);
    } else await page.click(`button[data-inject="${inj.kind}"]`);
  }
  // Playback starts at the first frame the page received, so the shot is `seconds` long from there.
  await page.waitForFunction((t) => { const s = window.haloFilm.status(); return s.ended || (s.first_s !== null && s.buffered_s >= s.first_s + t + 0.5); }, need, { polling: 500, timeout: 0 });
  console.log("buffered", JSON.stringify(await buffered()));
  const dir = `${out}.frames`;
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const frames = Math.round(seconds * fps);
  for (let k = 0; k < frames; k++) {
    await page.evaluate(([dv, ds]) => window.haloFilm.step(dv, ds), [1 / fps, k ? playback / fps : 0]);
    await page.screenshot({ path: join(dir, `f${String(k).padStart(5, "0")}.jpg`), type: "jpeg", quality: 94 });
    if (k % fps === 0) process.stdout.write(`\r${k}/${frames} frames`);
  }
  await browser.close();
  execFileSync("ffmpeg", ["-v", "error", "-y", "-framerate", String(fps), "-i", join(dir, "f%05d.jpg"),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "slow", out]);
  rmSync(dir, { recursive: true, force: true });
  console.log(`\n${out}`);
}
