// Halo Systems dashboard: 3D view, live controls and logs. Talks to the local Python server.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const api = async (path, body) => {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};
// Simulation frame is x east, y north, z up; three.js is y up.
const toV = (p) => new THREE.Vector3(p[0], p[2], -p[1]);

// ---------------------------------------------------------------- scene
const canvas = $("#scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
const scene = new THREE.Scene();
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
scene.background = new THREE.Color(dark ? 0x0f1216 : 0xeef2f5);
scene.fog = new THREE.Fog(scene.background, 250, 900);
const camera = new THREE.PerspectiveCamera(50, 1, 0.5, 3000);
camera.position.set(-60, 55, 70);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.1));
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
sun.position.set(100, 200, 50);
scene.add(sun);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(4000, 4000),
  new THREE.MeshLambertMaterial({ color: dark ? 0x1a2128 : 0xdfe6dc }));
ground.rotation.x = -Math.PI / 2;
scene.add(ground);
const grid = new THREE.GridHelper(4000, 200, dark ? 0x2a323c : 0xc5cdc3, dark ? 0x20272f : 0xd2d9cf);
grid.position.y = 0.02;
scene.add(grid);

let world = new THREE.Group(); scene.add(world);
let droneMesh = null, slotMesh = null, shadowMesh = null, pathLine = null, windArrow = null;
let targetMeshes = [], surveyBox = null;
const TARGET_HIDDEN = new THREE.Color(0x5a4a3a), TARGET_FOUND = new THREE.Color(0xff5a1f);
const COLORS = { 0: new THREE.Color(0x4fb3a9), 1: new THREE.Color(0xe5625c), 2: new THREE.Color(0xe5625c),
  3: new THREE.Color(0x9aa3ad), 4: new THREE.Color(0xe5625c) };
const dummy = new THREE.Object3D();
let follow = new THREE.Vector3();
let userMoved = false;
controls.addEventListener("start", () => { userMoved = true; });

function resize() {
  const r = canvas.getBoundingClientRect();
  renderer.setSize(r.width, r.height, false);
  camera.aspect = r.width / Math.max(1, r.height);
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);

