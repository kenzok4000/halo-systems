"""Public alerts under pre-signed trigger rules, against today's automatic forecast rule, on fresh seeds.

    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9000-9019 --out results/wildfire_alerts/runs.jsonl
    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts/runs.jsonl

Each seed is flown with 16 drones (today's flight, improved+rule, as the main results) three times:

* forecast / auto: the committed rule. Any located fire, a smoulder included, can raise a zone to Level 3, and
  every checked alert goes out at once.
* presigned / auto: public alerts only for fires the station believes are spreading, each under a trigger rule
  the alerting authority signed in advance (T3 at Level 3, T4, T5); a newly located fire goes to fire dispatch
  as a card, not to the public. Checked alerts go out at once.
* presigned / one tap: the same, but an official approves each alert, after a modelled delay (median 90 s).

The drones fly the same way in all three: alerts do not change the flight. Every row carries the report_v2
scoring keys. ``--report`` writes REPORT.md and summary.json next to the runs.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

CONFIG = "improved+rule"
N = 16
ARMS = [("forecast", "auto"), ("presigned", "auto"), ("presigned", "modelled")]
LABEL = {("forecast", "auto"): "today's rule, automatic", ("presigned", "auto"): "pre-signed rules, automatic",
         ("presigned", "modelled"): "pre-signed rules, one-tap approval"}


def fly(job):
    seed, rule, approval = job
    from halo_systems.eval.runner import run_one
    head = {"seed": seed, "alert_rule": rule, "approval": approval}
    try:
        row = run_one("wildfire_valley", seed, CONFIG, n=N,
                      options={"alert_rule": rule, "approval": approval, "report_v2": True})
        return {**head, **row}
    except Exception as exc:  # one failed flight must not lose the sweep
        return {**head, "error": f"{type(exc).__name__}: {exc}"}


def report(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    bad = [r for r in rows if "error" in r]
    if bad:
        raise SystemExit(f"{len(bad)} rows have errors: {bad[0]['error']}")
    seeds = sorted({r["seed"] for r in rows})
    by = {arm: sorted((r for r in rows if (r["alert_rule"], r["approval"]) == arm), key=lambda r: r["seed"])
          for arm in ARMS}
    for arm, rs in by.items():
        if [r["seed"] for r in rs] != seeds:
            raise SystemExit(f"arm {arm} is missing seeds")
    keys = [("alerts_delivered", "Public alerts sent"), ("alerts_level3", "of them Level 3"),
            ("alerts_for_smoulders", "Sent while every fire behind it was still smouldering"),
            ("alerts_from_false_fires", "Sent for false fires only"),
            ("alerts_not_justified", "Not justified: the fire did not reach the zone in the level's window"),
            ("phones_warned_in_time", "Phones warned before the fire reached them"),
            ("phones_missed", "Phones reached without a warning (with signal)"),
            ("warned_not_reached_3h", "Phones warned that the fire did not reach within 3 h"),
            ("dispatch_cards", "Cards to fire dispatch"), ("dispatch_cards_false", "of them for false fires")]
    summary = {"seeds": [seeds[0], seeds[-1]], "flights": len(rows), "arms": {}}
    lines = ["# Public alerts under pre-signed trigger rules\n",
             f"Seeds {seeds[0]}-{seeds[-1]} ({len(seeds)} valleys), never flown before this measurement; 16 drones on "
             "today's flight (improved+rule), each valley flown under three alert policies. Made by "
             "`tools/wildfire_alerts_sweep.py`. Simulated phones only; no language model takes part in any alert.\n",
             "| Per flight (sum over all valleys) | " + " | ".join(LABEL[a] for a in ARMS) + " |",
             "|---|" + "---|" * len(ARMS)]
    for k, text in keys:
        vals = [sum((r.get(k) or 0) for r in by[a]) for a in ARMS]
        lines.append(f"| {text} | " + " | ".join(str(v) for v in vals) + " |")
        for a, v in zip(ARMS, vals):
            summary["arms"].setdefault(f"{a[0]}/{a[1]}", {})[k] = v
    for k, text in (("lead_time_min_median", "Median warning lead, minutes (median over valleys)"),
                    ("alert_send_delay_s_median", "Median wait for approval, seconds (median over valleys)")):
        vals = []
        for a in ARMS:
            v = [r[k] for r in by[a] if r.get(k) is not None]
            vals.append(float(np.median(v)) if v else None)
            summary["arms"][f"{a[0]}/{a[1]}"][k] = vals[-1]
        lines.append(f"| {text} | " + " | ".join("n/a" if v is None else f"{v:.1f}" for v in vals) + " |")
    reached = sum(r["phones_reached_3h"] for r in by[ARMS[0]])
    lines.append(f"\nPhones the fire reached within 3 hours, all valleys: {reached}.")
    # Paired by valley: each policy against today's rule on the same flight, with a bootstrap over valleys.
    rng = np.random.default_rng(31)
    idx = rng.integers(0, len(seeds), size=(10000, len(seeds)))
    lines += ["", "Paired by valley, against today's rule (total over all valleys, 95% bootstrap interval over valleys):",
              "", "| Difference | " + " | ".join(LABEL[a] for a in ARMS[1:]) + " |", "|---|" + "---|" * (len(ARMS) - 1)]
    summary["paired"] = {}
    for k, text in (("phones_warned_in_time", "Phones warned before the fire reached them"),
                    ("phones_missed", "Phones reached without a warning"),
                    ("alerts_for_smoulders", "Alerts while every fire behind it still smouldered"),
                    ("alerts_not_justified", "Alerts not justified"), ("alerts_delivered", "Public alerts sent")):
        cells = []
        for a in ARMS[1:]:
            d = np.array([(r2.get(k) or 0) - (r1.get(k) or 0) for r1, r2 in zip(by[ARMS[0]], by[a])], dtype=float)
            boot = d[idx].sum(1)
            lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
            summary["paired"].setdefault(f"{a[0]}/{a[1]}", {})[k] = {"total": float(d.sum()), "ci95": [lo, hi]}
            cells.append(f"{d.sum():+.0f} ({lo:+.0f} to {hi:+.0f})")
        lines.append(f"| {text} | " + " | ".join(cells) + " |")
    (path.parent / "REPORT.md").write_text("\n".join(lines) + "\n")
    (path.parent / "summary.json").write_text(json.dumps(summary, indent=1))
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/wildfire_alerts/runs.jsonl")
    ap.add_argument("--seeds", default="9000-9019")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--report", action="store_true", help="write the report from the rows already flown")
    a = ap.parse_args()
    out = Path(a.out)
    if a.report:
        report(out)
        return 0
    lo, _, hi = a.seeds.partition("-")
    seeds = list(range(int(lo), int(hi or lo) + 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["seed"], r["alert_rule"], r["approval"]))
    jobs = [(s, rule, appr) for s in seeds for rule, appr in ARMS if (s, rule, appr) not in done]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers) as pool, out.open("a") as f:
        futures = [pool.submit(fly, j) for j in jobs]
        for k, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{k}/{len(jobs)} seed {row['seed']} {row['alert_rule']}/{row['approval']}"
                  f"{' ERROR ' + row['error'] if 'error' in row else ''} ({time.perf_counter() - t0:.0f} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
