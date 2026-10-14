// Halo Systems dashboard: the wildfire valley in the 3D view. Everything here comes from the run: the
// fires' true growth (the arrival-time grid sent once), each fire's burning area and smoke-plume height
// (every frame), the drones' smoke sightings, the fires the ground station has located, its spread
// forecast, its evacuation alerts and the simulated phones they go to. Flame and smoke particles are
// drawn at the simulated burning cells and rise to the simulated plume tops; their look is decoration.
import * as THREE from "three";
import { hash } from "/static/util3d.js";

const FLAME_S = 300;   // a cell is drawn flaming for this long after the fire reaches it, then charred
// Forecast bands: the station's median arrival time within 30, 60, 120 and 180 minutes, soonest reddest.
export const BANDS = [[30, [214, 40, 32, 0.34]], [60, [240, 96, 32, 0.26]], [120, [250, 150, 40, 0.2]], [180, [252, 204, 80, 0.15]]];
const LEVEL_RGB = { evacuate: "220,40,60", warning: "245,140,30" };
const PHONE_RGB = [[0.93, 0.95, 0.97], [1.0, 0.62, 0.12], [1.0, 0.24, 0.24]];   // no alert, warning, evacuate

export class WildfireLayer {
  constructor(view, hello) {
    this.view = view;
    const wf = hello.scenario.wildfire;
    this.fires = wf.fires;
    this.smokeModel = wf.smoke_model;
    this.truth = hello.wildfire.fire_truth;
    this.state = wf.fires.map(() => ({ area_m2: 0, smoke_top_m: 0, active: false }));
    this.located = [];
    this.alerts = new Map();     // alert id -> { level, zone, state }
    this.forecast = null;        // the latest forecast event
    this.t = 0;
    this.group = new THREE.Group();
    view.world.add(this.group);
    // Truth cells sorted by arrival, so each step only looks at cells the fire has newly reached.
    const a = this.truth.arrival_s;
    this.order = [...a.keys()].filter((k) => a[k] !== null).sort((p, q) => a[p] - a[q]);
    this.burnCv = document.createElement("canvas");
    this.burnCv.width = this.truth.nx; this.burnCv.height = this.truth.ny;
    this.burnNext = 0;           // index into order of the first cell not yet reached
    this.burnDrawnAt = -Infinity;
    this.bandCv = null;
    this._overhangs(wf.overhangs || []);
    this._phones(wf.phones, new Set(hello.wildfire.phones_unreachable || []));
    for (const p of wf.places || []) {
      const label = view._label(p.name, "#7cc4ff");
      label.position.set(p.x, view.groundAt(p.x, p.y) + 18, -p.y);
      this.group.add(label);
    }
    this.labels = new Map();     // located fire id -> label sprite
    this._sightLines();
  }

  // -- built once ---------------------------------------------------------------------------------
  _overhangs(list) {
    // A rock shelf over a fire (the simulation's collision box), with a back wall into the slope, so it
    // reads as a shallow cave: no camera looking down sees the fire under it.
    const rock = new THREE.MeshStandardMaterial({ color: 0x8a7f72, roughness: 1, flatShading: true });
    const rough = (geo, amp, seed) => {
      const p = geo.attributes.position;
      for (let k = 0; k < p.count; k++) {
        const x = p.getX(k), y = p.getY(k), z = p.getZ(k);
        p.setXYZ(k, x + (hash(x * 3 + seed, z) - 0.5) * amp, y + (hash(z + seed, x * 2) - 0.5) * amp, z + (hash(y + seed, x) - 0.5) * amp);
      }
      geo.computeVertexNormals();
      return geo;
    };
    this.shelves = [];
    for (const [i, o] of list.entries()) {
      const [x0, y0, z0] = o.lo, [x1, y1, z1] = o.hi;
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      const slab = new THREE.Mesh(rough(new THREE.BoxGeometry(x1 - x0, z1 - z0, y1 - y0, 7, 2, 5), 0.7, i), rock);
      slab.position.set(cx, (z0 + z1) / 2, -cy);
      // Uphill: where the slope rises, the shelf joins the hillside.
      const g = this.view.groundAt.bind(this.view);
      const gx = g(cx + 3, cy) - g(cx - 3, cy), gy = g(cx, cy + 3) - g(cx, cy - 3);
      const up = Math.hypot(gx, gy) > 1e-3 ? [gx / Math.hypot(gx, gy), gy / Math.hypot(gx, gy)] : [0, 1];
      const half = Math.abs(up[0]) * (x1 - x0) / 2 + Math.abs(up[1]) * (y1 - y0) / 2;
      const bx = cx + up[0] * half, by = cy + up[1] * half, gb = g(bx, by);
      const span = Math.abs(up[1]) * (x1 - x0) + Math.abs(up[0]) * (y1 - y0);
      const wallH = Math.max(1, z1 - gb + 1.5);
      const wall = new THREE.Mesh(rough(new THREE.BoxGeometry(span + 2, wallH, 3, 6, 3, 2), 0.9, i + 7), rock);
      wall.position.set(bx, gb - 1 + wallH / 2, -by);
      wall.rotation.y = Math.atan2(up[1], up[0]) - Math.PI / 2;
      for (const m of [slab, wall]) { m.castShadow = m.receiveShadow = true; this.group.add(m); }
      // Smoke from under the shelf comes out at its downhill lip.
      this.shelves.push({ x: cx - up[0] * half, y: cy - up[1] * half, z: z1, down: [-up[0], -up[1]] });
    }
  }

