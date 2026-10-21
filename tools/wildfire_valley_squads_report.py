"""Fires found by squad size: the report for tools/wildfire_valley_squads_sweep.py.

    PYTHONPATH=src python3 tools/wildfire_valley_squads_report.py --runs results/wildfire_valley_squads/runs.jsonl

Writes REPORT.md, summary.json, by_size.csv and squads.png next to the runs. The primary measure (PREREG.md) is
fires found by their own evidence per flight: a fix placed by the fire's own hotspot or by bearings to its own
plume, so a fire is never credited for a fix its neighbour made. The committed reports' nearest-fire rule
(``fires_located``) is shown beside it for comparison with them.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

ARMS = {"A": "today's flight (one sweep of the floor)", "B": "floor patrol (one squad)",
        "D": "squads per area (floor and both slopes)"}
TYPES = ["open, spreading at take-off", "under tree crowns", "under a rock overhang", "smouldering in the open",
         "across the valley"]
BEST_CASE = ("geostationary", "polar", "camera", "public")          # "today's four", as the main report
BOOT = 10000


def load(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    bad = [r for r in rows if "error" in r]
    if bad:
        raise SystemExit(f"{len(bad)} rows have errors, e.g. seed {bad[0]['seed']} n {bad[0]['n']}: {bad[0]['error']}")
    return rows


def per_flight(r: dict) -> dict:
    """The per-flight numbers the tables use."""
    found = smoulder = first = 0
    types = [0] * len(TYPES)
    for i, (f, today) in enumerate(zip(r["fire_table"], r["today"])):
        t = f.get("own_t")
        if t is None:
            continue
        found += 1
        types[min(i, len(TYPES) - 1)] += 1
        smoulder += t < f["t_active_s"]
        c = [today[k]["t"] for k in BEST_CASE if today[k]["t"] is not None]
        first += not c or t < min(c)
    return {"found": found, "smoulder": smoulder, "first": first, "types": types, "fires": len(r["fire_table"]),
            "nearest": r["fires_located"], "false": r["false_locations"], "classes": r["fire_classes"]}


def boot_ci(diffs: np.ndarray, seed: int = 8000) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diffs), size=(BOOT, len(diffs)))
    means = diffs[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def table(rows: list[dict], station: str) -> dict:
    """{n: {arm: aggregate}} for one station."""
    out: dict = {}
    for r in rows:
        if r["station"] != station:
            continue
        out.setdefault(r["n"], {}).setdefault(r["arm"], []).append(r)
    agg: dict = {}
    for n, arms in sorted(out.items()):
        agg[n] = {}
        for arm, rs in sorted(arms.items()):
            rs = sorted(rs, key=lambda r: r["seed"])
            pf = [per_flight(r) for r in rs]
            fires = sum(p["fires"] for p in pf)
            agg[n][arm] = {
                "flights": len(rs), "seeds": [r["seed"] for r in rs],
                "split": rs[0].get("squads") or f"one line of {rs[0]['flying']}", "flying": rs[0]["flying"],
                "reserve": rs[0].get("reserve") or 0,
                "found_pct": 100.0 * sum(p["found"] for p in pf) / fires,
                "smoulder_pct": 100.0 * sum(p["smoulder"] for p in pf) / fires,
                "first_pct": 100.0 * sum(p["first"] for p in pf) / fires,
                "nearest_pct": 100.0 * sum(p["nearest"] for p in pf) / fires,
                "type_pct": [100.0 * sum(p["types"][k] for p in pf) / len(pf) for k in range(len(TYPES))],
                "false_per_flight": float(np.mean([p["false"] for p in pf])),
                "classes": {c: sum(p["classes"][c] for p in pf) for c in pf[0]["classes"]},
                "found_per_flight": [p["found"] for p in pf], "smoulder_per_flight": [p["smoulder"] for p in pf],
                "retired": sum(r["drones_retired"] for r in rs), "crashes": sum(r["collisions"] + r["obstacle_crashes"]
                                                                              + r["ground_crashes"] for r in rs),
                "near_misses": sum(r["near_misses"] for r in rs),
                "energy_wh_per_drone": float(np.mean([r["energy_wh_per_drone"] for r in rs])),
                "duration_s": float(np.mean([r["duration_s"] for r in rs])),
                "pass_s": {sq["code"]: sq["pass_s"] for sq in (rs[0].get("squad_table") or [])},
            }
    return agg


def paired(agg: dict, n: int, a: str, b: str, key: str) -> dict | None:
    if a not in agg.get(n, {}) or b not in agg.get(n, {}):
        return None
    x, y = agg[n][a], agg[n][b]
    if x["seeds"] != y["seeds"]:
        raise SystemExit(f"arms {a} and {b} at n={n} were not flown on the same seeds")
    d = np.array(x[key], dtype=float) - np.array(y[key], dtype=float)
    lo, hi = boot_ci(d)
    return {"mean": float(d.mean()), "lo": lo, "hi": hi, "better": int((d > 0).sum()), "worse": int((d < 0).sum())}


def chart(aggs: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colours = {"A": "#8c8c8c", "B": "#4e79a7", "D": "#d9542b"}
    k = max(1, len(aggs))            # one panel per ground station flown
    fig, axes = plt.subplots(1, k, figsize=(6.4 * k, 4.8), dpi=130, sharey=True, squeeze=False)
    axes = axes[0]
    for ax, (station, agg) in zip(axes, aggs.items()):
        ns = sorted(agg)
        for arm in ("A", "B", "D"):
            xs = [n for n in ns if arm in agg[n]]
            ax.plot(xs, [agg[n][arm]["found_pct"] for n in xs], "-o", color=colours[arm], label=ARMS[arm], lw=2)
            ax.plot(xs, [agg[n][arm]["smoulder_pct"] for n in xs], "--", color=colours[arm], lw=1.2, alpha=0.8)
        ax.set_xscale("log", base=2)
        ax.set_xticks(ns, [str(n) for n in ns])
        ax.set_xlabel("drones")
        ax.set_ylim(0, 105)
        ax.set_title(f"ground station: {station}", fontsize=11)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("fires found by their own evidence (%)\nsolid: found; dashed: found while smouldering")
    axes[0].legend(frameon=False, loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fmt(v, d=0, pct=False):
    return "n/a" if v is None else f"{v:.{d}f}{'%' if pct else ''}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="results/wildfire_valley_squads/runs.jsonl")
    a = ap.parse_args()
    runs = Path(a.runs)
    out = runs.parent
    rows = load(runs)
    stations = [s for s in ("legacy", "tight") if any(r["station"] == s for r in rows)]
    aggs = {s: table(rows, s) for s in stations}
    seeds = sorted({r["seed"] for r in rows})
    summary: dict = {"seeds": [seeds[0], seeds[-1]], "flights": len(rows), "stations": {}}
    lines: list[str] = []
    w = lines.append
    w("# Squads per area: fires found by squad size\n")
    w(f"Seeds {seeds[0]}-{seeds[-1]} ({len(seeds)} valleys), flown once after the plan in `PREREG.md` was written; "
      f"{len(rows)} flights. Made by `tools/wildfire_valley_squads_sweep.py` and "
      "`tools/wildfire_valley_squads_report.py`.\n")
    w("Three ways to fly the same number of drones over the same valleys:\n")
    for arm, text in ARMS.items():
        w(f"- **{arm}**, {text}.")
    w("\nThe squads patrol for 12 minutes, inside one battery. Drones that do not fit across an area stay in reserve "
      "at the launch point (spares for battery swaps). Each valley has five fires: four in or beside the valley floor "
      "and one across the valley on a slope. A fire counts as found when the ground station placed a fix from its own "
      "hotspot or from bearings to its own plume.\n")
    csv_rows = []
    for station in stations:
        agg = aggs[station]
        summary["stations"][station] = {"by_size": {}, "paired": {}}
        w(f"## Ground station: {station}\n")
        w("| Drones | D: drones per area (reserve) | Found: A / B / D | Found while smouldering: A / B / D | "
          "Across-valley fire: A / B / D | False fires per flight: A / B / D | D: time to see each area once |")
        w("|---|---|---|---|---|---|---|")
        for n, arms in agg.items():
            g = lambda arm, k, d=0, pct=True: fmt(arms[arm][k], d, pct) if arm in arms else "n/a"   # noqa: E731
            across = lambda arm: fmt(arms[arm]["type_pct"][4], 0, True) if arm in arms else "n/a"  # noqa: E731
            d = arms.get("D")
            split = d["split"] if d else "n/a"
            passes = ", ".join(f"{k} {v / 60:.1f} min" for k, v in (d["pass_s"] if d else {}).items())
            w(f"| {n} | {split} | {g('A', 'found_pct')} / {g('B', 'found_pct')} / {g('D', 'found_pct')} | "
              f"{g('A', 'smoulder_pct')} / {g('B', 'smoulder_pct')} / {g('D', 'smoulder_pct')} | "
              f"{across('A')} / {across('B')} / {across('D')} | "
              f"{g('A', 'false_per_flight', 2, False)} / {g('B', 'false_per_flight', 2, False)} / "
              f"{g('D', 'false_per_flight', 2, False)} | {passes} |")
            summary["stations"][station]["by_size"][n] = {k: {kk: vv for kk, vv in v.items()
                                                              if kk not in ("found_per_flight", "smoulder_per_flight")}
                                                          for k, v in arms.items()}
            for arm, v in arms.items():
                csv_rows.append({"station": station, "n": n, "arm": arm, "split": v["split"], "flying": v["flying"],
                                 "reserve": v["reserve"], "found_pct": round(v["found_pct"], 1),
                                 "found_smouldering_pct": round(v["smoulder_pct"], 1),
                                 "reported_before_todays_four_pct": round(v["first_pct"], 1),
                                 "across_valley_found_pct": round(v["type_pct"][4], 1),
                                 "false_fires_per_flight": round(v["false_per_flight"], 2),
                                 "flight_s": round(v["duration_s"], 1),
                                 "energy_wh_per_drone": round(v["energy_wh_per_drone"], 1),
                                 "pass_s": json.dumps(v["pass_s"]), "flights": v["flights"]})
        w("")
        w("Paired by valley (same seeds): extra fires found per flight, mean and 95% bootstrap interval over valleys.\n")
        w("| Drones | D minus B: found | D minus B: found while smouldering | D minus A: found | "
          "D minus A: found while smouldering |")
        w("|---|---|---|---|---|")
        for n in agg:
            cells = []
            for ref in ("B", "A"):
                for key in ("found_per_flight", "smoulder_per_flight"):
                    p = paired(agg, n, "D", ref, key)
                    summary["stations"][station]["paired"][f"{n}:D-{ref}:{key}"] = p
                    cells.append("n/a" if p is None else f"{p['mean']:+.2f} ({p['lo']:+.2f} to {p['hi']:+.2f})")
            w(f"| {n} | " + " | ".join(cells) + " |")
        w("")
        w("Why the others were missed (D, all valleys): ")
        for n, arms in agg.items():
            if "D" in arms:
                c = arms["D"]["classes"]
                w(f"- {n} drones: {c['not_in_view']} never in view, {c['hidden']} in view but hidden, "
                  f"{c['not_credited']} confirmed by a camera but no fix came from it, {c['after_spread']} found "
                  "only after spreading.")
        w("")
        w("Safety and energy, every flight: " + "; ".join(
            f"{n} drones: {sum(v['crashes'] for v in arms.values())} crashes, "
            f"{sum(v['retired'] for v in arms.values())} drones retired" for n, arms in agg.items()) + ".\n")
    if len(stations) == 2:
        w("## The tighter station merge\n")
        w("Extra fires found per flight with the tight merge, same flights' sensing (the station does not change "
          "how the drones fly):\n")
        w("| Drones | A | B | D |")
        w("|---|---|---|---|")
        for n in aggs["legacy"]:
            cells = []
            for arm in ("A", "B", "D"):
                x, y = aggs["tight"].get(n, {}).get(arm), aggs["legacy"].get(n, {}).get(arm)
                if not x or not y:
                    cells.append("n/a")
                    continue
                d = np.array(x["found_per_flight"], float) - np.array(y["found_per_flight"], float)
                lo, hi = boot_ci(d)
                cells.append(f"{d.mean():+.2f} ({lo:+.2f} to {hi:+.2f}); false {x['false_per_flight'] - y['false_per_flight']:+.2f}")
            w(f"| {n} | " + " | ".join(cells) + " |")
        w("")
    (out / "REPORT.md").write_text("\n".join(lines))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    with (out / "by_size.csv").open("w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(csv_rows[0]))
        wr.writeheader()
        wr.writerows(csv_rows)
    chart(aggs, out / "squads.png")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
