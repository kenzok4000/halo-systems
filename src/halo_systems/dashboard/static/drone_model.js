// Halo Systems dashboard: loads the scout drone's CAD model (hardware/cad/halo_scout.py, exported as
// glTF) and turns it into geometries the 3D view can draw as InstancedMeshes. The airframe is merged
// into one geometry per material; each propeller is kept apart and centred on its hub so it can spin.
// Everything comes out in the view's drone frame: +X forward, +Y up, origin at the centre of mass,
// multiplied by `scale`. Per drone the view draws the body with its drone matrix, and each prop with
// droneMatrix * translate(hub) * rotateY(spin * angle).
import * as THREE from "three";
import { GLTFLoader } from "/static/vendor/GLTFLoader.js";
import { mergeGeometries } from "/static/vendor/BufferGeometryUtils.js";

export const DRONE_MODEL_URL = "/static/models/halo_scout.glb";

// One geometry from parts that share a material. mergeGeometries wants the same attributes on every
// part and all parts indexed or none, so drop what not all of them carry.
function merge(geos) {
  const shared = Object.keys(geos[0].attributes).filter((k) => geos.every((g) => g.attributes[k]));
  const indexed = geos.every((g) => g.index);
  const parts = geos.map((g) => {
    const p = !indexed && g.index ? g.toNonIndexed() : g;
    for (const k of Object.keys(p.attributes)) if (!shared.includes(k)) p.deleteAttribute(k);
    p.morphAttributes = {};
    return p;
  });
  const out = parts.length === 1 ? parts[0] : mergeGeometries(parts);
  if (!out) throw new Error("drone model: parts could not be merged");
  out.computeBoundingBox();
  out.computeBoundingSphere();
  return out;
}

// GLTFLoader turns vertex colours on for parts that carry them; a merged geometry may have lost them,
// and a material asking for a missing colour attribute draws black.
function forInstancing(material, geometry) {
  material.vertexColors = !!geometry.attributes.color;
  return material;
}

export async function loadDroneModel(url = DRONE_MODEL_URL, scale = 1) {
  const gltf = await new GLTFLoader().loadAsync(url);
  const root = gltf.scene;
  root.updateMatrixWorld(true);
  // The glTF is metres, +Y up, +Z forward, +X left. A quarter turn about +Y takes its forward to the
  // view's +X and its left to -Z; with a uniform scale that is a similarity, so nothing is mirrored.
  const toView = new THREE.Matrix4().makeRotationY(Math.PI / 2).scale(new THREE.Vector3(scale, scale, scale));

  const entry = (map, key, make) => map.get(key) || map.set(key, make()).get(key);
  const bodyParts = new Map();   // material -> geometries in the view frame
  const propParts = new Map();   // prop node -> material -> geometries in the view frame
  root.traverse((o) => {
    if (!o.isMesh) return;
    const geo = o.geometry.clone().applyMatrix4(new THREE.Matrix4().multiplyMatrices(toView, o.matrixWorld));
    // Propellers are the nodes with a "spin" extra; a node with several materials is a group of meshes.
    let node = o;
    while (node && typeof node.userData.spin !== "string") node = node.parent;
    const byMat = node ? entry(propParts, node, () => new Map()) : bodyParts;
    entry(byMat, o.material, () => []).push(geo);
  });

  const body = [...bodyParts].map(([material, geos]) => {
    const geometry = merge(geos);
    return { name: material.name, geometry, material: forInstancing(material, geometry) };
  });

  // The hub is the prop node's origin (the CAD exporter puts it on the motor axis). The bounding-box
  // centre would not do: a three-blade prop is not symmetric about it.
  const props = [];
  let bladeR = 0;
  for (const [node, byMat] of propParts) {
    const hub = node.getWorldPosition(new THREE.Vector3()).applyMatrix4(toView);
    const spin = node.userData.spin === "ccw" ? 1 : -1;
    for (const [material, geos] of byMat) {
      const geometry = merge(geos.map((g) => g.translate(-hub.x, -hub.y, -hub.z)));
      const pos = geometry.attributes.position;
      for (let i = 0; i < pos.count; i++) bladeR = Math.max(bladeR, Math.hypot(pos.getX(i), pos.getZ(i)));
      props.push({ name: node.name, geometry, material: forInstancing(material, geometry), spin, hub });
    }
  }

  // Plan-view size with every prop drawn as its full disc, so it does not depend on the blades' angle.
  const box = new THREE.Box3();
  for (const b of body) box.union(b.geometry.boundingBox);
  for (const p of props) {
    box.expandByPoint(new THREE.Vector3(p.hub.x - bladeR, p.hub.y, p.hub.z - bladeR));
    box.expandByPoint(new THREE.Vector3(p.hub.x + bladeR, p.hub.y, p.hub.z + bladeR));
  }
  const span = Math.max(box.max.x - box.min.x, box.max.z - box.min.z);
  return { body, props, bladeDiameter: 2 * bladeR, span };
}
