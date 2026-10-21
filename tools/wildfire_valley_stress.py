"""The valley test engineer (T5b): search for conditions where the wildfire valley's station fails.

    PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher random --search 0 \
        --out results/valley_stress/random_0.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher nemotron --search 0 \
        --cache results/valley_stress/calls_cache.jsonl --out results/valley_stress/nemotron_0.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py drawn-day --out results/valley_stress/drawn_day.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py recheck results/valley_stress/*_?.json \
        results/valley_stress/drawn_day.json --out results/valley_stress/recheck_forecast.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py report results/valley_stress/*_?.json \
        results/valley_stress/drawn_day.json --recheck results/valley_stress/recheck_forecast.json \
        --out results/valley_stress/REPORT.md

Every searcher gets the same budget of tests, and test i of search s flies valley seed 9100 + 50 s + i whatever
the searcher, so searchers are compared on the same valleys. ``--replay`` answers Nemotron's turns from the
recorded calls only (no key, no spend), which reproduces a recorded search exactly. ``recheck`` flies every
failing test twice more, under the pre-signed rules and under today's alert rule (any located fire, a smoulder
included, can raise a zone to Level 3), with alert delivery lags drawn from a stream of their own so both flights
see the same station picture; the pair tells failures of the pre-signed rules from failures of the station. The
definitions are in ``halo_systems.ai.valley_tester``.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from halo_systems.ai import valley_tester as vt

MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
WHATS = ("late-warning", "missed-fire", "false-alarm", "needless-evacuation", "drone-lost")


def cmd_search(a) -> int:
    if a.searcher == "random":
        searcher = vt.RandomSearch(a.search)
    elif a.searcher == "corners":
        searcher = vt.CornerSearch(a.search)
    elif a.searcher == "nemotron":
        from halo_systems.ai.cache import CallCache
        from halo_systems.ai.client import NebiusClient
        cache = CallCache(a.cache, "replay" if a.replay else "record") if a.cache else None
        if a.replay and cache is None:
            print("--replay needs --cache", file=sys.stderr)
            return 2
        client = None if a.replay else NebiusClient(timeout_s=180.0, max_retries=2, log_path=a.ai_log)
        searcher = vt.NemotronSearch(client, a.model, seed=a.search, think=True, strict=True, cache=cache,
                                     salt={"search": a.search, "base": a.base})
    else:
        print(f"unknown searcher {a.searcher!r}: random, corners or nemotron", file=sys.stderr)
        return 2
    from halo_systems.ai.client import NebiusError
    aborted = Path(a.out + ".aborted_calls.jsonl")
    try:
        history = vt.run_search(searcher, budget=a.budget, batch=a.batch, search=a.search, base=a.base,
                                workers=a.workers, progress=lambda m: print(m, flush=True))
    except NebiusError as exc:
        # A search without the model would be random search, so it stops. Its calls, the one that stopped it
        # included, are kept, so their cost reaches the result of the run that finishes the search.
        with aborted.open("a") as f:
            for c in getattr(searcher, "calls", []):
                if not c.get("replayed"):
                    f.write(json.dumps({**c, "stopped_by": str(exc)[:200]}) + "\n")
        print(f"stopped: {exc}", file=sys.stderr)
        return 3
    calls = getattr(searcher, "calls", [])
    earlier = [json.loads(x) for x in aborted.read_text().splitlines() if x.strip()] if aborted.exists() else []
    out = {"searcher": a.searcher, "model": a.model if a.searcher == "nemotron" else None, "search": a.search,
           "base": a.base, "budget": a.budget, "batch": a.batch, "config": vt.CONFIG, "alerts": vt.ALERTS,
           "summary": vt.summarise_search(history), "failure_kinds": sorted(vt.failure_kinds(history)),
           "history": history, "model_calls": calls,
           # New spend in this run (failed but billed calls included), and the cost of every call behind the
           # result as recorded, replayed ones included (the same as new spend on the live run).
           "model_cost_usd": round(sum(c.get("cost_usd") or 0.0 for c in calls if not c.get("replayed")), 6),
           # Calls of earlier runs of this search that stopped (a failure that was not the model's reply).
           "aborted_runs_calls": earlier,
           "recorded_cost_usd": round(sum(c.get("cost_usd") or 0.0 for c in calls + earlier), 6)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"]))
    return 0


def cmd_drawn_day(a) -> int:
    """The control: the drawn day's conditions on the same valleys the searches fly."""
    seeds = [vt.valley_seed(k, i, a.base) for k in range(a.searches) for i in range(a.budget)]
    with ProcessPoolExecutor(a.workers) as ex:
        outs = list(ex.map(vt.evaluate, [vt.DEFAULTS] * len(seeds), seeds))
    history = [{"i": j, "params": vt.clip_params(vt.DEFAULTS), "why": "the drawn day", "seed": s, "outcome": o}
               for j, (s, o) in enumerate(zip(seeds, outs))]
    out = {"searcher": "drawn-day", "model": None, "search": None, "base": a.base, "budget": len(seeds),
           "config": vt.CONFIG, "alerts": vt.ALERTS, "summary": vt.summarise_search(history),
           "failure_kinds": sorted(vt.failure_kinds(history)), "history": history, "model_calls": [],
           "model_cost_usd": 0.0}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"]))
    return 0


