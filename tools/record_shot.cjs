// Screen-record one shot of the live dashboard with headless Chromium, for the demo video.
// The dashboard must already be running (`halo serve`); this only drives the page and
// films it, so every frame is the real dashboard flying a real simulation.
//
//   node tools/record_shot.cjs --scenario forest_trail --config original+fixed --seed 1000 \
//        --speed 2 --seconds 20 --view top --out video/shot2.webm
//
// Options: --n (drones, blank = the scenario's own), --url (default http://127.0.0.1:8765, where halo serve listens), --tab fly|mission|results|about,
// --inject gust@6,motor_failure@12 (kinds gust, motor_failure or blackout; seconds after the start),
// --mission "text" (Mission tab: compile, then fly it; --no-fly stops after compiling; --type [ms] types the
// request key by key, 45 ms apart unless given; with --film the typing, a moment of compiling and --hold S
// seconds (default 3) of the compiled plan are filmed before the flight, or end the video with --no-fly, and
// the wait for the compiler is cut),
// --speed 1|2|5 (the page's speed buttons; any other value has no button, so the click times out),
// --view follow|chase|orbit|ride|close|top|fire (the page's view buttons; --ride-drone N rides with drone N, not the one
// nearest the swarm's centre; fire, in the wildfire valley, looks along the forecast head of the fastest spreading
// fire, or of fire N with --fire N, counted from 1 as on screen), --seconds (default 15), --out (default video/shot.webm),
// --scroll (scroll the side panel while recording).
// --zoom Z (with --film) pulls the camera back Z times its usual distance in the follow, chase, orbit, top, close and fire views;
// --panel alerts|brief|log (with --film) keeps that section of the side panel at its top in every frame.
// --cinema (with --film) films the cinema layout: the 3D view fills the frame, with the flight's title, clock,
// counts, the fire crew brief and alerts on cards over it, and notices of what just happened (with a typed
// --mission, the request and the compiler's answer are shown on a card of their own before the flight).
// --camera "orbit@0,close:1.2@6,chase@12" (with --film) cuts between views during the shot: each entry is a
// view (and optionally its zoom) and the video second it starts at, counted from the flight's first frame;
// the first entry's view is the opening one.
// The video starts when the page opens, so it also holds the page load and set-up before the shot.
//
// --film [--fps 25] renders the shot frame by frame instead of screen-recording it, for a smooth video on a
// machine without a GPU: the page (opened with ?film=1) buffers the run as it streams, then steps through it
// one video frame at a time, and each frame is a screenshot of the whole page. --speed is then the playback
// speed (simulated seconds per video second, any value), --inject times are simulated seconds, and --out is
// an .mp4 made with ffmpeg. The run itself is the same live run of the real simulator. --from S starts the
// shot S simulated seconds into the flight (the page plays up to there first, unrecorded).
// In the wildfire valley the station sends each checked evacuation alert at once by its fixed rules;
// --cancel 3@60 cancels alert 3 (alert 0 is the first one drafted) and --allclear F4@120 gives zone F4 the
// all-clear at those simulated seconds, through the same actions as the page's buttons (simulated phones
// only). Pick times inside the flight (a valley flight lasts a few hundred simulated seconds); an action
// after the flight has ended is skipped. --approval manual makes each alert wait for a tap instead, and
// --approve 0@40,1@44 then approves them (with --film; without it, approve on the page yourself).
// Needs Playwright (`npm install playwright && npx playwright install chromium`, or a global install
// with NODE_PATH set).
const { chromium } = require("playwright");
const { copyFileSync, mkdirSync, renameSync, rmSync } = require("node:fs");
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
  for (const k of ["approve", "cancel", "allclear", "from", "ride-drone", "fire", "zoom", "panel", "cinema", "camera", "hold"]) if (args[k]) console.warn(`--${k} needs --film; ignored`);
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: dirname(out), size: { width: 1920, height: 1080 } } });
  const page = await context.newPage();
  page.on("pageerror", (e) => console.error("page error:", e.message));
  await page.goto(args.approval ? `${url}/?approval=${encodeURIComponent(args.approval)}` : url);
  await page.waitForFunction(() => document.querySelectorAll("#scenario option").length > 0);
  await page.waitForFunction(() => !/checking/.test(document.querySelector("#ai-pill").textContent));

  if (args.tab && args.tab !== "fly") {
    await page.click(`nav.tabs button[data-tab="${args.tab}"]`);
  }
  if (args.mission) {
    await page.click('nav.tabs button[data-tab="mission"]');
    if (args.type) {   // type the request key by key, as a person would, instead of pasting it
      await page.fill("#mission-text", "");
      await page.type("#mission-text", args.mission, { delay: Number(args.type) > 1 ? Number(args.type) : 45 });
    } else await page.fill("#mission-text", args.mission);
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
  const typed = Boolean(args.mission && args.type);
  // Every option is checked before anything is filmed, so a typo does not cost minutes of rendering.
  // The camera program: [{ mode, zoom, at }] in video seconds; without --camera, --view and --zoom for the whole shot.
  const program = (args.camera || "").split(",").filter(Boolean).map((s) => {
    const [view, at] = s.split("@"), [mode, z] = view.split(":");
    if (!["follow", "chase", "orbit", "ride", "close", "top", "fire"].includes(mode)) throw new Error(`--camera: no view ${mode}`);
    return { mode, zoom: z ? Number(z) : Number(args.zoom || 1), at: Number(at || 0) };
  }).sort((a, b) => a.at - b.at);
  if (program.length) { args.view = program[0].mode; args.zoom = String(program[0].zoom); }
  if (args.fire && !(Number.isInteger(Number(args.fire)) && Number(args.fire) >= 1)) throw new Error("--fire takes a fire number, counted from 1");
  const usesFire = args.view === "fire" || program.some((c) => c.mode === "fire");
  if (args.fire && !usesFire) console.warn("--fire picks the fire for the fire view, which this shot does not use; ignored");
  const panel = { alerts: "#zones", brief: "#brief", log: "#log" }[args.panel];
  if (args.panel && !panel) throw new Error("--panel takes alerts, brief or log");
  const actions = (kind, num) => (args[kind] || "").split(",").filter(Boolean).map((s) => s.split("@"))
    .map(([id, at]) => ({ kind, id: num ? Number(id) : id, at: Number(at || 0) }));
  const injects = [
    ...(args.inject || "").split(",").filter(Boolean).map((s) => s.split("@")).map(([kind, at]) => ({ kind, at: Number(at || 0) })),
    ...actions("approve", true), ...actions("cancel", true), ...actions("allclear", false),
  ].sort((a, b) => a.at - b.at);
  const bad = injects.find((i) => (i.kind === "approve" || i.kind === "cancel") && !Number.isInteger(i.id));
  if (bad) throw new Error(`--${bad.kind} takes alert numbers, as in 0@40,1@44`);
  await page.goto(`${url}/?film=1${args.cinema ? "&cinema=1" : ""}${args.approval ? `&approval=${encodeURIComponent(args.approval)}` : ""}`);
  await page.waitForFunction(() => document.querySelectorAll("#scenario option").length > 0);
  await page.waitForFunction(() => !/checking/.test(document.querySelector("#ai-pill").textContent));
  // The page's controls are worked by script, since the cinema layout hides the side panel they sit in.
  const click = (sel) => page.evaluate((q) => document.querySelector(q).click(), sel);
  const set = (sel, v) => page.evaluate(([q, v]) => { const el = document.querySelector(q); el.value = v; el.dispatchEvent(new Event("change")); }, [sel, v]);
  // Frames are numbered across the whole video: a typed mission's frames come before the flight's.
  const dir = `${out}.frames`;
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  let shot = 0;
  const frame = (k) => join(dir, `f${String(k).padStart(5, "0")}.jpg`);
  const shoot = () => page.screenshot({ path: frame(shot++), type: "jpeg", quality: 94, timeout: 300000 });
  // Video seconds of the page with no flight playing (the scenery still moves).
  const hold = async (s) => {
    for (let k = 0; k < Math.round(s * fps); k++) { await page.evaluate((dv) => window.haloFilm.idle(dv), 1 / fps); await shoot(); }
  };
  if (args.mission) {
    await click('nav.tabs button[data-tab="mission"]');
    if (typed) {
      // Type the request one video frame at a time, key by key at --type ms apart.
      const text = args.mission, ms = Number(args.type) > 1 ? Number(args.type) : 45;
      const box = (t) => page.evaluate((t) => { const el = document.querySelector("#mission-text"); el.value = t; el.focus(); el.setSelectionRange(t.length, t.length);
        el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event("input")); }, t);
      await box("");
      await hold(0.8);
      const keys = Math.ceil(text.length * ms / 1000 * fps);
      for (let k = 1; k <= keys; k++) {
        await box(text.slice(0, Math.min(text.length, Math.floor(k * 1000 / fps / ms))));
        await page.evaluate((dv) => window.haloFilm.idle(dv), 1 / fps);
        await shoot();
      }
      await hold(0.6);
    } else await set("#mission-text", args.mission);
    await click("#compile");
    if (typed) {
      // One frame taken at once, while the compiler is still working, held for half a second; the rest of the
      // wait is not filmed (with Nemotron the call takes a second or two, and a frame here takes longer).
      await shoot();
      for (let k = 1; k < Math.round(0.5 * fps); k++) copyFileSync(frame(shot - 1), frame(shot++));
    }
    await page.waitForFunction(() => { const t = document.querySelector("#compile-out").textContent.trim(); return t && !/^Compiling/.test(t); }, null, { timeout: 120000 });
    if (typed) await hold(Number(args.hold || 3));
    if (args["no-fly"]) {
      if (!typed) throw new Error("--no-fly with --film needs --type: there is no flight to film");
      console.log(await page.textContent("#compile-out"));
      return encode(browser, dir, fps);
    }
    if (await page.evaluate(() => document.querySelector("#fly-mission").disabled)) throw new Error(`mission not flyable: ${await page.textContent("#compile-out")}`);
    await click("#fly-mission");
  } else {
    await set("#scenario", args.scenario);
    if (args.config) await set("#config", args.config);
    if (args.seed) await set("#seed", String(args.seed));
    if (args.n) await set("#drones", String(args.n));
    await click("#start");
  }
  // The run exists, and the drone model has loaded (or failed, and simple drones are drawn).
  await page.waitForFunction(() => { const s = window.haloFilm.status(); return s !== null && s.model !== "loading"; }, null, { timeout: 120000 });
  if ((usesFire || args.fire) && !(await page.evaluate(() => !!window.haloView.wildfire))) throw new Error("--view fire and --fire need the wildfire valley");
  await click('#speed button[data-speed="5"]');   // buffer as fast as the server's pacing allows
  if (args.view) await click(`#view-${args.view === "follow" ? "reset" : args.view}`);
  if (args["ride-drone"]) await page.evaluate((i) => { window.haloView.rideDrone = i; window.haloView.ride = null; }, Number(args["ride-drone"]));
  if (args.fire) await page.evaluate((i) => { window.haloView.fireTarget = i; }, Number(args.fire) - 1);
  if (args.zoom) await page.evaluate((z) => window.haloView.setZoom(z), Number(args.zoom));
  // Scroll the side panel so the section's heading sits at its top (the sections above it grow during a flight).
  const pinPanel = () => panel && page.evaluate((sel) => {
    const aside = document.querySelector("aside");
    let h = document.querySelector(sel);
    while (h && h.tagName !== "H3") h = h.previousElementSibling;
    const top = document.querySelector("nav.tabs").getBoundingClientRect().bottom;   // the tab bar stays stuck over the panel's top
    if (h) aside.scrollTop += h.getBoundingClientRect().top - top - 8;
  }, panel);
  // The page buffers at 5x, but the video plays at --speed: show that speed on the buttons and the cinema clock
  // (display only; a speed with no button lights none).
  await page.evaluate((sp) => { const bar = document.querySelector("#speed"); bar.dataset.playback = sp;
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", Number(b.dataset.speed) === sp)); }, playback);
  const from = Number(args.from || 0);
  const need = from + seconds * playback;
  const commander = { approve: "haloApprove", cancel: "haloCancel", allclear: "haloAllClear" };
  const buffered = () => page.evaluate(() => window.haloFilm.status());
  for (const inj of injects) {
    await page.waitForFunction((t) => { const s = window.haloFilm.status(); return s.ended || s.buffered_s >= t; }, inj.at, { polling: 50, timeout: 0 });
    if ((await buffered()).ended) { console.warn(`run ended before ${inj.kind}@${inj.at}; skipped`); break; }
    if (commander[inj.kind]) {
      const r = await page.evaluate(([fn, id]) => window[fn](id), [commander[inj.kind], inj.id]);
      if (!r || !r.ok) console.warn(`${inj.kind} ${inj.id} at ${inj.at} s failed: ${r && r.reason}`);
    } else await click(`button[data-inject="${inj.kind}"]`);
  }
  // Playback starts at the first frame the page received (or at --from), so the shot is `seconds` long from there.
  await page.waitForFunction(([t, abs]) => { const s = window.haloFilm.status(); return s.ended || (s.first_s !== null && s.buffered_s >= (abs ? 0 : s.first_s) + t + 0.5); },
    [need, from > 0], { polling: 500, timeout: 0 });
  const st = await buffered();
  console.log("buffered", JSON.stringify(st));
  if (from > 0 && st.ended && st.buffered_s < from) throw new Error(`the flight ended at ${st.buffered_s.toFixed(1)} s, before --from ${from}`);
  const frames = Math.round(seconds * fps);
  if (from > 0) {
    // Jump to just before --from (simulated seconds into the flight, like --inject), growing the smoke
    // plumes to their height there, then play the last 1.5 video seconds unrecorded so the camera and
    // flames are settled when the shot starts; frame 0 then lands on --from.
    const pre = Math.round(1.5 * fps), end = from - playback / fps;
    const preSim = Math.max(0, Math.min(end - st.played_s, 1.5 * playback));
    await page.evaluate((ds) => window.haloFilm.seek(ds), Math.max(0, end - preSim - st.played_s));
    for (let k = 0; k < pre; k++) await page.evaluate(([dv, ds]) => window.haloFilm.step(dv, ds), [1 / fps, preSim / pre]);
  }
  let cue = 1, unframed = 0;
  for (let k = 0; k < frames; k++) {
    if (cue < program.length && k / fps >= program[cue].at) {
      const c = program[cue++];
      await page.evaluate(([m, z]) => { window.haloView.setZoom(z); window.haloView.setMode(m, true);
        document.querySelectorAll("#viewbtns button").forEach((b) => b.classList.toggle("active", b.id === `view-${m === "follow" ? "reset" : m}`)); }, [c.mode, c.zoom]);
    }
    await page.evaluate(([dv, ds]) => window.haloFilm.step(dv, ds), [1 / fps, k || from > 0 ? playback / fps : 0]);
    // The fire view with no fire to frame yet (not located, or no such fire): count the frames, to warn at the end.
    if (usesFire && !(await page.evaluate(() => { const v = window.haloView; return v.mode !== "fire" || !!v.wildfire.shot(v.fireTarget); }))) unframed++;
    await pinPanel();
    // The first frame also compiles every shader, which takes a while without a GPU.
    await shoot();
    if (k % fps === 0) process.stdout.write(`\r${k}/${frames} frames`);
  }
  if (unframed) console.warn(`\nthe fire view had no ${args.fire ? `Fire ${args.fire}` : "fire"} to frame in ${unframed} of ${frames} frames`);
  return encode(browser, dir, fps);
}

async function encode(browser, dir, fps) {
  await browser.close();
  execFileSync("ffmpeg", ["-v", "error", "-y", "-framerate", String(fps), "-i", join(dir, "f%05d.jpg"),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "slow", out]);
  rmSync(dir, { recursive: true, force: true });
  console.log(`\n${out}`);
}
