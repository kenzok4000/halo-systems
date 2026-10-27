// Halo Systems dashboard: live controls and logs. Talks to the local Python server; the 3D view is scene3d.js.
import { SwarmView, DRONE_SCALE, PERSON_SCALE, CLEAN } from "/static/scene3d.js";
import { Cinema, dispatchFacts, dispatchHead } from "/static/cinema.js";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const plural = (k, word) => `${k} ${word}${k === 1 ? "" : "s"}`;
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
// Cinema layout (?cinema=1): the view fills the window, with glass cards over it (cinema.js). Its controls are
// hidden, so a flight is started by script (the recorder) or by a link that starts one. ?clean=1 (which implies the
// cinema layout) gives it the clean film look: one badge, the clock, lower-third toasts and only the cards named in
// ?cards= (crew, alerts, dispatch, squads, plan, stats); ?toasts= picks the toasts' kinds, most important first.
const URLP = new URLSearchParams(location.search);
const CINEMA = URLP.get("cinema") ?? (CLEAN ? "1" : null);
const list = (k) => (URLP.get(k) || "").split(",").map((x) => x.trim()).filter(Boolean);
let cine = null;
if (CINEMA !== null) { cine = new Cinema($("#view"), view, { clean: CLEAN, cards: list("cards"), toasts: list("toasts") }); view.resize(); }

// Film mode (?film=1), for the demo video: the run plays as usual, but what it streams is buffered
// with its simulated time and played back by the recorder one video frame at a time
// (window.haloFilm.step), so the video is smooth however slowly this machine renders.
const FILM = new URLSearchParams(location.search).has("film");
let film = null;
view.film = FILM;
window.haloFilm = {
  status: () => film && { first_s: film.firstT, buffered_s: film.bufT, played_s: film.playT, ended: film.ended,
    queued: film.queue.length - film.next, model: view.modelState },
  step(dtVideo, dtSim) {
    if (!film) return null;
    film.playT += dtSim;
    while (film.next < film.queue.length && film.queue[film.next].T <= film.playT + 0.1) {
      const m = film.queue[film.next++];
      m.handler(m.data);
    }
    view.renderFilm(dtVideo, film.playT);
    if (cine) cine.update(view.vt);
    return film.playT;
  },
  // One video frame of the view with no flight to play (before the first one starts, as while a mission is typed).
  idle(dtVideo) {
    view._step(dtVideo, 0, null);
    if (cine) cine.update(view.vt);
  },
  // Jump ahead without filming (the recorder's --from): play the messages up to there, then grow the smoke
  // plumes to their present height, as at the start of a run.
  seek(dtSim) {
    const t = this.step(0, dtSim);
    if (view.wildfire) view.wildfire.warm();
    // What happened in the skipped stretch is not news when the shot starts. The clean look keeps the news of a seek
    // that skips nothing (a shot from the flight's first frame), which arrives with that frame.
    if (cine && (dtSim > 0 || !CLEAN)) cine.clearNotices();
    view.cut = true;                 // and the camera lands where it should be, rather than gliding from where it was
    return t;
  },
};
$("#scale-note").textContent = `Drones drawn up to ${DRONE_SCALE}x actual size`;
if (cine && !FILM) { const tick = () => { cine.update(view.vt); requestAnimationFrame(tick); }; tick(); }

// ---------------------------------------------------------------- live run
let run = null, source = null, spark = [], scenarios = [], status = null, compiled = null;
let valley = null;   // the wildfire valley's station state for the panel: located fires, alerts, smoke reports
// How the wildfire valley's evacuation alerts go out: "auto" (the default), the station's fixed rules send each
// checked alert at once, like Japan's earthquake early warning, and the incident commander can cancel it or give
// a zone the all-clear; "manual", each waits for the commander's tap here (for drills). ?approval=manual picks that.
const APPROVAL = new URLSearchParams(location.search).get("approval") === "manual" ? "manual" : "auto";
// A valley patrol from the Mission tab sends its alerts by the district's pre-signed rules: Level 3 for any located
// fire, smouldering or spreading, forecast to reach the zone; Level 4 and 5 only for a fire believed spreading; and
// every located fire also goes to fire dispatch as a card (simulated). ?alerts=forecast
// flies it with the forecast rule of the other valley runs. ?alert_rule=forecast|presigned|distance (the recorder's
// --alert-rule) flies every valley run under that rule, from the Fly tab too, whose runs otherwise use the forecast rule.
const ALERT_RULE = ((r) => (["forecast", "presigned", "distance"].includes(r) ? r : null))(new URLSearchParams(location.search).get("alert_rule"));
const VALLEY_ALERT_RULE = ALERT_RULE || (new URLSearchParams(location.search).get("alerts") === "forecast" ? "forecast" : "presigned");
// The valley's patrol areas (eval/scenarios.py valley_areas), for a plan before any patrol flies.
const AREA_NAME = { F: "Valley floor", N: "North slopes", S: "South slopes" };

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
  return li;
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// The rule supervisor works out a squeezed spacing from formation widths rounded to 0.1 m, so it can send
// its plan again a centimetre off every 5 s. An accepted plan that matches the newest logged one at the
// precision shown here (formation, spacing, speed, altitude and margin, same source and reason) is counted
// in that entry rather than logged again. Any change that shows gets its own entry.
let lastPlan = null;   // the newest logged plan change: { key, li, n, more }
const planKey = (src, d) => [src, d.formation, fmt(d.spacing_m), fmt(d.speed_mps), fmt(d.altitude_m), fmt(d.safety_margin_m), d.reason || ""].join("|");

function onDirective(d) {
  const src = d.action === "rejected" ? "rejected" : (d.source || "");
  if (d.action === "unchanged") {
    logItem(src, `<span class="chip ${esc(src)}">${esc(src)}</span> t=${fmt(d.t)} s keeps the plan <span class="why">${esc(d.reason)}</span>`);
    return;
  }
  const plain = d.action === "accepted" && !(d.violations && d.violations.length);
  const key = planKey(src, d);
  if (plain && lastPlan && lastPlan.key === key && lastPlan.li.isConnected) {
    lastPlan.n++;
    lastPlan.more.textContent = `${lastPlan.n === 1 ? "Once more" : `${lastPlan.n} more times`} to t=${fmt(d.t)} s, with changes below the precision shown`;
    return;
  }
  if (cine) {
    const who = { rule: "Rule supervisor", nemotron: "Nemotron supervisor", operator: "You" }[src] || "Plan";
    if (d.action === "rejected") cine.notice("bad", "Plan change refused by the safety check", (d.violations || []).join("; ") || d.reason);
    else { cine.planBy(src, d.reason); cine.notice(src === "nemotron" ? "nemotron" : "plan", `${who}: ${d.formation}, ${fmt(d.spacing_m)} m apart, ${fmt(d.speed_mps)} m/s`, d.reason); }
  }
  const viol = d.violations && d.violations.length ? `<div class="why">validator: ${esc(d.violations.join("; "))}</div>` : "";
  const li = logItem(src, `<span class="chip ${esc(src)}">${esc(src)}</span> t=${fmt(d.t)} s <b>${esc(d.action)}</b>: ${esc(d.formation)}, ${fmt(d.spacing_m)} m, ${fmt(d.speed_mps)} m/s` +
    `${d.reason ? `<div class="why">${esc(d.reason)}</div>` : ""}${viol}<div class="why more"></div>`);
  lastPlan = plain && li ? { key, li, n: 0, more: li.querySelector(".more") } : null;
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
  if (onAirspaceEvent(e)) return;   // aircraft inbound: the hold's orders and outcomes
  // Directive changes are logged from the "directive" stream, which has the reason and any validator
  // violations. Injections are logged by the button handlers ("you injected ...").
  const skip = { directive: 1, injected: 1 };
  // A squad order is logged from its event (onValleyEvent), as the run reports it; other injections are not.
  if (skip[e.kind] && !(valley && e.kind === "injected" && e.what === "squad")) return;
  if (valley && onValleyEvent(e)) return;
  // One function per kind: only the matching one runs, since each reads fields only its kind has.
  const text = { motor_failure: () => `motor failure on drone ${e.drone}`,
    gust_front: () => `gust front ${fmt(Math.hypot(...(e.velocity_mps || [0, 0]).slice(0, 2)))} m/s`,
    comms_blackout: () => `radio blackout for ${(e.drones || []).length} drone${(e.drones || []).length === 1 ? "" : "s"}`,
    collision: () => `collision: drones ${e.drone} and ${e.other}`,
    obstacle_crash: () => `drone ${e.drone} hit an obstacle`, ground_crash: () => `drone ${e.drone} hit the ground`,
    retired_low_battery: () => `drone ${e.drone} left at ${e.battery_pct}% battery`, mission_complete: () => "mission complete",
    target_found: () => e.what === "person" ? `missing person found by drone ${e.drone} at (${fmt(e.x, 0)}, ${fmt(e.y, 0)}) m`
      : `drone ${e.drone} found ${e.what || "target"} ${e.target + 1} at (${fmt(e.x, 0)}, ${fmt(e.y, 0)}) m`,
    directive_rejected: () => "a directive was rejected" }[e.kind];
  const what = text ? text() : e.kind;
  logItem("event", `t=${fmt(e.t)} s ${esc(what)}`);
  if (cine && e.kind === "target_found" && e.what === "person") {   // a missing person: who found them, then where
    const hidden = ((run && run.targets) || [])[e.target]?.cover > 0;
    cine.notice("station", `Missing person found by drone ${e.drone}`, `At (${fmt(e.x, 0)}, ${fmt(e.y, 0)}) m, ${hidden ? "partly under tree crowns" : "in the open"}`);
  } else if (cine) cine.notice({ motor_failure: "bad", collision: "bad", obstacle_crash: "bad", ground_crash: "bad", target_found: "fire", mission_complete: "plan" }[e.kind] || "event",
    what.charAt(0).toUpperCase() + what.slice(1));
}

