// Halo Systems dashboard: the cinema layout (?cinema=1), for the demo video and for showing a flight on a big
// screen. The 3D view fills the window and the side panel is hidden; over the view go a title card, the
// flight clock, a few live counts, cards for the fire crew brief and the raised evacuation zones (wildfire
// valley, with the squads and the fire dispatch cards on a patrol that has them) or the swarm's current plan (other
// scenarios), and short notices for what just happened. Every line comes from the same run messages as the side
// panel. Notices fade in and out on the view's own clock, so they move the same way in film mode, where each frame
// takes seconds to draw.
//
// The clean layout (?clean=1, the recorder's --clean) is for the remade video: one slim badge with what is on screen
// (SIMULATION, the scenario and seed, the swarm, the supervisor, the simulated phones, the drones' drawn size), the
// clock with the playback speed, the notices as lower-third toasts (at most two at once, the most important first),
// and only the cards a shot asks for (?cards=crew, alerts, dispatch, squads, plan or stats), drawn large.

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (x, d = 1) => (x === null || x === undefined ? "–" : Number(x).toFixed(d));
const clock = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
// The station's compass words (sim/wildfire.py compass), 22.5 degrees apart anticlockwise from east.
const COMPASS = ["east", "east-northeast", "northeast", "north-northeast", "north", "north-northwest", "northwest", "west-northwest",
  "west", "west-southwest", "southwest", "south-southwest", "south", "south-southeast", "southeast", "east-southeast"];
