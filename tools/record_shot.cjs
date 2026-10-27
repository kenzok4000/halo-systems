// Screen-record one shot of the live dashboard with headless Chromium, for the demo video.
// The dashboard must already be running (`halo serve`); this only drives the page and
// films it, so every frame is the real dashboard flying a real simulation.
//
//   node tools/record_shot.cjs --scenario forest_trail --config original+fixed --seed 1000 \
//        --speed 2 --seconds 20 --view top --out video/shot2.webm
//
// Options: --n (drones, blank = the scenario's own), --url (default http://127.0.0.1:8765, where halo serve listens), --tab fly|mission|results|about,
// --inject gust@6,motor_failure@12 (kinds gust, motor_failure or blackout; seconds after the start),
// --mission "text" (Mission tab: compile, then fly it, on --seed N when given; --no-fly stops after compiling; --type [ms] types the
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
// --clean (with --film --cinema) films the clean look for the remade video (the page's ?clean=1): one slim badge with
// SIMULATION, the scenario and seed, the swarm, the supervisor, the simulated phones and the drones' drawn size; the
// clock with the playback speed; what just happened as lower-third toasts in large type (at most two at once, the most
// important first); and in the 3D view a low warm morning sun, softer haze and shadows, a filmic grade, richer smoke,
// drones with position lights and trails of where they just flew, clearer alert zones and head arrows, and labels drawn
// larger, with any that would overlap a more important one or a card set back. The cards are left out, unless
// --clean-cards names the ones to show, large (one is best): crew (the fire crew brief), alerts (the evacuation
// alerts), dispatch (fire dispatch), squads, plan (a typed valley patrol's plan, or the swarm's plan elsewhere) or
// stats (the counts); the toasts of that card's kind then come first. --clean-toasts alert,dispatch shows only those
// toast kinds, in that order (kinds: clear, alert, dispatch, crew, fire, station, squad, aircraft, bad, event,
// nemotron, plan; plan, the supervisor's formation changes, is left out of valley films unless named). --ss 1.5 draws
// the 3D view at 1.5 times the pixels and lets the screenshot scale it down (smoother edges, slower; default 1).
// Without --clean every frame is drawn as before. --skip-typing (with --valley) fills the request in at once and
// films nothing before the flight (the edit cuts the typed request from the valley takes anyway).
// Extra views, any shot: crane (from low on the side the camera is on, rising and pulling back over about 16 s),
// dolly (a slow sideways track past the swarm) and low (a low pass just over the treetops behind the framed squad or
// swarm, looking the way it flies; with --squad-focus, behind that squad), in --view and in --camera.
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
// --valley "text" [--seed N] switches the Mission tab to "Valley patrol after lightning", puts N (default 4012; the
// night's strike ids belong to one seed) in its seed box, and types the request key by key as --mission --type does
// (--type ms sets the pace; with --film the same typing frames, the compiling frame and --hold S seconds of the
// plan, or the end of the video with --no-fly); then it flies the plan as a squad patrol of the valley, its alerts
// under the pre-signed rules, as the page's Fly button does. A patrol lasts patrol_min x 60 simulated seconds (12
// minutes unless the request says), so keep --from and --seconds x --speed inside it. A plan the compiler refused
// stops the recorder before anything flies.
// --squad-move "300:F:N:4,420:N:S:2" (with --film; squad patrols only) orders squad moves: at 300 simulated seconds,
// 4 drones from the valley floor squad (F) to the north slopes (N), and so on, through window.haloSquadMove, the
// page's Move drones action; the run carries each out at its first step at or after that second, and the safety
// check may refuse it (both show as events). --squad-focus F|N|S frames that squad in the follow, orbit, chase, ride
// and close-up views from the start of the flight (window.haloView.squadFocus).
// --alert-rule presigned|forecast|distance flies a wildfire valley run (--scenario wildfire_valley or --valley) with its
// alerts under that rule: presigned sends every located fire to fire dispatch as a card, and public alerts under its
// trigger rules (Level 3 for smouldering fires too, Level 4 and 5 only for fires believed spreading); forecast is
// today's rule, the scenario's default (a --valley
// patrol defaults to presigned).
// --aircraft "hold@90,resume@390" (with --film; wildfire valley runs) orders the "aircraft inbound" airspace hold, as the
// operator does when crewed firefighting aircraft come into the valley (the simulator does not detect aircraft): at 90
// simulated seconds every drone stops and drops to 10 m above the treetops (hold:8@90 holds at 8 m), and at 390 s the sweep
// resumes, through window.haloAircraft, the page's hold button. As with --squad-move, each order is sent a little early
// with its time, the run carries it out at its first step at or after then, and the safety validator may refuse it (both
// show as events). A valley run still ends at its own time limit (about 400 s on seed 3000), held or not.
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
// Squad codes are the areas' letters, F, N and S, in either case.
if (args["squad-focus"]) args["squad-focus"] = args["squad-focus"].toUpperCase();
if (args["squad-move"]) args["squad-move"] = args["squad-move"].toUpperCase();
if (args["alert-rule"] && !["forecast", "presigned", "distance"].includes(args["alert-rule"])) throw new Error("--alert-rule takes presigned, forecast or distance");
// Page options carried in the URL: how alerts are approved, and the rule they go out under.
const pageOpts = `${args.approval ? `&approval=${encodeURIComponent(args.approval)}` : ""}${args["alert-rule"] ? `&alert_rule=${args["alert-rule"]}` : ""}`;
const out = args.out || (args.film ? "video/shot.mp4" : "video/shot.webm");
const seconds = Number(args.seconds || 15);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The page's views: those with a button, and those only the recorder picks (no button).
const VIEWS = ["follow", "chase", "orbit", "ride", "close", "top", "fire", "crane", "dolly", "low"], SCRIPTED = ["crane", "dolly", "low"];
const CARDS = ["crew", "dispatch", "alerts", "squads", "plan", "stats"];
const TOASTS = ["clear", "alert", "dispatch", "crew", "fire", "station", "squad", "aircraft", "bad", "event", "nemotron", "plan"];

