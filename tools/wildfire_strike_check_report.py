"""Tables for the strike-check sweep (tools/wildfire_strike_check_sweep.py): per size and arm, and paired by valley.

    PYTHONPATH=src python3 tools/wildfire_strike_check_report.py results/strike_checks/runs.jsonl \
        --out results/strike_checks/summary.json

Prints Markdown tables and writes the numbers as JSON. A fire counts as found by its own evidence (report_v2: its
hotspot, its mapped ground, or a smoke fix made mostly from its own plume's bearings), and as found while
smouldering when the station's own class says so (fire_classes / fire_table "miss"). Where a resumed sweep wrote
an error row and later a good row for the same flight, the good row is used; a flight with no good row stops the
report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ARMS = {"none": "no team: every drone in the area squads", "nearest": "check team, nearest strike first",
        "rule": "check team, hand-written triage rule", "nemotron": "check team, Nemotron picks"}
PAIRS = [("nemotron", "rule"), ("nemotron", "nearest"), ("nemotron", "none"), ("rule", "none"), ("nearest", "none"),
         ("rule", "nearest")]
TYPES = ["open, spreading at take-off", "under tree crowns", "under a rock overhang", "smouldering in the open",
         "across the valley"]
BOOT = 10000


def load(path: Path) -> list[dict]:
    good, bad = {}, {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r["seed"], r["n"], r["arm"])
        (bad if "error" in r else good)[key] = r
    missing = sorted(set(bad) - set(good))
    if missing:
        s, n, arm = missing[0]
        raise SystemExit(f"{len(missing)} flights have only error rows, e.g. seed {s} n {n} {arm}: {bad[missing[0]]['error']}")
    return list(good.values())


def per_flight(r: dict) -> dict:
    found = smoulder = 0
    types = [0] * len(TYPES)
    for i, f in enumerate(r["fire_table"]):
        if f.get("own_t") is None:
            continue
        found += 1
        smoulder += f.get("miss") == "smouldering"
        types[min(i, len(TYPES) - 1)] += 1
    assert smoulder == r["fire_classes"]["smouldering"]
    return {"found": found, "smoulder": smoulder, "overhang": types[2], "across": types[4], "types": types,
            "fires": len(r["fire_table"]), "false": r["false_locations"], "classes": r["fire_classes"],
            "checks": r.get("strike_checks") or 0, "fire_checks": r.get("strike_checks_fire") or 0,
            "unfound_checks": r.get("strike_checks_fire_unfound") or 0,
            "warned": r.get("phones_warned_in_time"), "reached": r.get("phones_reached_3h"),
            "calls": r.get("strike_calls") or 0, "cost": r.get("strike_cost_usd") or 0.0,
            "invalid": r.get("strike_invalid") or 0, "latency": r.get("strike_latency_s_median"),
            "crashes": r["collisions"] + r["obstacle_crashes"] + r["ground_crashes"], "near_misses": r["near_misses"],
            "min_sep": r["min_separation_m"], "flying": r.get("flying", r["n"]), "squads": r.get("squads")}


def boot_ci(diffs: np.ndarray, seed: int = 9300) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diffs), size=(BOOT, len(diffs)))
    means = diffs[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rows = load(Path(a.runs))
    by: dict = {}
    for r in rows:
        by.setdefault(r["n"], {}).setdefault(r["arm"], {})[r["seed"]] = per_flight(r)
    out: dict = {}
    for n in sorted(by):
        arms = by[n]
        flying = {p["flying"] for a_ in arms.values() for p in a_.values()}
        if flying != {n}:
            raise SystemExit(f"at {n} drones some flights flew {sorted(flying)}")
        res = {"arms": {}, "pairs": {}}
        print(f"\n### {n} drones\n")
        print("| Arm | Split | Valleys | Found | Found smouldering | Overhang fire found | Across-valley fire found | "
              "False fires per flight | Checks per flight | Checks on a fire's strike (still unfound) | "
              "Phones warned in time |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for arm in [x for x in ARMS if x in arms]:
            pf = [arms[arm][s] for s in sorted(arms[arm])]
            fires = sum(p["fires"] for p in pf)
            reached = sum(p["reached"] or 0 for p in pf)
            agg = {"flights": len(pf), "seeds": sorted(arms[arm]), "split": pf[0]["squads"],
                   "found_pct": 100.0 * sum(p["found"] for p in pf) / fires,
                   "smoulder_pct": 100.0 * sum(p["smoulder"] for p in pf) / fires,
                   "overhang_found": sum(p["overhang"] for p in pf), "across_found": sum(p["across"] for p in pf),
                   "type_found": [sum(p["types"][k] for p in pf) for k in range(len(TYPES))],
                   "false_per_flight": float(np.mean([p["false"] for p in pf])),
                   "checks_per_flight": float(np.mean([p["checks"] for p in pf])),
                   "fire_checks": sum(p["fire_checks"] for p in pf), "unfound_checks": sum(p["unfound_checks"] for p in pf),
                   "checks": sum(p["checks"] for p in pf),
                   "phones_warned_pct": 100.0 * sum(p["warned"] or 0 for p in pf) / reached if reached else None,
                   "classes": {c: sum(p["classes"][c] for p in pf) for c in pf[0]["classes"]},
                   "calls": sum(p["calls"] for p in pf), "cost_usd": round(sum(p["cost"] for p in pf), 6),
                   "invalid": sum(p["invalid"] for p in pf),
                   "latency_s_median": float(np.median([p["latency"] for p in pf if p["latency"] is not None]))
                   if any(p["latency"] is not None for p in pf) else None,
                   "crashes": sum(p["crashes"] for p in pf), "near_misses": sum(p["near_misses"] for p in pf),
                   "min_separation_m": min(p["min_sep"] for p in pf if p["min_sep"] is not None)}
            res["arms"][arm] = agg
            warned = f"{agg['phones_warned_pct']:.1f}%" if agg["phones_warned_pct"] is not None else "-"
            print(f"| {ARMS[arm]} | {agg['split']} | {agg['flights']} | {agg['found_pct']:.0f}% | "
                  f"{agg['smoulder_pct']:.0f}% | {agg['overhang_found']} | {agg['across_found']} | "
                  f"{agg['false_per_flight']:.2f} | {agg['checks_per_flight']:.1f} | "
                  f"{agg['fire_checks']} ({agg['unfound_checks']}) of {agg['checks']} | {warned} |")
        print("\nPaired by valley: extra fires per flight, mean and 95% bootstrap interval over valleys.\n")
        print("| Comparison | Valleys | Found | Found smouldering |")
        print("|---|---|---|---|")
        for x, y in PAIRS:
            if x not in arms or y not in arms:
                continue
            seeds = sorted(set(arms[x]) & set(arms[y]))
            pr = {"valleys": len(seeds)}
            cells = []
            for key in ("found", "smoulder"):
                d = np.array([arms[x][s][key] - arms[y][s][key] for s in seeds], dtype=float)
                lo, hi = boot_ci(d)
                pr[key] = {"mean": float(d.mean()), "lo": lo, "hi": hi, "better": int((d > 0).sum()),
                           "worse": int((d < 0).sum())}
                cells.append(f"{d.mean():+.2f} [{lo:+.2f}, {hi:+.2f}] ({pr[key]['better']} better, "
                             f"{pr[key]['worse']} worse)")
            res["pairs"][f"{x}-{y}"] = pr
            print(f"| {x} minus {y} | {len(seeds)} | " + " | ".join(cells) + " |")
        out[str(n)] = res
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
