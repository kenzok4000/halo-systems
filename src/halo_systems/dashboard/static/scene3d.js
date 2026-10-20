// Halo Systems dashboard: the 3D view. Draws what the run sends: terrain and land cover (the wildfire
// valley), obstacles, path, drones (the scout's CAD model), hotspots, survey area, wind, camera
// coverage and found events; wildfire3d.js adds the valley's fires, smoke, forecasts and alerts.
// Drawn for looks only, never touching the simulation: the scenery beyond the run (distant hills and
// mountains, forests, and low shrubs under 1.5 m, also over the flight area where a run has no land
// cover), the tree crowns above the simulated trunks, and each found hotspot's glow, embers and smoke
// (the scorched patch marks every hotspot); the particles drift with the simulated wind.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";
import { mergeGeometries, canvasTexture, hash, fbm, ridge, smooth, conifer, coniferFar, shrub, stoneColors } from "/static/util3d.js";
import { mergeVertices } from "/static/vendor/BufferGeometryUtils.js";
import { WildfireLayer } from "/static/wildfire3d.js";
import { loadDroneModel, DRONE_MODEL_URL } from "/static/drone_model.js";
import { FilmLook } from "/static/postfx.js";

// Simulation frame is x east, y north, z up; three.js is y up, so (x, y, z) -> (x, z, -y).
const toV = (p) => new THREE.Vector3(p[0], p[2], -p[1]);
export const DRONE_SCALE = 4;   // drones are drawn up to this many times their real size so they show from 150 m away
// ...and at their real size close up: the drawn size grows from 1x within DRONE_TRUE_M of the camera to
// DRONE_SCALE times beyond DRONE_FULL_M (evenly on a log scale in between).
const DRONE_TRUE_M = 6, DRONE_FULL_M = 80;
const droneSize = (dist) => Math.pow(DRONE_SCALE, smooth(0, 1, Math.log(Math.max(1, dist) / DRONE_TRUE_M) / Math.log(DRONE_FULL_M / DRONE_TRUE_M))) / DRONE_SCALE;

// Status codes from sim/engine.py: 0 active, 1 failed (motor out, falling), 2 crashed, 3 retired, 4 down.
const STATUS_COLOR = { 0: new THREE.Color(0x3fe0c8), 1: new THREE.Color(0xff4d45), 2: new THREE.Color(0xff4d45),
  3: new THREE.Color(0xb5bcc4), 4: new THREE.Color(0xff4d45) };
const SKY_ZENITH = new THREE.Color(0x3f78bd), SKY_HORIZON = new THREE.Color(0xdfe3e2), HAZE = new THREE.Color(0xc9d2d8);
const SUN_DIR = new THREE.Vector3(-0.78, 0.5, 0.36).normalize();   // late-morning sun from the west-south-west

// ---------------------------------------------------------------- helpers
// Fine ground detail without a repeating texture: value noise of the world position, a few metres
// across, darkening and lightening the vertex colour; the finest octave fades out with distance.
// overlay ({ map, on } uniforms), on the run's terrain: what is painted on the ground (coverage, forecast,
// zones), blended over the finished ground colour as an unlit layer would be, in the same pass.
function addGroundDetail(material, overlay = null) {
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vGroundXZ;\nvarying vec2 vOverlayUv;")
      .replace("#include <project_vertex>", "#include <project_vertex>\n  vGroundXZ = (modelMatrix * vec4(transformed, 1.0)).xz;\n  vOverlayUv = uv;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>
varying vec2 vGroundXZ;
varying vec2 vOverlayUv;
uniform sampler2D uOverlay;
uniform float uOverlayOn;
float gHash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float gNoise(vec2 p) { vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
  return mix(mix(gHash(i), gHash(i + vec2(1.0, 0.0)), u.x), mix(gHash(i + vec2(0.0, 1.0)), gHash(i + vec2(1.0, 1.0)), u.x), u.y); }`)
      .replace("#include <color_fragment>", `#include <color_fragment>
  {
    vec2 gp = vGroundXZ;
    float fw = length(fwidth(gp));
    float fine = 1.0 - smoothstep(0.25, 0.9, fw);
    float n = gNoise(gp * 0.045) * 0.45 + gNoise(gp * 0.16 + 3.1) * 0.35 + mix(0.5, gNoise(gp * 0.7 + 7.7), fine) * 0.2;
    diffuseColor.rgb *= mix(0.8, 1.13, n);
    // Patches of greener, denser growth and of paler, drier grass, and tufts close up.
    float lush = smoothstep(0.5, 0.82, gNoise(gp * 0.021 + 13.0) * 0.7 + gNoise(gp * 0.09 + 5.0) * 0.3);
    diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * vec3(0.8, 0.97, 0.62), lush * 0.42);
    diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * vec3(1.09, 1.04, 0.9), smoothstep(0.62, 0.86, gNoise(gp * 0.035 + 21.0)) * 0.35);
    diffuseColor.rgb *= mix(1.0, 0.84, smoothstep(0.6, 0.8, gNoise(gp * 1.7 + 2.3)) * fine);
  }`)
      .replace("#include <fog_fragment>", `#include <fog_fragment>
  if (uOverlayOn > 0.5) {
    vec4 ov = texture2D(uOverlay, vOverlayUv);
    vec3 oc = ov.rgb;
    #if defined( TONE_MAPPING )
      oc = toneMapping(oc);
    #endif
    oc = linearToOutputTexel(vec4(oc, 1.0)).rgb;
    #ifdef USE_FOG
      oc = mix(oc, fogColor, fogFactor);
    #endif
    gl_FragColor.rgb = mix(gl_FragColor.rgb, oc, ov.a);
  }`);
    shader.uniforms.uOverlay = overlay ? overlay.map : { value: null };
    shader.uniforms.uOverlayOn = overlay ? overlay.on : { value: 0 };
  };
}

// The lowest value within r cells of each cell of an nx by ny grid (a square window, done row then column).
function minFilter(z, nx, ny, r) {
  const a = new Float32Array(nx * ny), b = new Float32Array(nx * ny);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      let m = Infinity;
      for (let k = Math.max(0, i - r); k <= Math.min(nx - 1, i + r); k++) m = Math.min(m, z[j * nx + k]);
      a[j * nx + i] = m;
    }
  }
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      let m = Infinity;
      for (let k = Math.max(0, j - r); k <= Math.min(ny - 1, j + r); k++) m = Math.min(m, a[k * nx + i]);
      b[j * nx + i] = m;
    }
  }
  return b;
}

// Many copies of one plant, split into square tiles so the renderer skips the tiles out of view (it culls
// whole meshes), each tile drawn in the detail its distance from the camera calls for. levels: [{geo, mat,
// maxD}] from nearest to furthest, a null geo drawing nothing out there; place(i, m, c) sets plant i's
// transform on the Object3D m (matrix updated) and its colour c. All levels of a tile share one set of
// instance transforms and colours, so the wildfire layer recolours a tree once (setColor, then commit).
class TiledPlants {
  constructor(parent, points, tile, levels, place, shadow = true) {   // shadow: true, "receive" or false
    const groups = new Map();
    points.forEach((p, i) => {
      const k = `${Math.floor(p[0] / tile)},${Math.floor(p[1] / tile)}`;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(i);
    });
    this.where = new Int32Array(points.length * 2);
    this.tiles = [];
    const m = new THREE.Object3D(), c = new THREE.Color();
    for (const idx of groups.values()) {
      const lead = new THREE.InstancedMesh(levels[0].geo, levels[0].mat, idx.length);
      const centre = new THREE.Vector3();
      idx.forEach((i, j) => {
        place(i, m, c);
        lead.setMatrixAt(j, m.matrix); lead.setColorAt(j, c);
        centre.add(m.position);
        this.where[i * 2] = this.tiles.length; this.where[i * 2 + 1] = j;
      });
      centre.multiplyScalar(1 / idx.length);
      lead.computeBoundingSphere();
      const meshes = levels.map((lv, k) => {
        if (!lv.geo) return null;
        const im = k === 0 ? lead : new THREE.InstancedMesh(lv.geo, lv.mat, idx.length);
        if (k) { im.instanceMatrix = lead.instanceMatrix; im.instanceColor = lead.instanceColor; im.boundingSphere = lead.boundingSphere; }
        im.castShadow = shadow === true; im.receiveShadow = shadow !== false;
        parent.add(im);
        return im;
      });
      this.tiles.push({ meshes, centre, radius: lead.boundingSphere.radius, dirty: false });
    }
    this.maxD = levels.map((lv) => lv.maxD);
  }
  // Show each tile at the detail for its distance from the camera.
  update(cam) {
    for (const t of this.tiles) {
      const d = Math.max(0, cam.distanceTo(t.centre) - 0.7 * t.radius);
      let lo = 0;
      t.meshes.forEach((im, k) => { if (im) im.visible = d >= lo && d < this.maxD[k]; lo = this.maxD[k]; });
    }
  }
  getColor(i, c) { this.tiles[this.where[i * 2]].meshes[0].getColorAt(this.where[i * 2 + 1], c); }
  setColor(i, c) { const t = this.tiles[this.where[i * 2]]; t.meshes[0].setColorAt(this.where[i * 2 + 1], c); t.dirty = true; }
  commit() { for (const t of this.tiles) if (t.dirty) { t.meshes[0].instanceColor.needsUpdate = true; t.dirty = false; } }
}

// Cutaway for tree crowns between the camera and the swarm: an instanced crown within `radius` of the line
// of sight to the swarm's centre (horizontally, the line carried on `ahead` metres past it to cover the drones
// beyond), where that line runs through the canopy, shrinks smoothly into its base, so drones under the
// canopy stay visible while the rest of the forest keeps its crowns; trunks, the collision obstacles, are
// always drawn. Shadows keep the full crowns. radius 0 turns it off (camera under the canopy).
function addCrownCutaway(material, uniforms) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uFocus = uniforms.focus;
    shader.uniforms.uFadeR = uniforms.radius;
    shader.uniforms.uCam = uniforms.cam;
    shader.uniforms.uAhead = uniforms.ahead;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform vec3 uFocus;\nuniform vec3 uCam;\nuniform float uFadeR;\nuniform float uAhead;")
      .replace("#include <begin_vertex>", `#include <begin_vertex>
#ifdef USE_INSTANCING
  if (uFadeR > 0.0) {
    vec3 cw = (modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz, top = cw + vec3(0.0, length(instanceMatrix[1].xyz), 0.0);
    vec2 ab = uFocus.xz - uCam.xz;
    float L = max(1.0, length(ab));
    float t = clamp(dot(cw.xz - uCam.xz, ab) / (L * L), 0.0, 1.0 + uAhead / L), t1 = min(t, 1.0);
    float d = length(cw.xz - (uCam.xz + ab * t)), ly = mix(uCam.y, uFocus.y, t1);
    float blocks = step(cw.y - 8.0, ly) * step(ly, top.y + 4.0);   // the line of sight is in the canopy here
    float r = uFadeR * mix(0.5, 1.0, t1);                           // narrower near the camera
    transformed *= max(0.001, mix(1.0, smoothstep(r * 0.6, r, d), blocks));
  }
#endif`);
  };
}

// Trees sway a little in the wind: the top of each instanced crown moves on a slow, uneven swing, out of
// step with its neighbours so gusts seem to cross the forest. Looks only (shadows keep the still crowns).
function addSway(material, time) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uSwayT = time;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform float uSwayT;")
      .replace("#include <begin_vertex>", `#include <begin_vertex>
#ifdef USE_INSTANCING
  {
    vec3 ip = vec3(instanceMatrix[3]);
    float h = max(0.0, transformed.y - 0.2), ph = uSwayT * 1.1 + ip.x * 0.043 + ip.z * 0.057;
    transformed.xz += vec2(sin(ph) + 0.4 * sin(ph * 2.3 + 1.7), 0.5 * cos(ph * 0.8)) * 0.035 * h * h;
  }
#endif`);
  };
}

// Wind streaks: faint lines drifting with the simulated wind round the swarm, seen only in strong wind (a gust
// front), so a gust shows in the picture as well as on the wind gauge. Looks only.
class WindStreaks {
  constructor(n = 1100) {
    this.n = n; this.p = null;
    const geo = new THREE.BufferGeometry();
    this.pos = new THREE.BufferAttribute(new Float32Array(n * 6), 3).setUsage(THREE.DynamicDrawUsage);
    this.alpha = new THREE.BufferAttribute(new Float32Array(n * 2), 1).setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute("position", this.pos); geo.setAttribute("aA", this.alpha);
    this.lines = new THREE.LineSegments(geo, new THREE.ShaderMaterial({
      vertexShader: "attribute float aA; varying float vA; void main() { vA = aA; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: "varying float vA; void main() { gl_FragColor = vec4(0.93, 0.96, 1.0, vA); }",
      transparent: true, depthWrite: false }));
    this.lines.frustumCulled = false; this.lines.renderOrder = 4; this.lines.visible = false;
  }
  update(dt, wind, focus) {
    const speed = Math.hypot(wind.x, wind.z), a = 0.62 * smooth(3.5, 9, speed);
    this.lines.visible = a > 0.01;
    if (!this.lines.visible || !dt) return;
    // Each streak drifts with the air over the ground (x, z in the world), and one that leaves the box around
    // the focus comes back in on its far side; heights are kept relative to the focus.
    const H = 90, LO = -14, HI = 26, n = this.n, p = this.p || (this.p = Float32Array.from({ length: n * 3 }, (_, k) =>
      (k % 3 === 1 ? LO + (HI - LO) * hash(k, 3) : (hash(k, 1) - 0.5) * 2 * H + (k % 3 ? focus.z : focus.x))));
    const wrap = (r) => ((((r + H) % (2 * H)) + 2 * H) % (2 * H)) - H;
    const tail = 3 + 0.8 * speed, ux = wind.x / speed, uz = wind.z / speed, P = this.pos.array, A = this.alpha.array;
    for (let i = 0; i < n; i++) {
      const v = 0.85 + 0.3 * hash(i, 7);
      const x = wrap(p[i * 3] + wind.x * dt * v - focus.x), z = wrap(p[i * 3 + 2] + wind.z * dt * v - focus.z);
      p[i * 3] = focus.x + x; p[i * 3 + 2] = focus.z + z;
      const y = p[i * 3 + 1], X = focus.x + x, Y = focus.y + y, Z = focus.z + z;
      const edge = (1 - smooth(0.7 * H, H, Math.max(Math.abs(x), Math.abs(z)))) * (0.4 + 0.6 * hash(i, 9));
      P.set([X, Y, Z, X - ux * tail, Y, Z - uz * tail], i * 6);
      A[i * 2] = a * edge; A[i * 2 + 1] = 0;
    }
    this.pos.needsUpdate = this.alpha.needsUpdate = true;
  }
}