// The wildfire valley: the ground station's finds, forecasts and alerts, in the log and the Fire station panel.
// Returns true when the event is handled here.
function onValleyEvent(e) {
  const t = `t=${fmt(e.t)} s`;
  switch (e.kind) {
    case "smoke_sighted": case "smoke_contact":
      valley.smoke++;   // hundreds per flight: counted in the panel, drawn as bearing lines, not logged one by one
      return true;
    case "target_found":   // the same find is logged as fire_located (thermal)
      return true;
    case "fire_located": {
      const how = e.by === "thermal" ? `confirmed by a thermal camera, ±${fmt(e.err_m, 0)} m` : `located from smoke bearings, ±${fmt(e.err_m, 0)} m`;
      logItem("station", `<span class="chip station">station</span> ${t} Fire ${e.fire + 1} ${how}`);
      if (cine) cine.notice("fire", `Fire ${e.fire + 1} located`, how.charAt(0).toUpperCase() + how.slice(1));
      return true;
    }
    case "fire_merged":   // two finds on one patch of burning ground are one fire
      logItem("station", `<span class="chip station">station</span> ${t} Fire ${e.fire + 1} is the same fire as Fire ${e.into + 1}; merged`);
      return true;
    case "forecast": {
      const lines = (e.brief || []).map((b) => `<div class="why">${esc(b.text)}</div>`).join("");
      logItem("station", `<span class="chip station">station</span> ${t} spread forecast ${e.forecast + 1} for the fire crews${lines}`);
      valley.brief = e.brief || [];
      valley.briefT = e.t;
      // A notice only when the brief says something new: a fire added, or one changing state or direction.
      // (Runs whose briefs carry headlines send crew updates for that instead, below.)
      const keys = valley.brief.map((b) => `${b.fire}:${b.state}:${b.direction}`), key = keys.join("|");
      if (cine && valley.brief.length && key !== valley.briefKey && !valley.brief.some((b) => b.headline)) {
        const old = new Set((valley.briefKey || "").split("|")), b = valley.brief[Math.max(0, keys.findIndex((k) => !old.has(k)))];
        const ifs = b.state === "smouldering" ? " (smouldering): if it takes off, head" : ": head";
        cine.notice("station", "New spread forecast", b.head_m_per_min > 0
          ? `Fire ${b.fire + 1}${ifs} runs about ${fmt(b.head_m_per_min)} m/min ${b.direction}` : `Fire ${b.fire + 1}: not spreading`);
      }
      valley.briefKey = key;
      drawValley();
      return true;
    }
    case "crew_update": {   // the station's update to the fire crews' tablets and radios (simulated devices)
      const why = e.reason ? `${e.reason.charAt(0).toUpperCase()}${e.reason.slice(1)}` : "";
      logItem("station", `<span class="chip station">crews</span> ${t} ${esc(e.headline || `Fire ${e.fire + 1}`)}${why ? `: ${esc(why.toLowerCase())}` : ""}` +
        `${e.text ? `<div class="why">${esc(e.text)}</div>` : ""}`);
      valley.told[e.fire] = e.t;
      if (cine) {
        // The updates sent with one forecast share a notice. It leads with the one that matters most: a spreading
        // fire on the move, then a new fire, then one that now reaches a place, then the fastest; withdrawals last.
        const key = `crew@${e.t}`, d = cine.live(key) || { all: [] };
        d.all.push(e);
        const gone = (u) => u.head_bearing_deg == null && /^(merged into|no longer tracked)/.test(u.reason || "");
        const rank = (u) => gone(u) ? 4 : u.head_bearing_deg != null && !/smouldering/.test(u.headline || "") ? 0
          : /new fire/.test(u.reason || "") ? 1 : /reaches/.test(u.reason || "") ? 2 : 3;
        const lead = d.all.slice().sort((a, b) => rank(a) - rank(b) || (b.head_m_per_min || 0) - (a.head_m_per_min || 0))[0];
        const lr = lead.reason ? `${lead.reason.charAt(0).toUpperCase()}${lead.reason.slice(1)}` : "";
        const more = d.all.length - 1, fresh = d.all.filter((u) => u !== lead && /new fire/.test(u.reason || "")).map((u) => `Fire ${u.fire + 1}`);
        cine.notice("crew", `Crew update: ${lead.headline || `Fire ${lead.fire + 1}`}`,
          `${gone(lead) ? "Disregard its earlier brief" : lr}${more ? ` · ${more} more fire${more === 1 ? "" : "s"} updated` : ""}` +
          `${fresh.length ? ` (new: ${fresh.join(", ")})` : ""} · to ${lead.to || "crew tablets and radio (simulated)"}`, key, d);
      }
      drawValley();
      return true;
    }
    case "alert_drafted":
      valley.alerts.set(e.alert, { id: e.alert, level: e.level, zone: typeof e.zone === "string" ? e.zone : null, phones: e.phones,
        message: e.message, state: "checked", check: null });
      logItem("station", `<span class="chip station">station</span> ${t} drafted alert ${e.alert + 1}: ${levelName(e.level)}${zoneText(e.zone)} for ${plural(e.phones, "simulated phone")}` +
        `${e.channel ? ` by ${esc(e.channel)}` : ""}<div class="why">${esc(e.message)}</div>`);
      drawValley();
      return true;
    case "alert_check": {
      const a = valley.alerts.get(e.alert);
      if (a) { a.check = e; if (!e.ok) a.state = "rejected"; }
      logItem("station", `<span class="chip station">station</span> ${t} alert ${e.alert + 1} ${e.ok ? `passed the alert check${valley.approval === "auto" ? "" : "; waiting for approval"}` : `rejected by the alert check: ${esc((e.reasons || []).join("; "))}`}`);
      drawValley();
      return true;
    }
    case "alert_approved": {
      const a = valley.alerts.get(e.alert);
      if (a) a.by = e.by || "";
      // Under the pre-signed rules an automatic alert at level L goes out under trigger rule TL (the alert check
      // refuses one without it).
      const how = valley.rule === "presigned" && a && typeof a.level === "number"
        ? `sent at once under pre-signed rule T${a.level}, signed in advance (simulated)` : "sent at once by the station's fixed rules";
      logItem("station", `<span class="chip station">station</span> ${t} alert ${e.alert + 1} ${/^automatic/.test(e.by || "") ? how : `approved by ${esc(e.by)}`}`);
      return true;
    }
    case "alert_delivered": {
      const a = valley.alerts.get(e.alert);
      if (a) { a.state = "delivered"; a.delivered = e.phones; a.of = e.of; }
      logItem("station", `<span class="chip station">station</span> ${t} alert ${e.alert + 1} delivered to ${e.phones} of ${plural(e.of, "simulated phone")}`);
      if (cine && a) {
        // Alerts of one level sent the same way while its notice is still up share it: "zones F2, F4 and N3".
        const auto = /^automatic/.test(a.by || ""), key = `alert:${a.level}:${auto ? "auto" : a.by || ""}`;
        const d = cine.live(key) || { zones: [], phones: 0, of: 0 };
        if (a.zone) d.zones.push(a.zone);
        d.phones += e.phones || 0; d.of += e.of || 0;
        const z = a.zone ? valley.zones.find((q) => q.code === a.zone) : null, lv = valley.levels && valley.levels[String(a.level)];
        const lvl = typeof a.level === "number" ? `Level ${a.level}` : levelName(a.level);
        const where = d.zones.length > 1 ? `s: zones ${d.zones.slice(0, -1).join(", ")} and ${d.zones[d.zones.length - 1]}` : a.zone ? `: zone ${a.zone}${z && z.name ? ` ${z.name}` : ""}` : "";
        cine.notice("alert", `${lvl} alert${where}`,
          `${lv && lv.head ? `${lv.head.charAt(0)}${lv.head.slice(1).toLowerCase()} · ` : ""}${auto ? (valley.rule === "presigned" && typeof a.level === "number" ? `sent automatically under pre-signed rule T${a.level}` : "sent automatically") : `approved by ${a.by || "the incident commander"}, sent`} to ${d.phones} of ${plural(d.of, "simulated phone")}`, key, d);
      }
      drawValley();
      return true;
    }
    case "dispatch_card": {   // pre-signed alert rule: a new fire, a smoulder now spreading or a fix that moved, to fire dispatch (simulated)
      const was = [...valley.dispatch].reverse().find((c) => c.fire === e.fire);   // the fire's card before this one
      e.was = was ? was.status : null;
      valley.dispatch.push(e);
      logItem("dispatch", `<span class="chip dispatch">dispatch</span> ${t} ${esc(e.text)}<div class="why">to ${esc(e.to)}</div>`);
      if (cine) {
        // Cards sent with one forecast share a notice, led by a smoulder now spreading, then a new spreading fire.
        const key = `dispatch@${e.t}`, all = (cine.live(key) || { all: [] }).all.concat([e]);
        const rank = (c) => (c.update ? (c.was !== c.status ? 0 : 2) : c.status === "spreading" ? 1 : 3);
        const [lead, ...rest] = all.slice().sort((a, b) => rank(a) - rank(b) || a.id - b.id);
        const d = dispatchFacts(lead), near = d.near ? `, near ${d.near}` : "";
        const sub = [lead.update && !d.moved ? (d.head ? (d.head.startsWith("not ") ? `Head ${d.head}` : `Head running ${d.head}`) : "") : `Within ${d.within} m, located by ${lead.by}`,
          rest.length ? `also ${rest.map((c) => (c.update ? `update on fire ${c.fire + 1}` : `new fire ${c.fire + 1}`)).join(", ")}` : "",
          "initial attack requested (simulated)"].filter(Boolean).join(" · ");
        cine.notice("dispatch", `Fire dispatch${lead.update ? " " : ": "}${dispatchHead(lead, lead.was)}${near}`,
          sub.charAt(0).toUpperCase() + sub.slice(1), key, { all });
      }
      drawValley();
      return true;
    }
    case "injected":   // a squad order sent from this page or the recorder; the run carries it out at its next step
      // Its arrival time depends on the machine, so a film leaves it out; squad_changed reports the order exactly.
      if (FILM) return true;
      logItem("squad", `<span class="chip squad">you</span> ${t} ordered ${esc(e.k)} ${e.k === 1 ? "drone" : "drones"} from the ${esc(squadName(e.from))} squad to the ${esc(squadName(e.to))}`);
      return true;
    case "squad_changed": {   // drones left one squad for another, which they fly across to join; the source is re-planned
      const k = (e.drones || []).length, from = squadName(e.from), to = squadName(e.to);
      for (const s of e.squads || []) squadDef(s);
      const src = (e.squads || []).find((s) => s.code === e.from), one = k === 1;
      logItem("squad", `<span class="chip squad">squads</span> ${t} ${plural(k, "drone")} (${esc((e.drones || []).join(", "))}) ${one ? "leaves" : "leave"} the ${esc(from)} for the ${esc(to)}` +
        `${src ? `<div class="why">The ${esc(from)} squad is re-planned on ${plural(src.lanes, "lane")}; the moved ${one ? "drone joins" : "drones join"} the ${esc(to)} squad in flight</div>` : ""}`);
      if (cine) cine.notice(`squad sq-${sqCode(e.to)}`, `${plural(k, "drone")}: ${from} to ${to}`,
        src ? `${from} re-planned on ${plural(src.lanes, "lane")} · the moved ${one ? "drone flies" : "drones fly"} across to join` : "");
      drawValley();
      return true;
    }
    case "squad_joined": {
      const now = e.squad_now || {}, name = squadName(e.squad);
      if (e.squad_now) squadDef(e.squad_now);
      logItem("squad", `<span class="chip squad">squads</span> ${t} drone ${e.drone} joined the ${esc(name)} squad${now.n ? `, now ${plural(now.n, "drone")} on ${now.lanes} lanes` : ""}`);
      if (cine) {   // drones joining one squad while its notice is up share it
        const key = `join:${e.squad}`, d = cine.live(key) || { drones: [] };
        d.drones.push(e.drone);
        cine.notice(`squad sq-${sqCode(e.squad)}`, `${d.drones.length === 1 ? "A drone" : `${d.drones.length} drones`} joined the ${name}`,
          now.n ? `Now ${plural(now.n, "drone")} on ${now.lanes} lanes` : "", key, d);
      }
      drawValley();
      return true;
    }
    case "squad_change_rejected": {
      const o = e.order || {}, k = `${o.k} ${o.k === 1 ? "drone" : "drones"}`;
      logItem("rejected", `<span class="chip">refused</span> ${t} squad order (move ${esc(k)} from the ${esc(squadName(o.from))} to the ${esc(squadName(o.to))}) refused by the safety check: ` +
        `${esc((e.reasons || []).join("; "))}`);
      if (cine) cine.notice("bad", `Squad move refused: ${k}, ${squadName(o.from)} to ${squadName(o.to)}`, (e.reasons || []).join("; "));
      return true;
    }
    case "alert_cancelled": case "all_clear": {
      const a = e.alert != null ? valley.alerts.get(e.alert) : null;
      if (a) a.state = "cancelled";
      const what = e.kind === "all_clear" ? `all-clear${zoneText(e.zone)}` : `alert ${e.alert + 1}${zoneText(e.zone)} cancelled`;
      if (cine) cine.notice("plan clear", e.kind === "all_clear" ? `All-clear for zone ${e.zone}` : `Alert ${e.alert + 1} cancelled`, `${plural(e.phones ?? 0, "simulated phone")} told`);
      logItem("station", `<span class="chip station">station</span> ${t} ${what} by ${esc(e.by || "the station")}; ${plural(e.phones ?? 0, "simulated phone")} told` +
        `${e.message ? `<div class="why">${esc(e.message)}</div>` : ""}`);
      drawValley();
      return true;
    }
  }
  return false;
}

