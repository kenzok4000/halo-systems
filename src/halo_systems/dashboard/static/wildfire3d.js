// Halo Systems dashboard: the wildfire valley in the 3D view. Everything here comes from the run: the
// fires' true growth (the arrival-time grid sent once), each fire's burning area and smoke-plume height
// (every frame), the drones' smoke sightings, the fires the ground station has located, its spread
// forecast, its evacuation alerts and the simulated phones they go to. Flame and smoke particles are
// drawn at the simulated burning cells and rise to the simulated plume tops; their look is decoration.
import * as THREE from "three";
import { hash, noise2, stoneColors } from "/static/util3d.js";

const FLAME_S = 300;   // a cell is drawn flaming for this long after the fire reaches it, then charred
const UP = 4;          // overlay grids are drawn this many pixels per cell, so their edges are smooth curves

// The value of a cell grid (f(ix, iy), row iy = 0 at the south edge) at pixel (px, py) of an image UP times
// larger, row 0 at the north edge: bilinear between the four nearest cell centres.
function sample(f, nx, ny, px, py) {
  const u = Math.max(0, Math.min(nx - 1, (px + 0.5) / UP - 0.5)), v = Math.max(0, Math.min(ny - 1, ny - 1 - ((py + 0.5) / UP - 0.5)));
  const i = Math.min(nx - 2, Math.floor(u)), j = Math.min(ny - 2, Math.floor(v)), a = u - i, b = v - j;
  return (f(i, j) * (1 - a) + f(i + 1, j) * a) * (1 - b) + (f(i, j + 1) * (1 - a) + f(i + 1, j + 1) * a) * b;
}
// Forecast bands: the station's median arrival time within 30, 60, 120 and 180 minutes, soonest reddest.
export const BANDS = [[30, [214, 40, 32, 0.25]], [60, [240, 96, 32, 0.19]], [120, [250, 150, 40, 0.15]], [180, [252, 204, 80, 0.11]]];
// Evacuation zones in Japan's alert colours: Level 3 red, Level 4 purple, Level 5 black.
export const ZONE_RGB = { 3: "214,40,40", 4: "142,52,178", 5: "22,22,28" };
const LEVEL_RGB = { evacuate: "220,40,60", warning: "245,140,30", ...ZONE_RGB };
// The station's compass words (sim/wildfire.py compass), 22.5 degrees apart anticlockwise from east.
const COMPASS = ["east", "east-northeast", "northeast", "north-northeast", "north", "north-northwest", "northwest", "west-northwest",
  "west", "west-southwest", "southwest", "south-southwest", "south", "south-southeast", "southeast", "east-southeast"];
const HEAD_MIN = 60;   // a head arrow runs to where the forecast puts the fire's head this many minutes from now
const GROW_S = 1.6;    // view seconds an arrow takes to draw itself out when the crews are told of its fire
// A phone's dot, by the latest alert level it received (0: none or cleared).
const PHONE_RGB = { 0: [0.93, 0.95, 0.97], 1: [1.0, 0.62, 0.12], 2: [1.0, 0.24, 0.24], 3: [1.0, 0.26, 0.22], 4: [0.74, 0.38, 1.0], 5: [0.2, 0.2, 0.24] };

export class WildfireLayer {
  constructor(view, hello) {
    this.view = view;
    const wf = hello.scenario.wildfire;
    this.fires = wf.fires;
    this.smokeModel = wf.smoke_model;
    this.truth = hello.wildfire.fire_truth;
    this.state = wf.fires.map(() => ({ area_m2: 0, smoke_top_m: 0, active: false }));
    this.located = [];
    this.alerts = new Map();     // alert id -> { level, zone, poly, state }
    this.zones = hello.wildfire.zones || wf.zones || [];   // fixed evacuation zones {code, name, polygon}
    this.zoneLevel = this.zones.map(() => 0);
    this.forecast = null;        // the latest forecast event
    this.told = new Map();       // fire -> simulated time of its latest crew update
    this.arrows = new Map();     // fire -> its head arrow { mesh, tags, L, born }
    this.t = 0;
    this.group = new THREE.Group();
    view.world.add(this.group);
    // Truth cells sorted by arrival, so each step only looks at cells the fire has newly reached.
    const a = this.truth.arrival_s;
    this.order = [...a.keys()].filter((k) => a[k] !== null).sort((p, q) => a[p] - a[q]);
    this.burnCv = document.createElement("canvas");
    this.burnCv.width = this.truth.nx * UP; this.burnCv.height = this.truth.ny * UP;
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
    this._forest(view.forestTrees);
  }

