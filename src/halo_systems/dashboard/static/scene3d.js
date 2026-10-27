// Halo Systems dashboard: the 3D view. Draws what the run sends: terrain and land cover (the wildfire
// valley), obstacles, path, drones (the scout's CAD model), hotspots and missing people, survey area, wind,
// camera coverage and found events; wildfire3d.js adds the valley's fires, smoke, forecasts and alerts.
// Drawn for looks only, never touching the simulation: the scenery beyond the run (distant hills and
// mountains, forests, and low shrubs under 1.5 m, also over the flight area where a run has no land
// cover), the tree crowns above the simulated trunks, and each found hotspot's glow, embers and smoke
// (the scorched patch marks every hotspot); the particles drift with the simulated wind. A missing person
// is a sitting hiker, under a clump of trees when the run hides them under tree crowns (their `cover`).
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";
import { mergeGeometries, canvasTexture, hash, fbm, ridge, smooth, conifer, coniferFar, shrub, stoneColors, CLEAN } from "/static/util3d.js";
import { mergeVertices } from "/static/vendor/BufferGeometryUtils.js";
import { WildfireLayer } from "/static/wildfire3d.js";
import { loadDroneModel, DRONE_MODEL_URL } from "/static/drone_model.js";
import { FilmLook } from "/static/postfx.js";

// The clean film look (?clean=1, the recorder's --clean): a low warm morning sun under a soft sky, haze that warms
// towards the sun, a filmic grade, richer smoke, drones with position lights and short trails of where they have just
// flown, and fewer, larger labels. Looks only, like the rest of this file: every position still comes from the run.
// Without it every frame draws exactly as before, so takes filmed without it can be made again.
export { CLEAN };
// Simulation frame is x east, y north, z up; three.js is y up, so (x, y, z) -> (x, z, -y).
const toV = (p) => new THREE.Vector3(p[0], p[2], -p[1]);
export const DRONE_SCALE = 4;   // drones are drawn up to this many times their real size so they show from 150 m away
// ...and at their real size close up: the drawn size grows from 1x within DRONE_TRUE_M of the camera to
// DRONE_SCALE times beyond DRONE_FULL_M (evenly on a log scale in between).
const DRONE_TRUE_M = 6, DRONE_FULL_M = 80;
const droneSize = (dist) => Math.pow(DRONE_SCALE, smooth(0, 1, Math.log(Math.max(1, dist) / DRONE_TRUE_M) / Math.log(DRONE_FULL_M / DRONE_TRUE_M))) / DRONE_SCALE;
// A missing person likewise: true size (about 1.7 m tall) within PERSON_TRUE_M, up to PERSON_SCALE times it
// beyond PERSON_FULL_M, so the hiker still shows in the follow and top views.
export const PERSON_SCALE = 2;
const PERSON_TRUE_M = 12, PERSON_FULL_M = 150;
const personSize = (dist) => Math.pow(PERSON_SCALE, smooth(0, 1, Math.log(Math.max(1, dist) / PERSON_TRUE_M) / Math.log(PERSON_FULL_M / PERSON_TRUE_M)));

// Status codes from sim/engine.py: 0 active, 1 failed (motor out, falling), 2 crashed, 3 retired, 4 down.
const STATUS_COLOR = { 0: new THREE.Color(0x3fe0c8), 1: new THREE.Color(0xff4d45), 2: new THREE.Color(0xff4d45),
  3: new THREE.Color(0xb5bcc4), 4: new THREE.Color(0xff4d45) };
const SKY_ZENITH = new THREE.Color(CLEAN ? 0x2a5fa8 : 0x3f78bd), SKY_HORIZON = new THREE.Color(CLEAN ? 0xc9d6df : 0xdfe3e2), HAZE = new THREE.Color(CLEAN ? 0xc3ccd2 : 0xc9d2d8);
// Late-morning sun from the west-south-west; in the clean look a low morning sun from the east-south-east, 21 degrees up.
const SUN_DIR = CLEAN ? new THREE.Vector3(0.865, 0.36, 0.352).normalize() : new THREE.Vector3(-0.78, 0.5, 0.36).normalize();
const FOG_DENSITY = CLEAN ? 0.00054 : 0.00062;
// Clean look: the haze and the horizon towards the sun take its warm light.
const SUN_HAZE = new THREE.Color(0xf0c58e);
// Clean look in film mode: the 3D view's pixels per page pixel (supersampling, downscaled by the browser).
const CLEAN_SS = (() => { const v = Number(new URLSearchParams(location.search).get("ss")); return v >= 0.5 && v <= 2 ? v : 1; })();
// The clean look's sky: deep blue overhead fading to a pale horizon that warms towards the low sun, a band of haze
// along the skyline, a few thin clouds lit from the sun's side, and the sun's glow.
const CLEAN_SKY = `uniform vec3 uZenith; uniform vec3 uHorizon; uniform vec3 uHaze; uniform vec3 uSun; uniform vec3 uSunHaze; uniform vec2 uDrift; varying vec3 vDir;
  float h21(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float vn(vec2 p) { vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
    return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), u.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), u.x), u.y); }
  float fbm(vec2 p) { float a = 0.5, s = 0.0; for (int k = 0; k < 5; k++) { s += a * vn(p); p = p * 2.03 + vec2(17.1, 9.2); a *= 0.5; } return s; }
  void main() { vec3 d = normalize(vDir); float h = d.y;
    float s = max(0.0, dot(d, uSun));
    vec2 dh = normalize(d.xz + vec2(1e-5)), sh = normalize(uSun.xz);
    float toward = pow(0.5 + 0.5 * dot(dh, sh), 2.5);              // 1 under the sun, 0 opposite it
    vec3 hor = mix(uHorizon, uSunHaze, toward * 0.85);
    vec3 c = h > 0.0 ? mix(hor, uZenith, pow(clamp(h, 0.0, 1.0), 0.42)) : uHaze;
    c = mix(c, mix(uHaze, uSunHaze, toward * 0.75), exp(-max(h, 0.0) * 16.0) * 0.85);
    if (h > 0.0) {
      vec2 p = d.xz / (h + 0.1) * 1.1 + uDrift;
      float n = fbm(p * vec2(1.0, 2.2));
      float cover = smoothstep(0.58, 0.8, n) * smoothstep(0.03, 0.25, h) * 0.75;
      float lit = 0.5 + 0.5 * smoothstep(0.3, 0.9, fbm(p * 1.9 + 4.0 + uSun.xz)) + 0.6 * pow(s, 4.0);
      vec3 cloud = mix(vec3(0.55, 0.58, 0.66), vec3(1.05, 0.92, 0.78), clamp(lit, 0.0, 1.0));
      c = mix(c, mix(cloud, hor, exp(-h * 7.0) * 0.6), cover);
    }
    c += vec3(1.0, 0.86, 0.64) * (pow(s, 1400.0) * 9.0 + pow(s, 48.0) * 0.5 + pow(s, 6.0) * 0.16);
    gl_FragColor = vec4(c, 1.0);
    #include <colorspace_fragment>
  }`;