// ---------------------------------------------------------------- particles (smoke, flames, embers)
class Particles {
  constructor(max, additive, texture) {
    this.max = max; this.n = 0;
    this.pos = new Float32Array(max * 3); this.vel = new Float32Array(max * 3);
    this.age = new Float32Array(max); this.life = new Float32Array(max);
    this.size0 = new Float32Array(max); this.grow = new Float32Array(max);
    this.alpha0 = new Float32Array(max); this.col = new Float32Array(max * 3);
    this.rot = new Float32Array(max); this.spin = new Float32Array(max);
    const geo = new THREE.BufferGeometry();
    const attr = (k) => new THREE.BufferAttribute(new Float32Array(max * k), k).setUsage(THREE.DynamicDrawUsage);
    this.aPos = attr(3); this.aSize = attr(1); this.aAlpha = attr(1); this.aColor = attr(3); this.aRot = attr(1);
    geo.setAttribute("position", this.aPos); geo.setAttribute("aSize", this.aSize);
    geo.setAttribute("aAlpha", this.aAlpha); geo.setAttribute("aColor", this.aColor); geo.setAttribute("aRot", this.aRot);
    geo.setDrawRange(0, 0);
    const mat = new THREE.ShaderMaterial({
      // uSun: the sun's direction across the screen (point-sprite coordinates, y down) and how far it is to one
      // side; smoke puffs are lit on that side and in shade on the other. Flames (additive) are not shaded.
      uniforms: { uTex: { value: texture }, uScale: { value: 600 }, fogColor: { value: HAZE }, fogDensity: { value: 0 },
        uSun: { value: new THREE.Vector3(0, 0, 0) } },
      vertexShader: `attribute float aSize; attribute float aAlpha; attribute vec3 aColor; attribute float aRot;
        varying float vAlpha; varying vec3 vColor; varying float vRot; varying float vDepth; uniform float uScale;
        void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_Position = projectionMatrix * mv;
          gl_PointSize = aSize * uScale / max(1.0, -mv.z); vAlpha = aAlpha; vColor = aColor; vRot = aRot; vDepth = -mv.z; }`,
      fragmentShader: `uniform sampler2D uTex; uniform vec3 fogColor; uniform float fogDensity; uniform vec3 uSun;
        varying float vAlpha; varying vec3 vColor; varying float vRot; varying float vDepth;
        void main() { vec2 c = gl_PointCoord - 0.5; float s = sin(vRot), k = cos(vRot);
          vec4 t = texture2D(uTex, vec2(k * c.x - s * c.y, s * c.x + k * c.y) + 0.5);
          float fog = 1.0 - exp(-fogDensity * fogDensity * vDepth * vDepth);
          float lit = 1.0 + uSun.z * (1.1 * dot(c, uSun.xy) - 0.08 * length(c) * 4.0);   // brighter towards the sun
          gl_FragColor = vec4(mix(pow(vColor, vec3(2.2)) * lit, fogColor, fog), t.a * vAlpha);   // vColor: display values
          #include <colorspace_fragment>
        }`,
      transparent: true, depthWrite: false,
      blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
    });
    this.points = new THREE.Points(geo, mat);
    this.points.frustumCulled = false;
  }
  emit(x, y, z, vx, vy, vz, life, size, grow, alpha, color) {
    let i = this.n;
    if (i >= this.max) i = Math.floor(Math.random() * this.max);   // full: replace a random slot
    else this.n++;
    this.pos[i * 3] = x; this.pos[i * 3 + 1] = y; this.pos[i * 3 + 2] = z;
    this.vel[i * 3] = vx; this.vel[i * 3 + 1] = vy; this.vel[i * 3 + 2] = vz;
    this.age[i] = 0; this.life[i] = life; this.size0[i] = size; this.grow[i] = grow; this.alpha0[i] = alpha;
    this.rot[i] = Math.random() * 6.283; this.spin[i] = (Math.random() - 0.5) * 0.5;
    this.col[i * 3] = color.r; this.col[i * 3 + 1] = color.g; this.col[i * 3 + 2] = color.b;
  }
  update(dt, wind) {
    let j = 0;
    for (let i = 0; i < this.n; i++) {
      this.age[i] += dt;
      if (this.age[i] >= this.life[i]) continue;
      if (j !== i) {   // compact the live particles to the front
        for (const a of [this.pos, this.vel, this.col]) { a[j * 3] = a[i * 3]; a[j * 3 + 1] = a[i * 3 + 1]; a[j * 3 + 2] = a[i * 3 + 2]; }
        this.age[j] = this.age[i]; this.life[j] = this.life[i]; this.size0[j] = this.size0[i];
        this.grow[j] = this.grow[i]; this.alpha0[j] = this.alpha0[i]; this.rot[j] = this.rot[i]; this.spin[j] = this.spin[i];
      }
      // Particles relax toward the wind; the rise slows as the smoke cools.
      const k = Math.min(1, dt * 0.6);
      this.vel[j * 3] += (wind.x - this.vel[j * 3]) * k;
      this.vel[j * 3 + 2] += (wind.z - this.vel[j * 3 + 2]) * k;
      this.vel[j * 3 + 1] *= 1 - Math.min(1, dt * 0.08);
      for (let c = 0; c < 3; c++) this.pos[j * 3 + c] += this.vel[j * 3 + c] * dt;
      this.rot[j] += this.spin[j] * dt;
      const f = this.age[j] / this.life[j];
      this.aPos.array.set([this.pos[j * 3], this.pos[j * 3 + 1], this.pos[j * 3 + 2]], j * 3);
      this.aSize.array[j] = Math.max(0.05, this.size0[j] + this.grow[j] * this.age[j]);
      this.aAlpha.array[j] = this.alpha0[j] * Math.min(1, f * 6) * (1 - f) * (1 - f);
      this.aColor.array.set([this.col[j * 3], this.col[j * 3 + 1], this.col[j * 3 + 2]], j * 3);
      this.aRot.array[j] = this.rot[j];
      j++;
    }
    this.n = j;
    this.points.geometry.setDrawRange(0, j);
    for (const a of [this.aPos, this.aSize, this.aAlpha, this.aColor, this.aRot]) a.needsUpdate = true;
  }
  clear() { this.n = 0; this.points.geometry.setDrawRange(0, 0); }
}

// ---------------------------------------------------------------- the view
export class SwarmView {
  constructor(canvas) {
    this.canvas = canvas;
    const r = this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    r.setPixelRatio(Math.min(2, window.devicePixelRatio));
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.0;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    const scene = this.scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(HAZE.clone(), 0.00062);
    scene.background = SKY_HORIZON.clone();
    this.camera = new THREE.PerspectiveCamera(46, 1, 0.5, 9000);
    this.camera.position.set(-60, 55, 70);
    // The film look (postfx.js): bloom, tone mapping, grade and vignette; ?fx=0 draws straight to the screen.
    this.fx = null;
    if (new URLSearchParams(location.search).get("fx") !== "0") {
      try { this.fx = new FilmLook(r, scene, this.camera); } catch (err) { console.warn("film look off", err); }
    }
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.mode = "follow";
    this.zoom = 1;            // camera distance in follow, orbit, chase and top views, as a multiple of the default
    this.userMoved = false;
    this.controls.addEventListener("start", () => { this.userMoved = true; });
    this.speed = 1;
    this.paused = false;
    this.clock = new THREE.Clock();
    this.vt = 0;              // view time, seconds: drives rotor spin, pulsing rings and the camera
    this.swayT = { value: 0 }; // the trees' sway clock (view time)
    this.film = false;        // film mode: the recorder calls renderFilm() once per video frame
    this.overlayDirty = false;
    this.overlayDrawnAt = -1;
    this.focus = new THREE.Vector3();
    this.fade = { focus: { value: new THREE.Vector3(0, -1e4, 0) }, radius: { value: 0 }, cam: { value: new THREE.Vector3() }, ahead: { value: 0 } };
    this._textures();
    this._sky();
    this._lights();
    this._environment();
    this._buildScenery(null);
    // The scout's CAD model; drones are drawn as simple shapes until it has loaded.
    this.model = null;
    this.modelState = "loading";
    loadDroneModel(DRONE_MODEL_URL, DRONE_SCALE).then((m) => {
      // The CAD materials are metallic, which looks black without an environment map: tone them down.
      for (const part of m.body) {
        if (part.material.metalness > 0.5) { part.material.metalness = 0.35; part.material.roughness = Math.max(0.45, part.material.roughness); }
      }
      this.model = m;
      this.modelState = "ready";
      if (this.hello) this._buildDrones(this.hello);
    }).catch((err) => { this.modelState = "failed"; console.warn("drone model not loaded; drawing simple drones", err); });
    this.smoke = new Particles(5000, false, this.puffTex);
    this.streaks = new WindStreaks();
    this.flames = new Particles(4000, true, this.flameTex);
    for (const p of [this.smoke, this.flames]) p.points.material.uniforms.fogDensity.value = scene.fog.density;
    this.smoke.points.renderOrder = 3; this.flames.points.renderOrder = 4;
    scene.add(this.smoke.points, this.flames.points, this.streaks.lines);
    this.world = new THREE.Group();
    scene.add(this.world);
    this.hello = null;
    this.wind = new THREE.Vector3();
    this.windEl = canvas.parentElement && canvas.parentElement.querySelector("#wind");
    if (this.windEl) { this.windArrowEl = this.windEl.querySelector("svg"); this.windTextEl = this.windEl.querySelector("span"); }
    window.addEventListener("resize", () => this.resize());
    this.resize();
    const tick = () => {
      if (!this.film) {
        const dt = Math.min(0.1, this.clock.getDelta());
        this._step(dt, this.paused ? 0 : dt * this.speed, null);
      }
      requestAnimationFrame(tick);
    };
    tick();
  }