// Squad runs (a valley patrol): the squads' names and sizes, kept current from squad_changed and squad_joined.
const squadName = (c) => (valley && valley.squadRun && valley.squadRun.names[c]) || AREA_NAME[c] || String(c ?? "");
const sqCode = (c) => (/^[A-Z]$/.test(String(c)) ? c : "x");
function squadDef(s) {
  const sr = valley && valley.squadRun;
  if (!sr || !s || !s.code) return;
  sr.defs.set(s.code, { code: s.code, name: s.name || squadName(s.code), n: s.n, lanes: s.lanes });
}
// Each frame's squads; laps add up the circuits flown, as a squad re-planned after a move or a join starts its new
// circuit at 0.
function squadFrame(list) {
  const sr = valley.squadRun;
  sr.now = list;
  for (const s of list) {
    const l = sr.laps[s.code] || (sr.laps[s.code] = { base: 0, last: 0 });
    if (s.passes < l.last - 0.02) l.base += l.last;
    l.last = s.passes;
  }
}

// Alert levels: Japan's five-level evacuation information, of which the station raises Levels 3, 4 and 5
// (runs from before the zones used "warning" and "evacuate").
function levelName(l) {
  if (typeof l !== "number") return l === "evacuate" ? "Evacuate" : "Warning";
  const lv = valley.levels && valley.levels[String(l)];
  return `Level ${l}${lv && lv.head ? ` (${esc(lv.head)})` : ""}`;
}
function zoneText(code) {
  if (typeof code !== "string") return "";
  const z = valley.zones.find((q) => q.code === code);
  return ` in zone ${esc(code)}${z && z.name ? ` ${esc(z.name)}` : ""}`;
}