(async () => {
  mkdirSync(dirname(out), { recursive: true });
  if (args.film) return film();
  for (const k of ["approve", "cancel", "allclear", "from", "ride-drone", "fire", "zoom", "panel", "cinema", "camera", "hold", "squad-move", "aircraft", "clean", "clean-cards", "clean-toasts", "ss"]) if (args[k]) console.warn(`--${k} needs --film; ignored`);
  if (args.mission && args.valley) throw new Error("--mission and --valley are two kinds of request: give one");
  if (args["squad-focus"] && !["F", "N", "S"].includes(args["squad-focus"])) throw new Error("--squad-focus takes a squad: F, N or S");
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: dirname(out), size: { width: 1920, height: 1080 } } });
  const page = await context.newPage();
  page.on("pageerror", (e) => console.error("page error:", e.message));
  // lab=1 opens the page's lab controls, which Playwright's clicks need visible; start=0 stops the page from
  // starting its own wildfire valley flight, so the one picked here is the only one.
  await page.goto(`${url}/?lab=1&start=0${pageOpts}`);
  await page.waitForFunction(() => document.querySelectorAll("#scenario option").length > 0);
  await page.waitForFunction(() => !/checking/.test(document.querySelector("#ai-pill").textContent));

  if (args.tab && args.tab !== "fly") {
    await page.click(`nav.tabs button[data-tab="${args.tab}"]`);
  }
  if (args.mission || args.valley) {
    // A mission flies on the Fly tab's seed box, filled here while that tab is showing.
    if (args.seed && !args.valley) await page.fill("#seed", String(args.seed));
    await page.click('nav.tabs button[data-tab="mission"]');
    if (args.valley) {   // the valley patrol request, on its seed
      await page.click('#mission-mode button[data-mode="valley"]');
      await page.fill("#valley-seed", String(args.seed || 4012));
    }
    const text = args.valley || args.mission;
    if (args.type || args.valley) {   // type the request key by key, as a person would, instead of pasting it
      await page.fill("#mission-text", "");
      await page.type("#mission-text", text, { delay: Number(args.type) > 1 ? Number(args.type) : 45 });
    } else await page.fill("#mission-text", text);
    await sleep(1500);
    await page.click("#compile");
    // Until the answer is in (a live Nemotron call can take longer than the pause below).
    await page.waitForFunction(() => { const t = document.querySelector("#compile-out").textContent.trim(); return t && !/^Compiling/.test(t); }, null, { timeout: 120000 });
    await sleep(4000);
    if (!args["no-fly"] && await page.isEnabled("#fly-mission")) {
      await page.click("#fly-mission");
    } else if (!args["no-fly"] && args.valley) {
      throw new Error(`the valley request cannot fly: ${(await page.textContent("#compile-out")).trim().slice(0, 200)}`);
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
  if (args["squad-focus"]) await page.evaluate((c) => { window.haloView.squadFocus = c; }, args["squad-focus"]);

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
  // The clean look: its page options, checked before anything is filmed.
  if ((args.clean || args["clean-cards"] || args["clean-toasts"] || args.ss) && !(args.clean && args.cinema)) throw new Error("--clean, --clean-cards, --clean-toasts and --ss need --clean with --cinema");
  const listOf = (k, ok) => { const v = args[k] ? String(args[k]).split(",").map((x) => x.trim()).filter(Boolean) : [];
    const bad = v.find((x) => !ok.includes(x)); if (bad) throw new Error(`--${k} takes ${ok.join(", ")}; not ${bad}`); return v; };
  const cards = listOf("clean-cards", CARDS), toasts = listOf("clean-toasts", TOASTS);
  if (args.ss && !(Number(args.ss) >= 0.5 && Number(args.ss) <= 2)) throw new Error("--ss takes a pixel ratio from 0.5 to 2");
  const cleanOpts = args.clean ? `&clean=1${cards.length ? `&cards=${cards.join(",")}` : ""}${toasts.length ? `&toasts=${toasts.join(",")}` : ""}${args.ss ? `&ss=${Number(args.ss)}` : ""}` : "";
  if (args.view && !VIEWS.includes(args.view)) throw new Error(`--view: no view ${args.view}`);
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on("pageerror", (e) => console.error("page error:", e.message));
  if (args.mission && args.valley) throw new Error("--mission and --valley are two kinds of request: give one");
  if ((args["squad-focus"] || args["squad-move"]) && !args.valley) throw new Error("--squad-focus and --squad-move need a squad patrol (--valley)");
  const request = args.valley || args.mission;
  // A valley request is always typed, unless --skip-typing fills it in at once and films nothing before the flight.
  const typed = Boolean((args.mission && args.type) || args.valley) && !args["skip-typing"];
  // Every option is checked before anything is filmed, so a typo does not cost minutes of rendering.
  // The camera program: [{ mode, zoom, at }] in video seconds; without --camera, --view and --zoom for the whole shot.
  const program = (args.camera || "").split(",").filter(Boolean).map((s) => {
    const [view, at] = s.split("@"), [mode, z] = view.split(":");
    if (!VIEWS.includes(mode)) throw new Error(`--camera: no view ${mode}`);
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
  // Squad moves, "at:from:to:k": each is sent SQUAD_LEAD simulated seconds early with at_s, so the run carries it
  // out at that second, as a headless run of the same orders would.
  for (const s of (args["squad-move"] || "").split(",").filter(Boolean)) {
    const [at, from, to, k] = s.split(":");
    if (!(Number.isFinite(Number(at)) && Number(at) >= 0 && ["F", "N", "S"].includes(from) && ["F", "N", "S"].includes(to) && Number.isInteger(Number(k)) && Number(k) >= 1)) {
      throw new Error(`--squad-move takes at:from:to:k, as in 300:F:N:4,420:N:S:2 (areas F, N, S); not ${s}`);
    }
    injects.push({ kind: "squad", at: Number(at), from, to, k: Number(k) });
  }
  // Airspace orders, "hold[:altitude]@at" or "resume@at", sent SQUAD_LEAD early with at_s like the squad moves. The
  // safety validator's plain refusals are caught here: a resume with no hold in force or within 3 s of it, and a
  // further hold that is not lower (10 m when no altitude is given).
  const air = (args.aircraft || "").split(",").filter(Boolean).map((s) => {
    const m = /^(hold|resume)(?::(\d+(?:\.\d+)?))?@(\d+(?:\.\d+)?)$/i.exec(s.trim());
    if (!m || (m[1].toLowerCase() === "resume" && m[2] !== undefined)) throw new Error(`--aircraft takes hold@90,resume@390 (hold:8@90 for a hold at 8 m); not ${s}`);
    return { kind: "aircraft", op: m[1].toLowerCase(), alt: m[2] !== undefined ? Number(m[2]) : null, at: Number(m[3]) };
  }).sort((a, b) => a.at - b.at);
  if (air.length && !(args.valley || args.scenario === "wildfire_valley")) throw new Error("--aircraft needs a wildfire valley run (--scenario wildfire_valley or --valley)");
  let held = null;
  for (const o of air) {
    if (o.op === "hold" && held && !((o.alt ?? 10) < held.alt)) throw new Error(`--aircraft: the hold at ${o.at} s comes while holding at ${held.alt} m; a further hold must be lower`);
    if (o.op === "resume" && !held) throw new Error(`--aircraft: the resume at ${o.at} s has no hold to end`);
    if (o.op === "resume" && o.at - held.at < 3) throw new Error(`--aircraft: the resume at ${o.at} s comes within 3 s of the hold, which the safety validator refuses`);
    held = o.op === "hold" ? { at: held ? held.at : o.at, alt: o.alt ?? 10 } : null;
    injects.push(o);
  }
  injects.sort((a, b) => a.at - b.at);
  if (args["squad-focus"] && !["F", "N", "S"].includes(args["squad-focus"])) throw new Error("--squad-focus takes a squad: F, N or S");
  await page.goto(`${url}/?film=1${args.cinema ? "&cinema=1" : ""}${cleanOpts}${pageOpts}`);
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
  if (request) {
    await click('nav.tabs button[data-tab="mission"]');
    if (args.valley) {   // the valley patrol request, on its seed (the night's strike ids belong to one seed)
      await click('#mission-mode button[data-mode="valley"]');
      await set("#valley-seed", String(args.seed || 4012));
      if (args.config) await set("#config", args.config);
    }
    if (typed) {
      // Type the request one video frame at a time, key by key at --type ms apart.
      const text = request, ms = Number(args.type) > 1 ? Number(args.type) : 45;
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
    } else await set("#mission-text", request);
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
    if (args.seed && !args.valley) await set("#seed", String(args.seed));   // a mission flies on the Fly tab's seed
    if (await page.evaluate(() => document.querySelector("#fly-mission").disabled)) throw new Error(`${args.valley ? "patrol" : "mission"} not flyable: ${await page.textContent("#compile-out")}`);
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
  if ((args["squad-focus"] || args["squad-move"]) && await page.evaluate(() => document.querySelector(".squads-only").hidden)) {
    throw new Error("--squad-focus and --squad-move need a squad patrol (--valley)");
  }
  if (args["squad-focus"]) await page.evaluate((c) => { window.haloView.squadFocus = c; }, args["squad-focus"]);
  await click('#speed button[data-speed="5"]');   // buffer as fast as the server's pacing allows
  if (args.view && SCRIPTED.includes(args.view)) await page.evaluate((m) => window.haloView.setMode(m), args.view);   // no button
  else if (args.view) await click(`#view-${args.view === "follow" ? "reset" : args.view}`);
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
  const SQUAD_LEAD = 2;   // simulated seconds; the page buffers at 5x, so about 0.4 s of wall time to spare
  for (const inj of injects) {
    const at = inj.kind === "squad" || inj.kind === "aircraft" ? Math.max(0, inj.at - SQUAD_LEAD) : inj.at;
    await page.waitForFunction((t) => { const s = window.haloFilm.status(); return s.ended || s.buffered_s >= t; }, at, { polling: 50, timeout: 0 });
    if ((await buffered()).ended) { console.warn(`run ended before ${inj.kind}@${inj.at}; skipped`); break; }
    if (inj.kind === "squad") {
      const r = await page.evaluate(([f, t, k, a]) => window.haloSquadMove(f, t, k, a), [inj.from, inj.to, inj.k, inj.at]);
      if (!r || !r.ok) console.warn(`squad move ${inj.from} to ${inj.to} (${inj.k}) at ${inj.at} s failed: ${r && r.reason}`);
      else if (r.t > inj.at + 0.5) console.warn(`squad move at ${inj.at} s carried out at ${r.t} s (the run was ahead)`);
    } else if (inj.kind === "aircraft") {
      const r = await page.evaluate(([op, a, alt]) => window.haloAircraft(op, a, alt), [inj.op, inj.at, inj.alt]);
      if (!r || !r.ok) console.warn(`aircraft ${inj.op} at ${inj.at} s failed: ${r && r.reason}`);
      else if (r.t > inj.at + 0.5) console.warn(`aircraft ${inj.op} at ${inj.at} s carried out at ${r.t} s (the run was ahead)`);
    } else if (commander[inj.kind]) {
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
  let cue = 1, unframed = 0, tFirst = null;
  const tLoop = Date.now();
  for (let k = 0; k < frames; k++) {
    if (k === 1) tFirst = Date.now();   // the first frame also compiles the shaders: timed apart
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
  // Seconds per frame: the first frame (with the shader compiles) and the mean of the rest.
  const done = Date.now();
  console.log(`\n${frames} frames in ${((done - tLoop) / 1000).toFixed(1)} s` + (tFirst && frames > 1
    ? `: first ${((tFirst - tLoop) / 1000).toFixed(1)} s, then ${((done - tFirst) / 1000 / (frames - 1)).toFixed(2)} s a frame` : ""));
  return encode(browser, dir, fps);
}

async function encode(browser, dir, fps) {
  await browser.close();
  execFileSync("ffmpeg", ["-v", "error", "-y", "-framerate", String(fps), "-i", join(dir, "f%05d.jpg"),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "slow", out]);
  rmSync(dir, { recursive: true, force: true });
  console.log(`\n${out}`);
}