  _phones(phones, unreachable) {
    // The simulated phones: a dot each, coloured by the alert it has received.
    const n = phones.length;
    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(n * 3);
    phones.forEach((p, i) => pos.set([p.x, this.view.groundAt(p.x, p.y) + 2.5, -p.y], i * 3));
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("aColor", new THREE.BufferAttribute(new Float32Array(n * 3), 3).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute("aAlpha", new THREE.BufferAttribute(new Float32Array(n), 1));
    phones.forEach((p, i) => geo.attributes.aAlpha.setX(i, unreachable.has(i) ? 0.45 : 1));
    const mat = new THREE.ShaderMaterial({
      uniforms: { uTex: { value: this.view.markerTex }, uSize: { value: 11 } },
      vertexShader: `attribute vec3 aColor; attribute float aAlpha; varying vec3 vColor; varying float vAlpha; uniform float uSize;
        void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_PointSize = uSize; vColor = aColor; vAlpha = aAlpha; }`,
      fragmentShader: `uniform sampler2D uTex; varying vec3 vColor; varying float vAlpha;
        void main() { vec4 t = texture2D(uTex, gl_PointCoord); gl_FragColor = vec4(t.rgb * vColor, t.a * vAlpha); }`,
      transparent: true, depthWrite: false,
    });
    this.phones = new THREE.Points(geo, mat);
    this.phones.frustumCulled = false;
    this.phones.renderOrder = 8;
    this.group.add(this.phones);
    this._colourPhones(new Array(n).fill(0));
  }

  _colourPhones(level) {
    const c = this.phones.geometry.attributes.aColor;
    level.forEach((l, i) => c.setXYZ(i, ...PHONE_RGB[l] || PHONE_RGB[0]));
    c.needsUpdate = true;
  }

  _sightLines() {
    // Each recent smoke sighting: a line from the drone along its bearing, fading over 5 s.
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(80 * 3), 3).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(80 * 4), 4).setUsage(THREE.DynamicDrawUsage));
    geo.setDrawRange(0, 0);
    this.sights = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false }));
    this.sights.frustumCulled = false;
    this.sights.renderOrder = 6;
    this.group.add(this.sights);
  }

  // -- per frame and per event --------------------------------------------------------------------
  frame(f) {
    this.t = f.t;
    if (f.fires_state) f.fires_state.forEach((s) => { this.state[s.id] = s; });
    if (f.phones_alerted) this._colourPhones(f.phones_alerted);
    if (f.alerts) for (const a of f.alerts) {
      const known = this.alerts.get(a.id);
      if (known && known.state !== a.state) { known.state = a.state; this.view.overlayDirty = true; }
    }
    if (f.located) this._located(f.located);
    if (f.sightings) this._drawSightings(f);
    // Redraw the burn scar when a cell starts or stops flaming (checked once a simulated second).
    if (f.t - this.burnDrawnAt >= 1) this._drawBurn(f.t);
  }

  event(e) {
    if (e.kind === "forecast") {
      this.forecast = e;
      this._drawBands();
      this.view.overlayDirty = true;
    } else if (e.kind === "alert_drafted") {
      this.alerts.set(e.alert, { level: e.level, zone: e.zone, state: "checked" });
      this.view.overlayDirty = true;
    } else if (e.kind === "alert_check" && !e.ok) {
      const a = this.alerts.get(e.alert);
      if (a) { a.state = "rejected"; this.view.overlayDirty = true; }
    } else if (e.kind === "alert_delivered") {
      const a = this.alerts.get(e.alert);
      if (a) { a.state = "delivered"; this.view.overlayDirty = true; }
    }
  }

  _located(list) {
    let changed = list.length !== this.located.length;
    for (const l of list) {
      const text = `Fire ${l.id + 1} · ${l.by === "thermal" ? "thermal fix" : "from smoke"} ±${Math.max(1, Math.round(l.err_m))} m`;
      let label = this.labels.get(l.id);
      if (!label) {
        label = this.view._label(text);
        this.labels.set(l.id, label);
        this.group.add(label);
      }
      if (label.userData.text !== text) { label.userData.setText(text); changed = true; }
      label.position.set(l.x, this.view.groundAt(l.x, l.y) + 16, -l.y);
    }
    this.located = list;
    if (changed) this.view.overlayDirty = true;
  }

  _drawSightings(f) {
    const geo = this.sights.geometry, pos = geo.attributes.position, col = geo.attributes.color;
    let k = 0;
    for (const s of f.sightings.slice(-40)) {
      const q = f.p[s.drone];
      if (!q) continue;
      const age = Math.max(0, f.t - s.t), a = Math.max(0, 1 - age / 5);
      const b = (s.bearing_deg * Math.PI) / 180, len = s.kind === "camera" ? 170 : 60;   // bearing: 0 = east, counter-clockwise
      const rgb = s.kind === "camera" ? [1, 0.8, 0.38] : [0.62, 0.86, 1];
      pos.setXYZ(k, q[0], q[2], -q[1]);
      pos.setXYZ(k + 1, q[0] + Math.cos(b) * len, q[2] - 0.08 * len, -(q[1] + Math.sin(b) * len));
      col.setXYZW(k, ...rgb, 0.85 * a);
      col.setXYZW(k + 1, ...rgb, 0);
      k += 2;
    }
    geo.setDrawRange(0, k);
    pos.needsUpdate = col.needsUpdate = true;
  }

  _drawBurn(t) {
    // One pixel per 10 m truth cell: glowing where it is flaming, charred behind the front.
    this.burnDrawnAt = t;
    const { nx, ny, arrival_s: a } = this.truth;
    while (this.burnNext < this.order.length && a[this.order[this.burnNext]] <= t) this.burnNext++;
    const g = this.burnCv.getContext("2d");
    const img = g.createImageData(nx, ny);
    for (let i = 0; i < this.burnNext; i++) {
      const k = this.order[i], age = t - a[k];
      const ix = k % nx, iy = Math.floor(k / nx), o = ((ny - 1 - iy) * nx + ix) * 4;
      const glow = Math.max(0, 1 - age / FLAME_S);
      img.data.set([34 + 200 * glow, 26 + 60 * glow, 20 + 6 * glow, 225], o);
    }
    g.putImageData(img, 0, 0);
    this.view.overlayDirty = true;
  }

  _drawBands() {
    const e = this.forecast, gr = e.grid;
    const cv = this.bandCv = document.createElement("canvas");
    cv.width = gr.nx; cv.height = gr.ny;
    const g = cv.getContext("2d"), img = g.createImageData(gr.nx, gr.ny);
    e.p50_arrival_s.forEach((s, k) => {
      if (s === null) return;
      const band = BANDS.find(([m]) => s <= m * 60);
      if (!band) return;
      const [r, gg, b, al] = band[1];
      const ix = k % gr.nx, iy = Math.floor(k / gr.nx);
      img.data.set([r, gg, b, 255 * al], ((gr.ny - 1 - iy) * gr.nx + ix) * 4);
    });
    g.putImageData(img, 0, 0);
  }

  // Painted on the ground overlay: under the camera coverage wash, the forecast bands and the burn scar.
  drawUnder(g) {
    const v = this.view, o = v.overlay;
    const grid = (gr, cv) => {
      const [u, w] = v._toPx(gr.x0 - gr.cell_m / 2, gr.y0 + (gr.ny - 0.5) * gr.cell_m);
      g.imageSmoothingEnabled = true;
      g.drawImage(cv, u, w, gr.nx * gr.cell_m * o.px, gr.ny * gr.cell_m * o.px);
    };
    if (this.bandCv) grid(this.forecast.grid, this.bandCv);
    grid(this.truth, this.burnCv);
  }

  // On top: alert zones (dashed while waiting for approval, solid once delivered) and located fires.
  drawOver(g) {
    const v = this.view, px = v.overlay.px;
    for (const a of this.alerts.values()) {
      if (a.state === "rejected") continue;
      const rgb = LEVEL_RGB[a.level] || LEVEL_RGB.warning;
      g.beginPath();
      a.zone.forEach((p, k) => { const [u, w] = v._toPx(p[0], p[1]); k ? g.lineTo(u, w) : g.moveTo(u, w); });
      g.closePath();
      g.fillStyle = `rgba(${rgb},${a.state === "delivered" ? 0.13 : 0.06})`; g.fill();
      g.lineWidth = Math.max(2.5, 1.1 * px); g.strokeStyle = `rgba(${rgb},.95)`;
      g.setLineDash(a.state === "delivered" ? [] : [5 * px, 3 * px]); g.stroke(); g.setLineDash([]);
    }
    for (const l of this.located) {
      const [u, w] = v._toPx(l.x, l.y), r = Math.max(3, l.err_m) * px;
      g.strokeStyle = l.by === "thermal" ? "rgba(255,70,40,.95)" : "rgba(255,200,70,.95)";
      g.lineWidth = Math.max(2, 0.7 * px);
      g.setLineDash(l.by === "thermal" ? [] : [2.5 * px, 2 * px]);
      g.beginPath(); g.arc(u, w, r, 0, Math.PI * 2); g.stroke(); g.setLineDash([]);
      g.beginPath(); g.moveTo(u - 2 * px, w); g.lineTo(u + 2 * px, w); g.moveTo(u, w - 2 * px); g.lineTo(u, w + 2 * px); g.stroke();
    }
  }

  // -- particles ----------------------------------------------------------------------------------
  step(dt) {
    if (!dt) return;
    const v = this.view, t = this.t, wind = v.wind;
    const chance = (rate) => { const e = rate * dt; return Math.floor(e) + (Math.random() < e - Math.floor(e) ? 1 : 0); };
    const ember = new THREE.Color(), smokeCol = new THREE.Color();
    // Flames on the cells the fire reached in the last few minutes.
    const { nx, cell_m: c, x0, y0, arrival_s: a } = this.truth;
    for (let i = this.burnNext - 1; i >= 0; i--) {
      const k = this.order[i], age = t - a[k];
      if (age > FLAME_S) break;
      const s = 1 - age / FLAME_S;
      const cx = x0 + (k % nx) * c, cy = y0 + Math.floor(k / nx) * c;
      for (let q = chance(14 * s + 2); q > 0; q--) {
        const x = cx + (Math.random() - 0.5) * c, y = cy + (Math.random() - 0.5) * c;
        ember.setHSL(0.02 + 0.08 * Math.random(), 1, 0.5 + 0.15 * Math.random());
        v.flames.emit(x, v.groundAt(x, y) + 0.3, -y, 0, 1.5 + 2.5 * Math.random() * s, 0,
          0.5 + 0.6 * Math.random(), 1.6 + 2.2 * s, -0.6, 0.9, ember);
      }
    }
    // Each fire: embers where it smoulders, and a plume that rises to its simulated smoke top.
    const rise = this.smokeModel.rise_mps;
    this.fires.forEach((fire, i) => {
      const st = this.state[i];
      if (!st || !st.smoke_top_m) return;
      const g = v.groundAt(fire.x, fire.y);
      if (!st.active) for (let q = chance(6); q > 0; q--) {
        ember.setHSL(0.03 + 0.05 * Math.random(), 1, 0.45 + 0.15 * Math.random());
        v.flames.emit(fire.x + (Math.random() - 0.5) * 1.6, g + 0.2, -fire.y + (Math.random() - 0.5) * 1.6,
          0, 0.4 + 0.6 * Math.random(), 0, 0.5 + 0.5 * Math.random(), 0.9, -0.4, 0.8, ember);
      }
      let sx = fire.x, sy = fire.y, sz = g + 1;
      if (fire.shelter === "overhang" && this.shelves.length) {
        const sh = this.shelves.reduce((b, s) => (Math.hypot(s.x - fire.x, s.y - fire.y) < Math.hypot(b.x - fire.x, b.y - fire.y) ? s : b));
        sx = sh.x + sh.down[0] * 1.5; sy = sh.y + sh.down[1] * 1.5; sz = sh.z;
      }
      // The particles slow as they rise (Particles.update damps vertical speed at 0.08/s), so start them
      // fast enough to reach the plume top at the end of their life.
      const top = st.smoke_top_m, life = 1.1 * Math.max(3, (top - (sz - g)) / rise);
      const vy0 = (Math.max(1, top - (sz - g)) * 0.08) / (1 - Math.exp(-0.08 * life));
      const big = Math.sqrt(st.area_m2);
      for (let q = chance(4 + 0.9 * big); q > 0; q--) {
        const d = 0.08 * Math.random(), base = st.active ? 0.5 : 0.74;
        smokeCol.setRGB(base + d + 0.03, base + d, base + d - 0.03);
        v.smoke.emit(sx + (Math.random() - 0.5) * Math.min(12, 1 + big / 4), sz, -sy + (Math.random() - 0.5) * Math.min(12, 1 + big / 4),
          wind.x, vy0 * (0.85 + 0.3 * Math.random()), wind.z, life * (0.8 + 0.4 * Math.random()),
          1.6 + big / 7, 0.3 + top / life / 4, st.active ? 0.55 : 0.42, smokeCol);
      }
    });
  }
}