function drawValley() {
  if (!valley) return;
  $("#fc-when").textContent = valley.briefT != null ? `forecast at t=${fmt(valley.briefT, 0)} s` : "";
  const brief = valley.brief.length ? valley.brief.map((b) => `<li>${esc(b.text)}</li>`).join("")
    : '<li class="muted">Waiting for the first fire to be located…</li>';
  if (brief !== valley.briefHtml) { $("#brief").innerHTML = valley.briefHtml = brief; }
  // Raised zones, highest level first, each with the incident commander's all-clear.
  let zones = "";
  if (valley.zones.length) {
    const up = valley.zones.map((z, i) => ({ ...z, level: valley.zonesLevel[i] || 0 })).filter((z) => z.level > 0).sort((a, b) => b.level - a.level);
    zones = up.length ? up.map((z) => `<li class="zone-row"><span class="lvl l${z.level}">Level ${z.level}</span> ${esc(z.code)} ${esc(z.name || "")}` +
      `${valley.ended ? "" : ` <button data-allclear="${esc(z.code)}">All clear</button>`}</li>`).join("")
      : '<li class="muted">No zone raised yet.</li>';
  }
  if (zones !== valley.zonesHtml) { $("#zones").innerHTML = valley.zonesHtml = zones; }
  // The latest alerts, newest first. Only a zone's newest alert still in force can be cancelled: an older
  // one has been replaced by it, and a zone given the all-clear has none in force.
  const newest = new Map(), level = new Map(valley.zones.map((z, i) => [z.code, valley.zonesLevel[i] || 0]));
  for (const a of valley.alerts.values()) if (a.zone && ["checked", "approved", "delivered"].includes(a.state)) newest.set(a.zone, Math.max(newest.get(a.zone) ?? -1, a.id));
  const cancellable = (a) => a.state === "delivered" && a.zone && newest.get(a.zone) === a.id && level.get(a.zone) > 0;
  const items = [...valley.alerts.values()].reverse().slice(0, 6).map((a) => {
    const state = a.state === "delivered" ? (a.delivered != null ? `delivered to ${a.delivered} of ${plural(a.of, "phone")}` : "delivered")
      : a.state === "rejected" ? "rejected by the alert check" : a.state === "cancelled" ? "cancelled"
      : a.state === "superseded" ? "replaced by a newer alert" : a.state === "cleared" ? "ended by the all-clear"
      : valley.ended ? "not approved before the flight ended" : valley.approval === "auto" ? "checked; sending" : "checked; waiting for the incident commander";
    const btn = valley.ended ? "" : a.state === "checked" && valley.approval !== "auto" ? `<button data-approve="${a.id}">Approve and send</button>`
      : cancellable(a) ? `<button data-cancel="${a.id}">Cancel alert</button>` : "";
    const lvl = typeof a.level === "number" ? `<span class="lvl l${a.level}">${levelName(a.level)}</span>` : `<b>${levelName(a.level)}</b>`;
    return `<li class="alert ${esc(String(a.level))} ${esc(a.state)}">${lvl}${a.zone ? ` · ${esc(a.zone)}` : ""} · ${plural(a.phones, "phone")} · ${state}` +
      `<div class="why">${esc(a.message)}</div>${btn}</li>`;
  });
  const html = items.length ? items.join("") : '<li class="muted">No alert drafted yet.</li>';
  if (html !== valley.alertsHtml) { $("#alerts").innerHTML = valley.alertsHtml = html; }   // frames come at 10 Hz: keep the buttons stable
  $("#valley-stats").textContent = `${plural(valley.located, "fire")} located · ${plural(valley.smoke, "smoke report")} from the drones · ${plural(valley.warned, "phone")} alerted`;
  const sr = valley.squadRun;
  if (sr) {   // the squads, each with its drones flying, laps of its circuit and drones on their way to it
    const now = new Map((sr.now || []).map((s) => [s.code, s]));
    const html = [...sr.defs.values()].map((d) => {
      const f = now.get(d.code), l = sr.laps[d.code], join = f && f.joining ? f.joining.length : 0;
      return `<li><i class="sqdot sq-${sqCode(d.code)}"></i><b>${esc(d.name)}</b> <span class="k">${f ? f.flying : d.n} flying` +
        `${l ? ` · ${fmt(l.base + l.last)} laps` : ""}${join ? ` · ${join} joining` : ""}</span></li>`;
    }).join("");
    if (html !== sr.html) $("#squads").innerHTML = sr.html = html;
  }
  drawAir();
  if (cine) cine.valley(valley);
}

// The incident commander's actions on alerts (simulated phones only): approve a checked alert, cancel a
// sent one, or give a zone the all-clear. Each returns the server's answer, so the recorder can tell
// whether it worked; the recorder acts through these, the same actions as the panel's buttons.
async function commanderAction(route, body, what) {
  if (!run) return { ok: false, reason: "no flight" };
  try {
    const out = await api(`/api/runs/${run.id}/${route}`, body);
    if (!out.ok) throw new Error(out.reason);
    logItem("operator", `<span class="chip">you</span> ${what} as the incident commander`);
    return out;
  } catch (err) { alert(err.message); return { ok: false, reason: err.message }; }
}
const approveAlert = (id) => commanderAction("approve", { alert: id }, `approved alert ${id + 1}`);
const cancelAlert = (id) => commanderAction("cancel", { alert: id }, `cancelled alert ${id + 1}`);
const allClear = (zone) => commanderAction("allclear", { zone }, `gave zone ${esc(zone)} the all-clear`);
Object.assign(window, { haloApprove: approveAlert, haloCancel: cancelAlert, haloAllClear: allClear });

// A squad order (valley patrols with squads): move k drones from one area's squad to another's. The safety check
// runs in the simulator, so the outcome arrives as events (squad_changed or squad_change_rejected). at_s (optional,
// simulated seconds) has the run carry it out at its first step at or after then, as the recorder does.
async function squadMove(from, to, k, at_s = null) {
  if (!run) return { ok: false, reason: "no flight" };
  if (valley && valley.ended) return { ok: false, reason: "the flight has ended" };
  const body = { kind: "squad", op: "move", from, to, k: Number(k) };
  if (at_s != null) body.at_s = Number(at_s);
  try {
    return { ok: true, ...(await api(`/api/runs/${run.id}/inject`, body)) };
  } catch (err) { alert(err.message); return { ok: false, reason: err.message, alerted: true }; }
}
window.haloSquadMove = squadMove;

