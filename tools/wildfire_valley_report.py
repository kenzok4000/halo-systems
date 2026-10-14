"""Summarise the wildfire valley sweep: finding fires, forecasting their spread, warning phones.

    PYTHONPATH=src python3 tools/wildfire_valley_report.py results/wildfire_valley/runs.jsonl

Writes REPORT.md and summary.json next to the rows. Intervals are 95% percentile bootstraps over seeds
(10,000 resamples, fixed generator seed), as in the main benchmark report.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

FIRES = ["Spreading at the start, open scrub", "Under tree crowns", "Under a rock overhang",
         "Smouldering in the open", "Across the valley"]
RNG_SEED = 0


def boot(values, stat=np.mean, n=10000):
    x = np.asarray([v for v in values if v is not None], dtype=float)
    if not len(x):
        return None, None, None
    rng = np.random.default_rng(RNG_SEED)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    s = np.array([stat(x[i]) for i in idx]) if stat is not np.mean else x[idx].mean(axis=1)
    return float(stat(x)), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def ratio_boot(num, den, n=10000):
    """Pooled ratio sum(num) / sum(den) over seeds, with a bootstrap over seeds."""
    num, den = np.asarray(num, dtype=float), np.asarray(den, dtype=float)
    if den.sum() == 0:
        return None, None, None
    rng = np.random.default_rng(RNG_SEED)
    idx = rng.integers(0, len(num), size=(n, len(num)))
    d = den[idx].sum(axis=1)
    r = np.where(d > 0, num[idx].sum(axis=1) / np.maximum(d, 1e-12), np.nan)
    return float(num.sum() / den.sum()), float(np.nanpercentile(r, 2.5)), float(np.nanpercentile(r, 97.5))


def fmt(t, digits=1, pct=False):
    v, lo, hi = t
    if v is None:
        return "n/a"
    k = 100.0 if pct else 1.0
    return f"{v * k:.{digits}f} [{lo * k:.{digits}f}, {hi * k:.{digits}f}]"


def med(values):
    x = [v for v in values if v is not None]
    return float(np.median(x)) if x else None


TODAY = [("geostationary", "Geostationary satellite (GOES, 5-min scans)", "a 2 km pixel or larger"),
         ("polar", "Polar-orbiting satellites (MODIS, VIIRS)", "a 375 m to 1 km pixel"),
         ("camera", "AI camera network, clear view", "a bearing from each camera"),
         ("camera_deployed", "AI camera network, as deployed (sees 22% of fires)", "a bearing from each camera"),
         ("public", "Public calls (911 / 119)", "the caller's description")]
BEST_CASE = ("geostationary", "polar", "camera", "public")      # "today's four": the camera with a clear view


def detect_stats(recs):
    """recs: per seed, a list of (t_active, t or None, area or None). Pooled shares with bootstraps over seeds."""
    n = [len(r) for r in recs]
    found = [sum(t is not None for _, t, _ in r) for r in recs]
    before = [sum(t is not None and t < ta for ta, t, _ in r) for r in recs]
    small = [sum(t is not None and a <= 1000.0 for _, t, a in r) for r in recs]
    after = [((t - ta) / 60.0 if t is not None else np.inf) for r in recs for ta, t, _ in r]
    sizes = [a for r in recs for _, t, a in r if t is not None]
    return {"found": ratio_boot(found, n), "before_spread": ratio_boot(before, n), "under_0_1ha": ratio_boot(small, n),
            "after_spread_median_min": float(np.median(after)) if after else None,
            "size_median_m2": float(np.median(sizes)) if sizes else None}


def _cell_minutes(v):
    if v is None:
        return "n/a"
    if np.isinf(v):
        return "not found"
    return f"{v:+.0f}"


def today_section(by, sizes, w, summary):
    """Drones against today's ways of finding fires, on the same fires (rows carry the 'today' key)."""
    base = by.get((max(sizes), "forecast"), []) if sizes else []
    if not base or "today" not in base[0]:
        return
    seeds = [r["seed"] for r in base]
    act = {r["seed"]: [f["t_active_s"] for f in r["fire_table"]] for r in base}
    today = {r["seed"]: r["today"] for r in base}

    def drones(n):
        g = {r["seed"]: r for r in by.get((n, "forecast"), [])}
        return {s: [(f["t"], f["area_m2"]) if f["located"] else (None, None) for f in g[s]["fire_table"]]
                for s in seeds if s in g}

    def earliest(s, extra=None):
        out = []
        for i, row in enumerate(today[s]):
            c = [(row[k]["t"], row[k]["area_m2"]) for k in BEST_CASE if row[k]["t"] is not None]
            if extra is not None and extra[s][i][0] is not None:
                c.append(extra[s][i])
            out.append(min(c) if c else (None, None))
        return out

    rows = []
    for key, label, where in TODAY:
        recs = [[(act[s][i], today[s][i][key]["t"], today[s][i][key]["area_m2"]) for i in range(len(act[s]))] for s in seeds]
        rows.append((label, where, detect_stats(recs)))
    first = {s: earliest(s) for s in seeds}
    rows.append(("Earliest of today's four (camera with a clear view)", "", detect_stats([[(act[s][i], *first[s][i]) for i in range(len(act[s]))] for s in seeds])))
    for n in sizes:
        d = drones(n)
        errs = [f["err_m"] for r in by.get((n, "forecast"), []) for f in r["fire_table"] if f["located"] and f["err_m"] is not None]
        rows.append((f"Halo drones, {n}, one flight", f"{np.median(errs):.0f} m (median, measured)" if errs else "",
                     detect_stats([[(act[s][i], *d[s][i]) for i in range(len(act[s]))] for s in seeds if s in d])))
    top = max(sizes)
    dtop = drones(top)
    both = {s: earliest(s, dtop) for s in seeds if s in dtop}
    rows.append((f"Today's four plus {top} drones", "", detect_stats([[(act[s][i], *both[s][i]) for i in range(len(act[s]))] for s in both])))
    w("## Drones against today's ways of finding fires\n")
    w("The same fires, found by the methods fire services use today, modelled from published figures with the benefit of "
      "the doubt: no cloud, a camera with a clear view of the whole valley, every satellite detection credited to the right "
      "fire, the fastest published delivery (`src/halo_systems/sim/detection.py`; sources in `docs/WILDFIRE.md`). They "
      "watch for 4 hours from 08:00; the drones fly once, for up to 40 minutes, and anything they miss counts as not found. "
      "Time is measured from when each fire started to spread: negative means found while it still smouldered.\n")
    w("| Method | Found in 4 h (%) | Found before it spread (%) | Found under 0.1 ha (%) | Minutes after it spread, median | Size when found, median (m²) | Says where to within |")
    w("|---|---|---|---|---|---|---|")
    summary["today"] = {}
    for label, where, st in rows:
        size = st["size_median_m2"]
        w(f"| {label} | {fmt(st['found'], 0, pct=True)} | {fmt(st['before_spread'], 0, pct=True)} | "
          f"{fmt(st['under_0_1ha'], 0, pct=True)} | {_cell_minutes(st['after_spread_median_min'])} | "
          f"{'n/a' if size is None else f'{size:,.0f}'} | {where} |")
        summary["today"][label] = st
    w("")


