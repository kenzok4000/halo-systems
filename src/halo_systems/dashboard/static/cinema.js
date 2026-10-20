// Halo Systems dashboard: the cinema layout (?cinema=1), for the demo video and for showing a flight on a big
// screen. The 3D view fills the window and the side panel is hidden; over the view go a title card, the
// flight clock, a few live counts, cards for the fire crew brief and the raised evacuation zones (wildfire
// valley) or the swarm's current plan (other scenarios), and short notices for what just happened. Every line
// comes from the same run messages as the side panel. Notices fade in and out on the view's own clock, so they
// move the same way in film mode, where each frame takes seconds to draw.

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

export class Cinema {
  constructor(root, view) {
    this.view = view;
    this.notices = [];
    this.el = document.createElement("div");
    this.el.id = "cine";
    this.el.innerHTML = `
      <div class="cine-title glass"><div class="cine-brand"><span class="logo">◆</span> HALO SYSTEMS <span class="badge">SIMULATION</span></div>
        <div class="cine-scn"></div><div class="cine-sub"></div><div class="cine-note"></div></div>
      <div class="cine-clock glass"><span class="t">00:00</span><span class="k">flight time</span><span class="speed"></span></div>
      <div class="cine-cards"></div>
      <div class="cine-notices"></div>
      <div class="cine-request glass" hidden><h4>Operator request <span class="when">in plain words</span></h4><p class="text"></p><div class="out"></div></div>
      <div class="cine-stats glass"></div>`;
    this.el.classList.add("idle");   // no flight yet: no clock or counts
    root.appendChild(this.el);
    this.$ = (s) => this.el.querySelector(s);
    this.cards = { html: "" };
    view.idleDrift = true;   // before the first flight, the camera drifts slowly over the empty scenery
    document.body.classList.add("cinema", "cine-idle");
  }

