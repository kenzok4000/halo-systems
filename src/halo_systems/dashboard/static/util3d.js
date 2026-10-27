// Halo Systems dashboard: small helpers shared by the 3D view's modules (geometry, canvas textures, noise).
import * as THREE from "three";

// The clean film look (?clean=1, the recorder's --clean; see scene3d.js). Off unless asked for: absent, empty, 0, false, no or off.
export const CLEAN = (() => { const v = new URLSearchParams(location.search).get("clean"); return v !== null && !["", "0", "false", "no", "off"].includes(v.trim().toLowerCase()); })();

export function mergeGeometries(geos) {
  // Concatenate non-indexed copies (position and normal only): enough for the solid parts drawn here.
  const parts = geos.map((g) => (g.index ? g.toNonIndexed() : g));
  const count = parts.reduce((s, g) => s + g.attributes.position.count, 0);
  const pos = new Float32Array(count * 3), nor = new Float32Array(count * 3);
  let o = 0;
  for (const g of parts) {
    pos.set(g.attributes.position.array, o * 3);
    nor.set(g.attributes.normal.array, o * 3);
    o += g.attributes.position.count;
  }
  const out = new THREE.BufferGeometry();
  out.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  out.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
  return out;
}

export function canvasTexture(w, h, draw, srgb = true) {
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  draw(cv.getContext("2d"), w, h);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  tex.anisotropy = 4;
  return tex;
}