// ---------------------------------------------------------------- aircraft inbound (wildfire valley)
// When crewed firefighting aircraft come into the valley, the operator orders an airspace hold: every drone stops and
// drops low, out of their way, until the operator resumes the sweep. The simulator does not detect aircraft; the order
// is the operator's. The safety validator checks it in the simulator, so the outcome arrives as an event
// (airspace_hold, airspace_resume or airspace_rejected). In film mode the control appears with the first of those, as
// a film draws only what plays at its simulated time.
let air = null;   // the hold's state for the panel (wildfire valley runs)
const heldFor = (s) => { const k = Math.round(s); return k < 60 ? `${k} s` : `${Math.floor(k / 60)} min${k % 60 ? ` ${k % 60} s` : ""}`; };
function airReset() {
  air = run && run.wildfire ? { holding: false, altitude_m: null, since_s: null, pending: null, note: "", shown: !FILM, key: "" } : null;
  drawAir();
}
function drawAir() {
  $("#aircraft-ctl").hidden = !(air && air.shown);
  if (!air) return;
  const ended = !!(valley && valley.ended);
  if (ended) air.pending = null;   // an order still waiting when the flight ends is never carried out
  const label = air.pending === "hold" ? "Hold ordered…" : air.pending === "resume" ? "Resume ordered…" : air.holding ? "Resume sweep" : "Aircraft inbound: hold low";
  const state = air.holding ? `Holding ${fmt(air.altitude_m, 0)} m above the treetops since t=${fmt(air.since_s, 0)} s` : "";
  const key = [label, ended, state, air.note].join("|");
  if (key === air.key) return;
  air.key = key;
  const b = $("#aircraft");
  b.textContent = label;
  b.disabled = ended || !!air.pending;
  b.classList.toggle("holding", air.holding);
  $("#aircraft-state").innerHTML = `${esc(state)}${air.note ? `<span class="refused">${esc(air.note)}</span>` : ""}`;
}
// op "hold" (altitude_m: metres above the treetops, or the ground where there are none; the validator's default when
// null) or "resume"; at_s (simulated seconds) has the run carry it out at its first step at or after then, as the
// recorder does.
async function aircraftOrder(op, at_s = null, altitude_m = null) {
  if (!run) return { ok: false, reason: "no flight" };
  if (!run.wildfire) return { ok: false, reason: "the aircraft hold is for wildfire valley runs" };
  if (valley && valley.ended) return { ok: false, reason: "the flight has ended" };
  if (op !== "hold" && op !== "resume") return { ok: false, reason: "op must be hold or resume" };
  if (at_s != null && !Number.isFinite(Number(at_s))) return { ok: false, reason: "at_s must be a time in seconds" };
  if (altitude_m != null && !Number.isFinite(Number(altitude_m))) return { ok: false, reason: "altitude_m must be metres above the treetops" };
  const body = { kind: "aircraft", op }, mine = air;
  if (at_s != null) body.at_s = Number(at_s);
  if (altitude_m != null) body.altitude_m = Number(altitude_m);
  // Live only (a film shows what plays): the button waits from the click, as the outcome's event can come before the
  // server's reply; onAirspaceEvent clears it.
  const live = !FILM && mine;
  if (live) { air.pending = op; air.note = ""; drawAir(); }
  try {
    const out = await api(`/api/runs/${run.id}/inject`, body);
    return { ok: true, ...out };
  } catch (err) {
    if (live && mine === air) { air.pending = null; drawAir(); }
    alert(err.message); return { ok: false, reason: err.message, alerted: true };
  }
}
window.haloAircraft = aircraftOrder;
$("#aircraft").addEventListener("click", async () => {
  if (!air || air.pending) return;
  const r = await aircraftOrder(air.holding ? "resume" : "hold");
  if (!r.ok && !r.alerted) alert(`No order: ${r.reason}.`);
});
// The hold's events, in the log and as cinema notices. Returns true when the event is one of them.
function onAirspaceEvent(e) {
  const byOperator = !e.source || e.source === "operator", t = `t=${fmt(e.t)} s`, by = byOperator ? "the operator" : esc(e.source);
  const who = byOperator ? "Operator" : e.source;   // named in cinema notices: the order is a person's, not a detection
  if (e.kind === "injected") {
    if (e.what !== "aircraft") return false;
    // Its arrival time depends on the machine, so a film leaves it out; airspace_hold and airspace_resume report it exactly.
    if (!FILM) logItem("aircraft", `<span class="chip aircraft">you</span> ${t} ordered ${e.op === "resume" ? "the sweep resumed"
      : `a hold${e.altitude_m != null ? ` at ${fmt(e.altitude_m, 0)} m` : ""}: crewed firefighting aircraft inbound`}`);
    return true;
  }
  if (!["airspace_hold", "airspace_resume", "airspace_rejected"].includes(e.kind)) return false;
  const lower = !!(air && air.holding && e.kind === "airspace_hold");
  if (air) { air.shown = true; air.pending = null; air.note = ""; }
  if (e.kind === "airspace_hold") {
    const alt = fmt(e.altitude_m, 0);
    if (air) Object.assign(air, { holding: true, altitude_m: e.altitude_m, since_s: lower ? air.since_s : e.t });
    logItem("aircraft", `<span class="chip aircraft">airspace</span> ${t} ${lower ? `hold lowered: every drone down to ${alt} m above the treetops`
      : `hold for crewed firefighting aircraft: every drone stops ${alt} m above the treetops, out of their way`}; ordered by ${by}, checked by the safety validator` +
      '<div class="why">The station keeps watching for smoke; the sweep resumes when the operator says the airspace is clear</div>');
    if (cine) cine.notice("aircraft", lower ? `${who}: swarm holding lower` : `${who}: aircraft inbound, swarm holding low`, lower
      ? `Every drone down to ${alt} m above the treetops · checked by the safety validator`
      : `Every drone stops ${alt} m above the treetops, out of the aircraft's way · checked by the safety validator · the station keeps watching`);
  } else if (e.kind === "airspace_resume") {
    if (air) Object.assign(air, { holding: false, altitude_m: null, since_s: null });
    logItem("aircraft", `<span class="chip aircraft">airspace</span> ${t} airspace clear: sweep resumed by ${by} after holding ${heldFor(e.held_s)}` +
      '<div class="why">The drones climb back to their working altitude and carry on from where they stopped</div>');
    if (cine) cine.notice("aircraft", `${who}: airspace clear, sweep resumed`, `Held ${heldFor(e.held_s)} · the drones climb back and carry on the sweep from where they stopped`);
  } else {
    const why = (e.violations || []).join("; ");
    if (air) air.note = `${e.op === "resume" ? "Resume" : "Hold"} refused by the safety validator: ${why}`;
    logItem("rejected", `<span class="chip">refused</span> ${t} airspace order (${esc(e.op)}) refused by the safety validator: ${esc(why)}`);
    if (cine) cine.notice("bad", `${e.op === "resume" ? "Resume" : "Hold"} refused by the safety validator`, why);
  }
  drawAir();
  return true;
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
  const people = run && run.targets && run.targets.length && run.targets.every((t) => t.kind === "person");
  const finds = !people ? `hotspots ${m.targets_found}/${m.targets_total}` : m.targets_total === 1
    ? `missing person ${m.targets_found ? "found" : "not found yet"}` : `missing people found ${m.targets_found}/${m.targets_total}`;
  let scout = m.targets_total != null ? `, ${finds}, area ${fmt(m.coverage_pct, 0)}%` : "";
  if (valley) {
    valley.located = (f.located || []).length;
    valley.warned = (f.phones_alerted || []).filter((l) => l > 0).length;
    if (f.zones_level) valley.zonesLevel = f.zones_level;
    if (f.squads && valley.squadRun) squadFrame(f.squads);
    scout = `, fires located ${valley.located}, area ${fmt(m.coverage_pct, 0)}%`;
    // "delivered" comes with its phone counts in the alert_delivered event, just after this frame.
    if (f.alerts) for (const a of f.alerts) { const k = valley.alerts.get(a.id); if (k && k.state !== a.state && a.state !== "approved" && a.state !== "delivered") k.state = a.state; }
    drawValley();
  }
  setHud(`${run.scenario.title}, t = ${fmt(f.t)} s${scout}`, f.directive);
  if (cine) cine.frame(f, valley, Number($("#speed").dataset.playback || $("#speed .active")?.dataset.speed || 1));
  $("#ai-wait").textContent = f.ai_pending ? "Nemotron is thinking…" : "";
  spark.push([f.t, m.formation_error_m]);
  if (spark.length > 600) spark.shift();
  drawSpark();
}

