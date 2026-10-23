"""Public alerts under pre-signed trigger rules, against today's automatic forecast rule, on fresh seeds.

    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9000-9019 --out results/wildfire_alerts/runs.jsonl
    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts/runs.jsonl

Each seed is flown with 16 drones (today's flight, improved+rule, as the main results), once per arm:

* today (forecast / auto): the committed rule. Any located fire, a smoulder included, can raise a zone to Level 3,
  and every checked alert goes out at once.
* presigned (presigned / auto): public alerts only for fires the station believes are spreading, each under a
  trigger rule the alerting authority signed in advance (T3 at Level 3, T4, T5); a newly located fire goes to fire
  dispatch as a card, not to the public. Checked alerts go out at once.
* onetap (presigned / one tap): the same, but an official approves each alert, after a modelled delay (median 90 s).
* presigned-smoulders (presigned / auto, smoulder_alerts): the entry's chosen design since 21 October. The
  pre-signed T3 rule counts a smouldering fire too, as if it took off now, so its public alerts should match
  today's rule alert for alert, while every located fire still goes to fire dispatch as a card.

``--arms`` picks the arms (default ``today,presigned,onetap``, the T3 measurement on seeds 9000-9019). The drones
fly the same way in every arm: alerts do not change the flight. Every row carries the report_v2 scoring keys.
``--report`` writes REPORT.md and summary.json next to the runs; when the presigned-smoulders arm is present it also
compares its public alerts with today's rule alert for alert, and its dispatch cards with the presigned arm's.
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
# name -> (alert_rule, approval, smoulder_alerts)
ARMS_BY_NAME = {"today": ("forecast", "auto", False), "presigned": ("presigned", "auto", False),
                "onetap": ("presigned", "modelled", False), "presigned-smoulders": ("presigned", "auto", True)}
ORDER = list(ARMS_BY_NAME.values())
LABEL = {("forecast", "auto", False): "today's rule, automatic",
         ("presigned", "auto", False): "pre-signed rules, automatic",
         ("presigned", "modelled", False): "pre-signed rules, one-tap approval",
         ("presigned", "auto", True): "pre-signed rules, smoulders too, automatic"}


WORDS = {2: "two", 3: "three", 4: "four"}


def arm_of(row: dict) -> tuple:
    """The arm a row belongs to; rows written before the smoulder arm existed carry no smoulder_alerts key."""
    return (row["alert_rule"], row["approval"], bool(row.get("smoulder_alerts", False)))


def fly(job):
    seed, rule, approval, smoulder = job
    from halo_systems.eval.runner import run_one
    head = {"seed": seed, "alert_rule": rule, "approval": approval}
    if smoulder:
        head["smoulder_alerts"] = True
    options = {"alert_rule": rule, "approval": approval, "report_v2": True}
    if smoulder:
        options["smoulder_alerts"] = True
    try:
        row = run_one("wildfire_valley", seed, CONFIG, n=N, options=options)
        return {**head, **row}
    except Exception as exc:  # one failed flight must not lose the sweep
        return {**head, "error": f"{type(exc).__name__}: {exc}"}


def alert_keys(row: dict) -> list:
    """Each public alert as (time sent, level, zone), the identity used to compare two policies alert for alert."""
    return sorted((round(a["sent_t"], 1), a["level"], a["zone"]) for a in (row.get("alert_table") or []))


def report(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    bad = [r for r in rows if "error" in r]
    if bad:
        raise SystemExit(f"{len(bad)} rows have errors: {bad[0]['error']}")
    seeds = sorted({r["seed"] for r in rows})
    present = {arm_of(r) for r in rows}
    arms = [a for a in ORDER if a in present]
    if arms[0] != ORDER[0]:
        raise SystemExit("today's rule (forecast/auto) must be flown: it is the baseline of the paired comparison")
    by = {arm: sorted((r for r in rows if arm_of(r) == arm), key=lambda r: r["seed"]) for arm in arms}
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

    def arm_id(a):
        return f"{a[0]}/{a[1]}" + ("/smoulders" if a[2] else "")

    lines = ["# Public alerts under pre-signed trigger rules\n",
             f"Seeds {seeds[0]}-{seeds[-1]} ({len(seeds)} valleys), never flown before this measurement; 16 drones on "
             f"today's flight (improved+rule), each valley flown under {WORDS.get(len(arms), len(arms))} alert policies. Made by "
             "`tools/wildfire_alerts_sweep.py`. Simulated phones only; no language model takes part in any alert.\n",
             "| Per flight (sum over all valleys) | " + " | ".join(LABEL[a] for a in arms) + " |",
             "|---|" + "---|" * len(arms)]
    for k, text in keys:
        vals = [sum((r.get(k) or 0) for r in by[a]) for a in arms]
        lines.append(f"| {text} | " + " | ".join(str(v) for v in vals) + " |")
        for a, v in zip(arms, vals):
            summary["arms"].setdefault(arm_id(a), {})[k] = v
    for k, text in (("lead_time_min_median", "Median warning lead, minutes (median over valleys)"),
                    ("alert_send_delay_s_median", "Median wait for approval, seconds (median over valleys)")):
        vals = []
        for a in arms:
            v = [r[k] for r in by[a] if r.get(k) is not None]
            vals.append(float(np.median(v)) if v else None)
            summary["arms"][arm_id(a)][k] = vals[-1]
        lines.append(f"| {text} | " + " | ".join("n/a" if v is None else f"{v:.1f}" for v in vals) + " |")
    reached = sum(r["phones_reached_3h"] for r in by[arms[0]])
    lines.append(f"\nPhones the fire reached within 3 hours, all valleys: {reached}.")
    # Paired by valley: each policy against today's rule on the same flight, with a bootstrap over valleys.
    rng = np.random.default_rng(31)
    idx = rng.integers(0, len(seeds), size=(10000, len(seeds)))
    lines += ["", "Paired by valley, against today's rule (total over all valleys, 95% bootstrap interval over valleys):",
              "", "| Difference | " + " | ".join(LABEL[a] for a in arms[1:]) + " |", "|---|" + "---|" * (len(arms) - 1)]
    summary["paired"] = {}
    for k, text in (("phones_warned_in_time", "Phones warned before the fire reached them"),
                    ("phones_missed", "Phones reached without a warning"),
                    ("alerts_for_smoulders", "Alerts while every fire behind it still smouldered"),
                    ("alerts_not_justified", "Alerts not justified"), ("alerts_delivered", "Public alerts sent")):
        cells = []
        for a in arms[1:]:
            d = np.array([(r2.get(k) or 0) - (r1.get(k) or 0) for r1, r2 in zip(by[arms[0]], by[a])], dtype=float)
            boot = d[idx].sum(1)
            lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
            summary["paired"].setdefault(arm_id(a), {})[k] = {"total": float(d.sum()), "ci95": [lo, hi]}
            cells.append(f"{d.sum():+.0f} ({lo:+.0f} to {hi:+.0f})")
        lines.append(f"| {text} | " + " | ".join(cells) + " |")
    smoulder = ("presigned", "auto", True)
    if smoulder in by:
        # Alert for alert: the smoulder arm's public alerts against today's rule's on the same valley, each alert
        # identified by the time it was sent, its level and its zone.
        same = 0
        only_today = only_smoulder = 0
        for r1, r2 in zip(by[arms[0]], by[smoulder]):
            k1, k2 = alert_keys(r1), alert_keys(r2)
            if k1 == k2:
                same += 1
            s1, s2 = set(k1), set(k2)
            only_today += len(s1 - s2)
            only_smoulder += len(s2 - s1)
        cards = None
        if ("presigned", "auto", False) in by:
            spreading_only = by[("presigned", "auto", False)]
            cards = sum(1 for r1, r2 in zip(spreading_only, by[smoulder])
                        if (r1.get("dispatch_cards"), r1.get("dispatch_cards_false"))
                        == (r2.get("dispatch_cards"), r2.get("dispatch_cards_false")))
        summary["alert_for_alert"] = {"valleys_identical_to_todays_rule": same, "valleys": len(seeds),
                                      "alerts_only_under_todays_rule": only_today,
                                      "alerts_only_under_smoulders_arm": only_smoulder,
                                      "valleys_with_the_spreading_only_arms_card_counts": cards}
        lines += ["", "## Alert for alert: the pre-signed rules with smoulders against today's rule", "",
                  f"Each public alert is identified by the time it was sent, its level and its zone. In {same} of "
                  f"{len(seeds)} valleys the pre-signed rules with smoulders sent the same public alerts as today's "
                  f"rule, alert for alert; {only_today} alerts went out under today's rule only and {only_smoulder} "
                  "under the pre-signed rules with smoulders only."]
        if cards is not None:
            lines.append(f"Their cards to fire dispatch (count, and count for false fires) matched the spreading-only "
                         f"pre-signed rules' in {cards} of {len(seeds)} valleys.")
    (path.parent / "REPORT.md").write_text("\n".join(lines) + "\n")
    (path.parent / "summary.json").write_text(json.dumps(summary, indent=1))
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/wildfire_alerts/runs.jsonl")
    ap.add_argument("--seeds", default="9000-9019")
    ap.add_argument("--arms", default="today,presigned,onetap",
                    help="comma-separated arm names: " + ", ".join(ARMS_BY_NAME))
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--report", action="store_true", help="write the report from the rows already flown")
    a = ap.parse_args()
    out = Path(a.out)
    if a.report:
        report(out)
        return 0
    try:
        arms = [ARMS_BY_NAME[name.strip()] for name in a.arms.split(",") if name.strip()]
    except KeyError as exc:
        raise SystemExit(f"unknown arm {exc}; choose from {', '.join(ARMS_BY_NAME)}")
    lo, _, hi = a.seeds.partition("-")
    seeds = list(range(int(lo), int(hi or lo) + 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["seed"],) + arm_of(r))
    jobs = [(s,) + arm for s in seeds for arm in arms if (s,) + arm not in done]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers) as pool, out.open("a") as f:
        futures = [pool.submit(fly, j) for j in jobs]
        for k, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{k}/{len(jobs)} seed {row['seed']} {row['alert_rule']}/{row['approval']}"
                  f"{'/smoulders' if row.get('smoulder_alerts') else ''}"
                  f"{' ERROR ' + row['error'] if 'error' in row else ''} ({time.perf_counter() - t0:.0f} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