def _recheck_job(job):
    params, seed, rule = job
    return vt.evaluate(params, seed, alert_rule=rule, paired=True)


def cmd_recheck(a) -> int:
    tests = []
    for f in a.files:
        r = json.loads(Path(f).read_text())
        for h in r["history"]:
            if h["outcome"]["failed"]:
                tests.append({"file": Path(f).name, "searcher": r["searcher"], "i": h["i"], "seed": h["seed"],
                              "params": h["params"], "as_searched": h["outcome"]})
    jobs = [(t["params"], t["seed"], rule) for t in tests for rule in ("presigned", "forecast")]
    with ProcessPoolExecutor(a.workers) as ex:
        outs = list(ex.map(_recheck_job, jobs))
    for j, t in enumerate(tests):
        t["presigned_paired"], t["today_rule"] = outs[2 * j], outs[2 * j + 1]
    Path(a.out).write_text(json.dumps({"alert_rule": "forecast", "paired": True, "tests": tests}, indent=1))
    print(f"re-flew {len(tests)} failing tests in pairs, pre-signed and today's rule")
    return 0


def _changed(p: dict) -> str:
    d = [f"{k} {p[k]:g}" for k in vt.NAMES if p[k] != vt.DEFAULTS[k]]
    return ", ".join(d) or "the drawn day"


def drawn_kinds(results: list[dict]) -> dict | None:
    """seed -> what went wrong on that valley under the drawn day, from the drawn-day file among ``results``."""
    d = [r for r in results if r["searcher"] == "drawn-day"]
    return {h["seed"]: h["outcome"]["kinds"] for r in d for h in r["history"]} if d else None


