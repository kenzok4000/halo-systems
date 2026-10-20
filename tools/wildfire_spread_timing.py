"""Who reported each fire first, by when the fire starts to spread (a post-hoc breakdown of committed rows).

    PYTHONPATH=src python3 tools/wildfire_spread_timing.py

The drones' lead over today's methods comes mostly from the smouldering phase, whose length the scenario sets
(docs/WILDFIRE.md, "Smouldering phase"). This splits the committed held-out rows by when each fire starts to spread,
so a reader can see the lead for short and long smoulders separately. It reads results/wildfire_valley/runs.jsonl
(seeds 4000-4019) and results/wildfire_valley_7000/runs.jsonl (seeds 7000-7019) and flies nothing. The bins were
chosen after those results were known, so this is a post-hoc breakdown, not a new test. Writes REPORT.md,
summary.json and timing.png to results/wildfire_spread_timing/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

SETS = [("4000-4019", Path("results/wildfire_valley/runs.jsonl")),
        ("7000-7019", Path("results/wildfire_valley_7000/runs.jsonl"))]
BEST_CASE = ("geostationary", "polar", "camera", "public")      # as tools/wildfire_valley_report.py: "today's four"
BINS = [(-np.inf, 0.0, "already spreading at take-off"), (0.0, 20.0, "0 to 20 min after take-off"),
        (20.0, 60.0, "20 to 60 min"), (60.0, 120.0, "60 to 120 min"), (120.0, np.inf, "after 120 min")]
SIZES = (1, 4, 16)


def fires(rows, n):
    """One record per fire for the n-drone flights under the forecast rule (detection does not depend on the rule)."""
    out = []
    for r in sorted((r for r in rows if r["n"] == n and r["alert_rule"] == "forecast"), key=lambda r: r["seed"]):
        for i, (f, today) in enumerate(zip(r["fire_table"], r["today"])):
            c = [today[k]["t"] for k in BEST_CASE if today[k]["t"] is not None]
            best = min(c) if c else None
            t = f["t"] if f["located"] else None
            first = t is not None and (best is None or t < best)
            out.append({"seed": r["seed"], "fire": i, "shelter": f["shelter"], "spread_min": f["t_active_s"] / 60.0,
                        "drones_s": t, "today_s": best, "first": first,
                        "lead_min": ((best - t) / 60.0 if best is not None else None) if first else None,
                        "smouldering": t is not None and t < f["t_active_s"]})
    return out


def binned(recs):
    out = []
    for lo, hi, label in BINS:
        g = [f for f in recs if lo <= f["spread_min"] < hi]
        leads = [f["lead_min"] for f in g if f["first"] and f["lead_min"] is not None]
        today_after = [(f["today_s"] / 60.0 - f["spread_min"]) for f in g if f["today_s"] is not None]
        out.append({"bin": label, "fires": len(g), "drones_found": sum(f["drones_s"] is not None for f in g),
                    "drones_first": sum(f["first"] for f in g),
                    "first_today_never": sum(f["first"] and f["today_s"] is None for f in g),
                    "lead_median_min": float(np.median(leads)) if leads else None,
                    "today_after_spread_median_min": float(np.median(today_after)) if today_after else None})
    return out


def chart(per_set, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5.2), dpi=130, gridspec_kw={"width_ratios": [1.0, 1.15]})
    colours = {"4000-4019": "#d9542b", "7000-7019": "#4e79a7"}
    x = np.arange(len(BINS))
    w = 0.38
    for k, (name, recs) in enumerate(per_set.items()):
        b = binned(recs[16])
        share = [100.0 * r["drones_first"] / r["fires"] if r["fires"] else 0.0 for r in b]
        bars = a1.bar(x + (k - 0.5) * w, share, w, color=colours[name], label=f"seeds {name}")
        for rect, r in zip(bars, b):
            h = rect.get_height()
            inside = h > 12.0
            a1.text(rect.get_x() + rect.get_width() / 2, h - 2.0 if inside else h + 1.5,
                    f"{r['drones_first']}\nof {r['fires']}", ha="center", va="top" if inside else "bottom", fontsize=8,
                    color="white" if inside else "#222222")
    a1.set_xticks(x, ["spreading\nat take-off", "0-20", "20-60", "60-120", "after\n120"])
    a1.set_xlabel("when the fire starts to spread (minutes after take-off)")
    a1.set_ylabel("fires the 16 drones reported before today's four (%)")
    a1.set_ylim(0, 112)
    a1.legend(frameon=False, loc="upper left")
    a1.set_title("Who reported it first, by when the fire spreads", fontsize=11)
    top = 0.0
    for name, recs in per_set.items():
        r16 = [f for f in recs[16] if f["first"]]
        fin = [f for f in r16 if f["lead_min"] is not None]
        inf = [f for f in r16 if f["lead_min"] is None]
        top = max(top, max((f["lead_min"] for f in fin), default=0.0))
        a2.scatter([f["spread_min"] for f in fin], [f["lead_min"] for f in fin], s=22, color=colours[name], alpha=0.75,
                   label=f"seeds {name}: {len(r16)} fires reported first")
        if inf:
            a2.scatter([f["spread_min"] for f in inf], [265.0] * len(inf), s=34, marker="^", color=colours[name],
                       alpha=0.75)
    xs = np.array([0.0, 250.0])
    a2.plot(xs, xs, color="#444444", lw=1.0, ls="--")
    a2.text(178.0, 128.0, "lead = time left before\nthe fire spreads", fontsize=8, color="#444444", rotation=33)
    a2.axhline(258.0, color="#bbbbbb", lw=0.8)
    a2.text(2.0, 268.0, "today's four never found it in 4 h", fontsize=8, color="#444444")
    a2.set_xlim(-10, 250)
    a2.set_ylim(0, 285)
    a2.set_xlabel("when the fire starts to spread (minutes after take-off)")
    a2.set_ylabel("16 drones' lead over today's four (minutes)")
    a2.legend(frameon=False, loc="upper left", fontsize=8, bbox_to_anchor=(0.0, 0.93))
    a2.set_title("The lead tracks the smouldering time the scenario sets", fontsize=11)
    for ax in (a1, a2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/wildfire_spread_timing")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    per_set = {}
    for name, path in SETS:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        per_set[name] = {n: fires(rows, n) for n in SIZES}
    summary = {"post_hoc": True, "sets": {}}
    lines = []
    w = lines.append
    w("# Who reported each fire first, by when it starts to spread\n")
    w("A post-hoc breakdown of the committed held-out rows (seeds 4000-4019 in `results/wildfire_valley/`, seeds "
      "7000-7019 in `results/wildfire_valley_7000/`). Nothing was flown for it, and the bins were picked after those "
      "results were known. Made by `tools/wildfire_spread_timing.py`.\n")
    w("Why: the drones' lead over today's methods comes mostly from the smouldering phase, and the scenario sets how "
      "long each fire smoulders (from 10 to 240 minutes after take-off, by type; `docs/WILDFIRE.md`). Observed lightning "
      "holdover times are longer: across 42 datasets the median time from strike to detection ranges from 1.4 to "
      "53.6 hours (Moris et al. 2023, Earth System Science Data 15: 1151). \"Today's four\" are the geostationary and "
      "polar satellites, an AI camera network with a clear view, and public calls, modelled as in the main report; "
      "\"first\" means the drones' report came before the earliest of the four.\n")
    for name, recs in per_set.items():
        summary["sets"][name] = {}
        for n in SIZES:
            b = binned(recs[n])
            summary["sets"][name][str(n)] = b
        w(f"## Seeds {name}\n")
        w("| When the fire starts to spread | Fires | Found by 16 drones | Reported first by 16 drones | Lead when first, "
          "median (min) | Today's four, median minutes after it spread | Reported first by 4 drones | by 1 drone |")
        w("|---|---|---|---|---|---|---|---|")
        b16, b4, b1 = (binned(recs[n]) for n in (16, 4, 1))
        for r, r4, r1 in zip(b16, b4, b1):
            lead = "n/a" if r["lead_median_min"] is None else f"{r['lead_median_min']:.0f}"
            after = "n/a" if r["today_after_spread_median_min"] is None else f"{r['today_after_spread_median_min']:.0f}"
            w(f"| {r['bin']} | {r['fires']} | {r['drones_found']} | {r['drones_first']} | {lead} | {after} | "
              f"{r4['drones_first']} | {r1['drones_first']} |")
        tot = sum(r["drones_first"] for r in b16)
        w(f"\nAll fires: 16 drones reported {tot} of {sum(r['fires'] for r in b16)} first.\n")
    a4, a7 = (binned(per_set[s][16]) for s in ("4000-4019", "7000-7019"))
    early = [(r["drones_first"], r["fires"], r["lead_median_min"]) for r in (a4[1], a7[1])]
    w("## What it shows\n")
    w(f"- Fires already spreading at take-off: the drones were first on {a4[0]['drones_first']} of {a4[0]['fires']} "
      f"and {a7[0]['drones_first']} of {a7[0]['fires']}. A satellite or a camera usually has them before the swarm "
      "takes off; the drones add a precise position and the spread forecast, not an earlier report.")
    w(f"- Fires that start to spread within 20 minutes of take-off, the shortest smoulders in the scenario: the drones "
      f"were first on {early[0][0]} of {early[0][1]} and {early[1][0]} of {early[1][1]}, by a median of "
      f"{early[0][2]:.0f} and {early[1][2]:.0f} minutes. That lead needs no long smoulder.")
    w("- Longer smoulders give longer leads, close to the time left before the fire spreads plus today's detection "
      "delay (right panel of `timing.png`). So the 69-minute median lead in the main report (62 on seeds 7000-7019) "
      "is a property of the smoulder times the scenario draws, not something the drones add on their own. Observed "
      "holdover times run from strike to detection, so they are not a smoulder time, but they are long (medians of "
      "1.4 to 53.6 hours), which suggests the scenario's 10 to 240 minutes are not generous.")
    w("")
    (out / "REPORT.md").write_text("\n".join(lines))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    chart(per_set, out / "timing.png")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