// A small deterministic hash, so scenery, tree shapes and colours do not change between frames or runs.
export const hash = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
// Smooth value noise in [0, 1], and a few octaves of it.
export function noise2(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
export const fbm = (x, y) => 0.55 * noise2(x, y) + 0.3 * noise2(x * 2.1 + 17, y * 2.1 + 5) + 0.15 * noise2(x * 4.3 + 3, y * 4.3 + 11);
export const ridge = (x, y) => { const n = 1 - Math.abs(2 * noise2(x, y) - 1); return n * n; };
export const smooth = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

// A conifer one unit tall and about two units across at the base: six whorls of branches with ragged,
// drooping edges, lighter at the tips and dark underneath, on a short trunk. The vertex colours are
// shading only; each tree's own colour comes from its instance colour, which multiplies them.
// Fewer whorls and branches make the cheaper versions drawn further away.
export function conifer(TIERS = 6, SEG = 11) {
  const pos = [], nor = [], col = [];
  const n = new THREE.Vector3(), a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const tri = (p, q, r, cp, cq, cr, soften) => {
    a.fromArray(p); b.fromArray(q); c.fromArray(r);
    n.subVectors(c, b).cross(new THREE.Vector3().subVectors(a, b)).normalize();
    for (const [v, cc] of [[p, cp], [q, cq], [r, cr]]) {
      pos.push(...v); col.push(cc, cc, cc);
      // Foliage scatters light: bend each normal toward "outward and up", so the facets read soft.
      const o = new THREE.Vector3(v[0], 0, v[2]).normalize().multiplyScalar(0.6).add(new THREE.Vector3(0, 0.8, 0));
      const m = n.clone().multiplyScalar(1 - soften).addScaledVector(o.normalize(), soften).normalize();
      nor.push(m.x, m.y, m.z);
    }
  };
  for (let t = 0; t < TIERS; t++) {
    const f = t / (TIERS - 1), y0 = 0.15 + 0.64 * f, h = 0.3 - 0.1 * f, r = 1 - 0.76 * f, turn = hash(t, 3) * 6.283;
    const apex = [0, y0 + h, 0], under = [0, y0 + 0.07, 0];
    const ring = [];
    for (let j = 0; j < SEG; j++) {
      const ang = turn + (j / SEG) * 6.283, tip = j % 2 === 0;   // alternate branch tips and the gaps between them
      const rr = r * (tip ? 0.9 + 0.22 * hash(t, j) : 0.62 + 0.1 * hash(j, t));
      ring.push([Math.cos(ang) * rr, y0 - (tip ? 0.05 * hash(j + 7, t) : -0.02), Math.sin(ang) * rr]);
    }
    for (let j = 0; j < SEG; j++) {
      const p = ring[j], q = ring[(j + 1) % SEG];
      tri(apex, q, p, 1.12, 0.78, 0.78, 0.55);   // upper surface, lit
      tri(under, p, q, 0.42, 0.5, 0.5, 0.2);      // underside, in its own shade
    }
  }
  // Trunk: five sides, mostly hidden under the lowest whorl.
  for (let j = 0; j < 5; j++) {
    const a0 = (j / 5) * 6.283, a1 = ((j + 1) / 5) * 6.283, R = 0.07, rt = 0.045;
    const p0 = [Math.cos(a0) * R, 0, Math.sin(a0) * R], p1 = [Math.cos(a1) * R, 0, Math.sin(a1) * R];
    const q0 = [Math.cos(a0) * rt, 0.3, Math.sin(a0) * rt], q1 = [Math.cos(a1) * rt, 0.3, Math.sin(a1) * rt];
    tri(p0, q0, p1, 0.55, 0.55, 0.55, 0); tri(p1, q0, q1, 0.55, 0.55, 0.55, 0);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  return g;
}

// The cheapest conifer, for trees a few pixels tall: two stacked open cones, with the same shading colours
// (lit above, so an instance colour gives the same tree as the detailed one from afar).
export function coniferFar() {
  const g = mergeGeometries([new THREE.ConeGeometry(1, 0.62, 7, 1, true).translate(0, 0.31, 0),
    new THREE.ConeGeometry(0.74, 0.55, 7, 1, true).translate(0, 0.66, 0)]);
  g.computeVertexNormals();
  const n = g.attributes.position.count, col = new Float32Array(n * 3);
  for (let k = 0; k < n; k++) col.fill(0.62 + 0.4 * g.attributes.position.getY(k), k * 3, k * 3 + 3);
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  return g;
}

// A bush about one unit across: a few lumpy blobs, shaded as one soft mass (its normals point out from the
// bush's middle, so no facet edges show), dark underneath and lighter on top. One blob for the cheap
// version drawn further away. The vertex colours are shading only, as for the conifers.
export function shrub(blobs = 4) {
  const parts = [];
  for (let b = 0; b < blobs; b++) {
    const a = b * 2.4, r = b ? 0.55 + 0.15 * hash(b, 1) : 0.8, d = b ? 0.45 : 0;
    parts.push(new THREE.IcosahedronGeometry(r, 0).translate(Math.cos(a) * d, (b ? -0.12 : 0.05) + 0.1 * hash(b, 2), Math.sin(a) * d));
  }
  const g = mergeGeometries(parts), p = g.attributes.position, nor = g.attributes.normal;
  const col = new Float32Array(p.count * 3), v = new THREE.Vector3();
  for (let k = 0; k < p.count; k++) {
    v.fromBufferAttribute(p, k);
    // Lumps: push each corner in or out a little (the same corner, shared by its faces, moves the same way).
    const s = 1 + 0.16 * (hash(v.x * 9.1 + v.z, v.y * 7.3) - 0.5);
    p.setXYZ(k, v.x * s, v.y * s, v.z * s);
    v.set(v.x, v.y + 0.35, v.z).normalize();
    nor.setXYZ(k, v.x, v.y, v.z);
    col.fill(0.5 + 0.6 * Math.max(0, Math.min(1, (p.getY(k) + 0.5) / 1.1)), k * 3, k * 3 + 3);
  }
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  return g;
}

// Mottled stone: darker and lighter patches and darker lower down, as vertex colours on a rock's geometry
// (local units; seed varies the pattern between rocks).
export function stoneColors(geo, seed = 0) {
  const p = geo.attributes.position, col = new Float32Array(p.count * 3);
  let lo = Infinity, hi = -Infinity;
  for (let k = 0; k < p.count; k++) { lo = Math.min(lo, p.getY(k)); hi = Math.max(hi, p.getY(k)); }
  for (let k = 0; k < p.count; k++) {
    const x = p.getX(k), y = p.getY(k), z = p.getZ(k);
    const n = fbm(x * 0.35 + seed * 3.1 + z * 0.21, z * 0.35 - y * 0.27 + seed);
    const c = (0.72 + 0.5 * n) * (0.78 + 0.22 * (y - lo) / Math.max(1e-6, hi - lo));
    col.set([c, c * 0.985, c * 0.96], k * 3);
  }
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  return geo;
}