async function start(extra = {}) {
  if (source) { source.close(); source = null; }
  if (run) api(`/api/runs/${run.id}/control`, { stop: true }).catch(() => {});
  $("#log").innerHTML = "";
  lastPlan = null;
  spark = [];
  const speed = Number($("#speed .active").dataset.speed);
  try {
    const drones = Number($("#drones").value) || undefined;   // blank = the scenario's own swarm size
    const body = { scenario: $("#scenario").value, config: $("#config").value, seed: Number($("#seed").value), speed, n: drones,
      approval: APPROVAL, ...(ALERT_RULE ? { alert_rule: ALERT_RULE } : {}), ...extra };
    run = await api("/api/runs", body);
    run.alertRule = body.alert_rule || "forecast";   // the rule the valley's alerts go out under (the server's default)
  } catch (err) { alert(err.message); return; }
  view.build(run);
  const hiker = (run.targets || []).some((t) => t.kind === "person");   // a missing person, drawn larger too, not a hotspot
  $("#scale-note").textContent = `Drones drawn up to ${DRONE_SCALE}x${hiker ? ` and the missing person up to ${PERSON_SCALE}x` : ""} actual size`;
  // What is flying, above the run controls: the lab controls that chose it may be folded away.
  $("#run-desc").innerHTML = `<b>${esc(run.scenario.title)}</b>, in simulation: ${plural(run.n, "drone")}, ` +
    `${esc($("#config").selectedOptions[0].textContent)}, seed ${run.seed}<span class="desc">${esc(run.scenario.description)}</span>`;
  if (cine) {
    // The side panel's disclosures, which the cinema layout hides: replayed calls and the drones' drawn size.
    const replay = /nemotron/.test($("#config").value) && status && status.mode === "replay" ? "Nemotron replayed from its recorded calls · " : "";
    cine.setRun(run, $("#config").selectedOptions[0].textContent, `${replay}${$("#scale-note").textContent}`);
  }
  $$(".scout-only").forEach((el) => { el.style.display = run.sensor ? "" : "none"; });
  $$(".hotspot-only").forEach((el) => { el.style.display = run.sensor && !run.wildfire && !hiker ? "" : "none"; });
  $$(".valley-only").forEach((el) => { el.hidden = !run.wildfire; });
  const squads = (run.wildfire && run.scenario.squads) || [];
  $$(".squads-only").forEach((el) => { el.hidden = !squads.length; });
  if (squads.length) {
    $("#squads-legend").innerHTML = squads.map((s) => `<i class="dot sq-${sqCode(s.code)}"></i>`).join("") +
      `squads: ${squads.map((s) => esc(String(s.name || AREA_NAME[s.code] || s.code).toLowerCase())).join(", ")}`;
  }
  if (squads.length) {   // the Move drones control lists this patrol's squads
    const opts = squads.map((s) => `<option value="${esc(s.code)}">${esc(s.name)}</option>`).join("");
    $("#move-from").innerHTML = opts;
    $("#move-to").innerHTML = opts;
    if (squads.length > 1) $("#move-to").value = squads[1].code;
    $(".row.move").hidden = squads.length < 2;
    $("#sq-patrol").textContent = run.scenario.patrol_s ? `${+(run.scenario.patrol_s / 60).toFixed(1)} min patrol` : "";
  }
  if (!run.wildfire && view.mode === "fire") {   // the fire view's button is gone: back to the default view
    view.setMode("follow");
    $$("#viewbtns button").forEach((b) => b.classList.toggle("active", b.id === "view-reset"));
  }
  valley = run.wildfire ? { located: 0, smoke: 0, warned: 0, brief: [], briefT: null, alerts: new Map(), approval: APPROVAL, rule: run.alertRule,
    zones: run.wildfire.zones || [], levels: run.wildfire.levels || {}, zonesLevel: [], told: {}, dispatch: [],
    squadRun: squads.length ? { names: Object.fromEntries([...(run.scenario.squad_areas || []), ...squads].map((a) => [a.code, a.name])),
      defs: new Map(squads.map((s) => [s.code, { code: s.code, name: s.name, n: s.n, lanes: s.lanes }])), laps: {}, now: null,
      patrolS: run.scenario.patrol_s, html: "" } : null } : null;
  airReset();
  drawValley();
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
    if (valley) { valley.ended = true; drawValley(); }   // alerts can no longer be approved
    if (source) { source.close(); source = null; }
  });
  if (film) source.addEventListener("end", () => { source.close(); source = null; });
}

