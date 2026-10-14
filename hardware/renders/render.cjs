// Render the PNGs in this folder from ../exports/halo_scout.glb with headless Chromium.
//
//   node hardware/renders/render.cjs            (from the swarmlab folder)
//
// Needs Node 18+ with Playwright (NODE_PATH pointing at it) and a Chromium. It serves the
// swarmlab folder on a local port so the page can import the dashboard's
// vendored three.js and fetch the GLB.
const { chromium } = require("playwright");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "..");
const types = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".glb": "model/gltf-binary" };
const server = http.createServer((req, res) => {
  const p = path.join(root, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!p.startsWith(root) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "Content-Type": types[path.extname(p)] || "application/octet-stream" });
  fs.createReadStream(p).pipe(res);
});

(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  // CHROMIUM_PATH if set, else the sandbox path, else Playwright's own browser.
  const exe = [process.env.CHROMIUM_PATH, "/opt/pw-browsers/chromium"]
    .find((p) => p && fs.existsSync(p) && fs.statSync(p).isFile());
  const browser = await chromium.launch({ executablePath: exe,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => console.error("page error:", e.message));
  page.on("console", (m) => { if (m.type() === "error") console.error("console:", m.text()); });
  await page.goto(`http://127.0.0.1:${port}/hardware/renders/render.html`);
  await page.waitForFunction(() => window.ready === true, null, { timeout: 120000 });
  const views = await page.evaluate(() => window.views);
  for (const v of views) {
    await page.evaluate((name) => window.renderView(name), v);
    const out = path.join(__dirname, `${v}.png`);
    await page.locator("#wrap").screenshot({ path: out });
    console.log("wrote", path.relative(root, out), fs.statSync(out).size, "bytes");
  }
  await browser.close();
  server.close();
})();