def report(results: list[dict], recheck: dict | None) -> str:
    drawn = drawn_kinds(results)
    by: dict[str, list[dict]] = {}
    for r in results:
        by.setdefault(r["searcher"], []).append(r)
    lines = ["| Searcher | Searches | Tests | Failures the conditions caused | Their kinds | Mildest of them (stress) "
             "| Failed tests (any) | " + " | ".join(WHATS) + " | Phones warned late | Random top-ups | Model spend |",
             "|---|---|---|---|---|---|---|" + "---|" * len(WHATS) + "---|---|---|"]
    for name, rs in by.items():
        hist = [h for r in rs for h in r["history"]]
        s = [vt.summarise_search(r["history"], drawn if name != "drawn-day" else None) for r in rs]
        own = [h for h in hist if vt.caused(h, drawn)] if drawn is not None and name != "drawn-day" else []
        kinds = {w + ":" + "+".join(vt.causes(h["params"]) or ["other"]) for h in own for w in vt.caused(h, drawn)}
        levels = [vt.stress_level(h["params"]) for h in own]
        na = "n/a" if drawn is None or name == "drawn-day" else None
        lines.append(f"| {name} | {len(rs)} | {len(hist)} | {na or len(own)} | {na or len(kinds)} "
                     f"| {na or (f'{min(levels):.2f}' if levels else 'none')} "
                     f"| {sum(h['outcome']['failed'] for h in hist)} | "
                     + " | ".join(str(sum(w in h["outcome"]["kinds"] for h in hist)) for w in WHATS)
                     + f" | {sum(x['phones_warned_late'] for x in s)} | {sum(x['random_top_ups'] for x in s)} "
                     + f"| USD {sum(r.get('recorded_cost_usd', r.get('model_cost_usd', 0.0)) for r in rs):.4f} |")
    text = ["# Valley test engineer (T5b)\n",
            "Searchers hunt for conditions under which the wildfire valley's ground station fails. Each search flies "
            "the same number of tests; test i of search s flies valley seed 9100 + 50 s + i for every searcher, and "
            "the drawn day (the committed valley's own conditions) is flown on the same valleys. The system under "
            "test is today's flight (improved controller, rule supervisor) with pre-signed alert rules, sent "
            "automatically; no language model takes part in any alert or card.\n",
            "A test fails on a late warning (a phone the fire reached within 3 h, with signal, warned late or never), "
            "a missed fire (a fire in the survey block had been spreading for 5 minutes or more by the end of the "
            "flight, time before take-off included, and was never located), a false alarm (a public alert resting only on fires that do not exist), a needless evacuation "
            "(a Level 4 or 5 alert for a zone no fire comes near in 4 h) or a lost drone. Some valleys fail under the "
            "drawn day too, so a failure counts as caused by the conditions only when the same valley under the "
            "drawn day did not fail that way. Kinds pair what went wrong with coarse labels for the conditions "
            "(`causes()`); the stress level runs from 0 (every condition at its easy end) to 1 (all at the hard "
            "end).\n", "## By searcher\n", "\n".join(lines) + "\n"]
    text.append("## Failing tests\n")
    rows = ["| Searcher | Search | Test | Seed | What went wrong | Not on the drawn day | Conditions (changed from the "
            "drawn day) | Stress | Why (model) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r["searcher"] == "drawn-day":
            continue
        for h in r["history"]:
            if h["outcome"]["failed"]:
                why = h["why"] if r["searcher"] == "nemotron" else ""
                new = ", ".join(sorted(vt.caused(h, drawn))) if drawn is not None else "?"
                rows.append(f"| {r['searcher']} | {r['search']} | {h['i'] + 1} | {h['seed']} "
                            f"| {', '.join(h['outcome']['kinds'])} | {new or 'none'} | {_changed(h['params'])} "
                            f"| {vt.stress_level(h['params']):.2f} | {' '.join(why.split()).replace('|', '/')} |")
    text.append("\n".join(rows) + "\n")
    if recheck:
        if any("presigned_paired" not in x for x in recheck["tests"]):
            raise ValueError("this recheck file is not paired (made before the paired recheck); run recheck again")
        t = recheck["tests"]
        with_drawn = any(x["file"] == "drawn_day.json" or x.get("searcher") == "drawn-day" for x in t)
        same = sum(set(x["presigned_paired"]["kinds"]) == set(x["as_searched"]["kinds"]) for x in t)
        rows = ["| What went wrong | Under pre-signed rules | Also under today's rule | Only under today's rule |",
                "|---|---|---|---|"]
        for w in WHATS:
            p_ = [w in x["presigned_paired"]["kinds"] for x in t]
            f_ = [w in x["today_rule"]["kinds"] for x in t]
            rows.append(f"| {w} | {sum(p_)} | {sum(a and b for a, b in zip(p_, f_))} | "
                        f"{sum(b and not a for a, b in zip(p_, f_))} |")
        late = (sum(x["presigned_paired"]["phones_missed"] for x in t), sum(x["today_rule"]["phones_missed"] for x in t))
        text.append("## Failing tests re-flown under both rules\n")
        text.append(f"Each failing test{' (drawn-day tests included)' if with_drawn else ''} flown twice more on the same valley and conditions, "
                    f"once under the pre-signed rules and once under today's rule (any located fire, a smoulder "
                    f"included, can raise a zone to Level 3). In both flights the alert delivery lags come from a "
                    f"stream of their own, so the station locates the same fires and forecasts the same way and only "
                    f"the alerts differ; the searches themselves drew the lags from the station's stream, so a "
                    f"re-flight can differ slightly from the test as searched ({same} of {len(t)} went wrong in "
                    f"exactly the same ways). Phones warned late: {late[0]} under pre-signed rules, {late[1]} under "
                    f"today's rule.\n")
        text.append("\n".join(rows) + "\n")
    return "\n".join(text)


def cmd_report(a) -> int:
    results = [json.loads(Path(f).read_text()) for f in a.files]
    recheck = json.loads(Path(a.recheck).read_text()) if a.recheck else None
    Path(a.out).write_text(report(results, recheck))
    drawn = drawn_kinds(results)
    summary = {r["searcher"] + f"_{r['search']}": vt.summarise_search(r["history"], drawn if r["searcher"] != "drawn-day"
                                                                        else None) for r in results}
    Path(a.out).with_name("summary.json").write_text(json.dumps(summary, indent=1))
    print(f"wrote {a.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--searcher", required=True, help="random, corners or nemotron")
    s.add_argument("--model", default=MODEL)
    s.add_argument("--search", type=int, default=0, help="search number: test i flies seed base + 50 x search + i")
    s.add_argument("--base", type=int, default=9100, help="first valley seed (development: 3000, at most 20 tests)")
    s.add_argument("--budget", type=int, default=20)
    s.add_argument("--batch", type=int, default=4)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--cache", default=None, help="record Nemotron's replies here (or answer from it with --replay)")
    s.add_argument("--replay", action="store_true")
    s.add_argument("--ai-log", default=None)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("drawn-day", help="fly the drawn day's conditions on the searches' valleys")
    s.add_argument("--searches", type=int, default=2)
    s.add_argument("--budget", type=int, default=20)
    s.add_argument("--base", type=int, default=9100)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_drawn_day)
    s = sub.add_parser("recheck")
    s.add_argument("files", nargs="+")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_recheck)
    s = sub.add_parser("report")
    s.add_argument("files", nargs="+")
    s.add_argument("--recheck", default=None)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
