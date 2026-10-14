// Halo Systems dashboard: small helpers shared by the 3D view's modules (geometry, canvas textures, noise).
import * as THREE from "three";

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