if (CLEAN) {
  // Aerial haze that is warmer looking towards the sun and cooler away from it: the fog chunks of every built-in
  // material, before any compiles. The view direction comes from the view-space position (viewMatrix is a uniform
  // in every fragment shader three.js builds).
  const s = SUN_DIR, h = SUN_HAZE, f = (x) => x.toFixed(4);
  THREE.ShaderChunk.fog_pars_vertex = "#ifdef USE_FOG\n\tvarying float vFogDepth;\n\tvarying vec3 vFogView;\n#endif";
  THREE.ShaderChunk.fog_vertex = "#ifdef USE_FOG\n\tvFogDepth = - mvPosition.z;\n\tvFogView = mvPosition.xyz;\n#endif";
  THREE.ShaderChunk.fog_pars_fragment = `#ifdef USE_FOG
	uniform vec3 fogColor;
	varying float vFogDepth;
	varying vec3 vFogView;
	#ifdef FOG_EXP2
		uniform float fogDensity;
	#else
		uniform float fogNear;
		uniform float fogFar;
	#endif
#endif`;
  THREE.ShaderChunk.fog_fragment = `#ifdef USE_FOG
	#ifdef FOG_EXP2
		float fogFactor = 1.0 - exp( - fogDensity * fogDensity * vFogDepth * vFogDepth );
	#else
		float fogFactor = smoothstep( fogNear, fogFar, vFogDepth );
	#endif
	vec3 fogDirW = normalize( ( vec4( vFogView, 0.0 ) * viewMatrix ).xyz );
	float fogSun = pow( max( dot( fogDirW, vec3( ${f(s.x)}, ${f(s.y)}, ${f(s.z)} ) ), 0.0 ), 4.0 );
	vec3 fogTint = mix( fogColor, vec3( ${f(h.r)}, ${f(h.g)}, ${f(h.b)} ), fogSun * 0.8 );
	gl_FragColor.rgb = mix( gl_FragColor.rgb, fogTint, fogFactor );
#endif`;
}
// Squads (wildfire valley runs flown as squads): one colour per patrol area, on each drone's slot ring and
// far-away marker and on the area's lanes; a drone flying between squads is white. Other codes, if a run ever
// has them, take the spare colours in turn.
const SQUAD_HEX = { F: "#4fc3f7", N: "#9ccc65", S: "#ffd54f" }, SQUAD_SPARE = ["#f48fb1", "#ce93d8", "#80cbc4", "#ffab91"];
const TRANSIT_COLOR = new THREE.Color(0xffffff);
// Clean look: which labels give way when two would overlap (higher stays): located fires, then the arrows' tags, the
// squads, then places.
const LABEL_PRIO = { fire: 4, target: 4, head: 3, squad: 2, place: 1 };
// Clean look: each drone's trail, where it flew in the last TRAIL_S simulated seconds (the run's own frames, 10 a
// second), drawn TRAIL_PX pixels wide (at 1080 lines), fading out behind it.
const TRAIL_S = 8, TRAIL_PTS = TRAIL_S * 10 + 4, TRAIL_PX = 3.2;
function squadStyle(code, id) {
  const hex = SQUAD_HEX[code] || SQUAD_SPARE[Math.abs(id | 0) % SQUAD_SPARE.length], v = parseInt(hex.slice(1), 16);
  return { col: new THREE.Color(hex), rgb: `${(v >> 16) & 255},${(v >> 8) & 255},${v & 255}` };   // col: linear; rgb: for the 2D overlay
}
// A lightning strike's glyph on the ground overlay, in units of its half-height (canvas y points south).
const BOLT = [[0.16, -1], [-0.46, 0.14], [-0.04, 0.14], [-0.22, 1], [0.46, -0.22], [0.04, -0.22]];

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
      oc = mix(oc, ${CLEAN ? "fogTint" : "fogColor"}, fogFactor);
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
// The clean look's particle shading. Smoke: the side of each puff towards the sun in warm light and the other side in
// cool shade, a glow where the camera looks towards the sun through it, and darker, browner smoke where it leaves the
// fire (by the particle's age). Flames: brighter than sunlit ground, so the film look's bloom makes them glow.
function cleanParticles(texture, additive) {
  return {
    defines: { ADDITIVE: additive ? 1 : 0 },
    uniforms: { uTex: { value: texture }, uScale: { value: 600 }, fogColor: { value: HAZE }, fogDensity: { value: 0 },
      uSun: { value: new THREE.Vector3(0, 0, 0) }, uSunW: { value: SUN_DIR }, uLight: { value: new THREE.Vector3(1.38, 1.15, 0.9) },
      uShade: { value: new THREE.Vector3(0.6, 0.66, 0.76) } },
    vertexShader: `attribute float aSize; attribute float aAlpha; attribute vec3 aColor; attribute float aRot; attribute float aF;
      varying float vAlpha; varying vec3 vColor; varying float vRot; varying float vDepth; varying float vF; varying float vFwd;
      uniform float uScale; uniform vec3 uSunW;
      void main() { vec4 wp = modelMatrix * vec4(position, 1.0); vec4 mv = viewMatrix * wp; gl_Position = projectionMatrix * mv;
        gl_PointSize = aSize * uScale / max(1.0, -mv.z); vAlpha = aAlpha; vColor = aColor; vRot = aRot; vDepth = -mv.z; vF = aF;
        vFwd = pow(max(dot(normalize(wp.xyz - cameraPosition), uSunW), 0.0), 5.0); }`,
    fragmentShader: `uniform sampler2D uTex; uniform vec3 fogColor; uniform float fogDensity; uniform vec3 uSun; uniform vec3 uLight; uniform vec3 uShade;
      varying float vAlpha; varying vec3 vColor; varying float vRot; varying float vDepth; varying float vF; varying float vFwd;
      void main() { vec2 c = gl_PointCoord - 0.5; float s = sin(vRot), k = cos(vRot);
        vec4 t = texture2D(uTex, vec2(k * c.x - s * c.y, s * c.x + k * c.y) + 0.5);
        float fog = 1.0 - exp(-fogDensity * fogDensity * vDepth * vDepth);
        vec3 base = pow(vColor, vec3(2.2));   // vColor: display values
        #if ADDITIVE
          gl_FragColor = vec4(base * 2.6 * (1.0 - fog), t.a * vAlpha);
        #else
          float side = clamp(0.5 + 1.7 * uSun.z * dot(c, uSun.xy), 0.0, 1.0);
          vec3 col = base * mix(uShade, uLight, side) + uLight * base * vFwd * 0.85;
          col *= mix(vec3(0.6, 0.54, 0.48), vec3(1.0), smoothstep(0.0, 0.5, vF));
          gl_FragColor = vec4(mix(col, fogColor, fog), t.a * vAlpha);
        #endif
        #include <colorspace_fragment>
      }`,
    transparent: true, depthWrite: false,
    blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
  };
}

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
    if (CLEAN) { this.aF = attr(1); geo.setAttribute("aF", this.aF); }   // each particle's age as a share of its life
    geo.setDrawRange(0, 0);
    const mat = new THREE.ShaderMaterial(CLEAN ? cleanParticles(texture, additive) : {
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
      if (this.aF) this.aF.array[j] = f;
      j++;
    }
    this.n = j;
    this.points.geometry.setDrawRange(0, j);
    for (const a of [this.aPos, this.aSize, this.aAlpha, this.aColor, this.aRot]) a.needsUpdate = true;
    if (this.aF) this.aF.needsUpdate = true;
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
    if (CLEAN) {
      // A filmic curve that rolls the highlights off gently (the grade in postfx.js adds the contrast back), and in
      // film mode a frame drawn at CLEAN_SS times the pixels, which the browser scales down: smoother edges.
      r.toneMapping = THREE.AgXToneMapping;
      r.toneMappingExposure = 1.3;
      if (new URLSearchParams(location.search).has("film")) r.setPixelRatio(CLEAN_SS);
    }
    const scene = this.scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(HAZE.clone(), FOG_DENSITY);
    scene.background = SKY_HORIZON.clone();
    this.camera = new THREE.PerspectiveCamera(46, 1, 0.5, 9000);
    this.camera.position.set(-60, 55, 70);
    // The film look (postfx.js): bloom, tone mapping, grade and vignette; ?fx=0 draws straight to the screen.
    this.fx = null;
    if (new URLSearchParams(location.search).get("fx") !== "0") {
      try { this.fx = new FilmLook(r, scene, this.camera, CLEAN); } catch (err) { console.warn("film look off", err); }
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
    this.squads = null;       // squad runs: each squad's latest layout {id, code, name, area, n, members, path, ...}
    this._squadFocus = null;  // squadFocus: the squad code the follow, orbit, chase, ride and close views frame (null: all)
    this.focus = new THREE.Vector3();
    // Clean look: labels are drawn by the page in a layer over the view (_pageLabel), under the cinema cards.
    this.pageLabels = new Set();
    if (CLEAN) { this.labelLayer = document.createElement("div"); this.labelLayer.id = "labels"; canvas.after(this.labelLayer); }
    this.trailHist = [];      // clean look: the run's latest frames, for the drones' trails
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
        uDrift: { value: new THREE.Vector2() }, uSunHaze: { value: SUN_HAZE } },
      vertexShader: "varying vec3 vDir; void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: CLEAN ? CLEAN_SKY : `uniform vec3 uZenith; uniform vec3 uHorizon; uniform vec3 uHaze; uniform vec3 uSun; uniform vec2 uDrift; varying vec3 vDir;
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
    // Clean look: a low, warmer morning sun, and a bluer skylight filling the long shadows.
    this.scene.add(CLEAN ? new THREE.HemisphereLight(0xa9c8f0, 0x6e5a40, 1.15) : new THREE.HemisphereLight(0xbcd5f2, 0x665644, 0.85));   // cool skylight in the shade
    const sun = this.sun = CLEAN ? new THREE.DirectionalLight(0xffc996, 4.3) : new THREE.DirectionalLight(0xffdcb2, 3.7);       // warm late-morning sun
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const c = sun.shadow.camera;
    c.left = -140; c.right = 140; c.top = 140; c.bottom = -140; c.near = 10; c.far = 900;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.6;
    this.sunOffset = SUN_DIR.clone().multiplyScalar(380);
    if (CLEAN) {   // the low sun: further out, so the ridges between it and the swarm cast their shadows too
      c.far = 1300; sun.shadow.normalBias = 0.9;
      this.sunOffset = SUN_DIR.clone().multiplyScalar(560);
    }
    this.shadowFar = c.far;
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
    if (CLEAN) {
      // A fuller, finer smoke puff, and a drone marker with a soft glow round its dark rim.
      this.puffTex = canvasTexture(256, 256, (g, w) => {
        for (let i = 0; i < 56; i++) {
          const a = hash(i, 21) * 6.283, rr = Math.sqrt(hash(i, 22)) * w * 0.22;
          const x = w / 2 + Math.cos(a) * rr, y = w / 2 + Math.sin(a) * rr, r = w * (0.08 + 0.15 * hash(i, 23));
          const gr = g.createRadialGradient(x, y, 0, x, y, r);
          gr.addColorStop(0, `rgba(255,255,255,${0.16 + 0.16 * hash(i, 24)})`); gr.addColorStop(0.6, `rgba(255,255,255,${0.06 + 0.06 * hash(i, 25)})`);
          gr.addColorStop(1, "rgba(255,255,255,0)");
          g.fillStyle = gr; g.fillRect(0, 0, w, w);
        }
      }, false);
      this.markerTex = canvasTexture(128, 128, (g, w) => {
        const c = w / 2, glow = g.createRadialGradient(c, c, w * 0.16, c, c, w * 0.5);
        glow.addColorStop(0, "rgba(255,255,255,.55)"); glow.addColorStop(0.45, "rgba(255,255,255,.16)"); glow.addColorStop(1, "rgba(255,255,255,0)");
        g.fillStyle = glow; g.fillRect(0, 0, w, w);
        g.fillStyle = "rgba(10,16,22,.8)"; g.beginPath(); g.arc(c, c, w * 0.235, 0, Math.PI * 2); g.fill();
        const gr = g.createRadialGradient(c, c, 0, c, c, w * 0.18);
        gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.7, "rgba(240,240,240,1)"); gr.addColorStop(1, "rgba(210,210,210,1)");
        g.fillStyle = gr; g.beginPath(); g.arc(c, c, w * 0.175, 0, Math.PI * 2); g.fill();
      }, false);
    }
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
    // Squads and lightning strikes (wildfire valley options): null on every other run, which draws as it always has.
    this._setSquads(hello);
    const strikes = hello.scenario && hello.scenario.strikes;
    this.strikes = Array.isArray(strikes) && strikes.length ? strikes : null;
    this._setFog(FOG_DENSITY);   // a squad run thins the haze as the camera pulls back (_step); every run starts from the usual
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
    this.trailHist = []; this.squadLabels = null;
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
    if (CLEAN && this.squads) this._buildSquadLabels();
    this.prev = null; this.cur = null; this.lastFrame = null; this.vel = null;
    this.swarmCentre = null; this.extent = null; this.lastFilmT = null;
    this.userMoved = false;
    const p0 = hello.path[0];
    const g0 = this.groundAt(p0[0], p0[1]);
    this.focus.set(p0[0], g0 + hello.directive.altitude_m, -p0[1]);
    this.camera.position.set(p0[0] - 70, g0 + hello.directive.altitude_m + 40, -p0[1] + 80);
    if (this.squads) this._squadStartView();
    this.controls.target.copy(this.focus);
    this.setMode(this.mode);
  }

  // -- squads ---------------------------------------------------------------------------------------
  // The squads as the run starts (hello.scenario.squads), each with its colour, and which squad each drone is in.
  _setSquads(hello) {
    const list = hello.scenario && hello.scenario.squads;
    this.squads = Array.isArray(list) && list.length ? list.map((s) => ({ ...s })) : null;
    this.squadLive = null;   // the latest frame's squads (flying counts, centres, headings)
    this.squadLabelAt = null;   // where each squad's name plate sits (_squadLabelY)
    this.squadStyles = new Map();
    for (const s of this.squads || []) this.squadStyles.set(s.id, squadStyle(s.code, s.id));
    this._squadMembers(hello.n);
  }

  // A squad_changed (every squad, re-planned) or squad_joined (the squad a drone just joined) event: the squads'
  // new members, lanes and circuits. A drone that has left its squad and not yet joined another is in none.
  _updateSquads(list) {
    for (const s of list || []) {
      if (!s || s.id == null) continue;
      const k = this.squads.findIndex((q) => q.id === s.id);
      if (k >= 0) this.squads[k] = { ...this.squads[k], ...s };
      else { this.squads.push({ ...s }); this.squadStyles.set(s.id, squadStyle(s.code, s.id)); this.squads.sort((p, q) => p.id - q.id); }
    }
    this._squadMembers(this.hello.n);
    this.overlayDirty = true;
  }

  _squadMembers(n) {
    // Each drone's squad id (-1: none), from the members lists; frames carry their own (f.sq), which wins.
    this.squadOf = null;
    if (!this.squads) return;
    const of = this.squadOf = new Array(n || 0).fill(-1);
    for (const s of this.squads) for (const d of s.members || []) if (d >= 0 && d < of.length) of[d] = s.id;
  }

  _squadColour(id) {
    const st = id != null && id >= 0 ? this.squadStyles.get(id) : null;
    return st ? st.col : TRANSIT_COLOR;
  }

  get squadFocus() { return this._squadFocus; }
  // A squad code ("F", "N", "S") or null. The follow, orbit and chase views then frame that squad's flying drones
  // (ride-along and close-up stay with one of them); a run without squads, or without that squad, ignores it.
  set squadFocus(code) {
    this._squadFocus = code == null || code === "" ? null : String(code).toUpperCase();
    this._refocus();
  }

  _focusSquadId() {
    if (!this.squads || this._squadFocus == null) return null;
    const s = this.squads.find((q) => String(q.code).toUpperCase() === this._squadFocus);
    return s ? s.id : null;
  }

  // Where the camera starts on a squad run: framing the squads at the start of their circuits (the focused
  // squad's alone, when one is set), from the usual side, so the first frames do not pull back from one corner.
  _squadStartView() {
    const id = this._focusSquadId(), alt = this.hello.directive.altitude_m, pts = [];
    for (const s of this.squads) {
      if ((id != null && s.id !== id) || !s.path || !s.path.length) continue;
      const [x, y] = s.path[0];
      for (let k = 0; k < Math.max(1, s.n || 0); k++) pts.push([x, y]);
    }
    if (!pts.length) return;
    const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length, cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
    const e = Math.max(20, ...pts.map((p) => Math.hypot(p[0] - cx, p[1] - cy))), dist = (e * 1.35 + 70) * this.zoom;
    const g = this.groundAt(cx, cy);
    this.focus.set(cx, g + alt, -cy);
    this.extent = e;   // until the first frame: the top view (setMode) starts high enough too
    const dir = new THREE.Vector3(-70, 0, 80).normalize();
    this.camera.position.copy(this.focus).addScaledVector(dir, dist * 0.92).add(new THREE.Vector3(0, dist * 0.38, 0));
  }

  // Squad runs: the haze thins as the camera pulls back to take in squads hundreds of metres apart, so the far
  // squads and their lanes stay clear (at the usual distances it is the usual haze).
  _setFog(density) {
    this.scene.fog.density = density;
    for (const p of [this.smoke, this.flames]) if (p) p.points.material.uniforms.fogDensity.value = density;
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
    const people = (hello.targets || []).filter((t) => t.kind === "person").length;
    let person = 0;
    for (const [i, t] of (hello.targets || []).entries()) {
      const g = this.groundAt(t.x, t.y);
      const r = Math.max(t.radius_m, 1.5);
      if (t.kind === "person") {   // a missing person: drawn as a hiker, with no scorch, embers or smoke
        this.targets.push(this._buildPerson(hello, t, g, r, people > 1 ? `Missing person ${++person}` : "Missing person"));
        continue;
      }
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

  // A missing person: a hiker sitting on the ground (_hiker), with their back to a clump of trees and under the
  // edge of its crowns when the run hides them under cover (_buildCopse). Once found: a warm heat-signature glow,
  // a ring, a beacon that shows above the crowns, and a label (_drawPeople).
  _buildPerson(hello, t, g, r, name) {
    const a = hash(t.x, t.y) * 6.28;   // from the person toward the trees (simulated x/y, radians); they face away
    const hiker = this._hiker();
    hiker.position.set(t.x, g, -t.y); hiker.rotation.y = a + Math.PI;
    this.world.add(hiker);
    // Drawn larger, the hiker grows about the crowns' edge (edge m along their legs), so the share under them holds.
    const edge = t.cover > 0 ? this._buildCopse(hello, t, a) : 0;
    const at = hiker.position.clone(), legs = new THREE.Vector3(-Math.cos(a), 0, Math.sin(a));
    // The heat signature: a soft orange patch with a hotter core.
    const glow = new THREE.Group();
    for (const [rad, color, blending] of [[1.6, 0xff6a1a, THREE.NormalBlending], [0.8, 0xffc070, THREE.AdditiveBlending]]) {
      const m = new THREE.Mesh(new THREE.CircleGeometry(rad, 24),
        new THREE.MeshBasicMaterial({ map: this.dotTex, color, transparent: true, opacity: 0, blending, depthWrite: false }));
      m.rotation.x = -Math.PI / 2; glow.add(m);
    }
    glow.position.set(t.x, g + 0.06, -t.y);
    const ring = new THREE.Mesh(new THREE.RingGeometry(r * 2.6, r * 2.6 + 0.6, 64),
      new THREE.MeshBasicMaterial({ color: 0xffc94a, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.set(t.x, g + 0.12, -t.y);
    const beacon = new THREE.Mesh(new THREE.CylinderGeometry(0.25, 0.25, 50, 8, 1, true),
      new THREE.MeshBasicMaterial({ color: 0xffc060, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
    beacon.position.set(t.x, g + 25, -t.y);
    const label = this._label(`${name} found`, "#ffc94a");
    label.position.set(t.x, g + 4.5, -t.y); label.visible = false;
    this.world.add(glow, ring, beacon, label);
    return { t, g, r, person: true, name, hiker, at, legs, edge, glow, ring, beacon, label, found: false };
  }

  // A low-poly hiker about 1.7 m tall, sitting with the right leg out and the left knee up, leaning back on one
  // hand: red jacket, blue backpack with a rolled mat, dark trousers, boots and a beanie. Legs along +x, y up. The
  // left arm (userData.arm, pivoting at the shoulder) rests on the knee. The simulation models only the person's
  // heat, not whether they respond, so the hiker does not move when found.
  _hiker() {
    const mat = (color) => new THREE.MeshLambertMaterial({ color, flatShading: true });
    const jacket = mat(0xe3321f), trousers = mat(0x34404f), boots = mat(0x4a3424), skin = mat(0xd39a74),
      pack = mat(0x2a4f9c), roll = mat(0xf0c22e), hat = mat(0x2c3038);
    const box = (parent, m, w, h, d, x, y, z, rz = 0) => {
      const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
      b.position.set(x, y, z); b.rotation.z = rz; parent.add(b);
    };
    const root = new THREE.Group();
    box(root, trousers, 0.3, 0.18, 0.36, 0, 0.11, 0);                  // seat
    box(root, trousers, 0.9, 0.15, 0.16, 0.5, 0.08, 0.1);              // right leg, stretched out
    box(root, boots, 0.14, 0.22, 0.13, 0.99, 0.11, 0.1);
    box(root, trousers, 0.48, 0.16, 0.17, 0.235, 0.27, -0.1, 0.68);    // left thigh and shin, knee up
    box(root, trousers, 0.47, 0.14, 0.15, 0.57, 0.24, -0.1, -0.876);
    box(root, boots, 0.26, 0.12, 0.13, 0.79, 0.06, -0.1);
    const torso = new THREE.Group();   // the upper body, leaning back a little
    torso.position.set(0, 0.17, 0); torso.rotation.z = 0.22; root.add(torso);
    box(torso, jacket, 0.27, 0.52, 0.42, 0, 0.27, 0);
    box(torso, jacket, 0.14, 0.12, 0.3, -0.07, 0.53, 0);               // the hood, down
    box(torso, pack, 0.2, 0.46, 0.34, -0.23, 0.28, 0);
    box(torso, pack, 0.22, 0.08, 0.32, -0.22, 0.53, 0);
    const mat2 = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.4, 8), roll);
    mat2.rotation.x = Math.PI / 2; mat2.position.set(-0.25, 0.04, 0); torso.add(mat2);
    const head = new THREE.Mesh(new THREE.IcosahedronGeometry(0.105, 1), skin);
    head.position.set(0.02, 0.68, 0); torso.add(head);
    const beanie = new THREE.Mesh(new THREE.SphereGeometry(0.112, 8, 4, 0, Math.PI * 2, 0, Math.PI / 2), hat);
    beanie.position.set(0.01, 0.7, 0); torso.add(beanie);
    const arm = (z, rz, rx) => {
      const p = new THREE.Group();
      p.position.set(0, 0.48, z); p.rotation.set(rx, 0, rz); torso.add(p);
      box(p, jacket, 0.09, 0.5, 0.1, 0, -0.25, 0);
      box(p, skin, 0.07, 0.09, 0.08, 0, -0.54, 0);
      return p;
    };
    arm(0.25, -0.45, -0.15);                 // right hand on the ground behind
    root.userData.arm = arm(-0.25, 0.95, 0); // left hand on the knee
    root.traverse((m) => { if (m.isMesh) m.castShadow = m.receiveShadow = true; });
    return root;
  }

  // The clump of trees over a person under cover, in direction `a` from them: the first tree's crown reaches over
  // their back so that about `cover` of them (backpack first) is under it seen from straight above; the others
  // stand behind it, none over the person. All top out well below the flight level. Looks only: the run has no
  // trees here. Returns how far along the hiker's legs from the seat that crown's edge lies.
  _buildCopse(hello, t, a) {
    const alt = (hello.directive && hello.directive.altitude_m) || 30;
    const top = Math.max(Math.min(7, alt - 4), Math.min(15, alt - 9));   // at least 4 m below the drones
    const c = Math.min(0.9, t.cover), R0 = 4.2 + 0.8 * hash(t.y, t.x);
    const D = 0.85 * R0 + 0.35 - 1.4 * c;   // seat to trunk; a crown's edge is about 0.85 R out, the hiker 1.4 m long
    const trees = [[t.x + Math.cos(a) * D, t.y + Math.sin(a) * D, R0, top]];
    for (let k = 1; k <= 14 && trees.length < 7; k++) {
      const [px, py] = trees[Math.floor(hash(k, t.y) * trees.length)];
      const b = a + (hash(k, t.x) - 0.5) * 3.2, d = 5 + 3.5 * hash(t.y, k);
      const x = px + Math.cos(b) * d, y = py + Math.sin(b) * d, R = 3 + 1.4 * hash(x, y);
      if (Math.hypot(x - t.x, y - t.y) < 0.85 * R + 2.5 || trees.some(([qx, qy]) => Math.hypot(x - qx, y - qy) < 4)) continue;
      trees.push([x, y, R, top * (0.72 + 0.28 * hash(y, x))]);
    }
    const trunkGeo = new THREE.CylinderGeometry(1, 1.15, 1, 7, 1); trunkGeo.translate(0, 0.5, 0);
    const leaf = new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: true });
    addSway(leaf, this.swayT);
    const trunks = new THREE.InstancedMesh(trunkGeo, new THREE.MeshLambertMaterial({ color: 0x5a4636 }), trees.length);
    const crowns = new THREE.InstancedMesh(conifer(), leaf, trees.length);
    const m = new THREE.Object3D(), col = new THREE.Color();
    trees.forEach(([x, y, R, h], i) => {
      const gz = this.groundAt(x, y), base = Math.min(4.8, Math.max(3.4, h * 0.3)), ch = h - base, w = 0.16 + 0.03 * R;
      m.rotation.set(0, hash(x, 7) * 6.28, 0);
      m.position.set(x, gz, -y); m.scale.set(w, base + 0.3 * ch, w); m.updateMatrix(); trunks.setMatrixAt(i, m.matrix);
      m.position.set(x, gz + base - 0.13 * ch, -y); m.scale.set(R, ch * 1.13, R); m.updateMatrix(); crowns.setMatrixAt(i, m.matrix);
      col.setHSL(0.22 + 0.07 * hash(x, 3), 0.3 + 0.18 * hash(y, 5), 0.17 + 0.09 * hash(x, y));
      crowns.setColorAt(i, col);
    });
    for (const im of [trunks, crowns]) { im.castShadow = im.receiveShadow = true; this.world.add(im); }
    return 0.85 * R0 - D;
  }

  // Each missing person: drawn larger the further the camera is (personSize); once found, the glow, ring and
  // beacon fade in. Timed by the view clock alone, so film frames are the same every take.
  _drawPeople() {
    for (const tg of this.targets || []) {
      if (!tg.person) continue;
      const s = personSize(this.camera.position.distanceTo(tg.at)), arm = tg.hiker.userData.arm;
      tg.hiker.scale.setScalar(s);
      tg.hiker.position.copy(tg.at).addScaledVector(tg.legs, tg.edge * (1 - s));
      const [halo, core] = tg.glow.children;
      arm.rotation.set(0, 0, 0.95);
      if (!tg.found) { halo.material.opacity = core.material.opacity = 0; continue; }
      const t = this.vt, age = t - tg.foundAt, heat = Math.min(1, age * 1.5) * (0.85 + 0.15 * Math.sin(t * 2.4));
      tg.glow.scale.setScalar(s);
      halo.material.opacity = 0.6 * heat; core.material.opacity = 0.9 * heat;
      tg.ring.material.opacity = Math.min(1, age * 2) * (0.6 + 0.3 * Math.sin(t * 3));
      tg.ring.scale.setScalar(1 + 0.06 * Math.sin(t * 3));
      tg.beacon.material.opacity = Math.min(0.32, age * 0.4);
    }
  }

  // kind (place, fire, head, squad, target) ranks the labels in the clean look, where overlapping ones give way.
  _label(text, dot = "#ff8a3d", kind = "target") {
    if (CLEAN) return this._pageLabel(text, dot, kind);
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

  // Clean look: a label drawn by the page over the view, so its text is crisp at any size: a glass pill with a
  // coloured dot, on a thin stem down to the point it names. The sprite returned is never drawn; it carries where
  // the label points (its position), whether it shows (visible, in the scene) and its owner's opacity, as a drawn
  // label's would, and its material's dispose removes it. Labels that would overlap give way to the more important
  // one, and none shows under a card or notice (_declutter).
  _pageLabel(text, dot, kind) {
    const mat = new THREE.SpriteMaterial({ visible: false });
    const sprite = new THREE.Sprite(mat);
    const el = document.createElement("div");
    el.className = `lbl lbl-${kind}`;
    el.innerHTML = '<div class="pill"><i></i><span></span></div><div class="stem"></div>';
    el.querySelector("i").style.background = dot;
    el.style.opacity = "0";
    this.labelLayer.appendChild(el);
    const rec = { sprite, el, prio: LABEL_PRIO[kind] ?? 1, fade: 0, target: 0, w: 0, h: 0, x: null, y: 0, k: 1 };
    this.pageLabels.add(rec);
    mat.addEventListener("dispose", () => { el.remove(); this.pageLabels.delete(rec); });
    sprite.userData.setText = (s) => { sprite.userData.text = s; el.querySelector("span").textContent = s; rec.w = 0; };
    sprite.userData.setText(text);
    return sprite;
  }

  // Clean look: place each page label over its point, largest priority first; one that would overlap a label already
  // placed, a card or a notice, or run off the frame, fades out (and back in when it is clear), on the view's clock.
  _declutter(dt) {
    if (!this.pageLabels.size) return;
    const W = this.canvas.clientWidth, H = this.canvas.clientHeight, cam = this.camera, base = this.canvas.getBoundingClientRect();
    cam.updateMatrixWorld();
    const ui = [...document.querySelectorAll("#cine .glass")].filter((e) => !e.hidden && e.offsetParent !== null && Number(getComputedStyle(e).opacity) > 0.05)
      .map((e) => { const r = e.getBoundingClientRect(); return [r.left - base.left, r.top - base.top, r.right - base.left, r.bottom - base.top]; });
    const v = new THREE.Vector3(), items = [];
    for (const r of this.pageLabels) {
      const s = r.sprite;
      let shown = s.visible && s.material.opacity > 0.01, top = s;
      for (let p = s.parent; shown && p; p = p.parent) { if (!p.visible) shown = false; top = p; }
      if (shown && top === this.scene) {
        s.getWorldPosition(v);
        r.dist = v.distanceTo(cam.position);
        v.project(cam);
        shown = v.z < 1 && Math.abs(v.x) < 1.3 && Math.abs(v.y) < 1.3;
      } else shown = false;
      if (!shown) { r.target = 0; continue; }
      r.k = s.scale.x;   // a smaller label (the arrows' tags) keeps its smaller size
      if (!r.w) { r.w = r.el.offsetWidth; r.h = r.el.offsetHeight - (r.lift || 0) / r.k; }   // measured once per text, at the usual stem
      r.x = ((v.x + 1) / 2) * W; r.y = ((1 - v.y) / 2) * H;
      r.box = [r.x - (r.w * r.k) / 2, r.y - r.h * r.k, r.x + (r.w * r.k) / 2, r.y];
      if (r.y > H - 36) { r.target = 0; continue; }   // the point it names at the frame's foot: no label
      items.push(r);
    }
    // More important first; between equals, one already showing, then the nearer.
    items.sort((a, b) => b.prio - a.prio || (b.fade > 0.5) - (a.fade > 0.5) || a.dist - b.dist);
    const hit = (a, b, m) => a[0] < b[2] + m && a[2] > b[0] - m && a[1] < b[3] + m && a[3] > b[1] - m;
    // A label (not a place's) that would cover a more important one is first tried higher up on a longer stem (one or
    // two label heights), and set back only if none is free. A raised label comes down only when there is room to spare, and
    // otherwise keeps the height it had, so it does not hop.
    const kept = [];
    for (const r of items) {
      const step = r.h * r.k + 4, was = r.liftT || 0;
      const tries = r.prio < 2 ? [0] : [...new Set(was ? [0, was, step, 2 * step] : [0, step, 2 * step])];   // a place is never raised
      let at = null;
      for (const L of tries) {
        const b = [r.box[0], r.box[1] - L, r.box[2], r.box[3] - L], m = L === 0 && was ? 24 : 0, stem = [r.x - 2, b[3], r.x + 2, r.y];
        if (b[0] > 12 && b[2] < W - 12 && b[1] > 12 && b[3] < H - 12 && !kept.some((q) => hit(b, q.box, 8 + m)) && !ui.some((u) => hit(b, u, 14 + m) || hit(stem, u, 4))) { at = L; r.box = b; break; }
      }
      r.target = at === null ? 0 : 1;
      if (at !== null) { r.liftT = at; kept.push(r); }
    }
    const a = 1 - Math.exp(-dt * 7);
    for (const r of this.pageLabels) {
      r.fade += (r.target - r.fade) * a;
      // A label coming in takes its height at once; one already up glides there.
      r.lift = r.fade < 0.05 ? r.liftT || 0 : (r.lift || 0) + ((r.liftT || 0) - (r.lift || 0)) * a;
      const op = r.fade * r.sprite.material.opacity;
      r.el.style.opacity = op.toFixed(3);
      r.el.style.visibility = op < 0.004 ? "hidden" : "visible";
      r.el.style.zIndex = r.lift > 1 ? "1" : "2";   // a raised label's long stem passes behind the labels at their usual height
      if (r.stem === undefined) r.stem = r.el.querySelector(".stem");
      r.stem.style.height = `${(20 + r.lift / (r.k || 1)).toFixed(1)}px`;
      if (r.x != null) r.el.style.transform = `translate(${r.x.toFixed(1)}px, ${r.y.toFixed(1)}px) translate(-50%, -100%) scale(${r.k.toFixed(3)})`;
    }
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
    if (this.squads) this._drawSquads(g);   // each squad's lanes and area replace the one survey box
    else if (o.area) {
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
    if (this.strikes) this._drawStrikes(g);   // under the zones, alerts and located fires drawn next
    if (this.wildfire) this.wildfire.drawOver(g);
    o.tex.needsUpdate = true;
  }

  // Squad runs, on the ground overlay: each squad's patrol circuit (its lanes, dashed, over a faint dark edge so they
  // show on pale grass) and a faint outline of its area in the squad's colour, named on a small plate just outside
  // the area's west edge, halfway up, with the drones flying in it now. Redrawn as squads change (squad_changed,
  // squad_joined) and with each overlay redraw.
  _drawSquads(g) {
    const o = this.overlay, px = o.px;
    const live = new Map((this.squadLive || []).map((s) => [s.id, s]));
    g.save();
    g.lineJoin = "round";
    for (const s of this.squads) {
      const rgb = (this.squadStyles.get(s.id) || squadStyle(s.code, s.id)).rgb;
      if (Array.isArray(s.area) && s.area.length === 4) {
        const [a, b] = this._toPx(s.area[0], s.area[3]), [c, d] = this._toPx(s.area[2], s.area[1]);
        g.lineWidth = Math.max(1, 0.45 * px); g.strokeStyle = `rgba(${rgb},.32)`;
        g.strokeRect(a, b, c - a, d - b);
      }
      if (Array.isArray(s.path) && s.path.length > 1) {
        g.beginPath();
        s.path.forEach((p, k) => { const [u, w] = this._toPx(p[0], p[1]); k ? g.lineTo(u, w) : g.moveTo(u, w); });
        g.closePath();   // a closed circuit: the last lane comes back along the west edge to the first
        g.setLineDash([8 * px, 5 * px]);
        g.lineWidth = Math.max(2.5, 2.2 * px); g.strokeStyle = "rgba(16,20,24,.22)"; g.stroke();
        g.lineWidth = Math.max(1.5, 1.2 * px); g.strokeStyle = `rgba(${rgb},.7)`; g.stroke();
        g.setLineDash([]);
      }
      if (Array.isArray(s.area) && s.area.length === 4 && s.name && !CLEAN) {   // the clean look labels the squads in the air instead
        const k = live.has(s.id) && live.get(s.id).flying != null ? live.get(s.id).flying : s.n;
        const text = k == null ? s.name : `${s.name} · ${k} drone${k === 1 ? "" : "s"}`;
        const fs = Math.round(11 * px);
        g.font = `600 ${fs}px Inter, system-ui, sans-serif`; g.textAlign = "right"; g.textBaseline = "middle";
        const tw = g.measureText(text).width, padX = 0.45 * fs, padY = 0.32 * fs;
        // The plate's place along the edge, from its widest text, so it stays put as the count changes.
        const wide = (g.measureText(`${s.name} · 00 drones`).width + 2 * padX) / px, high = (fs + 2 * padY) / px;
        const [u, w] = this._toPx(s.area[0] - 8, this._squadLabelY(s.area, s.name, wide, high));
        g.fillStyle = "rgba(16,20,24,.6)";
        g.beginPath(); g.roundRect(u - tw - 2 * padX, w - fs / 2 - padY, tw + 2 * padX, fs + 2 * padY, 0.35 * fs); g.fill();
        g.fillStyle = `rgba(${rgb},1)`; g.fillText(text, u - padX, w + 0.04 * fs);
      }
    }
    g.restore();
  }

  // Where (y, metres north) a squad's name plate sits just west of its area: the trees stand over the ground
  // overlay and would hide it, so the stretch of the edge with the fewest trees under a plate wide x high metres,
  // nearest halfway up on a tie. Worked out once per area, so the plate never moves.
  _squadLabelY(area, name, wide, high) {
    const key = `${area.join(",")}|${name}`, memo = this.squadLabelAt || (this.squadLabelAt = new Map());
    if (memo.has(key)) return memo.get(key);
    const [x0, y0, , y1] = area, mid = (y0 + y1) / 2, reach = high / 2 + 5;   // 5 m: about a crown's radius
    const xy = this.forestTrees ? this.forestTrees.xy : [];
    const near = xy.filter((p) => p[0] > x0 - 8 - wide - 5 && p[0] < x0 - 3 && p[1] > y0 - reach && p[1] < y1 + reach);
    let best = mid, fewest = Infinity;
    const span = Math.max(0, (y1 - y0) / 2 - high / 2 - 4);
    for (let d = 0; d <= span; d += 4) {   // outwards from halfway up, so a tie keeps the plate nearest the middle
      for (const y of d ? [mid - d, mid + d] : [mid]) {
        let k = 0;
        for (const p of near) if (Math.abs(p[1] - y) < reach) k++;
        if (k < fewest) { fewest = k; best = y; }
      }
    }
    memo.set(key, best);
    return best;
  }

  // The lightning strikes the run was given (hello.scenario.strikes), each a small bolt where it struck: the ones
  // the request picked (cue) bright, with the time of the strike; the rest dim, and those outside every patrol
  // area (area "-") dimmer still. On a squad run, a picked strike in no area a squad flies (the plan's
  // "uncovered") is drawn bright but hollow: asked for, and no drone is looking there. The times go on after
  // every bolt, each on the first side of its bolt (right, left, above or below right) clear of the others.
  _drawStrikes(g) {
    const px = this.overlay.px;
    const flown = this.squads ? new Set(this.squads.map((q) => q.code)) : null;
    const taken = [], timed = [];   // boxes [u0, v0, u1, v1] the bolts and times cover; the times to write
    g.save();
    g.lineJoin = "round";
    for (const s of this.strikes) {
      if (!Number.isFinite(s.x) || !Number.isFinite(s.y)) continue;
      const cue = !!s.cue, open = cue && !!flown && !flown.has(s.area);
      const [u, w] = this._toPx(s.x, s.y), a = cue ? 1 : s.area === "-" ? 0.3 : 0.6;
      const h = (cue ? 8 : 5.5) * px;   // half the bolt's height: 16 m tall when cued, 11 m otherwise
      g.beginPath();
      BOLT.forEach(([bx, by], k) => (k ? g.lineTo(u + bx * h, w + by * h) : g.moveTo(u + bx * h, w + by * h)));
      g.closePath();
      g.lineWidth = Math.max(1.5, (cue ? 0.8 : 0.6) * px); g.strokeStyle = `rgba(20,20,24,${(0.75 * a).toFixed(3)})`; g.stroke();
      if (open) { g.lineWidth = Math.max(1, 0.4 * px); g.strokeStyle = "rgba(255,226,92,1)"; g.stroke(); }
      else { g.fillStyle = cue ? "rgba(255,226,92,1)" : `rgba(250,242,212,${a})`; g.fill(); }
      taken.push([u - 0.5 * h, w - h, u + 0.5 * h, w + h]);
      if (cue && s.time) timed.push([u, w, h, String(s.time)]);
    }
    const fs = Math.round(9 * px);
    g.font = `600 ${fs}px Inter, system-ui, sans-serif`; g.textAlign = "left"; g.textBaseline = "middle";
    g.lineWidth = Math.max(2, 0.9 * px);
    const clear = (b) => !taken.some((q) => b[0] < q[2] && b[2] > q[0] && b[1] < q[3] && b[3] > q[1]);
    for (const [u, w, h, text] of timed) {
      const tw = g.measureText(text).width, gap = 0.55 * h + 2 * px;
      const spots = [[u + gap, w], [u - gap - tw, w], [u + gap, w - 0.9 * h], [u + gap, w + 0.9 * h]];
      const boxAt = ([x, y]) => [x - px, y - fs / 2 - px, x + tw + px, y + fs / 2 + px];
      const [x, y] = spots.find((p) => clear(boxAt(p))) || spots[0];
      taken.push(boxAt([x, y]));
      g.strokeStyle = "rgba(20,20,24,.7)"; g.strokeText(text, x, y);
      g.fillStyle = "rgba(255,240,170,1)"; g.fillText(text, x, y);
    }
    g.restore();
  }

  _buildPath(hello) {
    if (this.squads) return;   // squad runs: hello.path is the first squad's circuit only; the overlay draws every squad's lanes
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
    // Rings under the drones, where each one's slot in the formation is: amber, or on a squad run each drone's
    // squad colour (white in transit between squads), set per drone over a white material.
    const slots = inst(new THREE.TorusGeometry(0.9, 0.07, 6, 28),
      new THREE.MeshBasicMaterial({ color: this.squads ? 0xffffff : 0xffb347, transparent: true, opacity: 0.75 }), n);
    if (this.squads) for (let i = 0; i < n; i++) slots.setColorAt(i, this._squadColour(this.squadOf ? this.squadOf[i] : -1));
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
    this.navLights = this.trails = null;
    if (CLEAN) this._buildLightsAndTrails(n, model, R, group);
    this.yaw = new Float32Array(n);
    this.camFootR = hello.sensor ? Math.tan((hello.sensor.fov_deg * Math.PI) / 360) : 0;
    this.resize();
  }

  // Clean look: position lights on each drone, red to port and green to starboard at its front arms and a white
  // strobe underneath blinking on the view's clock, bright enough for the bloom to show them from afar; and each
  // drone's trail, a ribbon a few pixels wide through its positions in the run's latest frames (_drawTrails).
  _buildLightsAndTrails(n, model, R, group) {
    const hubs = this.motorOffsets;   // [x, z] in the drone's frame: x forward, z to starboard
    const pick = (sgn) => hubs.filter(([, z]) => Math.sign(z) === sgn).sort((a, b) => b[0] - a[0])[0] || [0, sgn * R];
    const port = pick(-1), star = pick(1), y = this.rotorY - 0.02 * R;
    const lights = new THREE.InstancedMesh(new THREE.SphereGeometry((model ? 0.055 : 0.08) * R, 8, 6),
      new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }), n * 3);
    lights.instanceMatrix.setUsage(THREE.DynamicDrawUsage); lights.frustumCulled = false;
    const red = new THREE.Color(7, 0.3, 0.2), green = new THREE.Color(0.25, 7, 1.1), white = new THREE.Color(9, 9, 9);
    for (let i = 0; i < n; i++) { lights.setColorAt(i * 3, red); lights.setColorAt(i * 3 + 1, green); lights.setColorAt(i * 3 + 2, white); }
    group.add(lights);
    this.navLights = { lights, at: [[port[0], y, port[1]], [star[0], y, star[1]], [0, -0.28 * R, 0]] };
    const P = TRAIL_PTS, cnt = n * P * 2, g = new THREE.BufferGeometry();
    const attr = (k) => new THREE.BufferAttribute(new Float32Array(cnt * k), k).setUsage(THREE.DynamicDrawUsage);
    for (const [k, w] of [["position", 3], ["aPrev", 3], ["aNext", 3], ["aCol", 3], ["aA", 1]]) g.setAttribute(k, attr(w));
    g.setAttribute("aSide", new THREE.BufferAttribute(Float32Array.from({ length: cnt }, (_, k) => (k % 2 ? 1 : -1)), 1));
    const idx = [];
    for (let i = 0; i < n; i++) for (let k = 0; k + 1 < P; k++) { const a = (i * P + k) * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
    g.setIndex(idx);
    const trails = new THREE.Mesh(g, new THREE.ShaderMaterial({
      uniforms: { uRes: { value: new THREE.Vector2(1920, 1080) }, uHalf: { value: TRAIL_PX / 2 } },
      vertexShader: `attribute vec3 aPrev; attribute vec3 aNext; attribute vec3 aCol; attribute float aA; attribute float aSide;
        uniform vec2 uRes; uniform float uHalf; varying vec3 vCol; varying float vA;
        void main() {
          mat4 m = projectionMatrix * modelViewMatrix;
          vec4 c = m * vec4(position, 1.0), p = m * vec4(aPrev, 1.0), q = m * vec4(aNext, 1.0);
          vec2 d = (q.xy / q.w - p.xy / p.w) * uRes;
          d = length(d) > 1e-4 ? normalize(d) : vec2(1.0, 0.0);
          c.xy += vec2(-d.y, d.x) * aSide * uHalf * 2.0 / uRes * c.w;
          gl_Position = c; vCol = aCol; vA = aA; }`,
      fragmentShader: "varying vec3 vCol; varying float vA; void main() { gl_FragColor = vec4(vCol, vA); }",
      transparent: true, depthWrite: false }));
    trails.frustumCulled = false; trails.renderOrder = 7;
    group.add(trails);
    this.trails = trails;
  }

  // Each flying drone's trail: its positions in the frames of the last TRAIL_S simulated seconds, then where it is
  // drawn now, fading out towards the oldest. Unused points sit on the newest one, invisible.
  _drawTrails(now, f, sqOf) {
    const P = TRAIL_PTS, g = this.trails.geometry, at = this.dronePos;
    const pos = g.attributes.position.array, prev = g.attributes.aPrev.array, next = g.attributes.aNext.array;
    const col = g.attributes.aCol.array, al = g.attributes.aA.array;
    const hist = this.trailHist.filter((h) => h.t < now - 0.02 && h.t > now - TRAIL_S);
    const pts = [];
    for (let i = 0; i < this.n; i++) {
      pts.length = 0;
      if (f.s[i] === 0) {
        for (const h of hist) if (h.s[i] === 0 && h.p[i]) { const q = h.p[i]; pts.push([q[0], q[2], -q[1], (now - h.t) / TRAIL_S]); }
        pts.push([at[i][0], at[i][1], at[i][2], 0]);
      }
      const first = Math.max(0, pts.length - P), m = pts.length - first;
      const c = sqOf ? this._squadColour(sqOf[i]) : STATUS_COLOR[0];
      for (let k = 0; k < P; k++) {
        const kk = Math.min(k, Math.max(0, m - 1));
        const cur = pts[first + kk], pv = pts[first + Math.max(0, kk - 1)], nx = pts[first + Math.min(m - 1, kk + 1)];
        const a = m && k < m ? 0.6 * Math.pow(1 - cur[3], 1.5) : 0;
        for (let v = 0; v < 2; v++) {
          const o = (i * P + k) * 2 + v;
          if (!m) { pos.fill(0, o * 3, o * 3 + 3); al[o] = 0; continue; }
          pos[o * 3] = cur[0]; pos[o * 3 + 1] = cur[1]; pos[o * 3 + 2] = cur[2];
          prev[o * 3] = pv[0]; prev[o * 3 + 1] = pv[1]; prev[o * 3 + 2] = pv[2];
          next[o * 3] = nx[0]; next[o * 3 + 1] = nx[1]; next[o * 3 + 2] = nx[2];
          col[o * 3] = c.r; col[o * 3 + 1] = c.g; col[o * 3 + 2] = c.b;
          al[o] = a;
        }
      }
    }
    for (const k of ["position", "aPrev", "aNext", "aCol", "aA"]) g.attributes[k].needsUpdate = true;
    this.trails.visible = this.mode !== "close" && this.mode !== "ride";   // among the drones they would cross the picture
  }

  // Clean look, squad runs: each squad's name and drones flying, over the middle of its flying drones.
  _buildSquadLabels() {
    this.squadLabels = new Map();
    for (const s of this.squads) this._squadLabel(s);
  }
  _squadLabel(s) {
    const st = this.squadStyles.get(s.id) || squadStyle(s.code, s.id), lab = this._label(String(s.name || s.code), `rgb(${st.rgb})`, "squad");
    lab.visible = false;
    this.world.add(lab);
    const rec = { lab, text: "" };
    this.squadLabels.set(s.id, rec);
    return rec;
  }
  _placeSquadLabels(sqOf, f) {
    const at = this.dronePos;
    for (const s of this.squads) {
      const rec = this.squadLabels.get(s.id) || this._squadLabel(s);
      let k = 0, x = 0, z = 0, top = -Infinity;
      for (let i = 0; i < at.length; i++) if (f.s[i] === 0 && sqOf[i] === s.id) { k++; x += at[i][0]; z += at[i][2]; top = Math.max(top, at[i][1]); }
      rec.lab.visible = k > 0;
      if (!k) continue;
      rec.lab.position.set(x / k, top + 5, z / k);
      const text = `${s.name || s.code} · ${k} drone${k === 1 ? "" : "s"}`;
      if (text !== rec.text) { rec.text = text; rec.lab.userData.setText(text); }
    }
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
    if (CLEAN) {   // the drones' trails are drawn from these, the run's own positions
      this.trailHist.push({ t: f.t, p: f.p, s: f.s });
      while (this.trailHist.length > TRAIL_PTS + 8) this.trailHist.shift();
    }
    if (this.squads) this.squadLive = f.squads || null;
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
      if (tg && !tg.person) tg.label.userData.setText(`Hotspot ${e.target + 1} found at ${Number(e.t).toFixed(1)} s`);
    }
    if (this.squads && e.kind === "squad_changed") this._updateSquads(e.squads);
    if (this.squads && e.kind === "squad_joined") this._updateSquads([e.squad_now]);
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
    this.modeAt = this.vt; this.moveDir = null; this.lowHd = null;   // the crane, dolly and low views run from here
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
    c.left = -half; c.right = half; c.top = half; c.bottom = -half; c.far = (CLEAN ? this.shadowFar : 900) * Math.max(1, this.zoom);
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
    if (this.drones) this.drones.markers.material.uniforms.uSize.value = Math.max(11, Math.min(18, r.height / 70)) * pr * (CLEAN ? 1.6 : 1);
    if (this.trails) {
      this.trails.material.uniforms.uRes.value.set(r.width * pr, r.height * pr);
      this.trails.material.uniforms.uHalf.value = (TRAIL_PX / 2) * (r.height * pr) / 1080;
    }
  }

  // One view step: dtView in view seconds (camera, spinning rotors), dtSim in simulated seconds
  // (particles), simNow the simulated time to draw drones at (null: interpolate on the wall clock).
  _step(dtView, dtSim, simNow) {
    this.vt += dtView;
    const cut = this.cut;
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
    this._drawPeople();
    if (this.squads) this._setFog(FOG_DENSITY * Math.min(1, 520 / Math.max(1, this.camera.position.distanceTo(this.focus))));
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
    if (CLEAN) { this._frameShift(dtView, cut); this._declutter(dtView); }
    if (this.fx) this.fx.render(); else this.renderer.render(this.scene, this.camera);
  }

  // Clean look: with a card up on the right, the picture's centre (what the camera looks at: the fire, the swarm)
  // moves left into the open part of the frame, and a little up, clear of the toasts; it glides when a card comes or
  // goes, and moves at once on a cut. Only the framing moves (the camera's view offset); nothing in the scene does.
  _frameShift(dt, cut) {
    const cards = document.querySelector("#cine .cine-cards");
    const W = this.canvas.clientWidth, H = this.canvas.clientHeight;
    let wx = 0;
    if (cards && cards.children.length && cards.offsetParent !== null) {
      const r = cards.getBoundingClientRect(), base = this.canvas.getBoundingClientRect();
      if (r.width > 0 && r.left - base.left > W * 0.4) wx = Math.min(W * 0.2, (base.right - r.left) / 2);
    }
    const wy = H * 0.05, k = cut || this.shiftX == null ? 1 : 1 - Math.exp(-dt * 1.2);
    this.shiftX = (this.shiftX ?? 0) + (wx - (this.shiftX ?? 0)) * k;
    this.shiftY = (this.shiftY ?? 0) + (wy - (this.shiftY ?? 0)) * k;
    const v = this.camera.view;
    if (!v || Math.abs(v.offsetX - this.shiftX) > 0.05 || Math.abs(v.offsetY - this.shiftY) > 0.05 || v.fullWidth !== W || v.fullHeight !== H) {
      this.camera.setViewOffset(W, H, this.shiftX, this.shiftY, W, H);
    }
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
    const m = new THREE.Object3D(), part = new THREE.Object3D();
    const mPos = markers.geometry.attributes.position, mCol = markers.geometry.attributes.aColor, mAlpha = markers.geometry.attributes.aAlpha;
    // Pixels per metre at distance 1, to decide when a drone is big enough on screen to drop its marker.
    const pxPerM = this.canvas.clientHeight / (2 * Math.tan((this.camera.fov * Math.PI) / 360));
    // Squad runs: each drone's squad (f.sq, -1 in transit) colours its slot ring and, while it flies, its marker;
    // with squadFocus set, only that squad's flying drones are framed (all of them if it has none flying).
    const sqOf = this.squads ? f.sq || this.squadOf : null, focusId = this._focusSquadId();
    const pts = [], others = [], feet = this.footprints = [], at = this.dronePos = [];
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
      if (this.navLights) {   // on while the rotors turn; the strobe flashes briefly about once a second
        const on = st === 0 || st === 3;
        this.navLights.at.forEach((o, k) => {
          const lit = on && (k < 2 || (t * 0.85 + i * 0.37) % 1 < 0.07);
          part.position.set(o[0], o[1], o[2]); part.rotation.set(0, 0, 0); part.scale.setScalar(lit ? 1 : 1e-4); part.updateMatrix();
          this.navLights.lights.setMatrixAt(i * 3 + k, m.matrix.clone().multiply(part.matrix));
        });
      }
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
      const mc = sqOf && st === 0 ? this._squadColour(sqOf[i]) : col;   // a failed or retired drone keeps its status colour
      mCol.setXYZ(i, mc.r, mc.g, mc.b);
      mAlpha.setX(i, 1 - smooth(16, 30, (2.6 * R * size * pxPerM) / Math.max(1, dist)));
      const slot = this.mode === "ride" || this.mode === "close" ? null : f.slots[i];   // close in, the slot rings would fill the frame
      if (slot) {
        m.position.copy(toV(slot)); m.rotation.set(Math.PI / 2, 0, 0); m.scale.setScalar(1); m.updateMatrix();
        slots.setMatrixAt(i, m.matrix);
        if (sqOf) slots.setColorAt(i, this._squadColour(sqOf[i]));
      } else { m.position.set(0, -1e4, 0); m.scale.setScalar(1e-4); m.updateMatrix(); slots.setMatrixAt(i, m.matrix); }
      if (st === 0) {
        const g = this.groundAt(x, y), r = Math.max(0.01, (z - g) * this.camFootR);
        m.position.set(x, g + 0.3, -y); m.rotation.set(-Math.PI / 2, 0, 0); m.scale.set(r, r, 1); m.updateMatrix();
        fp.setMatrixAt(i, m.matrix); fpRing.setMatrixAt(i, m.matrix);
        feet.push([x, y, r]);
        (focusId == null || sqOf[i] === focusId ? pts : others).push([x, z, -y]);
      } else { m.position.set(0, -1e4, 0); m.scale.setScalar(1e-4); m.updateMatrix(); fp.setMatrixAt(i, m.matrix); fpRing.setMatrixAt(i, m.matrix); }
    }
    for (const im of [...bodies, ...blades.map((b) => b.im), leds, rotors, slots, fp, fpRing]) im.instanceMatrix.needsUpdate = true;
    if (this.terrain && this.hello.sensor) this.overlayDirty = true;   // the footprints move with the drones
    leds.instanceColor.needsUpdate = true;
    if (sqOf && slots.instanceColor) slots.instanceColor.needsUpdate = true;
    mPos.needsUpdate = mCol.needsUpdate = mAlpha.needsUpdate = true;
    if (this.navLights) this.navLights.lights.instanceMatrix.needsUpdate = true;
    if (this.trails) this._drawTrails(simNow ?? this.simLatest, f, sqOf);
    if (this.squadLabels) this._placeSquadLabels(sqOf, f);
    this.droneGroup.visible = true;
    this._frameSwarm(pts.length ? pts : others);
  }

  // The swarm the follow, orbit and chase views frame: the centre and spread of these drones ([x, z, -y], flying),
  // and how far they reach across and along the line of sight (the crown cutaway).
  _frameSwarm(pts) {
    const k = pts.length;
    if (!k) return;
    const c = new THREE.Vector3();
    let extent = 0;
    for (const p of pts) c.add(new THREE.Vector3(p[0], p[1], p[2]));
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

  // squadFocus changed: frame the new squad from where the drones were last drawn, so a programmed cut that
  // follows (setMode(mode, true)) lands on it rather than gliding over from the last one.
  _refocus() {
    const f = this.lastFrame, at = this.dronePos;
    if (this.hello && this.squads && !f) {   // set before the first frame (the recorder): start on that squad
      this._squadStartView(); this.controls.target.copy(this.focus); this.setMode(this.mode);
      return;
    }
    if (!this.hello || !f || !at || !at.length) return;
    const sqOf = this.squads ? f.sq || this.squadOf : null, focusId = this._focusSquadId(), pts = [], others = [];
    at.forEach((p, i) => { if (f.s[i] === 0) (focusId == null || sqOf[i] === focusId ? pts : others).push(p); });
    this._frameSwarm(pts.length ? pts : others);
    if (this.squads) this.ride = null;   // the ride-along and close-up views pick a drone of the new squad
  }

  _drawFire(dt, t) {
    // Embers and smoke at every found hotspot.
    if (!dt) return;
    const ember = new THREE.Color(), smokeCol = new THREE.Color();
    const chance = (rate) => { const e = rate * dt; return Math.floor(e) + (Math.random() < e - Math.floor(e) ? 1 : 0); };
    for (const tg of this.targets || []) {
      // A hotspot is only a scorched patch until a camera finds it; then it glows, smokes and gets a ring.
      if (tg.person) continue;   // drawn by _drawPeople
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
      const hd = this._heading(), dist = (e * 1.2 + 60) * z;
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
      const hd = this._heading();
      const back = (e * 1.1 + 35) * z;
      want = new THREE.Vector3(f.x - Math.cos(hd) * back, f.y + (10 + e * 0.28) * z, f.z + Math.sin(hd) * back);
    } else if (this.mode === "top" && !this.userMoved) {
      want = new THREE.Vector3(f.x, f.y + Math.max(150, e * 2.6 + 70) * z, f.z + 1);
    } else if ((this.mode === "crane" || this.mode === "dolly") && !this.userMoved) {
      // Crane: from low on the side the camera is on, rising and pulling back over about 16 s while turning slowly.
      // Dolly: a slow sideways track past the swarm at the follow view's height, keeping it in frame.
      const tau = this.vt - (this.modeAt ?? this.vt);
      if (!this.moveDir) {
        const d = this.camera.position.clone().sub(f); d.y = 0;
        if (d.lengthSq() < 1) d.set(-0.6, 0, 0.8);
        this.moveDir = d.normalize();
      }
      const d = this.moveDir;
      if (this.mode === "crane") {
        const u = smooth(0, 16, tau), ang = Math.atan2(d.z, d.x) + tau * 0.014;
        const el = ((6 + 28 * u) * Math.PI) / 180, dist = (e * 1.45 + 75) * z * (0.78 + 0.4 * u);
        want = new THREE.Vector3(f.x + Math.cos(ang) * Math.cos(el) * dist, f.y + Math.sin(el) * dist, f.z + Math.sin(ang) * Math.cos(el) * dist);
        aim = dist * 0.1 * (1 - u);
      } else {
        const dist = (e * 1.35 + 70) * z, side = new THREE.Vector3(-d.z, 0, d.x);
        want = f.clone().addScaledVector(d, dist * 0.92).addScaledVector(side, dist * 0.04 * (tau - 6)).add(new THREE.Vector3(0, dist * 0.32, 0));
        aim = dist * 0.13;
      }
    } else if (this.mode === "low" && !this.userMoved && this.lastFrame) {
      // Low pass: just over the treetops behind the framed squad (squadFocus) or swarm, looking past it the way it
      // flies, drifting slowly from one side to the other; the heading is smoothed so a lane's end turns it gently.
      const tau = this.vt - (this.modeAt ?? this.vt), hd = this._heading();
      this.lowHd = this.lowHd == null ? hd : this.lowHd + Math.atan2(Math.sin(hd - this.lowHd), Math.cos(hd - this.lowHd)) * (1 - Math.exp(-dt * 0.5));
      const h = this.lowHd, back = (e * 0.9 + 34) * z, lat = back * 0.4 * Math.sin(tau * 0.08 - 0.7);
      const fwd = new THREE.Vector3(Math.cos(h), 0, -Math.sin(h)), right = new THREE.Vector3(Math.sin(h), 0, Math.cos(h));
      const gx = f.x - fwd.x * back + right.x * lat, gz = f.z - fwd.z * back + right.z * lat;
      const ground = this.sceneryHeight ? this.sceneryHeight(gx, -gz) : 0, canopy = this._canopyAt(gx, -gz);
      want = new THREE.Vector3(gx, Math.max(f.y - 4, ground + canopy + 9), gz);
      aim = -3;
    }
    if (want) this.camera.position.lerp(want, snap ? 1 : this.film ? Math.min(1, dt * 2.5) : k);
    // Never under the ground (on a run with hills the camera can swing behind a slope).
    const cp = this.camera.position, floor = (this.sceneryHeight ? this.sceneryHeight(cp.x, -cp.z) : 0) + 12;
    if (cp.y < floor) cp.y = floor;
    this.aim = this.aim == null ? aim : this.aim + (aim - this.aim) * k;
    this.controls.target.set(f.x, f.y + this.aim, f.z);
  }

  // The tallest tree crown within a cell of (x, y), simulated metres north and east (0 off the run's land cover).
  _canopyAt(x, y) {
    const lc = this.landcover;
    if (!lc || !lc.canopy_m) return 0;
    const ix = Math.round((x - lc.x0) / lc.cell_m), iy = Math.round((y - lc.y0) / lc.cell_m);
    let h = 0;
    for (let j = iy - 1; j <= iy + 1; j++) for (let i = ix - 1; i <= ix + 1; i++) if (i >= 0 && j >= 0 && i < lc.nx && j < lc.ny) h = Math.max(h, lc.canopy_m[j * lc.nx + i] || 0);
    return h;
  }

  // The way the framed drones fly (radians, 0 east, anticlockwise): the focused squad's, else the run's.
  _heading() {
    const f = this.lastFrame, id = this._focusSquadId();
    const s = id != null && Array.isArray(f.squads) ? f.squads.find((q) => q.id === id) : null;
    return s && s.heading != null ? s.heading : f.heading ?? 0;
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
    // With squadFocus on a squad run: a drone of that squad (any flying drone if none of it flies).
    const focusId = this._focusSquadId(), sqOf = focusId == null ? null : f.sq || this.squadOf;
    let mine = (i) => focusId == null || sqOf[i] === focusId;
    if (focusId != null && !at.some((_, i) => f.s[i] === 0 && mine(i))) mine = () => true;
    let r = this.ride;
    if (!r || f.s[r.i] !== 0 || !mine(r.i)) {
      let best = -1, bd = Infinity;
      at.forEach((p, i) => {
        const d = i === this.rideDrone ? -1 : Math.hypot(p[0] - this.swarmCentre.x, p[2] - this.swarmCentre.z);
        if (f.s[i] === 0 && mine(i) && d < bd) { bd = d; best = i; }
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