const SHORT = ["E", "ENE", "NE", "NNE", "N", "NNW", "NW", "WNW", "W", "WSW", "SW", "SSW", "S", "SSE", "SE", "ESE"];
// A compass bearing (degrees clockwise from north, as the brief's head_bearing_deg and wind_from_deg) as a short word.
const CW = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const word = (deg) => CW[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
const LEVEL_HEAD = { 3: "Prepare to leave", 4: "Evacuate now", 5: "Emergency" };
const NOTICE_S = 6.5;   // how long a notice stays up, in view seconds
const MAX_NOTICES = 3;
const REQUEST_HEAD = { title: "Operator mission", sub: "A request in plain words, compiled before take-off" };
// The valley's patrol areas (eval/scenarios.py valley_areas), short, for the request card and the squads card.
export const SQUAD_SHORT = { F: "floor", N: "north", S: "south" };
// The right column with squads or dispatch cards, fullest first: fires in the crew brief, dispatch cards, raised
// zones, and the squads card in full, as one line, or left out. The first that fits the frame is shown: dispatch
// cards and the squads are cut first, the crew brief and the raised zones last.
const FIT = [[3, 3, 5, "full"], [3, 2, 5, "full"], [3, 2, 5, "line"], [3, 2, 4, "line"], [3, 1, 4, "line"], [2, 2, 5, "line"],
  [2, 1, 5, "line"], [2, 1, 4, "line"], [2, 1, 4, null], [2, 1, 3, null], [1, 1, 3, null]];
const code = (c) => (/^[A-Z]$/.test(String(c)) ? c : "x");
// Clean layout: the supervisor as the badge names it, by the run's config.
const SUPERVISOR = { "improved+rule": "rule supervisor", "improved+nemotron": "Nemotron supervisor", "improved+fixed": "fixed plan, no supervisor",
  "original+fixed": "original controller, fixed plan" };
// Clean layout: the toasts' kinds, most important first (the shot's card's kind is moved to the front), and the line
// over each. "plan" (the supervisor's formation changes) is left out of wildfire valley films unless asked for.
const TOAST_ORDER = ["clear", "alert", "dispatch", "crew", "fire", "station", "squad", "aircraft", "bad", "event", "nemotron", "plan"];
const TOAST_HEAD = { clear: "Operator action", alert: "Evacuation alert", dispatch: "Fire dispatch", crew: "Crew update", fire: "Ground station",
  station: "Ground station", squad: "Squads", aircraft: "Airspace", bad: "Safety", event: "Event", nemotron: "Nemotron supervisor", plan: "Supervisor" };
const CARD_TOAST = { crew: "crew", alerts: "alert", dispatch: "dispatch", squads: "squad" };
export const CLEAN_CARDS = ["crew", "dispatch", "alerts", "squads", "plan", "stats"];
const MAX_TOASTS = 2;
const usd = (c) => (c > 0 && c < 0.0001 ? "under $0.0001" : `$${Number(c).toFixed(4)}`);

// What a fire dispatch card says, read from its own text (sim/wildfire.py _dispatch): the place it is near, how
// close the station located it, the way the head runs (spreading fires only), and how far the fix moved since the
// fire's last card (moved_m, an update sent because the fix moved; 0 otherwise).
export function dispatchFacts(e) {
  const t = String(e.text || "");
  const near = /\bnear (.+?) at x -?\d/.exec(t), within = /\(within (\d+) m[,)]/.exec(t), head = /Head running ([^.]+)\./.exec(t);
  return { near: near ? near[1] : null, within: within ? within[1] : String(Math.max(1, Math.round(e.err_m || 0))), head: head ? head[1] : null,
    moved: e.moved_m > 0 ? Math.round(e.moved_m) : 0 };
}
// A dispatch card in a few words, for a notice: "new fire 2, smouldering" or "UPDATE: fire 2 now spreading, moved 45 m".
// was: the status of the fire's card before this one, when known.
export function dispatchHead(e, was = null) {
  const d = dispatchFacts(e);
  if (!e.update) return `new fire ${e.fire + 1}, ${e.status}`;
  const now = was !== e.status || !d.moved ? ` now ${e.status}` : "";
  return `UPDATE: fire ${e.fire + 1}${now}${d.moved ? `${now ? "," : ""} moved ${d.moved} m` : ""}`;
}

// A small map compass: north up, the arrow pointing where the fire's head runs (degrees anticlockwise from east),
// with the forecast's uncertainty as a wedge of half-width cone degrees (a full ring when 90 or more).
function rose(deg, cone) {
  const a = (-deg * Math.PI) / 180, x = Math.cos(a) * 15, y = Math.sin(a) * 15;
  const wedge = cone == null ? "" : cone >= 90 ? '<circle r="17" class="wedge"/>' : (() => {
    const lo = (-(deg - cone) * Math.PI) / 180, hi = (-(deg + cone) * Math.PI) / 180;
    return `<path d="M0 0 L${Math.cos(lo) * 17} ${Math.sin(lo) * 17} A17 17 0 0 0 ${Math.cos(hi) * 17} ${Math.sin(hi) * 17} Z" class="wedge"/>`;
  })();
  return `<svg class="rose" viewBox="-21 -21 42 42" aria-hidden="true"><circle r="19" class="ring"/><text y="-12.5" class="n">N</text>${wedge}` +
    `<line x1="${-x * 0.45}" y1="${-y * 0.45}" x2="${x}" y2="${y}" class="arrow"/>` +
    `<path d="M${x} ${y} L${x - Math.cos(a - 0.5) * 7} ${y - Math.sin(a - 0.5) * 7} L${x - Math.cos(a + 0.5) * 7} ${y - Math.sin(a + 0.5) * 7} Z" class="head"/></svg>`;
}

// An airspace hold in force (aircraft inbound), after the counts: the order (not where the drones are yet; they take a
// few seconds to get down) and since when.
const PLANE = '<svg class="plane" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1.5c.9 0 1.4.8 1.4 1.8v6l8.1 4.9v2.1l-8.1-2.6v5.1l2.4 1.8v1.7L12 21.4l-3.8.9v-1.7l2.4-1.8v-5.1l-8.1 2.6v-2.1l8.1-4.9v-6c0-1 .5-1.8 1.4-1.8z"/></svg>';
const holdBadge = (a) => `<div class="s hold"><b>${PLANE}Hold at ${fmt(a.altitude_m, 0)}<small> m</small></b>` +
  `<span>above the treetops, for crewed aircraft · operator's order · since ${clock(a.since_s)}</span></div>`;

export class Cinema {
  // opts: { clean, cards: the clean layout's cards by name, toasts: the toast kinds to show, in order (default all) }
  constructor(root, view, opts = {}) {
    this.view = view;
    this.notices = [];
    this.clean = !!opts.clean;
    this.cardsWanted = (opts.cards || []).filter((c) => CLEAN_CARDS.includes(c));
    this.toastsAsked = opts.toasts && opts.toasts.length ? opts.toasts : null;
    const lead = this.cardsWanted.map((c) => CARD_TOAST[c]).filter(Boolean);
    this.toastOrder = this.toastsAsked || [...lead, ...TOAST_ORDER.filter((k) => !lead.includes(k))];
    this.preflight = null;   // clean layout, before a flight: () => { seed, supervisor (the config), valley }, set by the page
    this.el = document.createElement("div");
    this.el.id = "cine";
    this.el.innerHTML = this.clean ? `
      <div class="cine-title glass"><div class="cine-head"><span class="badge">SIMULATION</span><span class="cine-scn"></span>
        <span class="cine-brand"><span class="logo">◆</span> HALO SYSTEMS</span></div><div class="cine-sub"></div><div class="cine-note"></div></div>
      <div class="cine-clock glass"><span class="t">00:00</span><span class="k">sim time</span><span class="speed"></span></div>
      <div class="cine-cards"></div>
      <div class="cine-notices"></div>
      <div class="cine-request glass" hidden><h4>Operator request <span class="when">in plain words</span></h4><p class="text"></p><div class="out"></div></div>
      <div class="cine-stats glass"></div>` : `
      <div class="cine-title glass"><div class="cine-brand"><span class="logo">◆</span> HALO SYSTEMS <span class="badge">SIMULATION</span></div>
        <div class="cine-scn"></div><div class="cine-sub"></div><div class="cine-note"></div></div>
      <div class="cine-clock glass"><span class="t">00:00</span><span class="k">flight time</span><span class="speed"></span></div>
      <div class="cine-cards"></div>
      <div class="cine-notices"></div>
      <div class="cine-request glass" hidden><h4>Operator request <span class="when">in plain words</span></h4><p class="text"></p><div class="out"></div></div>
      <div class="cine-stats glass"></div>`;
    this.el.classList.add("idle");   // no flight yet: no clock or counts
    if (this.clean) {
      this.el.classList.add("clean", ...this.cardsWanted.map((c) => `show-${c}`));
      document.body.classList.add("clean");
    }
    root.appendChild(this.el);
    this.$ = (s) => this.el.querySelector(s);
    this.cards = { html: "" };
    view.idleDrift = true;   // before the first flight, the camera drifts slowly over the empty scenery
    document.body.classList.add("cinema", "cine-idle");
  }

  // Before a mission flight: the operator's request as it is typed, then what the mission compiler made of it.
  // head: the title card's {title, sub} for another kind of request (a valley patrol); a search mission by default.
  request(text, head = null) {
    const card = this.$(".cine-request"), h = head || REQUEST_HEAD;
    if (card.hidden || h !== this.requestHead) {
      if (!card.hidden) this.$(".cine-request .out").innerHTML = "";   // an answer to the other kind of request
      card.hidden = false;
      this.requestHead = h;
      this._scn(h.title);
      this._sub(h.sub);
    } else if (h !== REQUEST_HEAD) this._sub(h.sub);   // a valley request typed after a refusal
    this.$(".cine-request .text").innerHTML = `${esc(text)}<span class="caret"></span>`;
  }
  // The Mission tab switched to another kind of request: show its draft, if a request is on screen.
  switchRequest(text, head = null) { if (!this.$(".cine-request").hidden) this.request(text, head); }
  compiling() {
    this.$(".cine-request .caret")?.remove();
    this.$(".cine-request .out").innerHTML = '<div class="state busy">Compiling the request<span class="dots"></span></div>';
  }
  // flyable: the page's own verdict (a typed request the regex baseline parsed has had no civilian check).
  compiled(c, flyable) {
    const out = this.$(".cine-request .out");
    this.patrolCard = "";   // a refused or failed request leaves no plan card from an earlier one
    if (c.error) { out.innerHTML = `<div class="state bad"><b>Not compiled</b><span>${esc(c.error)}</span></div>`; return; }
    const s = c.spec, by = c.source === "nemotron" ? `Nemotron (${esc(String(c.model || "").split("/").pop())})` : c.source === "guard" ? "the safety guard" : "the regex baseline";
    if (s.refused) {
      out.innerHTML = `<div class="state bad"><b>Refused</b><span>by ${by}: ${esc(s.refusal_reason)}</span></div>`;
      this._sub("Refused before take-off");
      return;
    }
    const d = s.directive, r = s.route, problems = c.problems || [];
    if (!flyable) this._sub("Parsed, not flown");
    const cell = (v, unit, k) => `<div><b>${v === null || v === undefined ? '<i class="dflt">default</i>' : `${esc(v)}${unit}`}</b><span>${k}</span></div>`;
    const area = r && r.width_m ? `${fmt(r.width_m, 0)} × ${fmt(r.height_m, 0)}` : null;
    out.innerHTML = (flyable && !problems.length ? '<div class="state ok"><b>Checked mission plan</b>' : flyable ? '<div class="state"><b>Mission plan, with problems</b>' : '<div class="state"><b>Parsed, not flown</b>') +
      `<span>by ${by}${c.call ? ` · ${fmt(c.call.latency_s, 2)} s · ${c.call.total_tokens} tokens` : ""}</span></div>` +
      `<div class="plan-grid">${cell(s.drones, "", "drones")}${cell(d.formation, "", "formation")}${cell(d.spacing_m, " m", "spacing")}` +
      `${cell(d.altitude_m, " m", "altitude")}${area ? cell(area, " m", `${esc(r.type)} area`) : cell(r && r.type, "", "route")}</div>` +
      (problems.length ? `<p class="why">${esc(problems.join("; "))}</p>` : "") +
      (flyable ? '<p class="why">Before take-off every field is clamped to the flight envelope, and a blank one takes its default</p>'
        : "<p class=\"why\">Flying a typed request needs Nemotron's civilian check, which did not run here; the default request and the examples can be flown</p>");
  }

  // The valley patrol compiler's answer (POST /api/valley/compile): who read the request and the patrol it plans,
  // all from the response. flyable: the page's verdict (a refused request cannot fly).
  valleyCompiled(c, flyable = true) {
    const out = this.$(".cine-request .out");
    // The title card's line under the title starts from the request's own each time, so a refusal's "Refused before
    // take-off" does not stay up over the next request's plan.
    this._sub((this.requestHead || REQUEST_HEAD).sub);
    if (c.error) { out.innerHTML = `<div class="state bad"><b>Not compiled</b><span>${esc(c.error)}</span></div>`; return; }
    const s = c.spec || {}, p = c.plan || {}, nemotron = c.source === "nemotron";
    const by = nemotron ? `Nemotron (${esc(String(c.model || "").split("/").pop())})` : c.source === "guard" ? "the keyword guard" : "the regex baseline";
    if (p.refused || s.refused) {   // the keyword guard's reason already says it was the guard
      out.innerHTML = `<div class="state bad"><b>Refused</b><span>${c.source === "guard" ? "" : `by ${by}: `}${esc(p.reason || s.refusal_reason || "outside civilian use")}</span></div>`;
      this._sub("Refused before take-off");
      return;
    }
    if (!flyable) this._sub("Parsed, not flown");
    else if (c.source === "regex-baseline") this._sub("A request in plain words, read by fixed rules before take-off");
    const call = c.call || null, problems = [...(c.problems || []), ...(p.problems || [])];
    const spent = [...(call && call.latency_s != null ? [`${fmt(call.latency_s, 2)} s`] : []), ...(call && call.cost_usd != null ? [usd(call.cost_usd)] : [])];
    const facts = [`by ${by}`, ...(c.how === "replayed" ? [`recorded call, replayed${spent.length ? ` (${spent.join(", ")} when recorded)` : ""}`] : spent)];
    const state = !flyable ? '<div class="state"><b>Parsed, not flown</b>' : !nemotron ? '<div class="state"><b>Patrol plan</b>'
      : problems.length ? '<div class="state"><b>Patrol plan, with problems</b>' : '<div class="state ok"><b>Checked patrol plan</b>';
    const counts = p.counts || {}, areas = ["F", "N", "S"].filter((a) => counts[a] > 0);
    const strikes = c.strikes || [], off = p.uncovered || [], check = (p.strike_ids || []).filter((i) => !off.includes(i)).length;
    const outside = strikes.filter((k) => off.includes(k.id)).every((k) => k.area === "-");
    const cell = (v, k) => `<div><b>${v}</b><span>${k}</span></div>`;
    // Each fact on one line (a long model name can push the last one to a second line, but never split it).
    out.innerHTML = `${state}<span>${facts.map((f) => f.replace(/ /g, " ")).join(" · ")}</span></div>` +
      `<div class="plan-grid patrol">${cell(esc(p.drones), counts.R ? `drones, ${esc(counts.R)} in reserve` : "drones")}` +
      `${cell(areas.map((a) => esc(counts[a])).join(" · "), `squads: ${areas.map((a) => SQUAD_SHORT[a]).join(" · ")}`)}` +
      `${cell(`${esc(p.patrol_min)} min`, "patrol")}${cell(`${check}<small> of ${strikes.length}</small>`, "strikes to check")}` +
      `${cell(off.length, outside ? "outside every area" : "outside the areas flown")}</div>` +
      (problems.length ? `<p class="why">${esc(problems.join("; "))}</p>` : "");
    // Clean layout: the same plan as a card over the flight (?cards=plan), with the same numbers.
    this.patrolCard = !flyable ? "" : `<div class="card glass"><h4>Patrol plan <span class="when">${facts.map((f) => esc(f)).join(" · ")}</span></h4>` +
      `<div class="plan-grid patrol">${cell(esc(p.drones), "drones")}${cell(areas.map((a) => esc(counts[a])).join(" · "), areas.map((a) => SQUAD_SHORT[a]).join(" · "))}` +
      `${cell(`${esc(p.patrol_min)} min`, "patrol")}${cell(`${check}<small> of ${strikes.length}</small>`, "strikes to check")}</div></div>`;
  }

  // The title card's title and the line under it. In the clean layout, before a flight, the title takes the seed the
  // request is for, and the line the supervisor and, for a valley patrol, the simulated phones.
  _scn(title) {
    const pf = this.clean && this.preflight ? this.preflight() : null;
    if (!pf) { this.$(".cine-scn").textContent = title; return; }
    this.$(".cine-scn").innerHTML = `${esc(title)}${pf.seed !== "" && pf.seed != null ? `<span class="seed">seed ${esc(pf.seed)}</span>` : ""}`;
  }
  _sub(text) {
    const pf = this.clean && this.preflight ? this.preflight() : null;
    this.$(".cine-sub").textContent = pf ? [text, SUPERVISOR[pf.supervisor] || pf.supervisor, pf.valley ? "alerts to simulated phones only" : ""].filter(Boolean).join(" · ") : text;
  }

  // note: the disclosures the side panel would show (replayed calls, the drones' drawn size).
  setRun(run, supervisor, note = "") {
    this.run = run;
    this.el.classList.remove("idle");
    document.body.classList.remove("cine-idle");
    this.$(".cine-request").hidden = true;
    this.notices.forEach((n) => n.el.remove());
    this.notices = [];
    this.cards.html = "";
    this.cards.key = "";
    this.$(".cine-cards").innerHTML = "";
    if (this.clean) {   // one badge: SIMULATION, the scenario and seed; the swarm, supervisor, phones and drawn size
      this.$(".cine-scn").innerHTML = `${esc(run.scenario.title)}<span class="seed">seed ${esc(run.seed)}</span>`;
      const notes = String(note || "").split(" · ").filter(Boolean).map((t) => t.replace(/^Drones drawn/, "drones drawn").replace(/(\d)x\b/g, "$1×"));
      this.$(".cine-sub").textContent = [`${run.n} drones`, SUPERVISOR[run.config] || supervisor,
        run.wildfire ? "alerts to simulated phones only" : "", ...notes].filter(Boolean).join(" · ");
      this.$(".cine-note").textContent = "";
      return;
    }
    this.$(".cine-scn").textContent = run.scenario.title;
    this.$(".cine-sub").textContent = `${run.n} drones · ${supervisor}${run.wildfire ? (run.alertRule === "presigned" ? " · public alerts under pre-signed rules, to simulated phones only" : " · alerts to simulated phones only") : ""}`;
    this.$(".cine-note").textContent = note;
  }

  // Each frame: the clock and the counts along the bottom.
  frame(f, valley, speed) {
    const m = f.metrics;
    this.$(".cine-clock .t").textContent = clock(f.t);
    this.$(".cine-clock .speed").textContent = this.clean ? `${speed || 1}× speed` : speed && speed !== 1 ? `×${speed}` : "";
    const stat = (v, k, cls = "") => `<div class="s ${cls}"><b>${v}</b><span>${k}</span></div>`;
    const html = valley
      ? stat(valley.located, "fires located", valley.located ? "hot" : "") + stat(`${fmt(m.coverage_pct, 0)}%`, valley.squadRun ? "patrol area searched" : "survey block searched") +
        stat(`${valley.warned}<small> of ${(this.run.scenario.wildfire?.phones || []).length}</small>`, "phones alerted", valley.warned ? "warn" : "") +
        stat(`${m.flying}<small> / ${this.run.n}</small>`, "drones flying")
      : stat(`${fmt(m.formation_error_m, 2)}<small> m</small>`, "formation error") + stat(`${fmt(m.min_sep_m, 1)}<small> m</small>`, "closest pair") +
        stat(`${m.flying}<small> / ${this.run.n}</small>`, "drones flying", m.lost ? "warn" : "") + stat(`${fmt(m.leader_speed, 1)}<small> m/s</small>`, "speed");
    const hold = f.airspace && f.airspace.holding ? holdBadge(f.airspace) : "";   // aircraft inbound, until the sweep resumes
    if (html + hold !== this.statsHtml) this.$(".cine-stats").innerHTML = this.statsHtml = html + hold;
    if (!valley && f.directive) this.plan(f.directive);
  }

  // The swarm's plan now (scenarios other than the valley), and who set it.
  plan(d) {
    if (this.clean && !this.cardsWanted.includes("plan")) return;
    const src = this.planSrc || "plan";
    const html = `<div class="card glass"><h4>Swarm plan <span class="chip ${esc(src)}">${esc(src === "plan" ? "mission plan" : src)}</span></h4>` +
      `<div class="plan-grid"><div><b>${esc(d.formation)}</b><span>formation</span></div><div><b>${fmt(d.spacing_m)} m</b><span>spacing</span></div>` +
      `<div><b>${fmt(d.speed_mps)} m/s</b><span>speed</span></div><div><b>${fmt(d.altitude_m, 0)} m</b><span>altitude</span></div></div>` +
      (this.planWhy ? `<p class="why">${esc(this.planWhy)}</p>` : "") + "</div>";
    if (html !== this.cards.html) this.$(".cine-cards").innerHTML = this.cards.html = html;
  }
  planBy(src, why) { this.planSrc = src; this.planWhy = why || ""; }

  // The fire crew brief and the raised zones (wildfire valley); with squads or fire dispatch cards (valley patrols),
  // those cards too, shortened until the column fits the frame.
  valley(valley) {
    if (this.clean) { this._cleanCards(valley); return; }
    if (valley.squadRun || (valley.dispatch && valley.dispatch.length)) { this._fit(valley); return; }
    const html = this._brief(valley) + this._zones(valley);
    if (html !== this.cards.html) this.$(".cine-cards").innerHTML = this.cards.html = html;
  }

  // Clean layout: only the cards the shot asked for, in its order, each kept short enough to read at a glance.
  _cleanCards(valley) {
    const make = { crew: () => this._brief(valley, 2), dispatch: () => this._dispatch(valley, 3), alerts: () => this._zones(valley, 6),
      squads: () => this._squads(valley, "full"), plan: () => this.patrolCard || "" };
    const html = this.cardsWanted.map((c) => (make[c] ? make[c]() : "")).join("");
    if (html !== this.cards.html) this.$(".cine-cards").innerHTML = this.cards.html = html;
  }

  // The column at the fullest level of FIT that fits above the frame's bottom edge. Measured only when what it
  // would show changes; layout depends on nothing but the page, so film mode draws the same every time.
  _fit(valley) {
    const build = ([b, d, z, s]) => this._brief(valley, b) + this._dispatch(valley, d) + this._zones(valley, z) + this._squads(valley, s);
    const key = build(FIT[0]);
    if (key === this.cards.key) return;
    this.cards.key = key;
    const box = this.$(".cine-cards"), edge = parseFloat(getComputedStyle(this.el).getPropertyValue("--edge")) || 28;
    const room = this.el.clientHeight - edge;
    for (let i = 0; i < FIT.length; i++) {
      const html = i ? build(FIT[i]) : key;
      if (html !== this.cards.html) box.innerHTML = this.cards.html = html;
      if (box.offsetTop + box.offsetHeight <= room) break;
    }
  }

  // Fire dispatch (pre-signed alert rule): the newest cards the station sent, newest first (simulated).
  _dispatch(valley, max) {
    const cards = valley.dispatch || [];
    if (!cards.length) return "";
    const rows = cards.slice(-max).reverse().map((e) => {
      const d = dispatchFacts(e);
      return `<li><b>Fire ${e.fire + 1}</b>${e.update ? '<span class="upd">UPDATE</span>' : ""}<span class="st ${esc(e.status)}">${esc(e.status)}</span>` +
        `${d.moved ? `<span class="mv">moved ${d.moved} m</span>` : ""}` +
        `<div class="line">${d.near ? `near ${esc(d.near)} · ` : ""}within ${esc(d.within)} m · located by ${esc(e.by)}</div></li>`;
    });
    const more = cards.length > max ? `<li class="more">and ${cards.length - max} earlier</li>` : "";
    return `<div class="card glass"><h4>Fire dispatch <span class="when">simulated · initial attack</span></h4><ul class="dispatch">${rows.join("")}${more}</ul></div>`;
  }

  // The squads of a valley patrol: per area, drones flying, laps of its circuit and drones flying across to join it.
  // form: "full" (a row per squad), "line" (one line of counts) or null (left out).
  _squads(valley, form) {
    const sq = valley.squadRun;
    if (!sq || !form) return "";
    const now = new Map((sq.now || []).map((s) => [s.code, s])), defs = [...sq.defs.values()];
    const patrol = sq.patrolS ? `<span class="when">${esc(+(sq.patrolS / 60).toFixed(1))} min patrol</span>` : "";
    const flying = (d) => (now.get(d.code) ? now.get(d.code).flying : d.n);
    if (form === "line") {   // drones flying, and "+k" on their way to join
      const joining = (d) => { const j = now.get(d.code)?.joining?.length || 0; return j ? `<span class="join">+${j}</span>` : ""; };
      return `<div class="card glass"><h4>Squads ${patrol}</h4><div class="sq-line">` +
        defs.map((d) => `<span><i class="sqdot sq-${code(d.code)}"></i>${esc(SQUAD_SHORT[d.code] || d.name)} ${flying(d)}${joining(d)}</span>`).join("") + "</div></div>";
    }
    const rows = defs.map((d) => {
      const f = now.get(d.code), l = sq.laps[d.code], join = f && f.joining ? f.joining.length : 0;
      return `<li><i class="sqdot sq-${code(d.code)}"></i><b>${esc(d.name)}</b><span class="k">${flying(d)} flying</span>` +
        `${l ? `<span class="k">${fmt(l.base + l.last)} laps</span>` : ""}${join ? `<span class="join">+${join} joining</span>` : ""}</li>`;
    });
    return `<div class="card glass"><h4>Squads ${patrol}</h4><ul class="squads">${rows.join("")}</ul></div>`;
  }

  // The fire crew brief: the max fires that matter most (spreading first, then the fastest).
  _brief(valley, max = 3) {
    const fires = (valley.brief || []).slice().sort((a, b) => (b.state === "spreading") - (a.state === "spreading") || b.head_m_per_min - a.head_m_per_min);
    const rows = fires.slice(0, max).map((b) => {
      // Direction: the brief's compass bearing when it sends one, else its compass word.
      const k = COMPASS.indexOf(b.direction), compass = b.head_bearing_deg != null;
      const deg = compass ? 90 - b.head_bearing_deg : k >= 0 ? k * 22.5 : null;
      const dir = compass ? word(b.head_bearing_deg) : k >= 0 ? SHORT[k] : esc(b.direction);
      const sure = b.cone_deg == null || b.cone_deg < 90;
      const within = b.cone_deg != null && sure ? ` <span class="cone">±${Math.round(b.cone_deg)}°</span>` : !sure ? ' <span class="cone">(direction uncertain)</span>' : "";
      // A smouldering fire's numbers are the station's what-if forecast, as its own brief text says.
      const ifs = b.state === "smouldering";
      const where = (b.places || []).slice(0, 1).map((p) => `${ifs ? "could reach" : "reaches"} ${esc(p.name)} ${p.eta_min < 1 ? "now" : `in about ${p.eta_min} min`}`);
      // Why it runs that way: what drives the head (the brief's main driver, with the measured wind) and the fuel ahead.
      // A smouldering fire's drivers are what-ifs too ("would be pushed"). The slope pushes the head uphill, so only
      // the wind gets a "from".
      const d = b.drivers, from = d && d.wind_from_deg != null ? ` from ${word(d.wind_from_deg)}` : "";
      const by = d && { wind: `by the wind${from}`, slope: "up the slope", "wind and slope": `by the wind${from} and up the slope` }[d.main];
      const drive = !d || !(b.head_m_per_min > 0) ? [] : [
        by ? `${ifs ? "would be pushed" : "pushed"} ${by}` : d.main === "fuel" ? (ifs ? "would follow the fuel" : "following the fuel") : "",
        d.against ? `held back by the ${esc(d.against)}` : "",
        d.fuel_ahead && d.fuel_ahead.length ? `into ${esc(d.fuel_ahead[0].fuel)}` : ""].filter(Boolean);
      const told = valley.told && valley.briefT != null && valley.told[b.fire] === valley.briefT;
      return `<li>${deg != null ? rose(deg, b.cone_deg) : '<span class="rose-none">·</span>'}<div>` +
        `<b>Fire ${b.fire + 1}</b> <span class="st ${esc(b.state)}">${esc(b.state)}</span>${told ? ' <span class="st told">crews updated</span>' : ""}` +
        `<div class="line">${b.head_m_per_min > 0 ? `${ifs ? "if it takes off:" : sure ? "head runs" : "head most likely"} <b>${dir}</b>${within} at ${fmt(b.head_m_per_min)} m/min` : esc(b.direction || "not spreading")}${b.area_1h_ha != null ? ` · ${fmt(b.area_1h_ha)} ha in 1 h` : ""}</div>` +
        (drive.length ? `<div class="line why-line">${drive.join(" · ")}</div>` : "") + (where.length ? `<div class="line">${where[0]}</div>` : "") + "</div></li>";
    });
    const more = fires.length > max ? `<li class="more">and ${fires.length - max} more</li>` : "";
    return `<div class="card glass"><h4>Fire crew brief <span class="when">${valley.briefT != null ? `forecast at ${clock(valley.briefT)}` : ""}</span></h4>` +
      (rows.length ? `<ul class="brief">${rows.join("")}${more}</ul>` : '<p class="why">Waiting for the first fire to be located…</p>') +
      `<div class="bands"><i class="b30"></i><i class="b60"></i><i class="b120"></i><i class="b180"></i>fire likely within 30, 60, 120, 180 min</div></div>`;
  }

  // The raised evacuation zones, highest level first (at most max of them).
  _zones(valley, max = 5) {
    const up = (valley.zones || []).map((z, i) => ({ ...z, level: (valley.zonesLevel || [])[i] || 0 })).filter((z) => z.level > 0).sort((a, b) => b.level - a.level);
    return up.length ? `<div class="card glass"><h4>Evacuation alerts <span class="when">simulated phones</span></h4><ul class="zones">` +
      up.slice(0, max).map((z) => `<li><span class="lvl l${z.level}">Level ${z.level}</span><b>${esc(z.code)}</b><span class="name">${esc(z.name || "")}</span></li>`).join("") +
      (up.length > max ? `<li class="more">and ${up.length - max} more zones</li>` : "") + "</ul>" +
      `<div class="key">${[...new Set(up.map((z) => z.level))].sort().map((l) => `Level ${l}: ${LEVEL_HEAD[l].toLowerCase()}`).join(" · ")}</div></div>` : "";
  }

  clearNotices() { this.notices.forEach((n) => n.el.remove()); this.notices = []; }

  // The data of the notice with this key while it is still up and not about to go (null otherwise), so that things
  // sent together can share one notice.
  live(key) {
    const n = this.notices.find((q) => q.key === key && !q.leaving && this.view.vt - q.at < NOTICE_S - 1.5);
    return n ? n.data : null;
  }

  // A short notice of something that just happened: kind picks its colour (station, alert, crew, plan, event, and
  // in valley patrols dispatch, or "squad sq-<code>" in the colour of the squad that drones join).
  // With a key, a notice still up under that key is rewritten in place (and kept up a little longer) instead.
  notice(kind, title, sub = "", key = null, data = null) {
    if (this.clean) { this._toast(kind, title, sub, key, data); return; }
    const html = `<b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ""}`;
    const same = key && this.notices.find((q) => q.key === key && !q.leaving && this.view.vt - q.at < NOTICE_S - 1.5);
    if (same) {
      same.el.innerHTML = html;
      same.data = data;
      same.at = Math.max(same.at, this.view.vt - 0.35);
      this.update(this.view.vt);
      return;
    }
    const el = document.createElement("div");
    el.className = `notice glass ${kind}`;
    el.innerHTML = html;
    this.$(".cine-notices").appendChild(el);
    this.notices.push({ el, at: this.view.vt, key, data });
    // Too many at once: the oldest go early.
    const live = this.notices.filter((n) => !n.leaving);
    for (const n of live.slice(0, Math.max(0, live.length - MAX_NOTICES))) { n.leaving = true; n.at = Math.min(n.at, this.view.vt - NOTICE_S + 0.6); }
    this.update(this.view.vt);
  }

  // Clean layout: a notice as a lower-third toast: a line naming what kind of news it is, the news in large type, and
  // under it the first part of the detail and any part saying what is simulated. At most MAX_TOASTS are up: a new one
  // pushes out the least important (the oldest of equals), and one less important than all those up is not shown.
  _toast(kind, title, sub, key, data) {
    const k = String(kind).split(" "), cls = k.includes("clear") ? "clear" : k[0];
    const wildfire = !!(this.run && this.run.wildfire);
    if (cls === "plan" && wildfire && !this.toastsAsked) return;
    const rank = this.toastOrder.indexOf(cls);
    if (rank < 0) return;
    let head = String(title), eyebrow = TOAST_HEAD[cls] || "";
    const m = /^(Crew update|Fire dispatch|Rule supervisor|Nemotron supervisor|You)(?::\s*|\s+(?=UPDATE))(.+)$/.exec(head);
    if (m) { eyebrow = m[1] === "You" ? "Operator" : m[1]; head = m[2]; }
    if (/^UPDATE: /.test(head)) head = `Update: ${head.slice(8)}`;
    head = head.charAt(0).toUpperCase() + head.slice(1);
    const parts = String(sub || "").split(" · ").filter(Boolean), keep = parts.filter((p, i) => i === 0 || /simulated/i.test(p));
    const html = `<div class="eyebrow">${esc(eyebrow)}</div><b>${esc(head)}</b>${keep.length ? `<span>${esc(keep.join(" · "))}</span>` : ""}`;
    const vt = this.view.vt;
    const same = key && this.notices.find((q) => q.key === key && !q.leaving && vt - q.at < NOTICE_S - 1.5);
    if (same) { same.el.innerHTML = html; same.data = data; same.at = Math.max(same.at, vt - 0.35); this.update(vt); return; }
    const live = this.notices.filter((n) => !n.leaving);
    if (live.length >= MAX_TOASTS) {
      const worst = live.slice().sort((a, b) => b.rank - a.rank || a.at - b.at)[0];
      if (worst.rank < rank) return;   // everything up matters more
      worst.leaving = true; worst.at = Math.min(worst.at, vt - NOTICE_S + 0.5);
    }
    const el = document.createElement("div");
    el.className = `notice toast glass ${kind}`;
    el.innerHTML = html;
    this.$(".cine-notices").appendChild(el);
    this.notices.push({ el, at: vt, key, data, rank });
    this.update(vt);
  }

  // Fade the notices in and out by the view's clock (and pulse the compiler's dots).
  update(vt) {
    const dots = this.$(".cine-request .dots");
    if (dots) dots.textContent = ".".repeat(1 + (Math.floor(vt * 3) % 3));
    this.notices = this.notices.filter((n) => {
      const age = vt - n.at;
      if (age > NOTICE_S) { n.el.remove(); return false; }
      const a = Math.min(1, age / 0.35, (NOTICE_S - age) / 0.6);
      n.el.style.opacity = a.toFixed(3);
      n.el.style.transform = this.clean ? `translateY(${((1 - Math.min(1, age / 0.35)) * 18).toFixed(1)}px)`
        : `translateX(${((1 - Math.min(1, age / 0.35)) * -24).toFixed(1)}px)`;
      return true;
    });
  }
}