  _forest(ft) {
    // The forest's trees, in the order the fire reaches them: lit orange as the front passes, then charred.
    this.trees = null;
    if (!ft) return;
    const { nx, ny, cell_m: c, x0, y0, arrival_s: a } = this.truth;
    const list = [], base = new Float32Array(ft.xy.length * 3), col = new THREE.Color();
    ft.xy.forEach(([x, y], i) => {
      ft.plants.getColor(i, col); base.set([col.r, col.g, col.b], i * 3);
      const ix = Math.round((x - x0) / c), iy = Math.round((y - y0) / c);
      if (ix < 0 || iy < 0 || ix >= nx || iy >= ny || a[iy * nx + ix] === null) return;
      list.push([a[iy * nx + ix], i]);
    });
    list.sort((p, q) => p[0] - q[0]);
    this.trees = { plants: ft.plants, list, base, reached: 0, settled: 0 };
  }

  _canopy(x, y) {
    const lc = this.view.landcover;
    if (!lc || !lc.canopy_m) return 0;
    const ix = Math.round((x - lc.x0) / lc.cell_m), iy = Math.round((y - lc.y0) / lc.cell_m);
    return ix < 0 || iy < 0 || ix >= lc.nx || iy >= lc.ny ? 0 : lc.canopy_m[iy * lc.nx + ix] || 0;
  }