// ---------------------------------------------------------------- landing and lab controls
// With no options, the page lands on the incident: the wildfire valley starts flying on its own, with the fire
// crew brief, the evacuation alerts and the log in front, and the scenario, supervisor, seed and drone-count
// pickers, the disturbances and formation orders behind the "Lab controls" toggle, folded at first. Each browser
// remembers whether the toggle was left open. URL options: ?lab=1 opens the lab controls for this visit and
// ?lab=0 folds them (neither is remembered); ?start=0 opens the page without starting a flight. Film mode and
// the cinema layout never start one, since the recorder picks the flight and presses Start itself, and film
// mode keeps the lab controls open, as its tools work the pickers.
const PARAMS = new URLSearchParams(location.search);
const LAB_FLAG = PARAMS.get("lab");
const AUTOSTART = !FILM && CINEMA === null && PARAMS.get("start") !== "0";
const LAB_KEY = "halo.labControls";
{
  let open = LAB_FLAG !== null ? LAB_FLAG !== "0" : FILM;
  if (LAB_FLAG === null && !FILM) { try { open = localStorage.getItem(LAB_KEY) === "open"; } catch { /* storage blocked: folded */ } }
  $("#lab").open = open;
  $("#lab").addEventListener("toggle", () => {
    if (LAB_FLAG !== null || FILM) return;
    try { localStorage.setItem(LAB_KEY, $("#lab").open ? "open" : "closed"); } catch { /* not remembered, still works */ }
  });
}
// A flight whose page has gone is stopped, so reloading the page does not leave flights running on the server.
addEventListener("pagehide", (e) => {
  if (!run || FILM || e.persisted) return;
  fetch(`/api/runs/${run.id}/control`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stop: true }), keepalive: true }).catch(() => {});
});

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
for (const [id, mode] of [["view-reset", "follow"], ["view-top", "top"], ["view-orbit", "orbit"], ["view-chase", "chase"], ["view-ride", "ride"], ["view-close", "close"], ["view-fire", "fire"]]) {
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
$("#alerts").addEventListener("click", (e) => {
  const b = e.target.closest("[data-approve], [data-cancel]");
  if (!b) return;
  b.disabled = true;
  if (b.dataset.approve) approveAlert(Number(b.dataset.approve)); else cancelAlert(Number(b.dataset.cancel));
});
$("#zones").addEventListener("click", (e) => {
  const b = e.target.closest("[data-allclear]");
  if (b) { b.disabled = true; allClear(b.dataset.allclear); }
});
$("#move-go").addEventListener("click", async () => {
  const k = Number($("#move-k").value);
  if (!Number.isInteger(k) || k < 1) { alert("Move a whole number of drones, at least 1."); return; }
  const r = await squadMove($("#move-from").value, $("#move-to").value, k);
  if (!r.ok && !r.alerted) alert(`No move: ${r.reason}.`);
});
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
// Without Nemotron (no key, or a failed call) the regex baseline's keyword guard catches only explicit wording,
// so only the page's own requests can be flown: the default text and the examples.
const KNOWN_MISSIONS = new Set([$("#mission-text").defaultValue, ...$$(".examples button").map((b) => b.dataset.ex)].map((t) => t.trim()));
$("#mission-text").addEventListener("input", () => { if (cine) cine.request($("#mission-text").value, missionMode === "valley" ? VALLEY_HEAD : undefined); });
$("#compile").addEventListener("click", async () => {
  if (missionMode === "valley") return compileValley();
  $("#compile-out").innerHTML = '<p class="muted">Compiling…</p>';
  $("#fly-mission").disabled = true;
  const text = $("#mission-text").value;
  if (cine) { cine.request(text); cine.compiling(); }
  try {
    compiled = await api("/api/compile", { text });
  } catch (err) { compiled = null; if (missionMode !== "search") return searchDropped(text); $("#compile-out").innerHTML = `<p>${esc(err.message)}</p>`; if (cine) cine.compiled({ error: err.message }); return; }
  if (missionMode !== "search") { compiled = null; return searchDropped(text); }
  compiled.text = text;
  const s = compiled.spec;
  const by = compiled.source === "nemotron" ? `Nemotron (${esc(compiled.model)})` : compiled.source === "guard" ? "the safety guard" : "the regex baseline";
  if (s.refused) {
    $("#compile-out").innerHTML = `<p><b>Refused</b> by ${by}: ${esc(s.refusal_reason)}</p>`;
    if (cine) cine.compiled(compiled, false);
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
  const flyable = compiled.source !== "regex-baseline" || KNOWN_MISSIONS.has(text.trim());
  if (!flyable) $("#compile-out").innerHTML += "<p>Flying a typed request needs Nemotron's civilian check, which did not run here (see the note above). The default request and the examples can be flown.</p>";
  $("#fly-mission").disabled = !flyable;
  if (cine) cine.compiled(compiled, flyable);
});
$("#fly-mission").addEventListener("click", () => {
  if (missionMode === "valley") {   // the patrol on the seed it was compiled for, with the plan's squads, minutes and strikes
    if (!valleyPlan) return;
    $$(".tabs button")[0].click();
    start({ scenario: "wildfire_valley", seed: valleyPlan.seed, ...valleyPlan.plan.options, alert_rule: VALLEY_ALERT_RULE });
    return;
  }
  if (!compiled) return;
  $$(".tabs button")[0].click();
  start({ mission: compiled.spec, mission_text: compiled.text });
});

// ---------------------------------------------------------------- Mission tab, valley patrol after lightning
// The Mission tab's second kind of request: a patrol of the wildfire valley after a lightning storm. The same box,
// buttons and output as a search mission; each kind keeps its own draft and answer while the other is shown.
// POST /api/valley/compile reads the request (Nemotron, a recorded call replayed, the keyword guard, or without a
// key the regex baseline) and plans one squad per area by fixed rules; any plan not refused can fly.
const VALLEY_HEAD = { title: "Valley patrol after lightning", sub: "A request in plain words, checked before take-off" };
const VALLEY_COMPILING = '<p class="muted">Compiling…</p>';
let missionMode = "search", valleyPlan = null, valleyAsk = 0;
// The clean layout's badge before a flight: the seed and supervisor the request will fly with, and whether it is a valley patrol.
if (cine) cine.preflight = () => ({ seed: missionMode === "valley" ? $("#valley-seed").value : $("#seed").value, supervisor: $("#config").value, valley: missionMode === "valley" });
const missionDrafts = { valley: { text: $(".valley-examples button").dataset.ex, out: "", fly: true } };
function setMissionMode(mode) {
  if (mode === missionMode || !["search", "valley"].includes(mode)) return;
  // A valley compile still on its way is dropped (valleyAsk), so its draft keeps no "Compiling…" line.
  const out = $("#compile-out").innerHTML;
  missionDrafts[missionMode] = { text: $("#mission-text").value, out: missionMode === "valley" && out === VALLEY_COMPILING ? "" : out, fly: $("#fly-mission").disabled };
  missionMode = mode;
  valleyAsk++;
  $$("#mission-mode button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $$(".search-only").forEach((el) => { el.hidden = mode !== "search"; });
  $$(".valley-mode-only").forEach((el) => { el.hidden = mode !== "valley"; });
  const d = missionDrafts[mode];
  $("#mission-text").value = d.text;
  $("#compile-out").innerHTML = d.out;
  $("#fly-mission").disabled = d.fly;
  $("#compile").textContent = mode === "valley" ? "Compile patrol" : "Compile mission";
  $("#fly-mission").textContent = mode === "valley" ? "Fly this patrol" : "Fly this mission";
  if (cine) cine.switchRequest(d.text, mode === "valley" ? VALLEY_HEAD : undefined);
}
// A search compile that came back after the tab switched to a valley patrol: not shown, so it cannot write over the
// patrol's answer; the search draft keeps its text, to compile again.
function searchDropped(text) { missionDrafts.search = { text, out: "", fly: true }; }
$$("#mission-mode button").forEach((b) => b.addEventListener("click", () => setMissionMode(b.dataset.mode)));
$$(".valley-examples button").forEach((b) => b.addEventListener("click", () => { $("#mission-text").value = b.dataset.ex; }));

async function compileValley() {
  const ask = ++valleyAsk, text = $("#mission-text").value, raw = $("#valley-seed").value.trim(), seed = raw === "" ? NaN : Number(raw);
  $("#compile-out").innerHTML = VALLEY_COMPILING;
  $("#fly-mission").disabled = true;
  valleyPlan = null;
  if (cine) { cine.request(text, VALLEY_HEAD); cine.compiling(); }
  let out;
  try {
    if (!Number.isInteger(seed) || seed < 0) throw new Error("The seed must be a whole number, 0 or more.");
    out = await api("/api/valley/compile", { text, seed });
  } catch (err) {
    if (ask !== valleyAsk) return;
    $("#compile-out").innerHTML = `<p>${esc(err.message)}</p>`;
    if (cine) cine.valleyCompiled({ error: err.message });
    return;
  }
  if (ask !== valleyAsk) return;
  out.text = text;
  const flyable = !!out.plan && !out.plan.refused && !(out.spec && out.spec.refused);
  valleyPlan = flyable ? out : null;
  $("#compile-out").innerHTML = valleyHtml(out);
  $("#fly-mission").disabled = !flyable;
  if (cine) cine.valleyCompiled(out, flyable);
}

const usd = (c) => (c > 0 && c < 0.0001 ? "under $0.0001" : `$${Number(c).toFixed(4)}`);
const STRIKE_WHEN = { all: "every strike on file", last_night: "last night's", night_before: "the night before's", today: "since midnight" };
const STRIKE_WHERE = { named: "in the areas named", outside: "outside the patrol areas", any: "anywhere" };
function strikeFilter(f) {
  if (!f) return "none asked for";
  const w = String(f.when || "all");
  const when = STRIKE_WHEN[w] || (w.startsWith("since ") ? `since ${w.slice(6)}` : w.startsWith("within ") ? `in the last ${w.slice(7)} h` : w);
  return `${when}, ${STRIKE_WHERE[f.where] || f.where}`;
}

// The valley compiler's answer in the panel: who read the request, what it asked for, and the patrol planned.
function valleyHtml(c) {
  const s = c.spec || {}, p = c.plan || {}, call = c.call;
  const who = c.source === "nemotron"
    ? `Nemotron (${esc(String(c.model || "").split("/").pop())}), ${c.how === "replayed" ? "a recorded call, replayed" : "a live call"}` +
      (call ? `: ${fmt(call.latency_s, 2)} s, ${call.total_tokens} tokens${call.cost_usd != null ? `, ${usd(call.cost_usd)}` : ""}${c.how === "replayed" ? " when recorded" : ""}` : "")
    : c.source === "guard" ? "the keyword guard (the model was not called)" : "the regex baseline";
  const name = (a) => esc(AREA_NAME[a] || a);
  if (p.refused || s.refused) {
    // The keyword guard's reason already says it was the guard and that the model was not called.
    return `<p><b class="refused">Refused</b>${c.source === "guard" ? ":" : ` by ${who}:`} ${esc(p.reason || s.refusal_reason || "outside civilian use")}</p>` +
      `${c.source === "regex-baseline" && c.problems && c.problems.length ? `<p class="muted small">${esc(c.problems.join(" "))}</p>` : ""}<p class="muted small">A refused request cannot fly.</p>`;
  }
  const none = '<span class="muted">not given</span>', val = (v) => (v === null || v === undefined ? none : esc(v));
  const table = (rows) => `<table>${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("")}</table>`;
  const counts = p.counts || {}, flown = ["F", "N", "S"].filter((a) => counts[a] > 0);
  const strikes = c.strikes || [], area = new Map(strikes.map((k) => [k.id, k.area]));
  const off = p.uncovered || [], check = (p.strike_ids || []).filter((i) => !off.includes(i));
  const outside = off.filter((i) => area.get(i) === "-"), unflown = off.filter((i) => area.get(i) !== "-");
  const notFlown = [outside.length ? `${outside.join(", ")} (outside every patrol area)` : "",
    unflown.length ? `${unflown.join(", ")} (in an area not flown)` : ""].filter(Boolean).join("; ");
  const problems = [...(c.source === "nemotron" ? c.problems || [] : []), ...(p.problems || [])];
  return `<p class="muted small">Parsed by ${who}.${c.source === "regex-baseline" && c.problems && c.problems.length ? ` ${esc(c.problems.join(" "))}` : ""} Seed ${esc(c.seed)}.</p>` +
    "<h4>Request</h4>" + table([["Drones", val(s.drones)], ["Areas", s.areas && s.areas.length ? s.areas.map(name).join(", ") : none],
      ["Per area", s.per_area ? Object.entries(s.per_area).map(([a, k]) => `${name(a)} ${esc(k)}`).join(" · ") : none],
      ["Patrol (min)", val(s.patrol_min)], ["Strikes", esc(strikeFilter(s.strikes))]]) +
    "<h4>Patrol plan</h4>" + table([["Squads", flown.map((a) => `${name(a)} ${esc(counts[a])}`).join(" · ") || none],
      ["Reserve", counts.R ? `${plural(counts.R, "drone")} kept at launch` : "none"], ["Patrol", `${esc(p.patrol_min)} min`],
      ["Strikes to check", `${check.length ? `${check.join(", ")} ` : ""}(${check.length} of the ${strikes.length} on file)`],
      ["Not flown over", notFlown || "none"]]) +
    (s.notes ? `<p class="muted small">Notes: ${esc(s.notes)}</p>` : "") +
    (problems.length ? `<p class="muted small">Problems: ${esc(problems.join("; "))}</p>` : "") +
    (c.source === "regex-baseline" ? '<p class="small"><b>Regex baseline:</b> Nemotron did not read this request, so this plan is the regex parser\'s reading of it. It can still be flown (in simulation).</p>' : "");
}

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
    // A looped route has no finish: the flight runs for a set time, so "completed" does not apply.
    const looped = r.scenarios.filter((sc) => (scenarios.find((s) => s.name === sc) || {}).loop);
    if (metric === "completed" && looped.length) {
      html += `<p class="muted small">No value for ${looped.map((sc) => esc(title(sc))).join(", ")}: it loops round its route for a set time, so it has no finish to reach.</p>`;
    }
  }
  return html;
}

// The committed reports, as the tab shows them: headings, paragraphs and tables. The first heading is left
// out, since the section has its own.
function reportHtml(md) {
  const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const blocks = md.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  return blocks.map((b) => {
    if (/^# /.test(b)) return "";
    if (/^#+ /.test(b)) return `<h4>${inline(b.replace(/^#+ /, ""))}</h4>`;
    if (b.startsWith("|")) {
      const rows = b.split("\n").filter((l) => !/^\|[\s|:-]+\|$/.test(l.trim()));
      return '<div class="table-wrap"><table class="report">' + rows.map((l, k) => `<tr>${cells(l).map((c) => k ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`).join("")}</tr>`).join("") + "</table></div>";
    }
    return b.split("\n").map((l) => `<p class="small">${inline(l)}</p>`).join("");
  }).join("");
}

const chartImg = (c) => `<img src="/results/${c.split("/").map(encodeURIComponent).join("/")}" alt="${esc(c)}">`;

async function loadResults() {
  const r = await api("/api/results").catch(() => ({ available: false }));
  let top = "";
  if (r.wildfire_valley) {
    top += "<h3>Wildfire valley (held-out seeds 4000-4019)</h3>" + reportHtml(r.wildfire_valley.report_md) +
      "<h4>Charts</h4>" + r.wildfire_valley.charts.map(chartImg).join("") +
      '<p class="muted small">Source: results/wildfire_valley/REPORT.md.</p>';
  }
  if (r.wildfire_scout) {
    top += `<p class="small">Wildfire scout (1, 4 and 16 drones, seeds 1000-1019): <a href="/results/${esc(r.wildfire_scout.report)}" target="_blank" rel="noopener">results/${esc(r.wildfire_scout.report)}</a>.</p>`;
  }
  if (top) top += "<h3>Supervision benchmark</h3>";
  if (!r.available) { $("#results").innerHTML = top + '<p class="muted">No benchmark results yet. Run <code>halo bench</code> then <code>halo report</code>.</p>'; return; }
  let html = top + headline(r) + "<h3>Charts</h3>" + r.charts.map(chartImg).join("");
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
    // Newer checks count the flight's calls too (usage.flight); the day-one check counts only its own.
    (e.usage?.flight ? `<p class="small">${plural(e.usage.calls + e.usage.flight.calls, "call")} in all, ${e.usage.run_prompt_tokens + e.usage.run_completion_tokens} tokens</p>`
      : e.usage ? `<p class="small">Not counting the flight: ${plural(e.usage.calls, "call")}, ${e.usage.prompt_tokens + e.usage.completion_tokens} tokens</p>` : "");
}

async function init() {
  scenarios = await api("/api/scenarios");
  $("#scenario").innerHTML = scenarios.map((s) => `<option value="${s.name}">${esc(s.title)}</option>`).join("");
  // The landing flies the wildfire valley with the page's own defaults (a reload's remembered form values are
  // dropped); film mode and the cinema layout start on the forest trail, as their tools expect.
  const landing = !FILM && CINEMA === null && scenarios.some((s) => s.name === "wildfire_valley");
  $("#scenario").value = landing ? "wildfire_valley" : "forest_trail";
  if (landing) {
    $("#config").value = "improved+rule";
    $("#seed").value = $("#seed").defaultValue;
    $("#drones").value = "";
  }
  $("#scenario").dispatchEvent(new Event("change"));
  status = await api("/api/status");
  const pill = $("#ai-pill");
  if (status.ai_available) {
    const name = status.model.split("/").pop();
    pill.textContent = status.mode === "replay" ? `Nemotron: replaying recorded calls (${name})` : `Nemotron on Nebius: ${name}`;
    pill.classList.add("on");
  }
  else { pill.textContent = status.key_present ? "Nemotron: no model set" : "Nemotron not configured (rules only)"; $("#opt-nemotron").disabled = true; }
  if (AUTOSTART && landing && !run) start();
}
init();
