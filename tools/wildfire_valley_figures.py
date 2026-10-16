"""Figures for the wildfire valley: one flight's map, the sweep by swarm size, drones against today's methods,
and which way the fires' heads run.

    PYTHONPATH=src python3 tools/wildfire_valley_figures.py --seed 3000 --n 16

Writes results/wildfire_valley/map.png (re-flies one flight; the run is deterministic, so it is the
sweep's flight) and, when the sweep's rows and summary.json exist, swarm_size.png and detection.png, and
direction.png when the rows score the briefs' head directions. ``--out`` picks another results folder;
``--brief`` adds the last brief's head arrows and cones to the map.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LightSource, ListedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyArrow, Patch, Rectangle, Wedge  # noqa: E402

OUT = Path("results/wildfire_valley")
COVER_COLOURS = ["#9a958c", "#d9cf8a", "#a8a564", "#4f6b3a", "#c9b79c"]     # rock, grass, scrub, forest, bare
SHELTER_MARK = {"open": "o", "canopy": "^", "overhang": "s"}
LEVEL_COLOURS = {3: "#ff2800", 4: "#aa00ff", 5: "#000000"}   # Japan's evacuation alert colours


def flight_map(seed: int, n: int, brief: bool = False) -> dict:
    from halo_systems.eval.runner import make_controller, make_supervisor
    from halo_systems.eval.scenarios import wildfire_valley
    from halo_systems.sim.body import DroneBody
    from halo_systems.sim.engine import Simulation
    sc = wildfire_valley(seed, n=n)
    body = DroneBody()
    sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=seed, body=body,
                     record_hz=1.0)
    res = sim.run()
    wf = sim.wildfire
    terrain = sc.world.terrain
    ny, nx = terrain.heights.shape
    ext = (terrain.x0, terrain.x0 + (nx - 1) * terrain.cell_m, terrain.y0, terrain.y0 + (ny - 1) * terrain.cell_m)
    fig, ax = plt.subplots(figsize=(13, 10), dpi=130)
    ls = LightSource(azdeg=315, altdeg=40)
    rgb = np.array([[matplotlib.colors.to_rgb(COVER_COLOURS[c]) for c in row] for row in terrain.cover])
    shaded = ls.shade_rgb(rgb, terrain.heights, vert_exag=2.0, blend_mode="soft", dx=terrain.cell_m, dy=terrain.cell_m)
    ax.imshow(shaded, origin="lower", extent=ext, interpolation="bilinear")
    ax.contour(np.linspace(ext[0], ext[1], nx), np.linspace(ext[2], ext[3], ny), terrain.heights,
               levels=np.arange(0, terrain.heights.max(), 20), colors="k", linewidths=0.3, alpha=0.35)
    g = wf.grid
    xs, ys = g.X[0], g.Y[:, 0]
    fc = wf.forecasts[-1] if wf.forecasts else None
    t0 = fc["t"] if fc else sim.t
    truth = wf.arrival10
    for hours, colour, alpha in ((3, "#f28e2b", 0.35), (1, "#d62728", 0.55)):
        burnt = np.where(truth <= t0 + 3600 * hours, 1.0, np.nan)
        ax.contourf(xs, ys, burnt, levels=[0.5, 1.5], colors=[colour], alpha=alpha)
    mapped = np.isfinite(wf.mapped_t) & (wf.mapped_t <= t0)
    if mapped.any():
        ax.contour(xs, ys, mapped.astype(float), levels=[0.5], colors="#00e5ff", linewidths=1.4)
    if fc is not None:
        p50 = fc["active"]["p50"]
        for hours, style in ((1, "-"), (3, "--")):
            ax.contour(xs, ys, np.where(np.isfinite(p50), p50 - t0, 1e9), levels=[3600 * hours], colors="k",
                       linewidths=1.6, linestyles=style)
    bx0, by0, bx1, by1 = sc.sensor.area
    ax.add_patch(Rectangle((bx0, by0), bx1 - bx0, by1 - by0, fill=False, ec="white", lw=1.2, ls=":"))
    path = np.array(sc.waypoints)
    ax.plot(path[:, 0], path[:, 1], color="white", lw=0.8, alpha=0.7)
    for i, f in enumerate(sc.wildfire.fires):
        ax.scatter(f.x, f.y, marker=SHELTER_MARK[f.shelter], s=110, c="#ffdd00", edgecolors="k", lw=1.2, zorder=6)
    for loc in wf.located:
        ax.scatter(loc["x"], loc["y"], marker="x", s=90, c="#00e5ff" if loc["method"] == "smoke" else "white",
                   lw=2.2, zorder=7)
    # Evacuation zones, outlined in the colour of the highest level they reached (Japan's colours).
    zone_max = {}
    for a in wf.alerts:
        if a["state"] in ("delivered", "cancelled") and a.get("delivered") is not None:
            zone_max[a["zone"]] = max(zone_max.get(a["zone"], 0), a["level"])
    for z in wf.zones:
        poly = np.array(z["polygon"] + z["polygon"][:1])
        lv = zone_max.get(z["code"], 0)
        ax.plot(poly[:, 0], poly[:, 1], color=LEVEL_COLOURS[lv] if lv else "white", lw=2.2 if lv else 0.6,
                alpha=0.95 if lv else 0.5, zorder=4)
        ax.annotate(z["code"], (poly[:, 0].min() + 8, poly[:, 1].max() - 8), va="top", fontsize=8, color="white",
                    path_effects=[matplotlib.patheffects.withStroke(linewidth=2, foreground="k")], zorder=4)
    level = np.zeros(len(wf.phones), dtype=int)
    for ph, deliveries in wf.phone_delivered.items():
        level[ph] = max(c for c, _, _ in deliveries)
    reached = (wf.phone_truth_s > 0) & (wf.phone_truth_s <= 3 * 3600)
    colours = [LEVEL_COLOURS.get(int(v), "#bbbbbb") if v else "#bbbbbb" for v in level]
    ax.scatter(wf.phone_xy[:, 0], wf.phone_xy[:, 1], s=26, c=colours, edgecolors=np.where(reached, "white", "none"),
               lw=1.0, zorder=5)
    for pl in sc.wildfire.places:
        ax.annotate(pl["name"], (pl["x"], pl["y"]), xytext=(6, 6), textcoords="offset points", fontsize=9,
                    color="white", weight="bold", path_effects=[matplotlib.patheffects.withStroke(linewidth=2.5, foreground="k")])
    for c in sc.world.cylinders[:1]:
        ax.scatter(c.x, c.y, marker="*", s=120, c="white", edgecolors="k", zorder=6)
    if brief and fc is not None:
        # Each fire's head in the next hour as the last brief gave it: an arrow, and the cone around it.
        for b in fc["brief"]:
            if b["head_xy"] is None:
                continue
            (x0, y0), (x1, y1) = b["head_from_xy"], b["head_xy"]
            run = max(float(np.hypot(x1 - x0, y1 - y0)), 1.0)
            mid = 90.0 - b["head_bearing_deg"]             # compass bearing to the plot's angle (east = 0, anticlockwise)
            ax.add_patch(Wedge((x0, y0), run, mid - b["cone_deg"], mid + b["cone_deg"], color="#ffdd00", alpha=0.3,
                               lw=0, zorder=8))
            ax.add_patch(FancyArrow(x0, y0, x1 - x0, y1 - y0, width=4.0, head_width=16.0, head_length=min(20.0, run / 2),
                                    length_includes_head=True, color="#ffdd00", ec="k", lw=0.8, zorder=9))
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_aspect("equal")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    m = res.metrics
    ax.set_title(f"Wildfire valley, seed {seed}, {n} drones: {m['fires_located']} of {m['fires_total']} fires found, "
                 f"forecast at {t0 / 60:.0f} min against the true spread", fontsize=12)
    handles = [
        Patch(color="#d62728", alpha=0.55, label="true burnt area, 1 h after the forecast"),
        Patch(color="#f28e2b", alpha=0.35, label="true burnt area, 3 h after"),
        Line2D([], [], color="#00e5ff", lw=1.4, label="burning ground mapped by the thermal cameras"),
        Line2D([], [], color="k", lw=1.6, label="forecast median, 1 h"),
        Line2D([], [], color="k", lw=1.6, ls="--", label="forecast median, 3 h"),
        Line2D([], [], marker="o", ls="", mfc="#ffdd00", mec="k", label="fire, open"),
        Line2D([], [], marker="^", ls="", mfc="#ffdd00", mec="k", label="fire, under trees"),
        Line2D([], [], marker="s", ls="", mfc="#ffdd00", mec="k", label="fire, under a rock overhang"),
        Line2D([], [], marker="x", ls="", color="white", mew=2, label="located by thermal camera"),
        Line2D([], [], marker="x", ls="", color="#00e5ff", mew=2, label="located by smoke"),
        Line2D([], [], marker="o", ls="", mfc=LEVEL_COLOURS[5], mec="white", label="phone and zone at Level 5, emergency (ringed: fire reaches it)"),
        Line2D([], [], marker="o", ls="", mfc=LEVEL_COLOURS[4], mec="none", label="Level 4, evacuate now"),
        Line2D([], [], marker="o", ls="", mfc=LEVEL_COLOURS[3], mec="none", label="Level 3, prepare to leave"),
        Line2D([], [], marker="o", ls="", mfc="#bbbbbb", mec="none", label="phone not alerted"),
        *([Line2D([], [], color="#ffdd00", lw=3, label="brief: head's run in the next hour, and its cone")] if brief else []),
        Line2D([], [], color="white", ls=":", label="survey block and sweep"),
        Line2D([], [], marker="*", ls="", mfc="white", mec="k", ms=11, label="radio mast (obstacle)"),
    ]
    leg = ax.legend(handles=handles, loc="lower left", fontsize=8, framealpha=0.85, ncol=2)
    leg.get_frame().set_edgecolor("none")
    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "map.png")
    plt.close(fig)
    return m


def swarm_size() -> None:
    path = OUT / "summary.json"
    if not path.exists():
        return
    s = json.loads(path.read_text())["groups"]
    sizes = sorted(int(k.split("_")[0]) for k in s if k.endswith("_forecast"))
    fig, axes = plt.subplots(1, 4, figsize=(16, 4), dpi=130)
    panels = (("fires_found_pct", "Fires found (%)", 100.0, 105.0),
              ("sorensen_1h", "Forecast overlap, 1 h (Sorensen)", 1.0, 1.05),
              ("phone_arrival_mae_min", "Arrival error at phones (min)", 1.0, None),
              ("warned_in_time", "Phones warned before the fire (%)", 100.0, 105.0))
    for ax, (key, label, k, top) in zip(axes, panels):
        vals = [s[f"{n}_forecast"].get(key) or [None, None, None] for n in sizes]
        mid = [v[0] * k if v[0] is not None else 0 for v in vals]
        err = [[(v[0] - v[1]) * k if v[0] is not None else 0 for v in vals],
               [(v[2] - v[0]) * k if v[0] is not None else 0 for v in vals]]
        ax.bar([str(n) for n in sizes], mid, yerr=err, color="#d9542b", capsize=4)
        ax.set_title(label, fontsize=11)
        ax.set_xlabel("drones")
        if top is not None:
            ax.set_ylim(0, top)
    fig.suptitle("Wildfire valley, held-out seeds: means with 95% bootstrap intervals. Each flight's last forecast is "
                 "scored; fewer drones finish later, so theirs is made later, with more of the fire mapped", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "swarm_size.png")
    plt.close(fig)


DETECTORS = (("geostationary", "Geostationary satellite", "#3b6fb6", "s"),
             ("polar", "Polar satellites", "#7a4fb0", "D"),
             ("public", "Public calls", "#8c8c8c", "v"),
             ("camera", "AI camera, clear view", "#2f8f5b", "^"))


def detection_chart() -> None:
    """Each fire on the held-out seeds: when it was found (minutes after it started to spread) and how big it was."""
    path = OUT / "runs.jsonl"
    if not path.exists():
        return
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if "error" not in r and r["alert_rule"] == "forecast" and "today" in r]
    if not rows:
        return
    top = max(r["n"] for r in rows)
    rows = sorted((r for r in rows if r["n"] == top), key=lambda r: r["seed"])
    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=130)
    rng = np.random.default_rng(0)
    total = sum(len(r["fire_table"]) for r in rows)

    def plot(points, label, colour, marker, z):
        found = [(dt, a) for dt, a in points if dt is not None]
        if found:
            x, y = np.array(found).T
            ax.scatter(x, y * np.exp(rng.normal(0.0, 0.08, len(y))), s=18, color=colour, marker=marker, alpha=0.45,
                       zorder=z, linewidths=0)
            ax.scatter([np.median(x)], [np.median(y)], s=150, color=colour, marker=marker, edgecolors="white",
                       linewidths=1.5, zorder=z + 5)
        ax.scatter([], [], s=60, color=colour, marker=marker, label=f"{label}: found {len(found)} of {total}")

    for z, (key, label, colour, marker) in enumerate(DETECTORS):
        pts = []
        for r in rows:
            for f, t in zip(r["fire_table"], r["today"]):
                v = t[key]
                pts.append(((v["t"] - f["t_active_s"]) / 60.0, v["area_m2"]) if v["t"] is not None else (None, None))
        plot(pts, label, colour, marker, 2 + z)
    pts = [((f["t"] - f["t_active_s"]) / 60.0, f["area_m2"]) if f["located"] else (None, None)
           for r in rows for f in r["fire_table"]]
    plot(pts, f"Halo, {top} drones, one flight", "#d9542b", "o", 20)
    ax.axvline(0.0, color="#444444", lw=1.0, ls="--", zorder=1)
    ax.text(1.5, 1.6, "fire starts to spread", fontsize=9, color="#444444", va="bottom")
    ax.set_yscale("log")
    ax.set_ylim(1.0, 1e6)
    ax.set_xlabel("minutes after the fire started to spread when it was reported (negative: still smouldering)")
    ax.set_ylabel("fire size when reported (m², log scale)")
    ax.set_title(f"Who finds the fire first: {len(rows)} held-out seeds, {total} fires "
                 "(large marker: median of the fires each method found)", fontsize=11)
    ax.grid(True, which="major", alpha=0.25)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "detection.png")
    plt.close(fig)


def direction_chart() -> None:
    """How far each brief's head bearing was from the truth, against two simple rules, at the largest swarm."""
    path = OUT / "runs.jsonl"
    if not path.exists():
        return
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if "error" not in r and r["alert_rule"] == "forecast" and "direction_table" in r]
    if not rows:
        return
    top = max(r["n"] for r in rows)
    table = [d for r in rows if r["n"] == top for d in r["direction_table"]]
    if not table:
        return
    fig, ax = plt.subplots(figsize=(9, 5), dpi=130)
    lines = (("err_deg", f"Halo brief ({top} drones): wind, slope and fuel in an ensemble", "#d9542b", "-"),
             ("wind_only_err_deg", "Straight downwind of the measured wind", "#3b6fb6", "--"),
             ("slope_only_err_deg", "Straight upslope (slope 2% or more)", "#2f8f5b", ":"))
    for key, label, colour, style in lines:
        # A brief that called a moving head not advancing is 180 off; a rule with no bearing is left out.
        errs = np.sort([180.0 if d[key] is None else d[key] for d in table
                        if key == "err_deg" or d[key] is not None])
        if not len(errs):
            continue
        ax.step(np.concatenate([[0.0], errs]), np.arange(len(errs) + 1) / len(errs) * 100.0, where="post", color=colour,
                ls=style, lw=2.2, label=f"{label}: median {np.median(errs):.0f}°")
    ax.axvline(22.5, color="#444444", lw=1.0, ls="--")
    ax.text(24.0, 4.0, "one compass point (22.5°)", fontsize=9, color="#444444")
    ax.set_xlim(0, 180)
    ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 181, 30))
    ax.set_xlabel("error in the head's direction (degrees)")
    ax.set_ylabel("fires within that error (%)")
    seeds = sorted({r["seed"] for r in rows})
    ax.set_title(f"Which way the fire's head runs in the next hour: {len(table)} briefs, seeds {seeds[0]}-{seeds[-1]}\n"
                 "each flight's last forecast; a brief that called it not advancing counts as 180°", fontsize=10.5)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="lower right", fontsize=9, frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "direction.png")
    plt.close(fig)


if __name__ == "__main__":
    import matplotlib.patheffects  # noqa: F401
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=3000)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--no-map", action="store_true")
    ap.add_argument("--brief", action="store_true", help="draw the last brief's head arrows and cones on the map")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    OUT = Path(a.out)
    if not a.no_map:
        print(json.dumps({k: v for k, v in flight_map(a.seed, a.n, a.brief).items()
                          if k not in ("fire_table", "direction_table")}))
    swarm_size()
    detection_chart()
    direction_chart()