function buildWorld(hello) {
  scene.remove(world);
  world = new THREE.Group();
  scene.add(world);
  const trunk = new THREE.MeshLambertMaterial({ color: 0x6b8f4e, transparent: true, opacity: 0.35, depthWrite: false });
  const bld = new THREE.MeshLambertMaterial({ color: dark ? 0x56606b : 0xa9b2bb, transparent: true, opacity: 0.9 });
  for (const o of hello.world.obstacles) {
    if (o.kind === "cylinder") {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(o.radius, o.radius * 1.1, o.height, 8), trunk);
      m.position.set(o.x, o.height / 2, -o.y);
      world.add(m);
    } else {
      const [lx, ly, lz] = o.lo, [hx, hy, hz] = o.hi;
      const m = new THREE.Mesh(new THREE.BoxGeometry(hx - lx, hz - lz, hy - ly), bld);
      m.position.set((lx + hx) / 2, (lz + hz) / 2, -(ly + hy) / 2);
      world.add(m);
    }
  }
  // Targets to find (hotspots): dull discs that light up when a drone's camera has seen them.
  targetMeshes = [];
  for (const t of hello.targets || []) {
    const m = new THREE.Mesh(new THREE.CircleGeometry(Math.max(t.radius_m, 1.5), 20),
      new THREE.MeshBasicMaterial({ color: TARGET_HIDDEN.clone(), transparent: true, opacity: 0.9 }));
    m.position.set(t.x, 0.12, -t.y); m.rotation.x = -Math.PI / 2;
    world.add(m); targetMeshes.push(m);
  }
  surveyBox = null;
  if (hello.sensor && hello.sensor.area) {
    const [x0, y0, x1, y1] = hello.sensor.area;
    const corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]].map((p) => new THREE.Vector3(p[0], 0.1, -p[1]));
    surveyBox = new THREE.Line(new THREE.BufferGeometry().setFromPoints(corners), new THREE.LineBasicMaterial({ color: 0xe09a3a }));
    world.add(surveyBox);
  }
  const pts = hello.path.map((p) => new THREE.Vector3(p[0], 0.15, -p[1]));
  pathLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineDashedMaterial({ color: 0xf2a541, dashSize: 3, gapSize: 2 }));
  pathLine.computeLineDistances();
  world.add(pathLine);
  const n = hello.n;
  const r = hello.body_radius_m * 6;
  droneMesh = new THREE.InstancedMesh(new THREE.SphereGeometry(r, 16, 12),
    new THREE.MeshStandardMaterial({ roughness: 0.35, emissive: 0x0d3b36, emissiveIntensity: 0.6 }), n);
  slotMesh = new THREE.InstancedMesh(new THREE.TorusGeometry(1.0, 0.1, 6, 24), new THREE.MeshBasicMaterial({ color: 0xf2a541 }), n);
  shadowMesh = new THREE.InstancedMesh(new THREE.CircleGeometry(r, 12), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.18 }), n);
  for (const m of [droneMesh, slotMesh, shadowMesh]) { m.instanceMatrix.setUsage(THREE.DynamicDrawUsage); m.frustumCulled = false; world.add(m); }
  for (let i = 0; i < n; i++) droneMesh.setColorAt(i, COLORS[0]);
  windArrow = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(), 10, 0x7fb6ff, 3, 2);
  world.add(windArrow);
  userMoved = false;
  const p0 = hello.path[0];
  follow.set(p0[0], hello.directive.altitude_m, -p0[1]);
  camera.position.set(p0[0] - 35, hello.directive.altitude_m + 50, -p0[1] + 45);
  controls.target.copy(follow);
}

function drawFrame(f) {
  if (!droneMesh) return;
  const n = f.p.length;
  const c = new THREE.Vector3();
  let k = 0;
  for (let i = 0; i < n; i++) {
    const v = toV(f.p[i]);
    dummy.position.copy(v); dummy.rotation.set(0, 0, 0); dummy.scale.setScalar(1); dummy.updateMatrix();
    droneMesh.setMatrixAt(i, dummy.matrix);
    droneMesh.setColorAt(i, COLORS[f.s[i]] || COLORS[0]);
    dummy.position.set(v.x, 0.05, v.z); dummy.rotation.set(-Math.PI / 2, 0, 0); dummy.updateMatrix();
    shadowMesh.setMatrixAt(i, dummy.matrix);
    const slot = f.slots[i];
    if (slot) {
      const sv = toV(slot);
      dummy.position.copy(sv); dummy.rotation.set(Math.PI / 2, 0, 0); dummy.scale.setScalar(1);
      c.add(v); k++;
    } else {
      dummy.position.set(0, -1000, 0); dummy.scale.setScalar(0.0001);
    }
    dummy.updateMatrix();
    slotMesh.setMatrixAt(i, dummy.matrix);
  }
  for (const m of [droneMesh, slotMesh, shadowMesh]) m.instanceMatrix.needsUpdate = true;
  droneMesh.instanceColor.needsUpdate = true;
  if (f.found) f.found.forEach((hit, i) => { const m = targetMeshes[i]; if (m) m.material.color.copy(hit ? TARGET_FOUND : TARGET_HIDDEN); });
  if (k) {
    c.multiplyScalar(1 / k);
    const w = new THREE.Vector3(f.wind[0], 0, -f.wind[1]);
    const len = w.length();
    windArrow.position.set(c.x - 25, c.y + 12, c.z);
    if (len > 0.1) windArrow.setDirection(w.clone().normalize());
    windArrow.setLength(Math.max(2, len * 2.5), 3, 2);
    const delta = c.clone().sub(follow);
    follow.copy(c);
    if (!userMoved) { camera.position.add(delta); }
    controls.target.copy(c);
  }
}