def main(path: str) -> int:
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    errors = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]
    by = defaultdict(list)
    for r in rows:
        by[(r["n"], r["alert_rule"])].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["seed"])
    sizes = sorted({n for n, rule in by if rule == "forecast"})
    seeds = sorted({r["seed"] for r in rows})
    out, summary = [], {"seeds": seeds, "errors": len(errors), "groups": {}}
    w = out.append
    w("# Wildfire valley: finding fires, forecasting their spread, warning phones\n")
    w(f"Generated by `tools/wildfire_valley_report.py` from `{Path(path).name}`: {len(rows)} flights on held-out seeds "
      f"{seeds[0]}-{seeds[-1]} ({len(seeds)} seeds), improved controller with the rule supervisor. Brackets are 95% "
      "bootstrap intervals over seeds. The scenario, the station and every assumption are described in "
      "`docs/WILDFIRE.md`." + (f" {len(errors)} flights failed and are left out." if errors else "") + "\n")

    # -- finding --------------------------------------------------------------------------------------------
    w("## Finding the fires\n")
    w("Five fires per flight. A fire counts as found when the station places one within 60 m of its burning ground "
      "(its ignition point while it smoulders). A false fire is a location more than 60 m from every fire's burning "
      "ground, including ones the station later merged into a real fire. Mapped: burning ground the thermal "
      "cameras mapped by the end of the flight.\n")
    w("| Drones | Fires found (%) | False fires per flight | Duplicates merged per flight | Smoke fix error, median (m) | Mapped (ha), median | All found by (s) |")
    w("|---|---|---|---|---|---|---|")
    for n in sizes:
        g = by[(n, "forecast")]
        found = boot([r["fires_located_pct"] / 100 for r in g])
        false = boot([r["false_locations"] for r in g])
        err = med([r["smoke_location_error_m"] for r in g])
        alls = [r["all_located_s"] for r in g]
        merged = np.mean([r.get("duplicates_merged", 0) for r in g])
        mapped = med([r.get("mapped_ha") for r in g])
        w(f"| {n} | {fmt(found, 1, pct=True)} | {fmt(false, 2)} | {merged:.2f} | "
          f"{'n/a' if err is None else f'{err:.1f}'} | {'n/a' if mapped is None else f'{mapped:.2f}'} | "
          f"{sum(a is not None for a in alls)} of {len(alls)} flights; median {med(alls) or 0:.0f} s |")
        summary["groups"][f"{n}_forecast"] = {"flights": len(g), "fires_found_pct": found, "false_fires": false,
                                              "duplicates_merged": merged, "smoke_fix_error_median_m": err,
                                              "mapped_ha_median": mapped}
    w("")
    w("By where the fire was: share found, share of those found by smoke, and the median time to find it.\n")
    w("| Fire | " + " | ".join(f"{n} drones" for n in sizes) + " |")
    w("|---|" + "---|" * len(sizes))
    for i, label in enumerate(FIRES):
        cells = []
        for n in sizes:
            g = by[(n, "forecast")]
            tab = [r["fire_table"][i] for r in g if len(r["fire_table"]) > i]
            found = [f for f in tab if f["located"]]
            smoke = sum(1 for f in found if f["by"] == "smoke")
            t = med([f["t"] for f in found])
            cells.append(f"{100 * len(found) / len(tab):.0f}% found, {100 * smoke / max(len(found), 1):.0f}% by smoke"
                         + (f", {t:.0f} s" if t is not None else ""))
        w(f"| {label} | " + " | ".join(cells) + " |")
    w("")
    today_section(by, sizes, w, summary)

    # -- forecast ---------------------------------------------------------------------------------------------
    w("## The spread forecast\n")
    w("The last forecast of each flight against the true spread; fewer drones sweep for longer, so their last forecast "
      "is made later, with more of the fire mapped. Sorensen index: overlap between the ground the "
      "forecast's median says will burn and the ground that does burn, counting only ground that burns after the "
      "forecast (1 is a perfect match). \"Located fires\" compares with the true spread of the fires the station "
      "knew were spreading (located as spreading, or mapped); \"all fires\" with every fire, found or not. Phone arrival error: forecast "
      "median arrival minus the true arrival, at phones the fire reaches within 3 hours (negative is early). Brier "
      "score: the forecast probability of fire within 60 minutes at each phone against what happened (0 is perfect).\n")
    w("| Drones | Made at (min), median | Sorensen, 1 h | Sorensen, 3 h | Sorensen, 3 h, all fires | Phone arrival error, mean abs (min) | Bias (min) | Brier, 60 min |")
    w("|---|---|---|---|---|---|---|---|")
    for n in sizes:
        g = by[(n, "forecast")]
        cols = {k: boot([r[k] for r in g]) for k in ("sorensen_1h", "sorensen_3h", "sorensen_3h_all_fires",
                                                     "phone_arrival_mae_min", "phone_arrival_bias_min", "brier_60min")}
        made = med([r.get("forecast_t_s") for r in g])
        w(f"| {n} | {'n/a' if made is None else f'{made / 60:.1f}'} | {fmt(cols['sorensen_1h'], 2)} | {fmt(cols['sorensen_3h'], 2)} | {fmt(cols['sorensen_3h_all_fires'], 2)} | "
          f"{fmt(cols['phone_arrival_mae_min'], 1)} | {fmt(cols['phone_arrival_bias_min'], 1)} | {fmt(cols['brier_60min'], 3)} |")
        summary["groups"][f"{n}_forecast"].update(cols)
    w("")

    # -- warnings ---------------------------------------------------------------------------------------------
    w("## Warnings to phones\n")
    w("Each flight has 63 simulated phones (homes, a campground, hikers, drivers and a ranch); 5% have no signal. "
      "Alerts go to whole evacuation zones at Japan-style levels (3 prepare, 4 evacuate, 5 emergency), automatically "
      "by fixed rules once each passes the alert check, and arrive 5 to 30 s later. "
      "A phone is reached when fire comes within 50 m of it within 3 hours of take-off. Warned in time: an alert "
      "at Level 3 or above delivered before the fire arrives, as a share of the phones reached. Lead time: "
      "how long before the fire arrives the first alert lands (median over phones, then over flights). Evacuated: "
      "told Level 4 or 5.\n")
    w("| Drones | Phones reached per flight | Warned in time (%) | Missed, with signal | Missed, no signal | Lead time, median (min) | Lead time, 10th pct (min) | Evacuation lead, median (min) | Evacuated but not reached in 3 h (%) | Alerts per flight, Levels 3 / 4 / 5 | Alerts rejected by the check |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for n in sizes:
        g = by[(n, "forecast")]
        reached = [r["phones_reached_3h"] for r in g]
        warned = ratio_boot([r["phones_warned_in_time"] for r in g], reached)
        evac_false = ratio_boot([r["evac_not_reached_3h"] for r in g], [r["phones_evacuated"] for r in g])
        lead = med([r["lead_time_min_median"] for r in g])
        lead10 = med([r["lead_time_min_p10"] for r in g])
        evac = med([r["evac_lead_time_min_median"] for r in g])
        w(f"| {n} | {np.mean(reached):.1f} | {fmt(warned, 1, pct=True)} | {sum(r['phones_missed'] for r in g)} | "
          f"{sum(r['phones_missed_no_signal'] for r in g)} | {lead or 0:.0f} | {lead10 or 0:.0f} | {evac or 0:.0f} | "
          f"{fmt(evac_false, 0, pct=True)} | "
          + " / ".join(f"{np.mean([r.get(f'alerts_level{k}', 0) for r in g]):.1f}" for k in (3, 4, 5))
          + f" | {sum(r['alerts_rejected'] for r in g)} of {sum(r['alerts_drafted'] for r in g)} |")
        summary["groups"][f"{n}_forecast"].update(warned_in_time=warned, evac_not_reached=evac_false, lead_median_min=lead,
                                                  lead_p10_min=lead10, evac_lead_median_min=evac)
    w("")

    # -- ablation --------------------------------------------------------------------------------------------
    abl = [n for n in sizes if (n, "distance") in by]
    if abl:
        w("## Forecast against distance (ablation)\n")
        w("The same flights with the alert rule replaced by distance alone: Level 5 within 150 m and Level 4 within "
          "800 m of a spreading fire, Level 3 within 1,600 m of any. Paired by seed; differences are forecast minus distance.\n")
        w("| Drones | Rule | Warned in time (%) | Lead time, median (min) | Phones evacuated per flight | Evacuated but not reached in 3 h (%) | Warned but not reached in 3 h per flight |")
        w("|---|---|---|---|---|---|---|")
        for n in abl:
            for rule in ("forecast", "distance"):
                g = by[(n, rule)]
                reached = [r["phones_reached_3h"] for r in g]
                warned = ratio_boot([r["phones_warned_in_time"] for r in g], reached)
                evac_false = ratio_boot([r["evac_not_reached_3h"] for r in g], [r["phones_evacuated"] for r in g])
                w(f"| {n} | {rule} | {fmt(warned, 1, pct=True)} | {med([r['lead_time_min_median'] for r in g]) or 0:.0f} | "
                  f"{np.mean([r['phones_evacuated'] for r in g]):.1f} | {fmt(evac_false, 0, pct=True)} | "
                  f"{np.mean([r['warned_not_reached_3h'] for r in g]):.1f} |")
                summary["groups"][f"{n}_{rule}"] = summary["groups"].get(f"{n}_{rule}", {})
                summary["groups"][f"{n}_{rule}"].update(warned_in_time=warned, evac_not_reached=evac_false)
            a = {r["seed"]: r for r in by[(n, "forecast")]}
            b = {r["seed"]: r for r in by[(n, "distance")]}
            common = sorted(set(a) & set(b))
            d_false = boot([a[s]["evac_not_reached_3h"] - b[s]["evac_not_reached_3h"] for s in common])
            d_warn = boot([a[s]["phones_warned_in_time"] - b[s]["phones_warned_in_time"] for s in common])
            w(f"\nWith {n} drones, over {len(common)} paired seeds the forecast rule changes phones warned in time by "
              f"{fmt(d_warn, 1)} per flight and phones evacuated that the fire did not reach by {fmt(d_false, 1)} per flight.\n")
            summary["groups"][f"{n}_paired"] = {"warned_in_time_diff": d_warn, "evac_false_diff": d_false}
    Path(path).with_name("REPORT.md").write_text("\n".join(out) + "\n")
    Path(path).with_name("summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "results/wildfire_valley/runs.jsonl"))
