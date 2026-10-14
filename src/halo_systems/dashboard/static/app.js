// Halo Systems dashboard: live controls and logs. Talks to the local Python server; the 3D view is scene3d.js.
import { SwarmView, DRONE_SCALE } from "/static/scene3d.js";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const api = async (path, body) => {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};
// ---------------------------------------------------------------- scene
const view = new SwarmView($("#scene"));
window.haloView = view;   // for the recorder and for poking at the scene from the browser console

// Film mode (?film=1), for the demo video: the run plays as usual, but what it streams is buffered
// with its simulated time and played back by the recorder one video frame at a time
// (window.haloFilm.step), so the video is smooth however slowly this machine renders.
const FILM = new URLSearchParams(location.search).has("film");
let film = null;
view.film = FILM;
window.haloFilm = {
  status: () => film && { first_s: film.firstT, buffered_s: film.bufT, played_s: film.playT, ended: film.ended, queued: film.queue.length - film.next },
  step(dtVideo, dtSim) {
    if (!film) return null;
    film.playT += dtSim;
    while (film.next < film.queue.length && film.queue[film.next].T <= film.playT + 0.1) {
      const m = film.queue[film.next++];
      m.handler(m.data);
    }
    view.renderFilm(dtVideo, film.playT);
    return film.playT;
  },
};
$("#scale-note").textContent = `Drones drawn ${DRONE_SCALE}x actual size`;

// ---------------------------------------------------------------- live run
let run = null, source = null, spark = [], scenarios = [], status = null, compiled = null;

function fmt(x, d = 1, unit = "") { return x === null || x === undefined ? "–" : `${Number(x).toFixed(d)}${unit}`; }

function setHud(title, d) {
  $("#hud-title").textContent = title;
  $("#hud-directive").textContent = d ? `${d.formation}, ${fmt(d.spacing_m)} m spacing, ${fmt(d.speed_mps)} m/s, ${fmt(d.altitude_m, 0)} m up, margin ${fmt(d.safety_margin_m)} m` : "";
}

function logItem(cls, html) {
  if (film && !film.delivering && cls === "operator") {   // an operator's click shows up at its simulated time
    film.queue.push({ T: film.bufT, handler: () => { film.delivering = true; logItem(cls, html); film.delivering = false; }, data: null });
    return;
  }
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
  view.event(e);
  // Directive changes are logged from the "directive" stream, which has the reason and any validator
  // violations. Injections are logged by the button handlers ("you injected ...").
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
  view.frame(f);
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
    const drones = Number($("#drones").value) || undefined;   // blank = the scenario's own swarm size
    run = await api("/api/runs", { scenario: $("#scenario").value, config: $("#config").value, seed: Number($("#seed").value), speed, n: drones, ...extra });
  } catch (err) { alert(err.message); return; }
  view.build(run);
  $$(".scout-only").forEach((el) => { el.style.display = run.sensor ? "" : "none"; });
  view.setSpeed(speed); view.setPaused(false);
  setHud(run.scenario.title, run.directive);
  $("#pause").textContent = "Pause";
  source = new EventSource(`/api/runs/${run.id}/stream`);
  film = FILM ? { queue: [], next: 0, firstT: null, bufT: 0, playT: 0, ended: false, delivering: false } : null;
  const on = (kind, handler) => source.addEventListener(kind, (e) => {
    const data = JSON.parse(e.data);
    if (!film) return handler(data);
    if (kind === "frame") {
      // Frames sent before the page subscribed never arrive, so playback starts at the first one that did.
      if (film.firstT === null) { film.firstT = data.t; film.playT = Math.max(film.playT, data.t - 0.1); }
      film.bufT = data.t;
    }
    if (kind === "end") film.ended = true;
    film.queue.push({ T: kind === "end" ? film.bufT + 0.05 : film.bufT, handler, data });
  });
  on("frame", onFrame);
  on("directive", onDirective);
  on("ai", onAi);
  on("event", onEvent);
  on("end", (data) => {
    const m = data.metrics || {};
    const crashes = (m.collisions || 0) * 2 + (m.obstacle_crashes || 0) + (m.ground_crashes || 0);   // drones destroyed, as in the Crashes tile
    const done = m.completed == null ? "not applicable (looped route)" : m.completed ? "yes" : "no";
    logItem("event", `<b>Flight ended.</b> In formation ${fmt(m.time_formed_pct, 0)}% of the time, ${crashes} ${crashes === 1 ? "drone" : "drones"} lost in crashes, completed: ${done}`);
    if (source) { source.close(); source = null; }
  });
  if (film) source.addEventListener("end", () => { source.close(); source = null; });
}

// ---------------------------------------------------------------- UI wiring
$$(".tabs button").forEach((b) => b.addEventListener("click", () => {
  $$(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${b.dataset.tab}`));
  document.body.classList.toggle("wide", b.dataset.tab === "results");
  view.resize();
  if (b.dataset.tab === "results") loadResults();
  if (b.dataset.tab === "about") loadEvidence();
}));
$("#start").addEventListener("click", () => start());
for (const [id, mode] of [["view-reset", "follow"], ["view-top", "top"], ["view-orbit", "orbit"], ["view-chase", "chase"]]) {
  $(`#${id}`).addEventListener("click", () => {
    view.setMode(mode);
    $$("#viewbtns button").forEach((b) => b.classList.toggle("active", b.id === id));
  });
}
$$("#speed button").forEach((b) => b.addEventListener("click", () => {
  $$("#speed button").forEach((x) => x.classList.toggle("active", x === b));
  view.setSpeed(Number(b.dataset.speed));
  if (run) api(`/api/runs/${run.id}/control`, { speed: Number(b.dataset.speed) }).catch(() => {});
}));
$("#pause").addEventListener("click", async () => {
  if (!run) return;
  const paused = $("#pause").textContent === "Pause";
  await api(`/api/runs/${run.id}/control`, { paused });
  view.setPaused(paused);
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