function loop() {
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(loop);
}

// ---------------------------------------------------------------- live run
let run = null, source = null, spark = [], scenarios = [], status = null, compiled = null;

function fmt(x, d = 1, unit = "") { return x === null || x === undefined ? "–" : `${Number(x).toFixed(d)}${unit}`; }

function setHud(title, d) {
  $("#hud-title").textContent = title;
  $("#hud-directive").textContent = d ? `${d.formation}, ${fmt(d.spacing_m)} m spacing, ${fmt(d.speed_mps)} m/s, ${fmt(d.altitude_m, 0)} m up, margin ${fmt(d.safety_margin_m)} m` : "";
}

function logItem(cls, html) {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = html;
  const log = $("#log");
  log.prepend(li);
  while (log.children.length > 80) log.lastChild.remove();
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function onDirective(d) {
  const src = d.action === "rejected" ? "rejected" : (d.source || "");
  if (d.action === "unchanged") {
    logItem(src, `<span class="chip ${esc(src)}">${esc(src)}</span> t=${fmt(d.t)} s keeps the plan <span class="why">${esc(d.reason)}</span>`);
    return;
  }
  const viol = d.violations && d.violations.length ? `<div class="why">validator: ${esc(d.violations.join("; "))}</div>` : "";
  logItem(src, `<span class="chip ${esc(src)}">${esc(src)}</span> t=${fmt(d.t)} s <b>${esc(d.action)}</b>: ${esc(d.formation)}, ${fmt(d.spacing_m)} m, ${fmt(d.speed_mps)} m/s` +
    `${d.reason ? `<div class="why">${esc(d.reason)}</div>` : ""}${viol}`);
}

function onAi(a) {
  const tok = a.total_tokens ? `, ${a.total_tokens} tokens` : "";
  if (a.fallback) {
    logItem("fallback", `<span class="chip">fallback</span> Nemotron call at t=${fmt(a.t_request)} s failed (${esc(a.error || "")}); rules decided this step`);
  } else {
    logItem("nemotron", `<span class="chip nemotron">Nemotron</span> answered in ${fmt(a.latency_s, 2)} s${tok}${a.cached ? " (replayed)" : ""}`);
  }
}

function onEvent(e) {
  const skip = { directive: 1, injected: 1 };
  if (skip[e.kind]) return;
  // One function per kind: only the matching one runs, since each reads fields only its kind has.
  const text = { motor_failure: () => `motor failure on drone ${e.drone}`,
    gust_front: () => `gust front ${fmt(Math.hypot(...(e.velocity_mps || [0, 0]).slice(0, 2)))} m/s`,
    comms_blackout: () => `radio blackout for ${(e.drones || []).length} drones`,
    collision: () => `collision: drones ${e.drone} and ${e.other}`,
    obstacle_crash: () => `drone ${e.drone} hit an obstacle`, ground_crash: () => `drone ${e.drone} hit the ground`,
    retired_low_battery: () => `drone ${e.drone} left at ${e.battery_pct}% battery`, mission_complete: () => "mission complete",
    target_found: () => `drone ${e.drone} found ${e.what || "target"} ${e.target + 1} at (${fmt(e.x, 0)}, ${fmt(e.y, 0)}) m`,
    directive_rejected: () => "a directive was rejected" }[e.kind];
  const what = text ? text() : e.kind;
  logItem("event", `t=${fmt(e.t)} s ${esc(what)}`);
}

function drawSpark() {
  const cv = $("#spark");
  const w = cv.width = cv.clientWidth * devicePixelRatio, h = cv.height = 70 * devicePixelRatio;
  const g = cv.getContext("2d");
  g.clearRect(0, 0, w, h);
  if (spark.length < 2) return;
  const max = Math.max(3, ...spark.map((p) => p[1] || 0));
  const t0 = spark[0][0], t1 = spark[spark.length - 1][0] || t0 + 1;
  const y = (v) => h - 4 - (v / max) * (h - 8);
  g.strokeStyle = "rgba(229,98,92,.6)"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(0, y(1)); g.lineTo(w, y(1)); g.stroke();
  g.setLineDash([]); g.strokeStyle = "#4fb3a9"; g.lineWidth = 2 * devicePixelRatio; g.beginPath();
  spark.forEach(([t, v], i) => { const x = ((t - t0) / Math.max(1e-6, t1 - t0)) * w; i ? g.lineTo(x, y(v || 0)) : g.moveTo(x, y(v || 0)); });
  g.stroke();
}

function onFrame(f) {
  drawFrame(f);
  const m = f.metrics;
  $("#m-err").textContent = fmt(m.formation_error_m, 2, " m");
  $("#m-sep").textContent = fmt(m.min_sep_m, 2, " m");
  $("#m-fly").textContent = `${m.flying} / ${m.lost} / ${m.retired}`;
  $("#m-crash").textContent = `${m.collisions}`;
  $("#m-batt").textContent = fmt(m.battery_mean, 0, "%");
  $("#m-speed").textContent = fmt(m.leader_speed, 1, " m/s");
  $("#m-progress").style.width = `${Math.min(100, (100 * m.progress_m) / Math.max(1, m.path_m))}%`;
  const scout = m.targets_total != null ? `, hotspots ${m.targets_found}/${m.targets_total}, area ${fmt(m.coverage_pct, 0)}%` : "";
  setHud(`${run.scenario.title}, t = ${fmt(f.t)} s${scout}`, f.directive);
  $("#ai-wait").textContent = f.ai_pending ? "Nemotron is thinking…" : "";
  spark.push([f.t, m.formation_error_m]);
  if (spark.length > 600) spark.shift();
  drawSpark();
}

async function start(extra = {}) {
  if (source) { source.close(); source = null; }
  if (run) api(`/api/runs/${run.id}/control`, { stop: true }).catch(() => {});
  $("#log").innerHTML = "";
  spark = [];
  const speed = Number($("#speed .active").dataset.speed);
  try {
    run = await api("/api/runs", { scenario: $("#scenario").value, config: $("#config").value, seed: Number($("#seed").value), speed, ...extra });
  } catch (err) { alert(err.message); return; }
  buildWorld(run);
  setHud(run.scenario.title, run.directive);
  $("#pause").textContent = "Pause";
  source = new EventSource(`/api/runs/${run.id}/stream`);
  source.addEventListener("frame", (e) => onFrame(JSON.parse(e.data)));
  source.addEventListener("directive", (e) => onDirective(JSON.parse(e.data)));
  source.addEventListener("ai", (e) => onAi(JSON.parse(e.data)));
  source.addEventListener("event", (e) => onEvent(JSON.parse(e.data)));
  source.addEventListener("end", (e) => {
    const m = JSON.parse(e.data).metrics || {};
    logItem("event", `<b>Flight ended.</b> In formation ${fmt(m.time_formed_pct, 0)}% of the time, ${(m.collisions || 0) * 2 + (m.obstacle_crashes || 0) + (m.ground_crashes || 0)} crashes, completed: ${m.completed ? "yes" : "no"}`);
    source.close(); source = null;
  });
}

// ---------------------------------------------------------------- UI wiring
$$(".tabs button").forEach((b) => b.addEventListener("click", () => {
  $$(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${b.dataset.tab}`));
  document.body.classList.toggle("wide", b.dataset.tab === "results");
  resize();
  if (b.dataset.tab === "results") loadResults();
  if (b.dataset.tab === "about") loadEvidence();
}));
$("#start").addEventListener("click", () => start());
$("#view-reset").addEventListener("click", () => {
  userMoved = false;
  camera.position.set(follow.x - 35, follow.y + 50, follow.z + 45);
});
$("#view-top").addEventListener("click", () => {
  userMoved = false;
  camera.position.set(follow.x, follow.y + 160, follow.z + 1);
});
$$("#speed button").forEach((b) => b.addEventListener("click", () => {
  $$("#speed button").forEach((x) => x.classList.toggle("active", x === b));
  if (run) api(`/api/runs/${run.id}/control`, { speed: Number(b.dataset.speed) }).catch(() => {});
}));
$("#pause").addEventListener("click", async () => {
  if (!run) return;
  const paused = $("#pause").textContent === "Pause";
  await api(`/api/runs/${run.id}/control`, { paused });
  $("#pause").textContent = paused ? "Resume" : "Pause";
});
$$("[data-inject]").forEach((b) => b.addEventListener("click", async () => {
  if (!run) return;
  try {
    const info = await api(`/api/runs/${run.id}/inject`, { kind: b.dataset.inject });
    logItem("operator", `<span class="chip">you</span> injected ${esc(info.kind.replace("_", " "))}${info.drone !== undefined ? ` on drone ${info.drone}` : ""}`);
  } catch (err) { alert(err.message); }
}));
$("#order").addEventListener("click", async () => {
  if (!run) return;
  await api(`/api/runs/${run.id}/inject`, { kind: "directive", changes: { formation: $("#order-shape").value } }).catch((e) => alert(e.message));
});
$("#export").addEventListener("click", async () => {
  if (!run) return;
  const data = await api(`/api/runs/${run.id}/export`);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" }));
  a.download = `halo_${run.scenario.name}_${run.config.replace(/[^a-z0-9]+/gi, "-")}_seed${run.seed}.json`;
  a.click();
});
$("#scenario").addEventListener("change", () => {
  const s = scenarios.find((x) => x.name === $("#scenario").value);
  $("#scenario-desc").textContent = s ? `${s.description} (${s.n} drones; ${s.use_case})` : "";
});
$$(".examples button").forEach((b) => b.addEventListener("click", () => { $("#mission-text").value = b.dataset.ex; }));
$("#compile").addEventListener("click", async () => {
  $("#compile-out").innerHTML = '<p class="muted">Compiling…</p>';
  $("#fly-mission").disabled = true;
  try {
    compiled = await api("/api/compile", { text: $("#mission-text").value });
  } catch (err) { $("#compile-out").innerHTML = `<p>${esc(err.message)}</p>`; return; }
  const s = compiled.spec;
  const by = compiled.source === "nemotron" ? `Nemotron (${esc(compiled.model)})` : compiled.source === "guard" ? "the safety guard" : "the regex baseline";
  if (s.refused) {
    $("#compile-out").innerHTML = `<p><b>Refused</b> by ${by}: ${esc(s.refusal_reason)}</p>`;
    return;
  }
  const rows = [["Drones", s.drones], ["Formation", s.directive.formation], ["Spacing (m)", s.directive.spacing_m],
    ["Speed (m/s)", s.directive.speed_mps], ["Altitude (m)", s.directive.altitude_m], ["Safety margin (m)", s.directive.safety_margin_m],
    ["Route", s.route.type], ["Length (m)", s.route.length_m], ["Area (m)", s.route.width_m ? `${s.route.width_m} x ${s.route.height_m}` : null],
    ["Lane (m)", s.route.lane_m], ["Ceiling (m)", s.constraints.altitude_max_m]];
  $("#compile-out").innerHTML = `<p class="muted small">Parsed by ${by}${compiled.call ? `, ${fmt(compiled.call.latency_s, 2)} s, ${compiled.call.total_tokens} tokens` : ""}. Blank = default.</p>` +
    `<table>${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v === null || v === undefined ? '<span class="muted">default</span>' : esc(v)}</td></tr>`).join("")}</table>` +
    (s.notes ? `<p class="muted small">${esc(s.notes)}</p>` : "") +
    (compiled.problems && compiled.problems.length ? `<p class="muted small">${esc(compiled.problems.join("; "))}</p>` : "");
  $("#fly-mission").disabled = false;
});
$("#fly-mission").addEventListener("click", () => {
  if (!compiled) return;
  $$(".tabs button")[0].click();
  start({ mission: compiled.spec, mission_text: $("#mission-text").value });
});

const HEADLINE = [["completed", "Missions completed (%)", 0], ["time_formed_pct", "Time in formation (%)", 0],
                  ["crashes", "Drones lost per flight", 2]];

function headline(r) {
  const title = (sc) => (scenarios.find((s) => s.name === sc) || {}).title || sc;
  const n = Object.values(r.summary)[0]?.completed?.n;
  let html = `<p class="muted small">Mean per flight${n ? ` over ${n} seeds` : ""}, every configuration on the same seeds.</p>`;
  for (const [metric, name, digits] of HEADLINE) {
    html += `<h3>${name}</h3><table class="headline"><tr><th>Scenario</th>${r.configs.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr>`;
    for (const sc of r.scenarios) {
      html += `<tr><td>${esc(title(sc))}</td>` + r.configs.map((c) => `<td>${fmt(r.summary[`${sc}|${c.id}`]?.[metric]?.mean, digits)}</td>`).join("") + "</tr>";
    }
    html += "</table>";
  }
  return html;
}

async function loadResults() {
  const r = await api("/api/results").catch(() => ({ available: false }));
  if (!r.available) { $("#results").innerHTML = '<p class="muted">No benchmark results yet. Run <code>halo bench</code> then <code>halo report</code>.</p>'; return; }
  let html = headline(r) + "<h3>Charts</h3>" + r.charts.map((c) => `<img src="/results/${encodeURIComponent(c)}" alt="${esc(c)}">`).join("");
  if (r.mission_eval) {
    html += "<h3>Mission compiler accuracy</h3><table><tr><th>Prompt set</th><th>Parser</th><th>Fields right</th><th>All right</th></tr>";
    for (const [set, sum] of Object.entries(r.mission_eval)) {
      for (const [who, s] of Object.entries(sum)) html += `<tr><td>${esc(set)}</td><td>${esc(who)}</td><td>${s.field_accuracy_pct}%</td><td>${s.exact}/${s.prompts}</td></tr>`;
    }
    html += "</table>";
  }
  html += '<p class="muted small">Full tables with confidence intervals: results/REPORT.md.</p>';
  $("#results").innerHTML = html;
}

async function loadEvidence() {
  const e = await api("/api/evidence").catch(() => ({ available: false }));
  if (!e.available) { $("#evidence").textContent = "No end-to-end evidence in this folder yet (run halo doctor)."; return; }
  $("#evidence").innerHTML = `<p>Last check ${esc(e.finished_utc)}: <b>${e.passed ? "passed" : "failed"}</b></p><ul>` +
    e.steps.map((s) => `<li>${s.ok ? "✓" : "✗"} ${esc(s.step)}${s.note ? `: ${esc(s.note)}` : ""}</li>`).join("") + "</ul>" +
    (e.usage ? `<p class="small">${e.usage.calls} calls, ${e.usage.prompt_tokens + e.usage.completion_tokens} tokens</p>` : "");
}

async function init() {
  resize();
  loop();
  scenarios = await api("/api/scenarios");
  $("#scenario").innerHTML = scenarios.map((s) => `<option value="${s.name}">${esc(s.title)}</option>`).join("");
  $("#scenario").value = "forest_trail";
  $("#scenario").dispatchEvent(new Event("change"));
  status = await api("/api/status");
  const pill = $("#ai-pill");
  if (status.ai_available) {
    const name = status.model.split("/").pop();
    pill.textContent = status.mode === "replay" ? `Nemotron: replaying recorded calls (${name})` : `Nemotron on Nebius: ${name}`;
    pill.classList.add("on");
  }
  else { pill.textContent = status.key_present ? "Nemotron: no model set" : "Nemotron not configured (rules only)"; $("#opt-nemotron").disabled = true; }
}
init();