  // Before a mission flight: the operator's request as it is typed, then what the mission compiler made of it.
  request(text) {
    const card = this.$(".cine-request");
    if (card.hidden) {
      card.hidden = false;
      this.$(".cine-scn").textContent = "Operator mission";
      this.$(".cine-sub").textContent = "A request in plain words, compiled before take-off";
    }
    this.$(".cine-request .text").innerHTML = `${esc(text)}<span class="caret"></span>`;
  }
  compiling() {
    this.$(".cine-request .caret")?.remove();
    this.$(".cine-request .out").innerHTML = '<div class="state busy">Compiling the request<span class="dots"></span></div>';
  }
  // flyable: the page's own verdict (a typed request the regex baseline parsed has had no civilian check).
  compiled(c, flyable) {
    const out = this.$(".cine-request .out");
    if (c.error) { out.innerHTML = `<div class="state bad"><b>Not compiled</b><span>${esc(c.error)}</span></div>`; return; }
    const s = c.spec, by = c.source === "nemotron" ? `Nemotron (${esc(String(c.model || "").split("/").pop())})` : c.source === "guard" ? "the safety guard" : "the regex baseline";
    if (s.refused) {
      out.innerHTML = `<div class="state bad"><b>Refused</b><span>by ${by}: ${esc(s.refusal_reason)}</span></div>`;
      this.$(".cine-sub").textContent = "Refused before take-off";
      return;
    }
    const d = s.directive, r = s.route, problems = c.problems || [];
    if (!flyable) this.$(".cine-sub").textContent = "Parsed, not flown";
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

  // note: the disclosures the side panel would show (replayed calls, the drones' drawn size).
  setRun(run, supervisor, note = "") {
    this.run = run;
    this.el.classList.remove("idle");
    document.body.classList.remove("cine-idle");
    this.$(".cine-request").hidden = true;
    this.notices.forEach((n) => n.el.remove());
    this.notices = [];
    this.cards.html = "";
    this.$(".cine-cards").innerHTML = "";
    this.$(".cine-scn").textContent = run.scenario.title;
    this.$(".cine-sub").textContent = `${run.n} drones · ${supervisor}${run.wildfire ? " · alerts to simulated phones only" : ""}`;
    this.$(".cine-note").textContent = note;
  }

  // Each frame: the clock and the counts along the bottom.
  frame(f, valley, speed) {
    const m = f.metrics;
    this.$(".cine-clock .t").textContent = clock(f.t);
    this.$(".cine-clock .speed").textContent = speed && speed !== 1 ? `×${speed}` : "";
    const stat = (v, k, cls = "") => `<div class="s ${cls}"><b>${v}</b><span>${k}</span></div>`;
    const html = valley
      ? stat(valley.located, "fires located", valley.located ? "hot" : "") + stat(`${fmt(m.coverage_pct, 0)}%`, "survey block searched") +
        stat(`${valley.warned}<small> of ${(this.run.scenario.wildfire?.phones || []).length}</small>`, "phones alerted", valley.warned ? "warn" : "") +
        stat(`${m.flying}<small> / ${this.run.n}</small>`, "drones flying")
      : stat(`${fmt(m.formation_error_m, 2)}<small> m</small>`, "formation error") + stat(`${fmt(m.min_sep_m, 1)}<small> m</small>`, "closest pair") +
        stat(`${m.flying}<small> / ${this.run.n}</small>`, "drones flying", m.lost ? "warn" : "") + stat(`${fmt(m.leader_speed, 1)}<small> m/s</small>`, "speed");
    if (html !== this.statsHtml) this.$(".cine-stats").innerHTML = this.statsHtml = html;
    if (!valley && f.directive) this.plan(f.directive);
  }

  // The swarm's plan now (scenarios other than the valley), and who set it.
  plan(d) {
    const src = this.planSrc || "plan";
    const html = `<div class="card glass"><h4>Swarm plan <span class="chip ${esc(src)}">${esc(src === "plan" ? "mission plan" : src)}</span></h4>` +
      `<div class="plan-grid"><div><b>${esc(d.formation)}</b><span>formation</span></div><div><b>${fmt(d.spacing_m)} m</b><span>spacing</span></div>` +
      `<div><b>${fmt(d.speed_mps)} m/s</b><span>speed</span></div><div><b>${fmt(d.altitude_m, 0)} m</b><span>altitude</span></div></div>` +
      (this.planWhy ? `<p class="why">${esc(this.planWhy)}</p>` : "") + "</div>";
    if (html !== this.cards.html) this.$(".cine-cards").innerHTML = this.cards.html = html;
  }
  planBy(src, why) { this.planSrc = src; this.planWhy = why || ""; }

  // The fire crew brief and the raised zones (wildfire valley).
  valley(valley) {
    const fires = (valley.brief || []).slice().sort((a, b) => (b.state === "spreading") - (a.state === "spreading") || b.head_m_per_min - a.head_m_per_min);
    const rows = fires.slice(0, 3).map((b) => {
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
    const more = fires.length > 3 ? `<li class="more">and ${fires.length - 3} more</li>` : "";
    const brief = `<div class="card glass"><h4>Fire crew brief <span class="when">${valley.briefT != null ? `forecast at ${clock(valley.briefT)}` : ""}</span></h4>` +
      (rows.length ? `<ul class="brief">${rows.join("")}${more}</ul>` : '<p class="why">Waiting for the first fire to be located…</p>') +
      `<div class="bands"><i class="b30"></i><i class="b60"></i><i class="b120"></i><i class="b180"></i>fire likely within 30, 60, 120, 180 min</div></div>`;
    const up = (valley.zones || []).map((z, i) => ({ ...z, level: (valley.zonesLevel || [])[i] || 0 })).filter((z) => z.level > 0).sort((a, b) => b.level - a.level);
    const zones = up.length ? `<div class="card glass"><h4>Evacuation alerts <span class="when">simulated phones</span></h4><ul class="zones">` +
      up.slice(0, 5).map((z) => `<li><span class="lvl l${z.level}">Level ${z.level}</span><b>${esc(z.code)}</b><span class="name">${esc(z.name || "")}</span></li>`).join("") +
      (up.length > 5 ? `<li class="more">and ${up.length - 5} more zones</li>` : "") + "</ul>" +
      `<div class="key">${[...new Set(up.map((z) => z.level))].sort().map((l) => `Level ${l}: ${LEVEL_HEAD[l].toLowerCase()}`).join(" · ")}</div></div>` : "";
    const html = brief + zones;
    if (html !== this.cards.html) this.$(".cine-cards").innerHTML = this.cards.html = html;
  }

  clearNotices() { this.notices.forEach((n) => n.el.remove()); this.notices = []; }

  // The data of the notice with this key while it is still up and not about to go (null otherwise), so that things
  // sent together can share one notice.
  live(key) {
    const n = this.notices.find((q) => q.key === key && !q.leaving && this.view.vt - q.at < NOTICE_S - 1.5);
    return n ? n.data : null;
  }

  // A short notice of something that just happened: kind picks its colour (station, alert, crew, plan, event).
  // With a key, a notice still up under that key is rewritten in place (and kept up a little longer) instead.
  notice(kind, title, sub = "", key = null, data = null) {
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

  // Fade the notices in and out by the view's clock (and pulse the compiler's dots).
  update(vt) {
    const dots = this.$(".cine-request .dots");
    if (dots) dots.textContent = ".".repeat(1 + (Math.floor(vt * 3) % 3));
    this.notices = this.notices.filter((n) => {
      const age = vt - n.at;
      if (age > NOTICE_S) { n.el.remove(); return false; }
      const a = Math.min(1, age / 0.35, (NOTICE_S - age) / 0.6);
      n.el.style.opacity = a.toFixed(3);
      n.el.style.transform = `translateX(${((1 - Math.min(1, age / 0.35)) * -24).toFixed(1)}px)`;
      return true;
    });
  }
}
