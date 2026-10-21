"""Fly every drawn-day valley of T5b (seeds 9100-9119 and 9150-9169) under both alert rules, paired.

Added after the held-out run: the recheck re-flew only the tests that failed under the pre-signed rules, which
cannot say whether today's rule fails on the valleys the pre-signed rules passed. This flies all 40 drawn-day
valleys under both rules with delivery lags on their own stream (the recheck's pairing) and writes
results/valley_stress/drawn_day_both_rules.json. The committed file was made from a checkout of 902d69d.
No language model and no key: it reads results/valley_stress/drawn_day.json. It overwrites --out whole, so give a
new --out to compare with the committed file.

    env -u NEBIUS_API_KEY PYTHONPATH=src python3 tools/wildfire_valley_both_rules.py --out check/drawn_day_both_rules.json
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from halo_systems.ai import valley_tester as vt


def job(j):
    params, seed, rule = j
    return vt.evaluate(params, seed, alert_rule=rule, paired=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--drawn-day", default="results/valley_stress/drawn_day.json", help="the drawn-day search to re-fly")
    ap.add_argument("--out", default="results/valley_stress/drawn_day_both_rules.json")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    out = Path(a.out)
    d = json.loads(Path(a.drawn_day).read_text())
    tests = [{"i": h["i"], "seed": h["seed"], "params": h["params"], "as_searched": h["outcome"]} for h in d["history"]]
    jobs = [(t["params"], t["seed"], rule) for t in tests for rule in ("presigned", "forecast")]
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(job, jobs))
    for k, t in enumerate(tests):
        t["presigned_paired"], t["today_rule"] = res[2 * k], res[2 * k + 1]
    out.write_text(json.dumps({"what": "every drawn-day valley under both alert rules, paired delivery lags",
                               "code": "902d69d", "tests": tests}, indent=1))
    print(f"wrote {out}: {len(tests)} valleys x 2 rules")


if __name__ == "__main__":
    main()