  // -- built once ---------------------------------------------------------------------------------
  _overhangs(list) {
    // A rock shelf over a fire (the simulation's collision box), with a back wall into the slope, so it
    // reads as a shallow cave: no camera looking down sees the fire under it.
    const rock = new THREE.MeshStandardMaterial({ color: 0x968b7d, roughness: 1, vertexColors: true });
    const rough = (geo, amp, seed) => {
      const p = geo.attributes.position;
      for (let k = 0; k < p.count; k++) {
        const x = p.getX(k), y = p.getY(k), z = p.getZ(k);
        p.setXYZ(k, x + (hash(x * 3 + seed, z) - 0.5) * amp, y + (hash(z + seed, x * 2) - 0.5) * amp, z + (hash(y + seed, x) - 0.5) * amp);
      }
      geo.computeVertexNormals();
      return stoneColors(geo, seed);
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
        void main() { vec4 t = texture2D(uTex, gl_PointCoord); gl_FragColor = vec4(pow(t.rgb * vColor, vec3(2.2)), t.a * vAlpha);   // display values
          #include <colorspace_fragment>
        }`,
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
    level.forEach((l, i) => c.setXYZ(i, ...(PHONE_RGB[l] || PHONE_RGB[0])));
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
    if (!this.warmed) { this.warmed = true; this.warm(); }
    if (f.phones_alerted) this._colourPhones(f.phones_alerted);
    if (f.zones_level && f.zones_level.some((l, i) => l !== this.zoneLevel[i])) { this.zoneLevel = f.zones_level.slice(); this.view.overlayDirty = true; }
    if (f.alerts) for (const a of f.alerts) {
      const known = this.alerts.get(a.id);
      if (known && known.state !== a.state) { known.state = a.state; this.view.overlayDirty = true; }
    }
    if (f.located) this._located(f.located);
    if (f.sightings) this._drawSightings(f);
    // Redraw the burn scar when a cell starts or stops flaming (checked once a simulated second).
    if (f.t - this.burnDrawnAt >= 1) this._drawBurn(f.t);
  }

  warm() {
    for (const r of this.arrows.values()) if (r.born != null) { r.born = -Infinity; this.tick(this.view.vt); }   // a jump: no arrow grows on camera
    // A fire that was already burning when the run began (or when the recorder jumps ahead) already has its
    // plume: grow the plumes to their present height at once, instead of rising from the ground on camera.
    this.view.smoke.clear();
    const rise = this.smokeModel.rise_mps, top = Math.max(0, ...this.state.map((s) => s.smoke_top_m || 0));
    for (let s = 0; s < (1.3 * top) / rise; s += 0.5) { this._smoke(0.5); this.view.smoke.update(0.5, this.view.wind); }
  }

  event(e) {
    if (e.kind === "forecast") {
      this.forecast = e;
      this._drawBands();
      this._heads();
      this.view.overlayDirty = true;
    } else if (e.kind === "crew_update") {
      // The crews were told about this fire with this forecast: its arrow draws itself out from the fire.
      this.told.set(e.fire, e.t);
      if (this.forecast && this.forecast.t === e.t) this._grow(e.fire);
    } else if (e.kind === "alert_drafted") {
      const poly = Array.isArray(e.polygon) ? e.polygon : Array.isArray(e.zone) ? e.zone : null;
      this.alerts.set(e.alert, { level: e.level, zone: e.zone, poly, state: "checked" });
      this.view.overlayDirty = true;
    } else if (e.kind === "alert_cancelled" || e.kind === "all_clear") {
      const a = e.alert != null && this.alerts.get(e.alert);
      if (a) a.state = "cancelled";
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
    // A located fire folded into another (both on one patch of burning ground) drops out of the list.
    const ids = new Set(list.map((l) => l.id));
    for (const [id, label] of this.labels) {
      if (ids.has(id)) continue;
      this.group.remove(label); label.material.map?.dispose(); label.material.dispose();
      this.labels.delete(id); changed = true;
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
    this.sights.visible = this.view.mode !== "ride" && this.view.mode !== "close";   // among the drones, their bearing lines cross the whole picture
  }

  _drawBurn(t) {
    // The 10 m truth cells, drawn smooth with a ragged edge: glowing where flaming, charred behind the front.
    this.burnDrawnAt = t;
    const { nx, ny, arrival_s: a } = this.truth;
    while (this.burnNext < this.order.length && a[this.order[this.burnNext]] <= t) this.burnNext++;
    const g = this.burnCv.getContext("2d"), W = nx * UP, H = ny * UP;
    const img = g.createImageData(W, H);
    if (this.burnNext) {
      const burnt = (i, j) => { const s = a[j * nx + i]; return s !== null && s <= t ? 1 : 0; };
      const glow = (i, j) => { const s = a[j * nx + i]; return s !== null && s <= t ? Math.max(0, 1 - (t - s) / FLAME_S) : 0; };
      for (let py = 0; py < H; py++) {
        for (let px = 0; px < W; px++) {
          const b = sample(burnt, nx, ny, px, py);
          if (b <= 0.2 || b < 0.5 + 0.3 * (noise2(px * 0.3, py * 0.3) - 0.5)) continue;
          const gl = sample(glow, nx, ny, px, py) / Math.max(0.5, b), n = noise2(px * 0.9 + 5, py * 0.9);
          img.data.set([28 + 12 * n + 200 * gl, 22 + 8 * n + 60 * gl, 18 + 6 * n + 6 * gl, 215 + 25 * gl], (py * W + px) * 4);
        }
      }
    }
    g.putImageData(img, 0, 0);
    this.view.overlayDirty = true;
    this._charTrees(t);
  }

  _charTrees(t) {
    const tr = this.trees;
    if (!tr) return;
    while (tr.reached < tr.list.length && tr.list[tr.reached][0] <= t) tr.reached++;
    const c = new THREE.Color(), hot = new THREE.Color(1, 0.45, 0.14), ember = new THREE.Color(0.32, 0.12, 0.05), soot = new THREE.Color(0.075, 0.065, 0.06);
    for (let j = tr.settled; j < tr.reached; j++) {
      const [ta, i] = tr.list[j], age = t - ta;
      if (age >= FLAME_S) { tr.plants.setColor(i, soot); if (j === tr.settled) tr.settled++; continue; }
      if (age < 25) c.setRGB(tr.base[i * 3], tr.base[i * 3 + 1], tr.base[i * 3 + 2]).lerp(hot, Math.min(1, age / 6));
      else if (age < 90) c.copy(hot).lerp(ember, (age - 25) / 65);
      else c.copy(ember).lerp(soot, (age - 90) / (FLAME_S - 90));
      tr.plants.setColor(i, c);
    }
    tr.plants.commit();
  }

  _drawBands() {
    // The forecast's median arrival time, drawn smooth: each band a light wash with a thin line along its
    // outer edge (where the fire is due at that band's time), like the contours on a map.
    const e = this.forecast, gr = e.grid, a = e.p50_arrival_s, W = gr.nx * UP, H = gr.ny * UP;
    const cv = this.bandCv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    const g = cv.getContext("2d"), img = g.createImageData(W, H), band = new Int8Array(W * H).fill(-1);
    const LAST = BANDS[BANDS.length - 1][0] * 60;
    // The median arrival per cell, softened over the neighbouring cells the forecast reaches, so the contours
    // follow the forecast's shape rather than its cells' edges; a cell it never reaches (rock, or beyond the
    // horizon) stays out of every band and does not pull its neighbours later.
    const { nx, ny } = gr;
    let A = Float32Array.from(a, (s) => (s === null ? 0 : Math.min(s, 2 * LAST))), M = Float32Array.from(a, (s) => (s === null ? 0 : 1));
    const blur = (F, step, len) => {   // one [1, 2, 1] pass along rows (step 1) or columns (step nx)
      const B = new Float32Array(F.length);
      for (let k = 0; k < F.length; k++) {
        const c = step === 1 ? k % nx : Math.floor(k / nx);
        B[k] = (c > 0 ? F[k - step] : F[k]) + 2 * F[k] + (c < len - 1 ? F[k + step] : F[k]);
      }
      return B;
    };
    for (let pass = 0; pass < 2; pass++) {
      const S = blur(blur(A.map((v, k) => v * M[k]), 1, nx), nx, ny), N = blur(blur(M, 1, nx), nx, ny);
      A = S.map((v, k) => (N[k] > 0 ? v / N[k] : 0));
    }
    a.forEach((s, k) => { if (s === null) A[k] = 2 * LAST; });
    // Keep the cells already burning (arrival now) inside the first band however the softening moved them.
    a.forEach((s, k) => { if (s !== null && s <= 0) A[k] = Math.min(A[k], 0); });
    const at = (i, j) => A[j * nx + i];
    for (let py = 0; py < H; py++) {
      for (let px = 0; px < W; px++) {
        const s = sample(at, gr.nx, gr.ny, px, py);
        band[py * W + px] = BANDS.findIndex(([m]) => s <= m * 60);
      }
    }
    for (let py = 0; py < H; py++) {
      for (let px = 0; px < W; px++) {
        const k = py * W + px, b = band[k];
        if (b < 0) continue;
        const [r, gg, bl, al] = BANDS[b][1];
        const later = (q) => band[q] > b || band[q] < 0;
        const edge = (px > 0 && later(k - 1)) || (px < W - 1 && later(k + 1)) || (py > 0 && later(k - W)) || (py < H - 1 && later(k + W));
        img.data.set([r, gg, bl, 255 * (edge ? Math.min(0.9, al * 3.2) : al)], k * 4);
      }
    }
    g.putImageData(img, 0, 0);
    this._drawMapped(e.mapped_cells || [], gr);
  }

  _drawMapped(cells, gr) {
    // The ground the drones' downward cameras saw burning, which the forecast starts from: white hatching,
    // over the bounding box of those cells only.
    this.mapped = null;
    if (!cells.length) return;
    let i0 = Infinity, i1 = -1, j0 = Infinity, j1 = -1;
    for (const k of cells) {
      const ix = k % gr.nx, iy = Math.floor(k / gr.nx);
      i0 = Math.min(i0, ix); i1 = Math.max(i1, ix); j0 = Math.min(j0, iy); j1 = Math.max(j1, iy);
    }
    const w = i1 - i0 + 1, h = j1 - j0 + 1;
    const mask = document.createElement("canvas");
    mask.width = w; mask.height = h;
    const mg = mask.getContext("2d"), img = mg.createImageData(w, h);
    for (const k of cells) {
      const ix = k % gr.nx - i0, iy = Math.floor(k / gr.nx) - j0;
      img.data[((h - 1 - iy) * w + ix) * 4 + 3] = 255;
    }
    mg.putImageData(img, 0, 0);
    const s = Math.max(4, Math.round(gr.cell_m * this.view.overlay.px));   // overlay pixels per forecast cell
    const cv = document.createElement("canvas");
    cv.width = w * s; cv.height = h * s;
    const g = cv.getContext("2d");
    g.strokeStyle = "rgba(240,250,255,.9)"; g.lineWidth = Math.max(1, s * 0.14);
    g.beginPath();
    for (let d = -cv.height; d < cv.width; d += Math.max(3, s * 0.5)) { g.moveTo(d, cv.height); g.lineTo(d + cv.height, 0); }
    g.stroke();
    g.globalCompositeOperation = "destination-in";
    g.imageSmoothingEnabled = false;
    g.drawImage(mask, 0, 0, cv.width, cv.height);
    this.mapped = { cv, x: gr.x0 + (i0 - 0.5) * gr.cell_m, y: gr.y0 + (j1 + 0.5) * gr.cell_m, w: w * gr.cell_m, h: h * gr.cell_m };
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
    if (this.mapped) {
      const m = this.mapped, [u, w] = v._toPx(m.x, m.y);
      g.drawImage(m.cv, u, w, m.w * o.px, m.h * o.px);
    }
  }

  // On top: alert zones (dashed while waiting for approval, solid once delivered) and located fires.
  drawOver(g) {
    const v = this.view, px = v.overlay.px;
    const path = (poly) => {
      g.beginPath();
      poly.forEach((p, k) => { const [u, w] = v._toPx(p[0], p[1]); k ? g.lineTo(u, w) : g.moveTo(u, w); });
      g.closePath();
    };
    // The evacuation zones: a faint outline and code each. A raised zone takes its alert level's colour as
    // a light wash with a deeper band inside its edge, so the ground under it still looks like ground.
    this.zones.forEach((z, i) => {
      const lvl = this.zoneLevel[i], rgb = ZONE_RGB[lvl];
      path(z.polygon);
      if (rgb) {
        g.fillStyle = `rgba(${rgb},${lvl === 5 ? 0.12 : 0.07})`; g.fill();
        g.save(); g.clip();
        g.lineWidth = 14 * px; g.strokeStyle = `rgba(${rgb},${lvl === 5 ? 0.32 : 0.24})`; g.stroke();
        g.restore();
        g.lineWidth = Math.max(2.5, 1.1 * px); g.strokeStyle = `rgba(${rgb},.95)`; g.stroke();
      } else {
        g.lineWidth = Math.max(1, 0.4 * px); g.strokeStyle = "rgba(255,255,255,.45)";
        g.setLineDash([3 * px, 3 * px]); g.stroke(); g.setLineDash([]);
      }
      const [cu, cw] = z.polygon.reduce(([a, b], p) => { const [u, w] = v._toPx(p[0], p[1]); return [a + u / z.polygon.length, b + w / z.polygon.length]; }, [0, 0]);
      g.font = `600 ${Math.round(9 * px)}px Inter, system-ui, sans-serif`; g.textAlign = "center"; g.textBaseline = "middle";
      g.lineWidth = Math.max(2, 0.8 * px); g.strokeStyle = "rgba(20,20,24,.55)"; g.strokeText(z.code, cu, cw);
      g.fillStyle = rgb ? "rgba(255,255,255,.95)" : "rgba(255,255,255,.7)"; g.fillText(z.code, cu, cw);
    });
    // Alerts: with zones, only one still waiting for approval is drawn (dashed); the zone's fill shows the rest.
    for (const a of this.alerts.values()) {
      if (a.state === "rejected" || a.state === "cancelled" || !a.poly) continue;
      if (this.zones.length && a.state !== "checked") continue;
      const rgb = LEVEL_RGB[a.level] || LEVEL_RGB.warning;
      path(a.poly);
      g.fillStyle = `rgba(${rgb},${a.state === "delivered" ? 0.13 : 0.06})`; g.fill();
      g.lineWidth = Math.max(2.5, 1.1 * px); g.strokeStyle = `rgba(${rgb},.95)`;
      g.setLineDash(a.state === "delivered" ? [] : [5 * px, 3 * px]); g.stroke(); g.setLineDash([]);
    }
    // Each moving head's direction and its uncertainty: a wedge of ±cone_deg round the forecast bearing, an hour's
    // run long, shaded from the fire outwards for a spreading fire and only outlined for a smouldering one, fainter
    // the fewer forecast members spread. None when the direction is uncertain (90 degrees or more either side).
    for (const b of (this.forecast && this.forecast.brief) || []) {
      if (b.head_bearing_deg == null || b.cone_deg == null || b.cone_deg >= 90 || !b.head_from_xy || !(b.head_m_per_min > 0)) continue;
      const [u, w] = v._toPx(b.head_from_xy[0], b.head_from_xy[1]);
      const r = (b.head_xy ? Math.hypot(b.head_xy[0] - b.head_from_xy[0], b.head_xy[1] - b.head_from_xy[1]) : b.head_m_per_min * HEAD_MIN) * px;
      if (!(r > 1)) continue;
      const c = b.cone_deg, a0 = ((b.head_bearing_deg - c - 90) * Math.PI) / 180, a1 = ((b.head_bearing_deg + c - 90) * Math.PI) / 180;
      const share = b.spread_share ?? 1, spreading = b.state === "spreading";
      g.beginPath(); g.moveTo(u, w); g.arc(u, w, r, a0, a1); g.closePath();
      if (spreading) {
        const fill = g.createRadialGradient(u, w, 0, u, w, r);
        fill.addColorStop(0, `rgba(255,150,70,${(0.3 * share).toFixed(3)})`);
        fill.addColorStop(1, `rgba(255,150,70,${(0.06 * share).toFixed(3)})`);
        g.fillStyle = fill; g.fill();
      }
      g.lineWidth = Math.max(1.5, (spreading ? 0.6 : 0.4) * px);
      g.strokeStyle = `rgba(255,236,214,${((spreading ? 0.8 : 0.45) * share).toFixed(3)})`;
      g.setLineDash([3 * px, 2.5 * px]); g.stroke(); g.setLineDash([]);
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

  // -- the fires' heads -----------------------------------------------------------------------------
  // For each fire the forecast says is moving: an arrow above the trees along the path its head is forecast
  // to take (the brief's head track, from today's front through 15, 30, 60 and 120 minutes; on runs without
  // one, a line in the brief's direction from the located fire, labelled with no time), with chevrons running along it
  // towards the head. A spreading fire's arrow is labelled at its tip and at the hour mark; a smouldering fire's,
  // where it would run if it took off, is thinner and fainter, and left out while its direction is uncertain (its
  // brief says so). Redrawn with each new forecast; an arrow whose fire the crews were just told about draws
  // itself out from the fire (tick). The overlay draws each head's uncertainty wedge under it (drawOver).
  _heads() {
    for (const m of this.heads || []) { this.group.remove(m); m.geometry.dispose(); m.material.dispose?.(); m.material.map?.dispose(); }
    this.heads = [];
    this.arrows = new Map();
    for (const b of (this.forecast && this.forecast.brief) || []) {
      if (!(b.head_m_per_min > 0)) continue;
      const spreading = b.state === "spreading", sure = b.cone_deg == null || b.cone_deg < 90;
      if (!spreading && !sure) continue;
      let pts, mins = HEAD_MIN, timed = true;
      if (b.head_from_xy && Array.isArray(b.head_track) && b.head_track.length) {
        pts = [b.head_from_xy, ...b.head_track.map(([, x, y]) => [x, y])];
        mins = b.head_track[b.head_track.length - 1][0];
      } else if (b.head_from_xy && b.head_xy) pts = [b.head_from_xy, b.head_xy];
      else {
        const l = this.located.find((q) => q.id === b.fire), k = COMPASS.indexOf(b.direction);
        if (!l || k < 0) continue;
        const a = (k * 22.5 * Math.PI) / 180, L = Math.max(40, Math.min(700, b.head_m_per_min * HEAD_MIN));
        pts = [[l.x, l.y], [l.x + Math.cos(a) * L, l.y + Math.sin(a) * L]];
        timed = false;   // a direction only: the tip is not where the forecast puts the head at any time
      }
      const arrow = this._ribbon(pts, spreading ? (sure ? 0.95 : 0.55) : 0.4, spreading ? 1 : 0.55);
      if (!arrow) continue;
      const rec = { mesh: arrow.mesh, tags: [], L: arrow.L, born: null };
      this.arrows.set(b.fire, rec);
      this.heads.push(arrow.mesh);
      this.group.add(arrow.mesh);
      if (spreading) {   // only a spreading fire's arrow is labelled
        const name = `Fire ${b.fire + 1}`;
        const text = !sure ? `${name} · direction uncertain` : timed ? `${name} head in ${mins >= 60 ? `${+(mins / 60).toFixed(1)} h` : `${mins} min`}` : `${name} heading ${b.direction}`;
        const tag = this.view._label(text, "#ff9a4a");
        tag.position.copy(arrow.tip).add(new THREE.Vector3(0, 6, 0));
        tag.scale.multiplyScalar(0.8);
        rec.tags.push(tag);
        // Where the head is forecast to be in an hour, when the arrow runs on past it.
        const hour = timed && mins > 60 && b.head_track.find(([m]) => m === 60);
        if (hour) {
          const [, x, y] = hour, mark = this.view._label("1 h", "#ffc58a");
          mark.position.set(x, this.view.groundAt(x, y) + this._canopy(x, y) + 15, -y);
          mark.scale.multiplyScalar(0.7);
          rec.tags.push(mark);
        }
        for (const t of rec.tags) { this.heads.push(t); this.group.add(t); }
      }
      if (this.told.get(b.fire) === this.forecast.t) this._grow(b.fire);
    }
  }

  // An arrow drawing itself out from its fire over GROW_S view seconds, its labels fading in at the end.
  _grow(fire) {
    const r = this.arrows.get(fire);
    if (!r || r.born != null) return;
    r.born = this.view.vt;
    this.tick(this.view.vt);
  }
  tick(vt) {
    for (const r of this.arrows.values()) {
      if (r.born == null) continue;
      const k = Math.max(0, Math.min(1, (vt - r.born) / GROW_S)), ease = 1 - (1 - k) ** 3;
      r.mesh.material.uniforms.uGrow.value = k >= 1 ? 1e6 : ease * (r.L + 12);
      for (const t of r.tags) t.material.opacity = Math.max(0, Math.min(1, (k - 0.7) / 0.3));
      if (k >= 1) r.born = null;
    }
  }

  // What the fire camera frames: the fire's forecast head path from the front now (fire: 0-based; null picks the
  // fastest spreading fire), or the located fire while the station has no direction for it.
  shot(fire) {
    const brief = (this.forecast && this.forecast.brief) || [];
    const b = fire != null ? brief.find((q) => q.fire === fire)
      : brief.filter((q) => q.state === "spreading" && q.head_bearing_deg != null).sort((p, q) => q.head_m_per_min - p.head_m_per_min)[0];
    if (b && b.head_bearing_deg != null && b.head_from_xy) {
      const tr = Array.isArray(b.head_track) && b.head_track.length ? b.head_track[b.head_track.length - 1] : null;
      const end = tr ? [tr[1], tr[2]] : b.head_xy;
      const dx = end ? end[0] - b.head_from_xy[0] : 0, dy = end ? end[1] - b.head_from_xy[1] : 0, len = Math.hypot(dx, dy);
      // The arrow's own direction where it has one (a head track can bend), else the brief's bearing.
      const bearing = len > 5 ? (Math.atan2(dx, dy) * 180) / Math.PI : b.head_bearing_deg;
      return { x: b.head_from_xy[0], y: b.head_from_xy[1], bearing, len: Math.max(80, len) };
    }
    const id = b ? b.fire : fire, l = this.located.find((q) => q.id === id) || (fire == null && this.located[0]);
    return l ? { x: l.x, y: l.y, bearing: null, len: 120 } : null;
  }

  _ribbon(pts, alpha, thin = 1) {
    // Resample the path every 4 m, at a height that clears the ground and the canopy below it.
    const v = this.view, path = [];
    for (let i = 0; i + 1 < pts.length; i++) {
      const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 4));
      for (let j = i ? 1 : 0; j <= n; j++) path.push([x0 + ((x1 - x0) * j) / n, y0 + ((y1 - y0) * j) / n]);
    }
    if (path.length < 3) return null;
    const lift = path.map(([x, y]) => v.groundAt(x, y) + this._canopy(x, y) + 9);
    const h = lift.map((_, i) => Math.max(...lift.slice(Math.max(0, i - 4), i + 5)));   // no dips between treetops
    const len = [0];
    for (let i = 1; i < path.length; i++) len.push(len[i - 1] + Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]));
    const L = len[len.length - 1], W = thin * Math.max(3.5, Math.min(9, L * 0.03)), headL = Math.min(L * 0.3, W * 4.2);
    const pos = [], aU = [], aV = [], idx = [];
    const push = (i, w) => {
      const p = path[i], q = path[Math.min(path.length - 1, i + 1)], o = path[Math.max(0, i - 1)];
      let tx = q[0] - o[0], ty = q[1] - o[1];
      const tl = Math.hypot(tx, ty) || 1; tx /= tl; ty /= tl;
      for (const s of [-1, 1]) { pos.push(p[0] - ty * w * s, h[i], -(p[1] + tx * w * s)); aU.push(len[i]); aV.push(s); }
    };
    let rows = 0;
    for (let i = 0; i < path.length; i++) {
      const toTip = L - len[i];
      if (toTip > headL) { push(i, W * (0.75 + 0.25 * Math.min(1, len[i] / 30))); rows++; }
      else {
        if (L - len[i - 1] > headL) { push(i, W); push(i, W * 2.3); rows += 2; }   // the arrowhead's shoulders
        else { push(i, W * 2.3 * (toTip / headL) + 0.05); rows++; }
      }
    }
    for (let r = 0; r + 1 < rows; r++) idx.push(2 * r, 2 * r + 1, 2 * r + 2, 2 * r + 1, 2 * r + 3, 2 * r + 2);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute("aU", new THREE.Float32BufferAttribute(aU, 1));
    geo.setAttribute("aV", new THREE.Float32BufferAttribute(aV, 1));
    geo.setIndex(idx);
    const mat = new THREE.ShaderMaterial({
      uniforms: { uT: this.view.swayT, uAlpha: { value: alpha }, uGrow: { value: 1e6 } },
      vertexShader: `attribute float aU; attribute float aV; varying float vU; varying float vV;
        void main() { vU = aU; vV = aV; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `uniform float uT, uAlpha, uGrow; varying float vU; varying float vV;
        void main() {
          float edge = smoothstep(0.7, 1.0, abs(vV));
          float c = fract((vU + abs(vV) * 5.0) / 16.0 - uT * 0.45);           // chevrons, moving towards the head
          float band = smoothstep(0.0, 0.06, c) * (1.0 - smoothstep(0.28, 0.36, c));
          float a = uAlpha * smoothstep(0.0, 20.0, vU) * (0.3 + 0.45 * band + 0.3 * edge) * (1.0 - smoothstep(uGrow - 10.0, uGrow, vU));
          vec3 col = mix(vec3(1.0, 0.45, 0.12), vec3(1.0, 0.86, 0.62), band * 0.65 + edge * 0.2);   // display values
          gl_FragColor = vec4(pow(col, vec3(2.2)), a);
          #include <colorspace_fragment>
        }`,
      transparent: true, depthWrite: false, side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.renderOrder = 5;
    const e = path[path.length - 1];
    return { mesh, L, tip: new THREE.Vector3(e[0], h[h.length - 1], -e[1]) };
  }

  // -- particles ----------------------------------------------------------------------------------
  step(dt) {
    if (!dt) return;
    this._flames(dt);
    this._smoke(dt);
  }

  _flames(dt) {
    const v = this.view, t = this.t;
    const chance = (rate) => { const e = rate * dt; return Math.floor(e) + (Math.random() < e - Math.floor(e) ? 1 : 0); };
    const ember = new THREE.Color();
    // Flames on the cells the fire reached in the last few minutes, strongest just behind the front; where
    // the cell is forest, the front's flames climb into the trees for the first half minute. When the cells
    // would need more flames than the pool holds, fewer and bigger ones are drawn.
    const { nx, cell_m: c, x0, y0, arrival_s: a } = this.truth;
    const cells = [];
    let demand = 0;
    for (let i = this.burnNext - 1; i >= 0; i--) {
      const k = this.order[i], age = t - a[k];
      if (age > FLAME_S) break;
      const s = 1 - age / FLAME_S, cx = x0 + (k % nx) * c, cy = y0 + Math.floor(k / nx) * c;
      const crown = age < 30 ? this._canopy(cx, cy) : 0;
      cells.push([cx, cy, s, crown]);
      demand += (14 * s + 2) * 0.8 + (crown > 3 ? 10 * 1.0 : 0);
    }
    const scale = Math.min(1, (0.85 * v.flames.max) / Math.max(1, demand)), grow = 1 / Math.sqrt(scale);
    for (const [cx, cy, s, crown] of cells) {
      for (let q = chance((14 * s + 2) * scale); q > 0; q--) {
        const x = cx + (Math.random() - 0.5) * c, y = cy + (Math.random() - 0.5) * c;
        ember.setHSL(0.02 + 0.08 * Math.random(), 1, 0.5 + 0.15 * Math.random());
        v.flames.emit(x, v.groundAt(x, y) + 0.3, -y, 0, 1.5 + 2.5 * Math.random() * s, 0,
          0.5 + 0.6 * Math.random(), (1.6 + 2.2 * s) * grow, -0.6, 0.9, ember);
      }
      if (crown > 3) for (let q = chance(10 * scale); q > 0; q--) {
        const x = cx + (Math.random() - 0.5) * c, y = cy + (Math.random() - 0.5) * c;
        ember.setHSL(0.03 + 0.07 * Math.random(), 1, 0.52 + 0.15 * Math.random());
        v.flames.emit(x, v.groundAt(x, y) + crown * (0.2 + 0.7 * Math.random()), -y, 0, 2 + 3 * Math.random(), 0,
          0.6 + 0.6 * Math.random(), (2.4 + 2 * Math.random()) * grow, -0.8, 0.9, ember);
      }
    }
    // Embers where a fire smoulders.
    this.fires.forEach((fire, i) => {
      const st = this.state[i];
      if (!st || !st.smoke_top_m || st.active) return;
      const g = v.groundAt(fire.x, fire.y);
      for (let q = chance(6); q > 0; q--) {
        ember.setHSL(0.03 + 0.05 * Math.random(), 1, 0.45 + 0.15 * Math.random());
        v.flames.emit(fire.x + (Math.random() - 0.5) * 1.6, g + 0.2, -fire.y + (Math.random() - 0.5) * 1.6,
          0, 0.4 + 0.6 * Math.random(), 0, 0.5 + 0.5 * Math.random(), 0.9, -0.4, 0.8, ember);
      }
    });
  }

  _smoke(dt) {
    // Each fire's plume rises to its simulated smoke top. When the plumes would need more puffs than the
    // pool holds, fewer and bigger puffs are emitted, so none is cut off before the top.
    const v = this.view, wind = v.wind, rise = this.smokeModel.rise_mps;
    const smokeCol = new THREE.Color();
    const plumes = [];
    this.fires.forEach((fire, i) => {
      const st = this.state[i];
      if (!st || !st.smoke_top_m) return;
      const g = v.groundAt(fire.x, fire.y);
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
      plumes.push({ st, sx, sy, sz, top, life, vy0, big, rate: 4 + 0.9 * big });
    });
    const demand = plumes.reduce((n, p) => n + p.rate * p.life, 0);
    const scale = Math.min(1, (0.85 * v.smoke.max) / Math.max(1, demand)), grow = 1 / Math.sqrt(scale);
    const chance = (rate) => { const e = rate * dt; return Math.floor(e) + (Math.random() < e - Math.floor(e) ? 1 : 0); };
    for (const { st, sx, sy, sz, top, life, vy0, big, rate } of plumes) {
      for (let q = chance(rate * scale); q > 0; q--) {
        const d = 0.08 * Math.random(), base = st.active ? 0.5 : 0.74;
        smokeCol.setRGB(base + d + 0.03, base + d, base + d - 0.03);
        v.smoke.emit(sx + (Math.random() - 0.5) * Math.min(12, 1 + big / 4), sz, -sy + (Math.random() - 0.5) * Math.min(12, 1 + big / 4),
          wind.x, vy0 * (0.85 + 0.3 * Math.random()), wind.z, life * (0.8 + 0.4 * Math.random()),
          (1.6 + big / 7) * grow, (0.3 + top / life / 4) * grow, st.active ? 0.55 : 0.42, smokeCol);
      }
    }
  }
}