  // -- sky, light, textures -----------------------------------------------------------------------
  _sky() {
    // A sky gradient with drifting fair-weather cumulus (value-noise clouds on a plane high above, lit from
    // the sun's side), and the sun's disc and glow.
    const geo = new THREE.SphereGeometry(7000, 32, 16);
    const mat = new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false, fog: false,
      uniforms: { uZenith: { value: SKY_ZENITH }, uHorizon: { value: SKY_HORIZON }, uHaze: { value: HAZE }, uSun: { value: SUN_DIR },
        uDrift: { value: new THREE.Vector2() } },
      vertexShader: "varying vec3 vDir; void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: `uniform vec3 uZenith; uniform vec3 uHorizon; uniform vec3 uHaze; uniform vec3 uSun; uniform vec2 uDrift; varying vec3 vDir;
        float h21(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float vn(vec2 p) { vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
          return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), u.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), u.x), u.y); }
        float fbm(vec2 p) { float a = 0.5, s = 0.0; for (int k = 0; k < 5; k++) { s += a * vn(p); p = p * 2.03 + vec2(17.1, 9.2); a *= 0.5; } return s; }
        void main() { vec3 d = normalize(vDir); float h = d.y;
          vec3 c = h > 0.0 ? mix(uHorizon, uZenith, pow(h, 0.5)) : uHaze;
          c = mix(c, uHaze, exp(-max(h, 0.0) * 18.0) * 0.8);
          float s = max(0.0, dot(d, uSun));
          if (h > 0.0) {
            vec2 p = d.xz / (h + 0.1) * 1.4 + uDrift;
            float n = fbm(p);
            float cover = smoothstep(0.5, 0.74, n) * smoothstep(0.02, 0.22, h);
            float lit = 0.55 + 0.45 * smoothstep(0.35, 0.9, fbm(p * 1.9 + 4.0 + uSun.xz)) + 0.4 * pow(s, 5.0);
            vec3 cloud = mix(vec3(0.5, 0.55, 0.63), vec3(1.0, 0.96, 0.9), clamp(lit, 0.0, 1.0)) * 1.05;
            c = mix(c, mix(cloud, uHaze, exp(-h * 9.0) * 0.6), cover * 0.9);
          }
          c += vec3(1.0, 0.92, 0.75) * (pow(s, 900.0) * 2.0 + pow(s, 16.0) * 0.18);
          gl_FragColor = vec4(c, 1.0);
          #include <colorspace_fragment>
        }`,
    });
    this.sky = new THREE.Mesh(geo, mat);
    this.sky.frustumCulled = false;
    this.sky.renderOrder = 1;   // after the ground and trees, so only the sky they leave open is shaded
    this.scene.add(this.sky);
  }

  // Image-based light for the shiny parts (the drones' CAD materials, masts, buildings): the sky above and
  // dry ground below, blurred, as a reflection and fill.
  _environment() {
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const env = new THREE.Scene();
    env.add(new THREE.Mesh(new THREE.SphereGeometry(10, 32, 16), new THREE.ShaderMaterial({
      side: THREE.BackSide,
      uniforms: { uZenith: { value: SKY_ZENITH }, uHorizon: { value: SKY_HORIZON }, uGround: { value: new THREE.Color(0x7a6a50) }, uSun: { value: SUN_DIR } },
      vertexShader: "varying vec3 vDir; void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: `uniform vec3 uZenith, uHorizon, uGround, uSun; varying vec3 vDir;
        void main() { vec3 d = normalize(vDir); vec3 c = d.y > 0.0 ? mix(uHorizon, uZenith, sqrt(d.y)) : mix(uHorizon, uGround, sqrt(-d.y * 4.0 > 1.0 ? 1.0 : -d.y * 4.0));
          c += vec3(1.0, 0.9, 0.75) * pow(max(0.0, dot(d, uSun)), 64.0) * 6.0;
          gl_FragColor = vec4(c, 1.0); }`,
    })));
    this.scene.environment = pmrem.fromScene(env, 0.03).texture;
    this.scene.environmentIntensity = 0.55;
    pmrem.dispose();
  }

  _lights() {
    this.scene.add(new THREE.HemisphereLight(0xbcd5f2, 0x665644, 0.85));   // cool skylight in the shade
    const sun = this.sun = new THREE.DirectionalLight(0xffdcb2, 3.7);       // warm late-morning sun
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const c = sun.shadow.camera;
    c.left = -140; c.right = 140; c.top = 140; c.bottom = -140; c.near = 10; c.far = 900;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.6;
    this.sunOffset = SUN_DIR.clone().multiplyScalar(380);
    this.scene.add(sun, sun.target);
  }

  _textures() {
    this.dotTex = canvasTexture(64, 64, (g, w) => {
      const gr = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
      gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.45, "rgba(255,255,255,.55)"); gr.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = gr; g.fillRect(0, 0, w, w);
    }, false);
    // A smoke puff: overlapping soft blobs, so a plume looks billowy rather than made of dots.
    this.puffTex = canvasTexture(128, 128, (g, w) => {
      for (let i = 0; i < 30; i++) {
        const a = hash(i, 21) * 6.283, rr = Math.sqrt(hash(i, 22)) * w * 0.2;
        const x = w / 2 + Math.cos(a) * rr, y = w / 2 + Math.sin(a) * rr, r = w * (0.12 + 0.16 * hash(i, 23));
        const gr = g.createRadialGradient(x, y, 0, x, y, r);
        gr.addColorStop(0, `rgba(255,255,255,${0.2 + 0.18 * hash(i, 24)})`); gr.addColorStop(1, "rgba(255,255,255,0)");
        g.fillStyle = gr; g.fillRect(0, 0, w, w);
      }
    }, false);
    this.flameTex = canvasTexture(64, 64, (g, w) => {
      const gr = g.createRadialGradient(w / 2, w * 0.58, 0, w / 2, w * 0.58, w * 0.46);
      gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.3, "rgba(255,255,255,.7)"); gr.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = gr; g.fillRect(0, 0, w, w);
    }, false);
    // Drone marker: a coloured dot with a dark rim, so the swarm reads from far away on bright ground.
    this.markerTex = canvasTexture(64, 64, (g, w) => {
      const c = w / 2;
      g.fillStyle = "rgba(12,22,28,.7)"; g.beginPath(); g.arc(c, c, w * 0.34, 0, Math.PI * 2); g.fill();
      const gr = g.createRadialGradient(c, c, 0, c, c, w * 0.27);
      gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.6, "rgba(235,235,235,1)"); gr.addColorStop(1, "rgba(200,200,200,1)");
      g.fillStyle = gr; g.beginPath(); g.arc(c, c, w * 0.26, 0, Math.PI * 2); g.fill();
    }, false);
    this.rotorTex = canvasTexture(128, 128, (g, w) => {
      const c = w / 2;
      const grad = g.createRadialGradient(c, c, w * 0.08, c, c, w / 2);
      grad.addColorStop(0, "rgba(30,34,40,.55)"); grad.addColorStop(1, "rgba(30,34,40,.08)");
      g.fillStyle = grad; g.beginPath(); g.arc(c, c, w / 2 - 1, 0, Math.PI * 2); g.fill();
      g.fillStyle = "rgba(20,22,26,.75)";
      for (const a of [0, Math.PI]) { g.save(); g.translate(c, c); g.rotate(a); g.beginPath(); g.ellipse(w * 0.24, 0, w * 0.24, w * 0.05, 0, 0, Math.PI * 2); g.fill(); g.restore(); }
    });
    // Burnt ground around a hotspot, with a ragged edge.
    this.scorchTex = canvasTexture(256, 256, (g, w) => {
      const c = w / 2;
      for (let i = 0; i < 60; i++) {
        const a = hash(i, 31) * 6.283, rr = hash(i, 32) * w * 0.22, r = w * (0.08 + 0.14 * hash(i, 33));
        const x = c + Math.cos(a) * rr, y = c + Math.sin(a) * rr;
        const gr = g.createRadialGradient(x, y, 0, x, y, r);
        gr.addColorStop(0, `rgba(${22 + 20 * hash(i, 34)},${16 + 12 * hash(i, 35)},12,.55)`); gr.addColorStop(1, "rgba(40,30,20,0)");
        g.fillStyle = gr; g.fillRect(0, 0, w, w);
      }
      for (let i = 0; i < 260; i++) {   // ash flecks
        const a = hash(i, 41) * 6.283, rr = Math.sqrt(hash(i, 42)) * w * 0.3;
        g.fillStyle = `rgba(${150 + 60 * hash(i, 43)},${145 + 50 * hash(i, 43)},${135 + 50 * hash(i, 43)},${0.25 + 0.3 * hash(i, 44)})`;
        g.fillRect(c + Math.cos(a) * rr, c + Math.sin(a) * rr, 2, 2);
      }
    });
  }

  // -- scenery ------------------------------------------------------------------------------------
  // The ground out to the horizon: flat where the run flies (or the run's own terrain), rolling hills
  // beyond, mountains on the skyline; forests on the hills and low shrubs near the flight area.
  // Rebuilt for each run, because it keeps clear of that run's path, obstacles and survey area.
  _buildScenery(hello) {
    if (this.scenery) {
      this.scene.remove(this.scenery);
      this.scenery.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
    }
    const group = this.scenery = new THREE.Group();
    this.scene.add(group);
    this.sceneryPlants = [];
    // The box the run uses: path, obstacles, survey area and hotspots.
    let box = [-150, -150, 150, 150];
    if (hello) {
      const xs = [], ys = [];
      for (const p of hello.path) { xs.push(p[0]); ys.push(p[1]); }
      for (const o of hello.world.obstacles) {
        if (o.kind === "box") { xs.push(o.lo[0], o.hi[0]); ys.push(o.lo[1], o.hi[1]); } else { xs.push(o.x); ys.push(o.y); }
      }
      if (hello.sensor && hello.sensor.area) { const a = hello.sensor.area; xs.push(a[0], a[2]); ys.push(a[1], a[3]); }
      for (const t of hello.targets || []) { xs.push(t.x); ys.push(t.y); }
      // Wide formations of many drones fly past the path's box; keep the scenery clear of them too.
      const m = Math.max(0, (hello.n * (hello.directive.spacing_m || 5)) / 2 - 40);
      box = [Math.min(...xs) - m, Math.min(...ys) - m, Math.max(...xs) + m, Math.max(...ys) + m];
    }
    const t = this.terrain;
    const tbox = t ? [t.origin[0], t.origin[1], t.origin[0] + (t.nx - 1) * t.cell_m, t.origin[1] + (t.ny - 1) * t.cell_m] : null;
    const outside = (b, x, y) => Math.hypot(Math.max(b[0] - x, 0, x - b[2]), Math.max(b[1] - y, 0, y - b[3]));
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    const R0 = Math.max(900, Math.hypot(box[2] - box[0], box[3] - box[1]) / 2 + 550);
    const rolling = (x, y) => {
      const d = outside(box, x, y), r = Math.hypot(x - cx, y - cy);
      const hills = smooth(90, 450, d) * 16 * fbm(x / 210 + 5, y / 210 + 2);
      const m = smooth(R0, R0 + 1400, r);
      const peaks = m > 0 ? m * (40 + 600 * (0.55 * ridge(x / 700, y / 700) + 0.3 * ridge(x / 280 + 7, y / 280 + 3)
        + 0.15 * ridge(x / 120 + 1, y / 120 + 9)) * (0.5 + 0.5 * m)) : 0;
      return hills + peaks;
    };
    const height = (x, y) => {
      if (!tbox) return rolling(x, y);
      const d = outside(tbox, x, y);
      if (d === 0) return this.groundAt(x, y);
      // Past the run's terrain its edge carries on, sinking into the hills and mountains beyond.
      return this.groundAt(x, y) * (1 - smooth(0, 900, d)) + rolling(x, y) * smooth(0, 320, d);
    };
    this.sceneryHeight = height;   // the ground's height everywhere (the camera keeps above it)
    // Under the run's terrain, which draws itself, the scenery's ground stays below the lowest terrain within
    // 30 m, so its coarser triangles never cut up through a gully; at the terrain's edge it meets the edge.
    let meshHeight = height;
    if (t) {
      const low = minFilter(t.z, t.nx, t.ny, 6);
      const lowAt = (x, y) => {
        const i = Math.max(0, Math.min(t.nx - 1.001, (x - t.origin[0]) / t.cell_m)), j = Math.max(0, Math.min(t.ny - 1.001, (y - t.origin[1]) / t.cell_m));
        const i0 = Math.floor(i), j0 = Math.floor(j), a = i - i0, b = j - j0, z = (ii, jj) => low[jj * t.nx + ii];
        return (z(i0, j0) * (1 - a) + z(i0 + 1, j0) * a) * (1 - b) + (z(i0, j0 + 1) * (1 - a) + z(i0 + 1, j0 + 1) * a) * b;
      };
      meshHeight = (x, y) => {
        if (outside(tbox, x, y) > 0) return height(x, y);
        const e = Math.min(x - tbox[0], tbox[2] - x, y - tbox[1], tbox[3] - y), w = smooth(0, 40, e);
        return (this.groundAt(x, y) - 3) * (1 - w) + (lowAt(x, y) - 3) * w;
      };
    }

    // Ground mesh, 8 km across.
    const SIZE = 8000, SEG = 320;
    const geo = new THREE.PlaneGeometry(SIZE, SIZE, SEG, SEG);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position, col = new Float32Array(pos.count * 3);
    const dry = new THREE.Color(0xcbb67e), straw = new THREE.Color(0xd8c793), scrub = new THREE.Color(0x8c8a55),
      green = new THREE.Color(0x66733f), earth = new THREE.Color(0xae8f63), forest = new THREE.Color(0x3f4d2c),
      rock = new THREE.Color(0x8c857c), pale = new THREE.Color(0xb4aea4), c = new THREE.Color();
    for (let k = 0; k < pos.count; k++) {
      const x = pos.getX(k) + cx, y = -pos.getZ(k) + cy;
      const h = meshHeight(x, y);
      pos.setXYZ(k, x, h, -y);
      const n1 = fbm(x / 160, y / 160), n2 = fbm(x / 55 + 40, y / 55 + 9), n3 = fbm(x / 300 + 11, y / 300 + 4);
      c.copy(dry).lerp(straw, smooth(0.35, 0.75, n2)).lerp(scrub, smooth(0.42, 0.78, n1) * 0.65).lerp(green, smooth(0.62, 0.88, n1) * 0.4)
        .lerp(earth, smooth(0.66, 0.9, n2) * 0.8);
      if (h > 20) {
        const slope = Math.hypot(height(x + 10, y) - height(x - 10, y), height(x, y + 10) - height(x, y - 10)) / 20;
        c.lerp(forest, smooth(0.5, 0.62, n3) * (1 - smooth(300, 460, h)) * 0.85)
          .lerp(rock, Math.max(smooth(0.55, 0.95, slope), smooth(380, 520, h)))
          .lerp(pale, smooth(560, 700, h) * 0.8);
      }
      col.set([c.r, c.g, c.b], k * 3);
    }
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.computeVertexNormals();
    const mat = new THREE.MeshLambertMaterial({ vertexColors: true });
    addGroundDetail(mat);
    const ground = new THREE.Mesh(geo, mat);
    ground.receiveShadow = true;
    ground.renderOrder = 0.5;   // after the run's terrain, which hides most of it near the flight
    group.add(ground);

    // Clear of the run: its box plus a margin, its hotspots, its buildings' block.
    const boxes = hello ? hello.world.obstacles.filter((o) => o.kind === "box") : [];
    const block = boxes.length ? [Math.min(...boxes.map((o) => o.lo[0])) - 6, Math.min(...boxes.map((o) => o.lo[1])) - 6,
      Math.max(...boxes.map((o) => o.hi[0])) + 6, Math.max(...boxes.map((o) => o.hi[1])) + 6] : null;
    const targets = hello ? hello.targets || [] : [];

    // Forests on the hills: instanced conifers, in patches, below the tree line.
    const hill = [];
    const STEP = 15;
    for (let x = cx - 2600; x < cx + 2600; x += STEP) {
      for (let y = cy - 2600; y < cy + 2600; y += STEP) {
        const jx = x + (hash(x, y) - 0.5) * STEP, jy = y + (hash(y, x) - 0.5) * STEP;
        const d = outside(box, jx, jy);
        if (d < 70 || (tbox && outside(tbox, jx, jy) === 0)) continue;
        const r = Math.hypot(jx - cx, jy - cy);
        const mask = fbm(jx / 300 + 11, jy / 300 + 4) + 0.08 * smooth(R0 - 200, R0 + 500, r) - 0.06 * (1 - smooth(70, 260, d));
        if (mask < 0.56 || hash(jx * 0.7, jy * 1.3) > 0.8) continue;
        const h = height(jx, jy);
        if (h > 400 + 60 * hash(jx, 2)) continue;
        hill.push([jx, jy, h]);
      }
    }
    // The detailed conifer near the camera, fewer branches further out, two plain cones a few pixels tall.
    const leaf = new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: true });
    addSway(leaf, this.swayT);
    this.sceneryPlants.push(new TiledPlants(group, hill, 260, [
      { geo: conifer(), mat: leaf, maxD: 260 }, { geo: conifer(3, 7), mat: leaf, maxD: 650 }, { geo: coniferFar(), mat: leaf, maxD: Infinity }],
    (i, o, col) => {
      const [x, y, h] = hill[i], s = 7 + 9 * hash(x, y), w = s * (0.32 + 0.1 * hash(y, x));
      o.position.set(x, h - 0.5, -y); o.rotation.set(0, hash(x, 7) * 6.28, 0); o.scale.set(w, s * 1.15, w); o.updateMatrix();
      col.setHSL(0.22 + 0.07 * hash(x, 3), 0.3 + 0.18 * hash(y, 5), 0.17 + 0.09 * hash(x, y));
    }));

    // Low shrubs and grass tussocks over and around the flight area (under 1.5 m: never in a drone's way).
    const shrubs = [];
    const pad = 320, S = Math.max(4.5, Math.sqrt(((box[2] - box[0] + 2 * pad) * (box[3] - box[1] + 2 * pad)) / 90000));   // at most ~90k candidates
    for (let x = box[0] - pad; x < box[2] + pad; x += S) {
      for (let y = box[1] - pad; y < box[3] + pad; y += S) {
        const jx = x + (hash(x, y + 1) - 0.5) * S, jy = y + (hash(y + 1, x) - 0.5) * S;
        if (block && jx > block[0] && jx < block[2] && jy > block[1] && jy < block[3]) continue;
        if (this.landcover && outside(tbox, jx, jy) === 0) continue;   // the run's own land cover draws its scrub
        if (targets.some((tg) => Math.hypot(jx - tg.x, jy - tg.y) < Math.max(tg.radius_m, 1.5) * 2.4 + 2)) continue;
        const mask = fbm(jx / 38 + 3, jy / 38 + 8);
        if (mask < 0.5 || hash(jx * 1.7, jy * 0.3) > 0.55 + mask * 0.4) continue;
        shrubs.push([jx, jy]);
      }
    }
    if (shrubs.length) {
      // Shrubs a few pixels across are not drawn.
      const mat = new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: true });
      this.sceneryPlants.push(new TiledPlants(group, shrubs, 120, [{ geo: shrub(), mat, maxD: 160 }, { geo: shrub(1), mat, maxD: 420 }],
        (i, o, col) => {
          const [x, y] = shrubs[i];
          const s = 0.35 + 0.6 * hash(x, y) ** 2, h = this.terrain && outside(tbox, x, y) === 0 ? this.groundAt(x, y) : height(x, y);
          o.position.set(x, h + s * 0.3, -y); o.rotation.set(0, hash(y, 2) * 6.28, 0); o.scale.set(s, s * 0.8, s); o.updateMatrix();
          col.setHSL(0.17 + 0.09 * hash(x * 0.3, y * 0.7), 0.3 + 0.2 * hash(y, x), 0.13 + 0.08 * hash(x + 2, y));
        }));
    }

    // Paved ground under a block of buildings.
    if (block) {
      const pave = new THREE.Mesh(new THREE.PlaneGeometry(block[2] - block[0], block[3] - block[1]),
        new THREE.MeshLambertMaterial({ color: 0xaeb0b0, polygonOffset: true, polygonOffsetFactor: -1 }));
      pave.rotation.x = -Math.PI / 2; pave.position.set((block[0] + block[2]) / 2, 0.02, -(block[1] + block[3]) / 2);
      pave.receiveShadow = true;
      group.add(pave);
    }
  }

  // -- terrain ------------------------------------------------------------------------------------
  groundAt(x, y) {
    const t = this.terrain;
    if (!t) return 0;
    const fx = (x - t.origin[0]) / t.cell_m, fy = (y - t.origin[1]) / t.cell_m;
    const i = Math.max(0, Math.min(t.nx - 1.001, fx)), j = Math.max(0, Math.min(t.ny - 1.001, fy));
    const i0 = Math.floor(i), j0 = Math.floor(j), a = i - i0, b = j - j0;
    const z = (ii, jj) => t.z[jj * t.nx + ii];
    return (z(i0, j0) * (1 - a) + z(i0 + 1, j0) * a) * (1 - b) + (z(i0, j0 + 1) * (1 - a) + z(i0 + 1, j0 + 1) * a) * b;
  }

  _buildTerrain(t, lc) {
    // The run's height grid, coloured by its land cover where it sends one (else by height and slope).
    const w = (t.nx - 1) * t.cell_m, h = (t.ny - 1) * t.cell_m;
    const geo = new THREE.PlaneGeometry(w, h, t.nx - 1, t.ny - 1);
    geo.rotateX(-Math.PI / 2);   // plane now in x/z, with z = -y
    const pos = geo.attributes.position, col = new Float32Array(pos.count * 3);
    let zmin = Infinity, zmax = -Infinity;
    for (const v of t.z) { zmin = Math.min(zmin, v); zmax = Math.max(zmax, v); }
    const dry = new THREE.Color(0xcbb67e), scrub = new THREE.Color(0x8c8a55), pine = new THREE.Color(0x4b5a33),
      rock = new THREE.Color(0x8c857c), c = new THREE.Color(), d = new THREE.Color();
    // Land-cover classes by name (rock, grass, scrub, forest, bare): dry-season colours.
    const COVER = { rock: 0x8f8579, grass: 0xbdb07c, scrub: 0x8f8c56, forest: 0x515c38, bare: 0xb5a488 };
    const coverCol = lc ? lc.classes.map((name) => new THREE.Color(COVER[name] ?? 0xb8a57c)) : null;
    for (let k = 0; k < pos.count; k++) {
      const x = pos.getX(k) + t.origin[0] + w / 2, y = -pos.getZ(k) + t.origin[1] + h / 2;
      const z = this.groundAt(x, y);
      pos.setXYZ(k, x, z, -y);
      const slope = Math.hypot(this.groundAt(x + 2, y) - this.groundAt(x - 2, y), this.groundAt(x, y + 2) - this.groundAt(x, y - 2)) / 4;
      if (coverCol) {
        // Average the classes around the node so the edges between them are soft.
        const ix = Math.round((x - lc.x0) / lc.cell_m), iy = Math.round((y - lc.y0) / lc.cell_m);
        c.setRGB(0, 0, 0);
        let n = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const jx = ix + dx, jy = iy + dy;
          if (jx < 0 || jy < 0 || jx >= lc.nx || jy >= lc.ny) continue;
          d.copy(coverCol[lc.grid[jy * lc.nx + jx]]); c.r += d.r; c.g += d.g; c.b += d.b; n++;
        }
        c.multiplyScalar(1 / Math.max(1, n));
        c.lerp(dry, 0.25 * smooth(0.45, 0.8, fbm(x / 90, y / 90))).lerp(rock, 0.5 * smooth(0.6, 1.1, slope));
      } else {
        const hf = (z - zmin) / Math.max(1, zmax - zmin);
        c.copy(dry).lerp(scrub, smooth(0.4, 0.75, fbm(x / 120, y / 120)))
          .lerp(pine, Math.min(0.75, Math.max(0, hf - 0.25) * 1.4)).lerp(rock, Math.min(1, smooth(0.5, 0.9, slope) + Math.max(0, hf - 0.82) * 2.5));
      }
      col.set([c.r, c.g, c.b], k * 3);
    }
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.computeVertexNormals();
    const mat = new THREE.MeshLambertMaterial({ vertexColors: true });
    this.groundOverlay = { map: { value: null }, on: { value: 0 } };   // set by _buildOverlay
    addGroundDetail(mat, this.groundOverlay);
    const mesh = new THREE.Mesh(geo, mat);
    mesh.receiveShadow = true;
    mesh.castShadow = true;
    return mesh;
  }

  _buildLandcover(lc) {
    // The run's trees and scrub: a conifer on every forest cell, as tall as the run's canopy height there
    // (drones fly above it), and a bush on about half the scrub cells.
    const forest = lc.classes.indexOf("forest"), scrubK = lc.classes.indexOf("scrub");
    const trees = [], bushes = [];
    for (let iy = 0; iy < lc.ny; iy++) {
      for (let ix = 0; ix < lc.nx; ix++) {
        const k = iy * lc.nx + ix, h = lc.canopy_m ? lc.canopy_m[k] : 0;
        const x = lc.x0 + ix * lc.cell_m + (hash(ix, iy) - 0.5) * lc.cell_m * 0.8;
        const y = lc.y0 + iy * lc.cell_m + (hash(iy, ix) - 0.5) * lc.cell_m * 0.8;
        if (lc.grid[k] === forest && h >= 3) trees.push([x, y, h]);
        else if (lc.grid[k] === scrubK && h > 0 && hash(ix * 0.37, iy * 1.3) < 0.5) bushes.push([x, y, h]);
      }
    }
    if (trees.length) {
      const leaf = new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: true });
      addSway(leaf, this.swayT);
      const plants = new TiledPlants(this.world, trees, 70, [
        { geo: conifer(), mat: leaf, maxD: 240 }, { geo: conifer(3, 7), mat: leaf, maxD: 560 }, { geo: coniferFar(), mat: leaf, maxD: Infinity }],
      (i, o, col) => {
        const [x, y, h] = trees[i], s = h * (0.92 + 0.16 * hash(x, y)), wd = s * (0.26 + 0.08 * hash(y, x));
        o.position.set(x, this.groundAt(x, y) - 0.3, -y); o.rotation.set(0, hash(x, 7) * 6.28, 0); o.scale.set(wd, s, wd); o.updateMatrix();
        col.setHSL(0.22 + 0.07 * hash(x, 3), 0.28 + 0.18 * hash(y, 5), 0.16 + 0.08 * hash(x, y));
      });
      this.worldPlants.push(plants);
      this.forestTrees = { plants, xy: trees };   // the wildfire layer chars the trees the fire reaches
    }
    if (bushes.length) {
      // Soft-shaded lumps, so it reads as a bush rather than a rock; plainer further out, and not drawn a few pixels across.
      const mat = new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: true });
      this.worldPlants.push(new TiledPlants(this.world, bushes, 70, [
        { geo: shrub(), mat, maxD: 200 }, { geo: shrub(1), mat, maxD: 430 }, { geo: null, maxD: Infinity }],
      (i, o, col) => {
        const [x, y, h] = bushes[i], s = Math.max(0.5, h * 0.55) * (0.7 + 0.6 * hash(x, y));
        o.position.set(x, this.groundAt(x, y) + s * 0.35, -y); o.rotation.set(0, hash(y, 2) * 6.28, 0); o.scale.set(s, s * 0.75, s); o.updateMatrix();
        col.setHSL(0.19 + 0.07 * hash(x * 0.3, y * 0.7), 0.3 + 0.15 * hash(y, x), 0.15 + 0.06 * hash(x + 2, y));
      }, "receive"));
    }
  }

  // -- a new run ----------------------------------------------------------------------------------
  build(hello) {
    this.hello = hello;
    this.scene.remove(this.world);
    const shared = new Set([this.dotTex, this.puffTex, this.flameTex, this.markerTex, this.rotorTex, this.scorchTex]);
    this.droneGroup = null;
    this.world.traverse((o) => {
      if (o.userData.shared) return;   // the CAD model's geometries and materials are reused by every run
      o.geometry?.dispose();
      for (const mat of [].concat(o.material || [])) {
        for (const tex of [mat.map, mat.uniforms?.uTex?.value]) if (tex && !shared.has(tex)) tex.dispose();
        mat.dispose();
      }
    });
    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.smoke.clear(); this.flames.clear();
    this.footprints = null; this.ride = null; this.forestTrees = null; this.crownBase = null;
    this.worldPlants = [];
    // Terrain (heights[iy * nx + ix] on a grid from (x0, y0)) and land cover, when the run has them.
    const wt = hello.world.terrain;
    this.terrain = wt ? { origin: [wt.x0, wt.y0], cell_m: wt.cell_m, nx: wt.nx, ny: wt.ny, z: wt.heights } : null;
    this.landcover = hello.world.landcover || null;
    this._buildScenery(hello);
    if (this.terrain) this.world.add(this._buildTerrain(this.terrain, this.landcover));
    if (this.landcover) this._buildLandcover(this.landcover);
    this._buildObstacles(hello);
    this.wildfire = hello.wildfire ? new WildfireLayer(this, hello) : null;
    this._buildTargets(this.wildfire ? {} : hello);   // the valley's fires are drawn by the wildfire layer
    this._buildOverlay(hello);
    this._buildPath(hello);
    this._buildDrones(hello);
    this.prev = null; this.cur = null; this.lastFrame = null; this.vel = null;
    this.swarmCentre = null; this.extent = null; this.lastFilmT = null;
    this.userMoved = false;
    const p0 = hello.path[0];
    const g0 = this.groundAt(p0[0], p0[1]);
    this.focus.set(p0[0], g0 + hello.directive.altitude_m, -p0[1]);
    this.camera.position.set(p0[0] - 70, g0 + hello.directive.altitude_m + 40, -p0[1] + 80);
    this.controls.target.copy(this.focus);
    this.setMode(this.mode);
  }

  _buildObstacles(hello) {
    if (this.terrain) return this._buildTerrainObstacles(hello);
    const obs = hello.world.obstacles;
    const trees = obs.filter((o) => o.kind === "cylinder" && (!o.type || o.type === "tree"));
    const rocks = obs.filter((o) => o.kind === "cylinder" && o.type && o.type !== "tree");
    const boxes = obs.filter((o) => o.kind === "box");
    const alt = hello.directive.altitude_m;
    this.canyon = boxes.some((o) => o.hi[2] > alt + 2);
    if (trees.length) {
      // Trunks are the collision cylinders, drawn at their true size. Crowns start above the flight level
      // (at least 4 m above the planned altitude), so a drone is never drawn inside foliage it does not hit.
      const bark = new THREE.MeshStandardMaterial({ color: 0x5a4636, roughness: 0.95 });
      const trunkGeo = new THREE.CylinderGeometry(1, 1.15, 1, 7, 1); trunkGeo.translate(0, 0.5, 0);
      const trunks = new THREE.InstancedMesh(trunkGeo, bark, trees.length);
      const crownGeo = conifer();
      const leaf = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.9, vertexColors: true });
      addCrownCutaway(leaf, this.fade);
      const crowns = new THREE.InstancedMesh(crownGeo, leaf, trees.length);
      const m = new THREE.Object3D(), c = new THREE.Color();
      let k = 0;
      trees.forEach((o, i) => {
        const g = this.groundAt(o.x, o.y);
        m.position.set(o.x, g, -o.y); m.rotation.set(0, hash(o.x, o.y) * 6.28, 0);
        m.scale.set(o.radius, o.height, o.radius); m.updateMatrix(); trunks.setMatrixAt(i, m.matrix);
        let base = Math.max(o.height * 0.42, alt + 4);
        this.crownBase = Math.min(this.crownBase ?? Infinity, g + base);
        // A tree too short for a crown above the flight level gets a slim one, close round its trunk, rather
        // than standing as a bare pole.
        const slim = base >= o.height - 3;
        if (slim) base = o.height * 0.42;
        if (base < o.height - 3) {
          const cr = slim ? o.radius + 0.9 : Math.max(1.8, o.radius * 5.5) * (0.85 + 0.3 * hash(o.y, o.x));
          m.position.set(o.x, g + base, -o.y); m.scale.set(cr, o.height - base + 1.2, cr); m.updateMatrix();
          crowns.setMatrixAt(k, m.matrix);
          c.setHSL(0.24 + 0.06 * hash(o.x, 3), 0.32 + 0.16 * hash(o.y, 5), 0.17 + 0.08 * hash(o.x, o.y));
          crowns.setColorAt(k, c);
          k++;
        }
      });
      crowns.count = k;
      for (const im of [trunks, crowns]) { im.castShadow = true; im.receiveShadow = true; this.world.add(im); }
    }
    if (rocks.length) {
      // Boulders: a lumpy ball, smooth-shaded and mottled, a little different for each rock.
      const stone = new THREE.MeshStandardMaterial({ color: 0x9a9286, roughness: 0.95, vertexColors: true });
      for (const [i, o] of rocks.entries()) {
        const geo = mergeVertices(new THREE.IcosahedronGeometry(1, 2).deleteAttribute("normal").deleteAttribute("uv"));
        const p = geo.attributes.position;
        for (let k = 0; k < p.count; k++) {
          const x = p.getX(k), y = p.getY(k), z = p.getZ(k), f = 0.82 + 0.3 * fbm(x * 1.3 + i * 7.1, z * 1.3 + y * 0.9 + i);
          p.setXYZ(k, x * f, Math.max(-0.6, y * f), z * f);
        }
        geo.computeVertexNormals();
        const mesh = new THREE.Mesh(stoneColors(geo, i), stone);
        mesh.position.set(o.x, this.groundAt(o.x, o.y) + o.height / 2, -o.y);
        mesh.scale.set(o.radius, o.height / 2, o.radius);
        mesh.castShadow = mesh.receiveShadow = true;
        this.world.add(mesh);
      }
    }
    if (boxes.length) {
      // One tile is one 4 m bay of one 3.5 m floor: a window with a sky reflection, and a floor slab.
      const windows = canvasTexture(128, 112, (g, w, h) => {
        g.fillStyle = "#d3d0c8"; g.fillRect(0, 0, w, h);
        g.fillStyle = "#b9b5ac"; g.fillRect(0, h - 10, w, 10);
        const gr = g.createLinearGradient(0, 22, 0, 82);
        gr.addColorStop(0, "#7d93a8"); gr.addColorStop(1, "#3e4c5a");
        g.fillStyle = "#8e8a82"; g.fillRect(22, 20, 84, 66);
        g.fillStyle = gr; g.fillRect(25, 23, 78, 60);
        g.fillStyle = "rgba(255,255,255,.18)"; g.beginPath(); g.moveTo(25, 23); g.lineTo(60, 23); g.lineTo(25, 70); g.fill();
        g.fillStyle = "#8e8a82"; g.fillRect(62, 23, 4, 60);
      });
      windows.wrapS = windows.wrapT = THREE.RepeatWrapping;
      for (const [i, o] of boxes.entries()) {
        const [lx, ly, lz] = o.lo, [hx, hy, hz] = o.hi;
        const sx = hx - lx, sy = hy - ly, sz = hz - lz;
        const tex = windows.clone(); tex.needsUpdate = true; tex.repeat.set(Math.max(1, sx / 4), Math.max(1, sz / 3.5));
        const tint = new THREE.Color().setHSL(0.08 + 0.05 * hash(i, 1), 0.08 + 0.1 * hash(i, 2), 0.78 + 0.12 * hash(i, 3));
        const mat = new THREE.MeshStandardMaterial({ map: tex, color: tint, roughness: 0.75, metalness: 0.05 });
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(sx, sz, sy), mat);
        mesh.position.set((lx + hx) / 2, (lz + hz) / 2, -(ly + hy) / 2);
        mesh.castShadow = mesh.receiveShadow = true;
        this.world.add(mesh);
        const roof = new THREE.Mesh(new THREE.BoxGeometry(sx, 0.6, sy), new THREE.MeshStandardMaterial({ color: 0x6d737a, roughness: 0.9 }));
        roof.position.set(mesh.position.x, hz + 0.3, mesh.position.z);
        this.world.add(roof);
      }
    }
  }

  _buildTerrainObstacles(hello) {
    // On terrain a cylinder's height is the absolute height of its top. The wide one is a radio mast,
    // the thin ones dead snags standing above the crowns; boxes are rock shelves (drawn with the fires).
    this.canyon = false;
    const steel = new THREE.MeshStandardMaterial({ color: 0xc9ccd0, roughness: 0.5, metalness: 0.6 });
    const red = new THREE.MeshStandardMaterial({ color: 0xc8382c, roughness: 0.6 });
    const wood = new THREE.MeshStandardMaterial({ color: 0x7a6d62, roughness: 1, flatShading: true });
    for (const o of hello.world.obstacles) {
      if (o.kind !== "cylinder") continue;
      const g = this.groundAt(o.x, o.y), hgt = Math.max(1, o.height - g);
      const grp = new THREE.Group();
      grp.position.set(o.x, g, -o.y);
      if (o.radius >= 1) {
        // A lattice mast: a slim four-sided tower in red and white bands, a beacon and two dishes.
        const bands = 8;
        for (let b = 0; b < bands; b++) {
          const r0 = o.radius * (1 - 0.6 * b / bands), r1 = o.radius * (1 - 0.6 * (b + 1) / bands);
          const seg = new THREE.Mesh(new THREE.CylinderGeometry(r1, r0, hgt / bands, 4, 1), b % 2 ? steel : red);
          seg.position.y = (b + 0.5) * hgt / bands; seg.rotation.y = Math.PI / 4; grp.add(seg);
        }
        const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.5, 10, 8), new THREE.MeshBasicMaterial({ color: 0xff3020 }));
        lamp.position.y = hgt + 0.4; grp.add(lamp);
        for (const [y, a] of [[0.72, 0.4], [0.58, 2.6]]) {
          const dish = new THREE.Mesh(new THREE.SphereGeometry(1.1, 14, 6, 0, Math.PI * 2, 0, 0.9), steel);
          dish.position.set(Math.cos(a) * o.radius * 0.6, hgt * y, Math.sin(a) * o.radius * 0.6);
          dish.rotation.set(Math.PI / 2, 0, -a); grp.add(dish);
        }
      } else {
        // A dead snag: a bare, tapering trunk with a few broken limbs.
        const trunk = new THREE.Mesh(new THREE.CylinderGeometry(o.radius * 0.25, o.radius, hgt, 7, 4), wood);
        trunk.position.y = hgt / 2; grp.add(trunk);
        for (let b = 0; b < 4; b++) {
          const len = 1.5 + 2.5 * hash(o.x + b, o.y), limb = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.16, len, 5), wood);
          const a = hash(o.y, b) * 6.28, y = hgt * (0.45 + 0.13 * b);
          limb.position.set(Math.cos(a) * len * 0.4, y, Math.sin(a) * len * 0.4);
          limb.rotation.set(Math.sin(a) * 1.0, 0, -Math.cos(a) * 1.0); grp.add(limb);
        }
      }
      grp.traverse((m) => { if (m.isMesh) m.castShadow = m.receiveShadow = true; });
      this.world.add(grp);
    }
  }

  _buildTargets(hello) {
    // Hotspots: a scorched patch; once found, a glow, embers and smoke, a ring, a beacon and a label.
    this.targets = [];
    for (const [i, t] of (hello.targets || []).entries()) {
      const g = this.groundAt(t.x, t.y);
      const r = Math.max(t.radius_m, 1.5);
      const scorch = new THREE.Mesh(new THREE.CircleGeometry(r * 2.3, 32),
        new THREE.MeshLambertMaterial({ map: this.scorchTex, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 }));
      scorch.rotation.x = -Math.PI / 2; scorch.rotation.z = hash(t.x, t.y) * 6.28; scorch.position.set(t.x, g + 0.05, -t.y);
      scorch.receiveShadow = true;
      const glow = new THREE.Mesh(new THREE.CircleGeometry(r * 0.8, 24),
        new THREE.MeshBasicMaterial({ map: this.dotTex, color: 0xff4a12, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false }));
      glow.rotation.x = -Math.PI / 2; glow.position.set(t.x, g + 0.08, -t.y);
      const ring = new THREE.Mesh(new THREE.RingGeometry(r * 2.6, r * 2.6 + 0.6, 64),
        new THREE.MeshBasicMaterial({ color: 0xffb02e, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
      ring.rotation.x = -Math.PI / 2; ring.position.set(t.x, g + 0.12, -t.y);
      const beacon = new THREE.Mesh(new THREE.CylinderGeometry(0.25, 0.25, 50, 8, 1, true),
        new THREE.MeshBasicMaterial({ color: 0xffa040, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
      beacon.position.set(t.x, g + 25, -t.y);
      const label = this._label(`Hotspot ${i + 1}`);
      label.position.set(t.x, g + 7, -t.y); label.visible = false;
      this.world.add(scorch, glow, ring, beacon, label);
      this.targets.push({ t, g, r, glow, ring, beacon, label, found: false });
    }
  }

  _label(text, dot = "#ff8a3d") {
    // A label that stays the same size on screen however far away it is.
    const cv = document.createElement("canvas");
    cv.width = 640; cv.height = 112;
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, sizeAttenuation: false }));
    sprite.scale.set(0.2, 0.035, 1);
    sprite.center.set(0.5, 0);
    sprite.renderOrder = 10;
    sprite.userData.setText = (s) => {
      sprite.userData.text = s;
      const g = cv.getContext("2d");
      g.clearRect(0, 0, cv.width, cv.height);
      g.font = "600 40px Inter, system-ui, sans-serif";
      const w = Math.min(cv.width - 8, g.measureText(s).width + 56);
      const x0 = (cv.width - w) / 2;
      g.fillStyle = "rgba(16,20,24,.82)";
      g.beginPath(); g.roundRect(x0, 8, w, 72, 16); g.fill();
      g.beginPath(); g.moveTo(cv.width / 2 - 12, 80); g.lineTo(cv.width / 2 + 12, 80); g.lineTo(cv.width / 2, 100); g.fill();
      g.fillStyle = dot; g.beginPath(); g.arc(x0 + 30, 44, 8, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#fff4e6"; g.textAlign = "left"; g.textBaseline = "middle";
      g.fillText(s, x0 + 48, 46);
      tex.needsUpdate = true;
    };
    sprite.userData.setText(text);
    return sprite;
  }

  _buildOverlay(hello) {
    // One draped texture for what is painted on the ground: the area the cameras have covered and
    // the survey block's outline.
    this.overlay = null;
    let box = null;
    if (hello.sensor && hello.sensor.area) box = hello.sensor.area.slice();
    if (this.terrain) {
      const t = this.terrain;
      box = [t.origin[0], t.origin[1], t.origin[0] + (t.nx - 1) * t.cell_m, t.origin[1] + (t.ny - 1) * t.cell_m];
    }
    if (!box) return;
    const pad = this.terrain ? 0 : 20;
    const [x0, y0, x1, y1] = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad];
    const w = x1 - x0, h = y1 - y0, px = Math.min(4, 2048 / Math.max(w, h));   // pixels per metre
    const cv = document.createElement("canvas");
    cv.width = Math.ceil(w * px); cv.height = Math.ceil(h * px);
    const cover = document.createElement("canvas"), wash = document.createElement("canvas");
    cover.width = wash.width = cv.width; cover.height = wash.height = cv.height;
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    if (this.terrain) {
      // On terrain the ground's own shader lays the overlay over it (its UVs span the same box).
      this.groundOverlay.map.value = tex; this.groundOverlay.on.value = 1;
    } else {
      const geo = new THREE.PlaneGeometry(w, h, 1, 1);
      geo.rotateX(-Math.PI / 2);
      const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false,
        polygonOffset: true, polygonOffsetFactor: -1 }));
      mesh.position.set(x0 + w / 2, 0.25, -(y0 + h / 2));
      mesh.renderOrder = 1;
      this.world.add(mesh);
    }
    this.overlay = { cv, cover, wash, coverDirty: null, tex, x0, y0, w, h, px, area: hello.sensor && hello.sensor.area };
    this._drawOverlay();
  }

  _toPx(x, y) { const o = this.overlay; return [(x - o.x0) * o.px, (o.y0 + o.h - y) * o.px]; }

  _drawOverlay() {
    const o = this.overlay;
    if (!o) return;
    const g = o.cv.getContext("2d");
    g.clearRect(0, 0, o.cv.width, o.cv.height);
    if (this.wildfire) this.wildfire.drawUnder(g);
    // Ground the cameras have seen: a faint cool tint, with a brighter thin edge where the searched ground
    // ends. Kept on its own canvas and redrawn only around the ground newly seen since the last frame.
    const b = o.coverDirty;
    if (b) {
      const e = o.wash.getContext("2d"), d = Math.max(1, 0.5 * o.px);
      const [x0, y0] = [Math.max(0, Math.floor(b[0])), Math.max(0, Math.floor(b[1]))];
      const [w, h] = [Math.min(o.cv.width, Math.ceil(b[2])) - x0, Math.min(o.cv.height, Math.ceil(b[3])) - y0];
      if (w > 0 && h > 0) {
        const part = (dx, dy) => e.drawImage(o.cover, x0, y0, w, h, x0 + dx, y0 + dy, w, h);
        e.save(); e.beginPath(); e.rect(x0, y0, w, h); e.clip();
        e.clearRect(x0, y0, w, h);
        for (const [dx, dy] of [[d, 0], [-d, 0], [0, d], [0, -d]]) part(dx, dy);                    // the seen ground, a little larger
        e.globalCompositeOperation = "destination-out"; part(0, 0);                                  // less the seen ground: its edge
        e.globalCompositeOperation = "destination-in"; e.fillStyle = "rgba(0,0,0,.5)"; e.fillRect(x0, y0, w, h);
        e.globalCompositeOperation = "source-over"; e.globalAlpha = 0.09; part(0, 0);
        e.restore();
      }
      o.coverDirty = null;
    }
    g.drawImage(o.wash, 0, 0);
    if (this.terrain && this.hello.sensor && this.footprints) {
      // Where each thermal camera is looking now: a faint fill and a thin ring, draped over the slopes.
      g.lineWidth = Math.max(1.2, 0.35 * o.px);
      for (const [x, y, r] of this.footprints) {
        const [u, v] = this._toPx(x, y);
        g.beginPath(); g.arc(u, v, r * o.px, 0, Math.PI * 2);
        g.fillStyle = "rgba(143,240,255,.08)"; g.fill();
        g.strokeStyle = "rgba(184,247,255,.6)"; g.stroke();
      }
    }
    if (o.area) {
      const [a, b] = this._toPx(o.area[0], o.area[3]), [c, d] = this._toPx(o.area[2], o.area[1]);
      g.strokeStyle = "rgba(255,196,96,.9)"; g.lineWidth = Math.max(1.5, 0.45 * o.px);
      g.setLineDash([4 * o.px, 3 * o.px]);
      g.strokeRect(a, b, c - a, d - b); g.setLineDash([]);
      g.lineWidth = Math.max(2.5, 0.9 * o.px);   // solid corners
      const L = 10 * o.px;
      for (const [u, v, su, sv] of [[a, b, 1, 1], [c, b, -1, 1], [a, d, 1, -1], [c, d, -1, -1]]) {
        g.beginPath(); g.moveTo(u + su * L, v); g.lineTo(u, v); g.lineTo(u, v + sv * L); g.stroke();
      }
    }
    if (this.wildfire) this.wildfire.drawOver(g);
    o.tex.needsUpdate = true;
  }

  _buildPath(hello) {
    const pts = hello.path.map((p) => new THREE.Vector3(p[0], this.groundAt(p[0], p[1]) + 0.4, -p[1]));
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineDashedMaterial({ color: 0xffe2a8, dashSize: 2.5, gapSize: 3, transparent: true, opacity: 0.6 }));
    line.computeLineDistances();
    this.world.add(line);
  }

  _buildDrones(hello) {
    if (this.droneGroup) {   // rebuilt when the CAD model arrives mid-run
      this.world.remove(this.droneGroup);
      this.droneGroup.traverse((o) => { if (!o.userData.shared) { o.geometry?.dispose(); o.material?.dispose(); } });
    }
    const n = this.n = hello.n;
    const R = hello.body_radius_m * DRONE_SCALE;
    const model = this.model;
    const inst = (geo, mat, count) => {
      const im = new THREE.InstancedMesh(geo, mat, count);
      im.instanceMatrix.setUsage(THREE.DynamicDrawUsage); im.frustumCulled = false;
      return im;
    };
    let bodies, blades = [], top;
    let rotorR;
    if (model) {
      // The scout's CAD model (hardware/), drawn DRONE_SCALE times its real size like everything here.
      bodies = model.body.map((b) => { const im = inst(b.geometry, b.material, n); im.userData.shared = true; return im; });
      blades = model.props.map((p) => { const im = inst(p.geometry, p.material, n); im.userData.shared = true; return { im, hub: p.hub, spin: p.spin }; });
      this.motorOffsets = model.props.map((p) => [p.hub.x, p.hub.z]);
      rotorR = model.bladeDiameter / 2;
      this.rotorY = model.props[0].hub.y + 0.03;
      top = 0.55 * R;
    } else {
      // Until the model has loaded (or if it cannot be): a simple quadcopter in units of the body radius.
      const arm = 1.3 * R;
      rotorR = 0.55 * R;
      const d = arm / Math.SQRT2;
      this.motorOffsets = [[d, d], [d, -d], [-d, d], [-d, -d]];
      this.rotorY = 0.22 * R;
      top = 0.32 * R;
      const frameParts = [new THREE.BoxGeometry(0.95 * R, 0.32 * R, 0.95 * R)];
      for (const [sx, sz] of [[1, 1], [1, -1]]) {
        const a = new THREE.BoxGeometry(2 * arm, 0.1 * R, 0.13 * R);
        a.rotateY(Math.atan2(sz, sx)); frameParts.push(a);
      }
      for (const [x, z] of this.motorOffsets) frameParts.push(new THREE.CylinderGeometry(0.13 * R, 0.13 * R, 0.24 * R, 10).translate(x, 0.08 * R, z));
      if (hello.sensor) frameParts.push(new THREE.SphereGeometry(0.2 * R, 10, 8).translate(0, -0.28 * R, 0));   // thermal camera
      for (const [x, z] of this.motorOffsets) frameParts.push(new THREE.BoxGeometry(0.06 * R, 0.4 * R, 0.06 * R).translate(x * 0.55, -0.3 * R, z * 0.55));   // legs
      bodies = [inst(mergeGeometries(frameParts), new THREE.MeshStandardMaterial({ color: 0x2b3036, roughness: 0.55, metalness: 0.35 }), n),
        inst(new THREE.SphereGeometry(0.42 * R, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2).translate(0, 0.14 * R, 0),
          new THREE.MeshStandardMaterial({ color: 0xd8dde2, roughness: 0.3, metalness: 0.1 }), n)];
    }
    // A status light on top of each drone, in the status colour (flying, failed, left for battery).
    const leds = inst(new THREE.SphereGeometry((model ? 0.045 : 0.1) * R, 8, 6),
      new THREE.MeshBasicMaterial({ color: this.fx ? new THREE.Color(5, 5, 5) : 0xffffff, toneMapped: false }), n);   // small, and bright enough to glow
    // Spinning rotors are drawn as blurred discs; blades show only when a rotor has stopped.
    const rotorGeo = new THREE.CircleGeometry(rotorR, 24); rotorGeo.rotateX(-Math.PI / 2);
    const rotors = inst(rotorGeo, new THREE.MeshBasicMaterial({ map: this.rotorTex, transparent: true, depthWrite: false, side: THREE.DoubleSide }), n * 4);
    const slots = inst(new THREE.TorusGeometry(0.9, 0.07, 6, 28), new THREE.MeshBasicMaterial({ color: 0xffb347, transparent: true, opacity: 0.75 }), n);
    // Camera footprints: a thin ring and a faint fill where each thermal camera is looking.
    const fp = inst(new THREE.CircleGeometry(1, 48),
      new THREE.MeshBasicMaterial({ color: 0x8ff0ff, transparent: true, opacity: 0.07, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -3 }), n);
    const fpRing = inst(new THREE.RingGeometry(0.965, 1, 64),
      new THREE.MeshBasicMaterial({ color: 0xb8f7ff, transparent: true, opacity: 0.55, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -3 }), n);
    // On a run with terrain the footprints are painted on the draped ground overlay instead, so they follow the slopes.
    fp.visible = fpRing.visible = !!hello.sensor && !this.terrain;
    // Hidden until the first frame places them; until then every instance would sit at the origin.
    const group = this.droneGroup = new THREE.Group();
    group.visible = false;
    this.world.add(group);
    for (const im of [...bodies, ...blades.map((b) => b.im), leds, rotors, slots, fp, fpRing]) group.add(im);
    for (const im of bodies) im.castShadow = true;
    for (let i = 0; i < n; i++) leds.setColorAt(i, STATUS_COLOR[0]);
    // Far-away markers: a dot per drone at a fixed size on screen, faded out once the drone itself is big enough to see.
    const mg = new THREE.BufferGeometry();
    mg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3).setUsage(THREE.DynamicDrawUsage));
    mg.setAttribute("aColor", new THREE.BufferAttribute(new Float32Array(n * 3), 3).setUsage(THREE.DynamicDrawUsage));
    mg.setAttribute("aAlpha", new THREE.BufferAttribute(new Float32Array(n), 1).setUsage(THREE.DynamicDrawUsage));
    const markers = new THREE.Points(mg, new THREE.ShaderMaterial({
      uniforms: { uTex: { value: this.markerTex }, uSize: { value: 15 } },
      vertexShader: `attribute vec3 aColor; attribute float aAlpha; varying vec3 vColor; varying float vAlpha; uniform float uSize;
        void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_PointSize = uSize; vColor = aColor; vAlpha = aAlpha; }`,
      fragmentShader: `uniform sampler2D uTex; varying vec3 vColor; varying float vAlpha;
        void main() { vec4 t = texture2D(uTex, gl_PointCoord); gl_FragColor = vec4(pow(t.rgb, vec3(2.2)) * vColor, t.a * vAlpha);
          #include <colorspace_fragment>
        }`,
      transparent: true, depthWrite: false, depthTest: false,
    }));
    markers.frustumCulled = false;
    markers.renderOrder = 9;
    group.add(markers);
    this.drones = { bodies, blades, leds, rotors, slots, fp, fpRing, markers, R, top };
    this.yaw = new Float32Array(n);
    this.camFootR = hello.sensor ? Math.tan((hello.sensor.fov_deg * Math.PI) / 360) : 0;
    this.resize();
  }

  // -- per frame ----------------------------------------------------------------------------------
  frame(f) {
    // Frames arrive at 10 Hz; the view interpolates between the last two, so motion is smooth at 60 fps.
    const now = performance.now();
    const p = f.p.map((q) => [q[0], q[1], q[2]]);
    if (this.cur) {
      const dtSim = Math.max(1e-3, f.t - this.cur.t);
      this.vel = p.map((q, i) => [(q[0] - this.cur.p[i][0]) / dtSim, (q[1] - this.cur.p[i][1]) / dtSim, (q[2] - this.cur.p[i][2]) / dtSim]);
    }
    this.prev = this.cur;
    this.cur = { t: f.t, p, at: now, interval: this.prev ? Math.min(400, Math.max(30, now - this.prev.at)) : 100 };
    this.simLatest = f.t;
    this.lastFrame = f;
    this.wind.set(f.wind[0], 0, -f.wind[1]);
    if (f.found) f.found.forEach((hit, i) => {
      const tg = this.targets[i];
      if (tg && hit && !tg.found) { tg.found = true; tg.label.visible = true; tg.foundAt = this.vt; }
    });
    if (this.wildfire) this.wildfire.frame(f);
    this._paintCoverage(f);
  }

  event(e) {
    if (e.kind === "target_found") {
      const tg = this.targets[e.target];
      if (tg) tg.label.userData.setText(`Hotspot ${e.target + 1} found at ${Number(e.t).toFixed(1)} s`);
    }
    if (this.wildfire) this.wildfire.event(e);
  }

  _paintCoverage(f) {
    const o = this.overlay;
    if (!o || !this.camFootR || !f.s) return;
    const g = o.cover.getContext("2d");
    g.fillStyle = "#9cf0f4";
    f.p.forEach((q, i) => {
      if (f.s[i] !== 0) return;
      const r = Math.max(0, q[2] - this.groundAt(q[0], q[1])) * this.camFootR;
      const [u, v] = this._toPx(q[0], q[1]), rp = r * o.px, m = rp + 2 + 0.5 * o.px, b = o.coverDirty;
      g.beginPath(); g.arc(u, v, rp, 0, Math.PI * 2); g.fill();
      o.coverDirty = b ? [Math.min(b[0], u - m), Math.min(b[1], v - m), Math.max(b[2], u + m), Math.max(b[3], v + m)] : [u - m, v - m, u + m, v + m];
    });
    this.overlayDirty = true;
  }

  setSpeed(s) { this.speed = s; }
  setPaused(p) { this.paused = p; }

  // cut: jump straight to the new view instead of gliding there (the recorder's camera program cuts between views).
  setMode(mode, cut = false) {
    this.mode = mode;
    this.fireLook = this.fireHold = null;
    if (cut) {
      this.cut = true;
      if (this.ride) this.ride.offset = this.ride.target = this.ride.aim = null;
      if (this.swarmCentre) this.focus.copy(this.swarmCentre);
    }
    // A longer lens close up, so the drone fills the frame without the camera sitting on it.
    this.camera.fov = mode === "close" ? 34 : 46;
    this.camera.updateProjectionMatrix();
    this.userMoved = false;
    this.orbitAngle = Math.atan2(this.camera.position.z - this.focus.z, this.camera.position.x - this.focus.x);
    if (mode === "top") this.camera.position.set(this.focus.x, this.focus.y + Math.max(140, (this.extent || 40) * 3.2) * this.zoom, this.focus.z + 1);
  }

  // Pull the camera back (z > 1) or in, keeping the sun's shadows over everything in view.
  setZoom(z) {
    this.zoom = Math.max(0.3, Math.min(4, Number(z) || 1));
    const c = this.sun.shadow.camera, half = 140 * Math.max(1, this.zoom);
    c.left = -half; c.right = half; c.top = half; c.bottom = -half; c.far = 900 * Math.max(1, this.zoom);
    c.updateProjectionMatrix();
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    this.renderer.setSize(r.width, r.height, false);
    if (this.fx) this.fx.setSize(r.width, r.height);
    this.camera.aspect = r.width / Math.max(1, r.height);
    this.camera.updateProjectionMatrix();
    const pr = this.renderer.getPixelRatio();
    this.smoke.points.material.uniforms.uScale.value = this.flames.points.material.uniforms.uScale.value = r.height * pr * 0.9;
    if (this.drones) this.drones.markers.material.uniforms.uSize.value = Math.max(11, Math.min(18, r.height / 70)) * pr;
  }

  // One view step: dtView in view seconds (camera, spinning rotors), dtSim in simulated seconds
  // (particles), simNow the simulated time to draw drones at (null: interpolate on the wall clock).
  _step(dtView, dtSim, simNow) {
    this.vt += dtView;
    if (this.cut && this.hello && this.cur) this._camera(0);   // a cut: move the camera first, so the drones' drawn size and the cutaway use it
    if (this.hello && this.cur) this._drawDrones(simNow);
    this._drawFire(dtSim, this.vt);
    if (this.wildfire) { this.wildfire.step(dtSim); this.wildfire.tick(this.vt); }
    this.smoke.update(dtSim, this.wind);
    this.flames.update(dtSim, this.wind);
    this.streaks.update(dtSim, this.wind, this.focus);
    if (this.overlayDirty && (this.film || this.vt - this.overlayDrawnAt > 0.25)) {
      this._drawOverlay(); this.overlayDirty = false; this.overlayDrawnAt = this.vt;
    }
    this._camera(dtView);
    this.controls.update();
    this.sun.position.copy(this.focus).add(this.sunOffset);   // the shadow box follows what the camera looks at
    this.sun.target.position.copy(this.focus);
    this._drawWind();
    this.sky.position.copy(this.camera.position);
    this.fade.cam.value.copy(this.camera.position);
    this.sky.material.uniforms.uDrift.value.set(this.vt * 0.0035, this.vt * 0.0012);
    this.swayT.value = this.vt;
    const sv = SUN_DIR.clone().transformDirection(this.camera.matrixWorldInverse), side = Math.hypot(sv.x, sv.y);
    this.smoke.points.material.uniforms.uSun.value.set(sv.x / Math.max(1e-3, side), -sv.y / Math.max(1e-3, side), 0.9 * Math.min(1, side));
    for (const p of [...(this.sceneryPlants || []), ...(this.worldPlants || [])]) p.update(this.camera.position);
    if (this.fx) this.fx.render(); else this.renderer.render(this.scene, this.camera);
  }

  // Film mode: draw the scene at simulated time simNow, one video frame of dtView seconds after the last.
  renderFilm(dtView, simNow) {
    const dtSim = this.lastFilmT == null ? 0 : Math.max(0, simNow - this.lastFilmT);
    this.lastFilmT = simNow;
    this._step(dtView, dtSim, simNow);
  }

  _drawDrones(simNow) {
    const { bodies, blades, leds, rotors, slots, fp, fpRing, markers, R, top } = this.drones;
    const f = this.lastFrame, n = this.n, t = this.vt;
    let a = 1;
    if (this.prev && simNow != null) a = Math.max(0, Math.min(1, (simNow - this.prev.t) / Math.max(1e-3, this.cur.t - this.prev.t)));
    else if (this.prev) a = Math.min(1, (performance.now() - this.cur.at) / this.cur.interval);
    const m = new THREE.Object3D(), part = new THREE.Object3D(), c = new THREE.Vector3();
    const mPos = markers.geometry.attributes.position, mCol = markers.geometry.attributes.aColor, mAlpha = markers.geometry.attributes.aAlpha;
    // Pixels per metre at distance 1, to decide when a drone is big enough on screen to drop its marker.
    const pxPerM = this.canvas.clientHeight / (2 * Math.tan((this.camera.fov * Math.PI) / 360));
    let k = 0, extent = 0;
    const pts = [], feet = this.footprints = [], at = this.dronePos = [];
    for (let i = 0; i < n; i++) {
      const q1 = this.cur.p[i], q0 = this.prev ? this.prev.p[i] : q1;
      const x = q0[0] + (q1[0] - q0[0]) * a, y = q0[1] + (q1[1] - q0[1]) * a, z = q0[2] + (q1[2] - q0[2]) * a;
      at.push([x, z, -y]);
      const v = this.vel ? this.vel[i] : [0, 0, 0];
      const sp = Math.hypot(v[0], v[1]);
      if (sp > 0.6) this.yaw[i] = Math.atan2(v[1], v[0]);
      const st = f.s[i];
      // Lean into the direction of travel, as a quadcopter does to fly forward.
      const lean = st === 0 ? Math.min(0.32, 0.03 * sp) : st === 1 ? 0.6 : 0;
      m.position.set(x, z, -y);
      m.rotation.set(0, this.yaw[i], 0, "YXZ");
      m.rotateZ(-lean);
      const dist = this.camera.position.distanceTo(m.position), size = droneSize(dist);
      m.scale.setScalar(size);
      m.updateMatrix();
      for (const im of bodies) im.setMatrixAt(i, m.matrix);
      part.position.set(0, top, 0); part.rotation.set(0, 0, 0); part.scale.setScalar(1); part.updateMatrix();
      leds.setMatrixAt(i, m.matrix.clone().multiply(part.matrix));
      const col = STATUS_COLOR[st] || STATUS_COLOR[0];
      leds.setColorAt(i, col);
      const turning = st === 0 || st === 3, spin = turning ? t * 55 + i : 0;
      this.motorOffsets.forEach(([ox, oz], j) => {
        part.position.set(ox, this.rotorY, oz); part.rotation.set(0, (j % 2 ? 1 : -1) * spin, 0);
        part.scale.setScalar(turning ? 1 : 1e-4); part.updateMatrix();
        rotors.setMatrixAt(i * 4 + j, m.matrix.clone().multiply(part.matrix));
      });
      for (const b of blades) {
        part.position.copy(b.hub); part.rotation.set(0, b.spin * (0.7 * i), 0); part.scale.setScalar(turning ? 1e-4 : 1); part.updateMatrix();
        b.im.setMatrixAt(i, m.matrix.clone().multiply(part.matrix));
      }
      mPos.setXYZ(i, x, z + 0.3 * R * size, -y);
      mCol.setXYZ(i, col.r, col.g, col.b);
      mAlpha.setX(i, 1 - smooth(16, 30, (2.6 * R * size * pxPerM) / Math.max(1, dist)));
      const slot = this.mode === "ride" || this.mode === "close" ? null : f.slots[i];   // close in, the slot rings would fill the frame
      if (slot) {
        m.position.copy(toV(slot)); m.rotation.set(Math.PI / 2, 0, 0); m.scale.setScalar(1); m.updateMatrix();
        slots.setMatrixAt(i, m.matrix);
      } else { m.position.set(0, -1e4, 0); m.scale.setScalar(1e-4); m.updateMatrix(); slots.setMatrixAt(i, m.matrix); }
      if (st === 0) {
        const g = this.groundAt(x, y), r = Math.max(0.01, (z - g) * this.camFootR);
        m.position.set(x, g + 0.3, -y); m.rotation.set(-Math.PI / 2, 0, 0); m.scale.set(r, r, 1); m.updateMatrix();
        fp.setMatrixAt(i, m.matrix); fpRing.setMatrixAt(i, m.matrix);
        feet.push([x, y, r]);
        c.add(new THREE.Vector3(x, z, -y)); k++; pts.push([x, z, -y]);
      } else { m.position.set(0, -1e4, 0); m.scale.setScalar(1e-4); m.updateMatrix(); fp.setMatrixAt(i, m.matrix); fpRing.setMatrixAt(i, m.matrix); }
    }
    for (const im of [...bodies, ...blades.map((b) => b.im), leds, rotors, slots, fp, fpRing]) im.instanceMatrix.needsUpdate = true;
    if (this.terrain && this.hello.sensor) this.overlayDirty = true;   // the footprints move with the drones
    leds.instanceColor.needsUpdate = true;
    mPos.needsUpdate = mCol.needsUpdate = mAlpha.needsUpdate = true;
    this.droneGroup.visible = true;
    if (k) {
      c.multiplyScalar(1 / k);
      for (const p of pts) extent = Math.max(extent, Math.hypot(p[0] - c.x, p[2] - c.z));
      this.extent = extent;
      this.swarmCentre = c;
      this.fade.focus.value.copy(this.focus);
      // How far the drones spread across the line of sight, and along it past the centre.
      const cam = this.camera.position, dx = c.x - cam.x, dz = c.z - cam.z, dl = Math.hypot(dx, dz) || 1;
      let side = 0, ahead = 0;
      for (const p of pts) {
        const ox = p[0] - c.x, oz = p[2] - c.z;
        side = Math.max(side, Math.abs((ox * dz - oz * dx) / dl)); ahead = Math.max(ahead, (ox * dx + oz * dz) / dl);
      }
      // Under the canopy (the ride-along camera in a forest) nothing is cut away, nor from straight above,
      // where cut crowns leave trunks that read as fallen logs.
      this.fade.radius.value = this.mode === "top" || (this.crownBase && cam.y < this.crownBase - 0.5) ? 0 : side + 18;
      this.fade.ahead.value = ahead + 8;
    }
  }

  _drawFire(dt, t) {
    // Embers and smoke at every found hotspot.
    if (!dt) return;
    const ember = new THREE.Color(), smokeCol = new THREE.Color();
    const chance = (rate) => { const e = rate * dt; return Math.floor(e) + (Math.random() < e - Math.floor(e) ? 1 : 0); };
    for (const tg of this.targets || []) {
      // A hotspot is only a scorched patch until a camera finds it; then it glows, smokes and gets a ring.
      if (!tg.found) { tg.glow.material.opacity = 0; continue; }
      const pulse = 0.55 + 0.25 * Math.sin(t * 7 + tg.t.x) + 0.15 * Math.sin(t * 13.7 + tg.t.y);
      tg.glow.material.opacity = pulse;
      const age = this.vt - tg.foundAt;
      tg.ring.material.opacity = Math.min(1, age * 2) * (0.6 + 0.3 * Math.sin(t * 3));
      tg.ring.scale.setScalar(1 + 0.06 * Math.sin(t * 3));
      tg.beacon.material.opacity = Math.min(0.28, age * 0.4);
      const ox = tg.t.x, oy = tg.t.y;
      // A smouldering plume: grey-brown near the ground, paling as it rises and spreads downwind.
      for (let s = chance(7); s > 0; s--) {
        const v = 0.06 * Math.random();
        smokeCol.setRGB(0.72 + v, 0.71 + v, 0.7 + v);
        this.smoke.emit(ox + (Math.random() - 0.5) * tg.r, tg.g + 1, -oy + (Math.random() - 0.5) * tg.r,
          (Math.random() - 0.5) * 0.4, 2.6 + Math.random() * 1.2, (Math.random() - 0.5) * 0.4, 16 + 8 * Math.random(), 2.4, 0.9, 0.55, smokeCol);
      }
      for (let s = chance(26); s > 0; s--) {
        ember.setHSL(0.02 + 0.07 * Math.random(), 1, 0.45 + 0.2 * Math.random());
        this.flames.emit(ox + (Math.random() - 0.5) * tg.r * 1.4, tg.g + 0.2, -oy + (Math.random() - 0.5) * tg.r * 1.4,
          (Math.random() - 0.5) * 0.4, 0.5 + Math.random() * 0.9, (Math.random() - 0.5) * 0.4, 0.6 + Math.random() * 0.6, 1.1, -0.5, 0.85, ember);
      }
    }
  }

  // The wind gauge (top right of the view): the simulated wind's speed, and an arrow showing where it
  // blows on screen, turned with the camera.
  _drawWind() {
    if (!this.windEl || !this.lastFrame) return;
    const w = this.wind, len = Math.hypot(w.x, w.z);
    const fwd = new THREE.Vector3(); this.camera.getWorldDirection(fwd);
    const ang = Math.atan2(fwd.x * w.z - fwd.z * w.x, fwd.x * w.x + fwd.z * w.z);   // 0: blowing away from the camera
    this.windArrowEl.style.transform = `rotate(${(ang * 180) / Math.PI}deg)`;
    this.windArrowEl.style.opacity = len > 0.2 ? 1 : 0.3;
    this.windTextEl.textContent = `Wind ${len.toFixed(1)} m/s`;
  }

  _camera(dt) {
    if (!this.hello) {
      if (this.idleDrift && !this.userMoved) {   // no flight yet (the cinema layout): a slow pan over the empty meadow
        this.idleAngle = (this.idleAngle ?? 2.3) + dt * 0.035;
        const a = this.idleAngle;
        this.camera.position.set(Math.cos(a) * 230, 58, Math.sin(a) * 230);
        this.controls.target.set(0, 6, 0);
      }
      return;
    }
    const snap = this.cut;
    this.cut = false;
    const k = snap ? 1 : 1 - Math.exp(-dt * 3);
    if (this.mode === "ride" && !this.userMoved && this._rideCamera(dt)) return;
    if (this.mode === "close" && !this.userMoved && this._closeCamera(dt)) return;
    if (this.mode === "fire" && !this.userMoved && this._fireCamera(dt, snap)) return;
    if (this.swarmCentre && this.mode !== "fire") {   // a user turning the fire view keeps looking at the fire
      const delta = this.swarmCentre.clone().sub(this.focus).multiplyScalar(k);
      this.focus.add(delta);
      this.camera.position.add(delta);   // every mode moves with the swarm; the user's own angle and zoom are kept
    }
    const f = this.focus, e = Math.max(20, this.extent || 20), z = this.zoom;
    let want = null, aim = 0;   // aim: look this far above the swarm, so the skyline shows at the top of the frame
    if (this.mode === "follow" && !this.userMoved && this.canyon && this.lastFrame) {
      // Between tall buildings: from high up behind the swarm, looking down the street.
      const hd = this.lastFrame.heading ?? 0, dist = (e * 1.2 + 60) * z;
      want = new THREE.Vector3(f.x - Math.cos(hd) * dist * 0.55, f.y + dist * 0.85, f.z + Math.sin(hd) * dist * 0.55);
    } else if (this.mode === "follow" && !this.userMoved) {
      // Low enough to see the skyline over the swarm, high enough to see the formation's shape.
      const dist = (e * 1.35 + 70) * z;
      const dir = this.camera.position.clone().sub(f);
      dir.y = 0; if (dir.lengthSq() < 1) dir.set(-0.6, 0, 0.8); dir.normalize();
      want = f.clone().add(dir.multiplyScalar(dist * 0.92)).add(new THREE.Vector3(0, dist * 0.38, 0));
      aim = dist * 0.15;
    } else if (this.mode === "orbit" && !this.userMoved) {
      this.orbitAngle += dt * 0.1;
      const rad = (e * 1.6 + 70) * z;
      want = new THREE.Vector3(f.x + Math.cos(this.orbitAngle) * rad, f.y + rad * 0.36, f.z + Math.sin(this.orbitAngle) * rad);
      aim = rad * 0.13;
    } else if (this.mode === "chase" && !this.userMoved && this.lastFrame) {
      const hd = this.lastFrame.heading ?? 0;
      const back = (e * 1.1 + 35) * z;
      want = new THREE.Vector3(f.x - Math.cos(hd) * back, f.y + (10 + e * 0.28) * z, f.z + Math.sin(hd) * back);
    } else if (this.mode === "top" && !this.userMoved) {
      want = new THREE.Vector3(f.x, f.y + Math.max(150, e * 2.6 + 70) * z, f.z + 1);
    }
    if (want) this.camera.position.lerp(want, snap ? 1 : this.film ? Math.min(1, dt * 2.5) : k);
    // Never under the ground (on a run with hills the camera can swing behind a slope).
    const cp = this.camera.position, floor = (this.sceneryHeight ? this.sceneryHeight(cp.x, -cp.z) : 0) + 12;
    if (cp.y < floor) cp.y = floor;
    this.aim = this.aim == null ? aim : this.aim + (aim - this.aim) * k;
    this.controls.target.set(f.x, f.y + this.aim, f.z);
  }
  // Fire (the wildfire valley): from above a fire, behind and to one side, looking along the path the station
  // forecasts for its head, drifting slowly; it glides to each new forecast. fireTarget picks the fire
  // (0-based; null: the fastest spreading one). zoom scales the distance.
  _fireCamera(dt, snap) {
    const s = this.wildfire && this.wildfire.shot(this.fireTarget);
    if (!s) return false;
    // With no forecast direction yet, look at the fire from where the camera already is.
    // The angle is worked out once and kept: worked out afresh each frame, the sideways drift would circle the fire.
    let b = s.bearing == null ? null : (s.bearing * Math.PI) / 180;
    if (b == null) {
      const h = this.fireHold, cp = this.camera.position;
      if (!h || h.x !== s.x || h.y !== s.y) this.fireHold = { x: s.x, y: s.y, b: Math.atan2(s.x - cp.x, s.y + cp.z) };
      b = this.fireHold.b;
    } else this.fireHold = null;
    const hx = Math.sin(b), hy = Math.cos(b);   // the head's way, in simulated x (east) and y (north)
    this.fireT = (this.fireT || 0) + dt;
    // From behind and to the left of the head's path (about 50 degrees round), so the arrow runs up and to the left
    // across the frame, clear of the cards on the right; the aim sits a little right of the path for the same reason.
    const mid = s.len * 0.42, mx = s.x + hx * mid, my = s.y + hy * mid;
    const dist = Math.max(170, s.len * 1.1) * this.zoom, a = 0.88 + 0.12 * Math.sin(this.fireT * 0.11);
    const fx = hx * Math.cos(a) + hy * Math.sin(a), fy = hy * Math.cos(a) - hx * Math.sin(a);   // the camera's forward
    const cx = mx - fx * dist, cy = my - fy * dist, ax = mx + fy * dist * 0.12, ay = my - fx * dist * 0.12;
    const want = new THREE.Vector3(cx, this.groundAt(mx, my) + dist * 0.5, -cy);
    const look = new THREE.Vector3(ax, this.groundAt(mx, my) + 6, -ay);
    const k = snap ? 1 : this.film ? Math.min(1, dt * 1.6) : 1 - Math.exp(-dt * 1.6);
    // A fire found mid-shot: glide to it from where the camera looks now (a programmed cut lands at once, k = 1).
    if (!this.fireLook) this.fireLook = this.controls.target.clone();
    this.camera.position.lerp(want, k);
    this.fireLook.lerp(look, k);
    const cp = this.camera.position, floor = (this.sceneryHeight ? this.sceneryHeight(cp.x, -cp.z) : 0) + 12;
    if (cp.y < floor) cp.y = floor;
    this.focus.copy(this.fireLook);
    this.controls.target.copy(this.fireLook);
    return true;
  }

  // The drone to stay with in the ride-along and close-up views: kept until it stops flying, else the one nearest
  // the swarm's centre (or rideDrone, set by the recorder).
  _pickDrone() {
    const f = this.lastFrame, at = this.dronePos;
    if (!f || !at || !at.length || !this.swarmCentre) return null;
    let r = this.ride;
    if (!r || f.s[r.i] !== 0) {
      let best = -1, bd = Infinity;
      at.forEach((p, i) => {
        const d = i === this.rideDrone ? -1 : Math.hypot(p[0] - this.swarmCentre.x, p[2] - this.swarmCentre.z);
        if (f.s[i] === 0 && d < bd) { bd = d; best = i; }
      });
      if (best < 0) return null;
      r = this.ride = { i: best, yaw: r ? r.yaw : this.yaw[best], target: null };
    }
    return r;
  }

  // Close-up: a few metres from one drone, drawn at its real size, swinging slowly round it from behind and
  // to the side, the valley beyond. zoom scales the distance.
  _closeCamera(dt) {
    const r = this._pickDrone();
    if (!r) return false;
    const p = new THREE.Vector3(...this.dronePos[r.i]);
    let dy = this.yaw[r.i] - r.yaw;
    dy = Math.atan2(Math.sin(dy), Math.cos(dy));
    r.yaw += dy * (1 - Math.exp(-dt * 1.5));
    this.closeT = (this.closeT || 0) + dt;
    const ang = r.yaw + Math.PI * 0.78 + 0.5 * Math.sin(this.closeT * 0.18), dist = 1.6 * this.zoom;
    const want = p.clone().add(new THREE.Vector3(Math.cos(ang) * dist, 0.42 * this.zoom, -Math.sin(ang) * dist));
    const look = p.clone().add(new THREE.Vector3(Math.cos(r.yaw) * 0.35, -0.12, -Math.sin(r.yaw) * 0.35));
    // Keep the drone where it is in the frame as it flies: the camera's offset is smoothed, not its position.
    if (!r.offset) r.offset = want.clone().sub(p);
    r.offset.lerp(want.clone().sub(p), this.film ? Math.min(1, dt * 2) : 1 - Math.exp(-dt * 2));
    this.camera.position.copy(p).add(r.offset);
    // The aim is smoothed relative to the drone too: smoothed in the world, it trails a metre behind a drone
    // flying at 5 m/s, which from 1.6 m away puts the drone out of the frame.
    if (!r.aim) r.aim = look.clone().sub(p);
    r.aim.lerp(look.clone().sub(p), this.film ? Math.min(1, dt * 5) : 1 - Math.exp(-dt * 5));
    const cp = this.camera.position, floor = (this.sceneryHeight ? this.sceneryHeight(cp.x, -cp.z) : 0) + 1.5;
    if (cp.y < floor) cp.y = floor;
    this.focus.copy(p);
    this.controls.target.copy(p).add(r.aim);
    return true;
  }

  // Ride along: close behind one drone near the middle of the swarm, looking ahead and down at the ground its
  // camera sweeps. The drone is kept until it stops flying; the view turns with it, smoothly, at each pass's end.
  _rideCamera(dt) {
    const r = this._pickDrone(), at = this.dronePos;
    if (!r) return false;
    const p = new THREE.Vector3(...at[r.i]);
    let dy = this.yaw[r.i] - r.yaw;
    dy = Math.atan2(Math.sin(dy), Math.cos(dy));
    r.yaw += dy * (1 - Math.exp(-dt * 1.2));
    const fwd = new THREE.Vector3(Math.cos(r.yaw), 0, -Math.sin(r.yaw));
    // In a forest, below the crowns and looking along the trail; elsewhere a little above, looking down ahead.
    const under = this.crownBase != null && p.y + 4 > this.crownBase - 1.5;
    const want = p.clone().addScaledVector(fwd, under ? -9 : -11).add(new THREE.Vector3(0, under ? Math.max(0.8, this.crownBase - 1.8 - p.y) : 4, 0));
    const look = p.clone().addScaledVector(fwd, under ? 40 : 32).add(new THREE.Vector3(0, under ? -2.5 : -11, 0));
    const k = this.film ? Math.min(1, dt * 4) : 1 - Math.exp(-dt * 4);
    if (!r.target) { r.target = look.clone(); this.camera.position.copy(want); }
    this.camera.position.lerp(want, k);
    r.target.lerp(look, k);
    const cp = this.camera.position, floor = (this.sceneryHeight ? this.sceneryHeight(cp.x, -cp.z) : 0) + 12;
    if (cp.y < floor) cp.y = floor;
    this.focus.copy(p);
    this.controls.target.copy(r.target);
    return true;
  }
}
