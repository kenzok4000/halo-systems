// Halo Systems dashboard: the 3D view's film look. The scene is drawn into a multisampled buffer in linear
// light; whatever is much brighter than sunlit ground (flames, embers, the drones' status lights, beacons)
// glows (bloom); the result is tone mapped once; then a light grade (contrast, a little warmth in the
// highlights and cool in the shadows) and a vignette. Looks only: it changes how a frame is drawn, never
// what is in it. ?fx=0 turns it off and the view draws straight to the screen as before.
import * as THREE from "three";
import { EffectComposer } from "/static/vendor/EffectComposer.js";
import { RenderPass } from "/static/vendor/RenderPass.js";
import { UnrealBloomPass } from "/static/vendor/UnrealBloomPass.js";
import { OutputPass } from "/static/vendor/OutputPass.js";
import { ShaderPass } from "/static/vendor/ShaderPass.js";

const GradeShader = {
  uniforms: { tDiffuse: { value: null }, uVignette: { value: 0.32 }, uContrast: { value: 1.06 }, uSaturation: { value: 1.08 },
    uWarm: { value: new THREE.Vector3(1.03, 1.0, 0.95) }, uCool: { value: new THREE.Vector3(0.96, 1.0, 1.04) }, uAspect: { value: 16 / 9 } },
  vertexShader: "varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
  fragmentShader: `uniform sampler2D tDiffuse; uniform float uVignette, uContrast, uSaturation, uAspect; uniform vec3 uWarm, uCool;
    varying vec2 vUv;
    void main() {
      vec3 c = texture2D(tDiffuse, vUv).rgb;   // display values, after tone mapping
      float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
      c = mix(vec3(l), c, uSaturation);
      c = (c - 0.5) * uContrast + 0.5;
      c *= mix(uCool, uWarm, smoothstep(0.15, 0.85, l));
      vec2 d = (vUv - 0.5) * vec2(uAspect, 1.0);
      c *= 1.0 - uVignette * smoothstep(0.35, 1.05, length(d) / sqrt(uAspect * uAspect * 0.25 + 0.25) * 1.18);
      gl_FragColor = vec4(clamp(c, 0.0, 1.0), 1.0);
    }`,
};

export class FilmLook {
  constructor(renderer, scene, camera) {
    this.renderer = renderer;
    const size = renderer.getDrawingBufferSize(new THREE.Vector2());
    const target = new THREE.WebGLRenderTarget(size.x, size.y, { type: THREE.HalfFloatType, samples: 4 });
    this.composer = new EffectComposer(renderer, target);
    this.render_ = new RenderPass(scene, camera);
    // Bloom only on what is several times brighter than sunlit ground: the threshold is in linear light.
    this.bloom = new UnrealBloomPass(new THREE.Vector2(size.x, size.y), 0.55, 0.45, 2.4);
    this.grade = new ShaderPass(GradeShader);
    this.composer.addPass(this.render_);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());   // tone mapping and the screen's colour space
    this.composer.addPass(this.grade);
  }
  setSize(w, h) {
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(w, h);
    this.grade.uniforms.uAspect.value = w / Math.max(1, h);
  }
  render() { this.composer.render(); }
}
