"""Command line: ``swarmlab <command>``. Run ``swarmlab -h`` for the list."""

from __future__ import annotations

import argparse
import json
import sys


def _seeds(text: str):
    from .eval.runner import HELD_OUT_SEEDS, TUNING_SEEDS
    if text == "tuning":
        return list(TUNING_SEEDS)
    if text == "heldout":
        return list(HELD_OUT_SEEDS)
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def cmd_list(args) -> int:
    from .eval.scenarios import SCENARIOS, make
    for name in SCENARIOS:
        sc = make(name, 0)
        print(f"{name:18s} {sc.n:3d} drones  {sc.title}: {sc.description}")
    return 0


def cmd_run(args) -> int:
    from .eval.runner import run_one
    kw = {}
    if args.cache or args.ai_log:
        kw["supervisor_kw"] = {"cache_path": args.cache, "cache_mode": args.cache_mode, "log_path": args.ai_log}
    row, result = run_one(args.scenario, args.seed, args.config, n=args.n, record_hz=args.record_hz,
                          keep_result=True, **kw)
    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"row": row, **result.to_dict(include_frames=args.record_hz > 0, include_series=args.series)}, fh)
    print(json.dumps(row, indent=2))
    return 0


def cmd_bench(args) -> int:
    from .eval.runner import run_grid
    extra = {}
    if args.cache or args.ai_log:
        extra["supervisor_kw"] = {"cache_path": args.cache, "cache_mode": args.cache_mode, "log_path": args.ai_log}
    if args.gps_scale is not None:
        extra["gps_scale"] = args.gps_scale
    ns = [int(x) for x in args.n.split(",")] if args.n else None
    rows = run_grid(args.scenarios.split(","), _seeds(args.seeds), args.configs.split(","), args.out,
                    n=ns, workers=args.workers, extra=extra)
    errors = [r for r in rows if "error" in r]
    print(f"{len(rows)} rows in {args.out}, {len(errors)} errors")
    return 1 if errors else 0


def cmd_doctor(args) -> int:
    from .ai.doctor import run
    return run(args.evidence, model=args.model, flight=args.flight, seed=args.seed, scenario=args.scenario)


def cmd_mission_eval(args) -> int:
    """Score the regex baseline and (with a key) Nemotron on the mission prompt sets."""
    import time as _time
    from .ai.client import NebiusClient, NebiusError
    from .ai.mission import compile_with_model, load_eval_set, regex_baseline, score
    client = None
    if args.model:
        client = NebiusClient(timeout_s=60.0, max_retries=3, log_path=args.ai_log)
        if not client.configured:
            print("NEBIUS_API_KEY is not set: scoring the baseline only")
            client = None
    out = {"sets": {}, "model": args.model if client else None, "reasoning": bool(args.think) if client else None}
    for name in args.sets.split(","):
        items = load_eval_set(name)
        rows = []
        for it in items:
            row = {"id": it["id"], "text": it["text"], "gold": it["gold"]}
            b = regex_baseline(it["text"])
            row["baseline"] = {"spec": b, **score(b, it["gold"])}
            if client:
                t0 = _time.perf_counter()
                try:
                    spec, problems, rec = compile_with_model(it["text"], client, args.model, think=args.think)
                    row["nemotron"] = {"spec": spec, "problems": problems, "record": rec, **score(spec, it["gold"])}
                except NebiusError as exc:
                    if exc.fatal:  # no key, key rejected or budget spent: every later call would fail too
                        print(f"stopping: {exc.reason}", file=sys.stderr)
                        return 3
                    row["nemotron"] = {"error": exc.reason, "correct": 0, "total": len(it["gold"]), "exact": False}
                row["nemotron"]["wall_s"] = round(_time.perf_counter() - t0, 2)
            rows.append(row)
        summary = {}
        for who in ("baseline", "nemotron"):
            got = [r[who] for r in rows if who in r]
            if got:
                summary[who] = {"field_accuracy_pct": round(100 * sum(g["correct"] for g in got) / max(1, sum(g["total"] for g in got)), 1),
                                "exact": sum(1 for g in got if g["exact"]), "prompts": len(got),
                                "refusals_correct": sum(1 for r in rows if r["gold"].get("refused") and r.get(who, {}).get("fields", {}).get("refused")),
                                "refusals_total": sum(1 for r in rows if r["gold"].get("refused")),
                                "errors": sum(1 for g in got if "error" in g)}
        out["sets"][name] = {"summary": summary, "rows": rows}
        print(name, json.dumps(summary))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=1)
    return 0


def cmd_compile(args) -> int:
    from .ai.client import NebiusClient
    from .ai.mission import compile_with_model, regex_baseline
    if args.model:
        spec, problems, rec = compile_with_model(args.text, NebiusClient(log_path=args.ai_log), args.model)
        print(json.dumps({"spec": spec, "problems": problems, "call": rec}, indent=2))
    else:
        print(json.dumps({"spec": regex_baseline(args.text), "note": "regex baseline (pass --model for Nemotron)"}, indent=2))
    return 0


def cmd_report(args) -> int:
    from .eval.report import build
    path = build(args.runs, args.out)
    print(f"wrote {path}")
    return 0


def cmd_stress(args) -> int:
    from pathlib import Path
    from .ai import tester
    kind, _, model = args.searcher.partition(":")
    if kind == "random":
        searcher = tester.RandomSearch(args.seed)
    elif kind == "corners":
        searcher = tester.CornerSearch(args.seed)
    elif kind == "cem":
        searcher = tester.CrossEntropySearch(args.seed)
    elif kind in ("nemotron", "nemotron-think"):
        import os
        from .ai.client import NebiusClient
        model = model or os.environ.get("SWARMLAB_SUPERVISOR_MODEL", "")
        if not model:
            print("give a model: --searcher nemotron:<model id> (see `swarmlab doctor`)", file=sys.stderr)
            return 2
        searcher = tester.NemotronSearch(NebiusClient(timeout_s=180.0, max_retries=2, log_path=args.ai_log), model,
                                         args.seed, think=kind == "nemotron-think", strict=True)
    else:
        print(f"unknown searcher {args.searcher!r}: random, corners, cem or nemotron:<model>", file=sys.stderr)
        return 2
    from .ai.client import NebiusError
    try:
        history = tester.run_search(searcher, budget=args.budget, batch=args.batch, seed=args.seed,
                                    workers=args.workers, progress=print)
    except NebiusError as exc:
        # A search the model could not take part in would be random search under another name.
        print(f"stopped: {exc}", file=sys.stderr)
        return 3
    out = {"searcher": args.searcher, "seed": args.seed, "budget": args.budget, "batch": args.batch,
           "summary": tester.summarise_search(history), "failure_kinds": sorted(tester.failure_kinds(history)),
           "history": history, "model_calls": getattr(searcher, "calls", [])}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"]))
    return 0


def cmd_stress_replay(args) -> int:
    from pathlib import Path
    from .ai import tester
    src = json.loads(Path(args.search).read_text())
    history = tester.replay(src["history"], args.config, workers=args.workers)
    out = {"searcher": src["searcher"], "replay_of": Path(args.search).name,
           "config": args.config, "seed": src["seed"], "budget": src["budget"], "batch": src["batch"],
           "summary": tester.summarise_search(history), "failure_kinds": sorted(tester.failure_kinds(history)),
           "history": history, "model_calls": []}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"]))
    return 0


def cmd_stress_report(args) -> int:
    from pathlib import Path
    from .ai import tester
    results = [json.loads(Path(f).read_text()) for f in args.files]
    rows = ["| File | Searcher | Failures | Distinct failing conditions | Failure kinds | Mildest failure | Random top-ups |",
            "|---|---|---|---|---|---|---|"]
    for f, r in zip(args.files, results):
        s = tester.summarise_search(r["history"])
        mild = "n/a" if s["mildest_failure_stress"] is None else f"{s['mildest_failure_stress']:.2f}"
        who = r["searcher"] + (f" (re-flown on {r['config']})" if r.get("config") else "")
        rows.append(f"| {Path(f).name} | {who} | {s['failures']} | {s['distinct_failing_conditions']} "
                    f"| {s['distinct_failure_kinds']} | {mild} | {s['random_top_ups']} |")
    text = ("# Stress-test search\n\n"
            "Each search spends the same number of simulated flights. A failure is any crash or near miss. "
            "Distinct failing conditions count the same test conditions once, however often they were re-flown. "
            "The stress level runs from 0 (every parameter at its easy end) to 1 (every parameter at its hard end); "
            "a failure at a low stress level is the more useful find.\n\n"
            "## By searcher\n\n" + tester.compare_searches(results) + "\n## By search\n\n" + "\n".join(rows) + "\n")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(f"wrote {args.out}")
    return 0


def cmd_budget(args) -> int:
    from .ai.budget import Budget
    budget = Budget(args.ledger, args.cap)
    if args.add_usd is not None:
        if not args.note:
            print("--add-usd needs --note saying where the spend came from", file=sys.stderr)
            return 2
        budget.add(model=args.model or "", role="manual", prompt_tokens=args.prompt_tokens,
                   completion_tokens=args.completion_tokens, cost_usd=args.add_usd, note=args.note)
    print(json.dumps(budget.summary(), indent=2))
    return 0


def cmd_serve(args) -> int:
    from .dashboard.server import serve
    serve(args.host, args.port, args.results, args.evidence, model=args.model, cache=args.cache, open_browser=args.open)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="swarmlab", description="Supervised drone-swarm formation testbed.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="list the scenarios")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("run", help="run one scenario")
    s.add_argument("scenario")
    s.add_argument("--config", default="improved+rule", help="controller+supervisor, e.g. original+fixed")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--n", type=int, default=None, help="number of drones")
    s.add_argument("--record-hz", type=float, default=0.0, help="frames per second to keep for replay")
    s.add_argument("--series", action="store_true", help="include time series in --out")
    s.add_argument("--out", help="write the full result as JSON")
    s.add_argument("--cache", help="model-call cache (JSON lines) for nemotron supervisors")
    s.add_argument("--cache-mode", default="record", choices=("record", "replay", "off"))
    s.add_argument("--ai-log", default=None, help="append model calls to this JSON-lines log")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("bench", help="run a benchmark grid")
    s.add_argument("--scenarios", required=True)
    s.add_argument("--configs", required=True)
    s.add_argument("--seeds", default="tuning", help="tuning, heldout, or e.g. 0-9,20")
    s.add_argument("--n", default=None, help="comma-separated drone counts (scaling study)")
    s.add_argument("--out", required=True)
    s.add_argument("--workers", type=int, default=None)
    s.add_argument("--gps-scale", type=float, default=None, help="multiply GPS noise and bias (use a separate --out per value)")
    s.add_argument("--cache")
    s.add_argument("--cache-mode", default="record", choices=("record", "replay", "off"))
    s.add_argument("--ai-log", default=None)
    s.set_defaults(fn=cmd_bench)

    s = sub.add_parser("doctor", help="end-to-end check of the Nebius Token Factory integration")
    s.add_argument("--evidence", default="evidence")
    s.add_argument("--model", default=None)
    s.add_argument("--flight", action="store_true", help="also fly a full scenario with Nemotron supervising")
    s.add_argument("--scenario", default="forest_trail")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("stress", help="search for conditions that make the swarm fail")
    s.add_argument("--searcher", default="cem", help="random, corners, cem, nemotron:<model id> or nemotron-think:<model id>")
    s.add_argument("--budget", type=int, default=40, help="simulated flights to spend")
    s.add_argument("--batch", type=int, default=4)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", required=True)
    s.add_argument("--ai-log", default=None)
    s.set_defaults(fn=cmd_stress)

    s = sub.add_parser("stress-replay", help="fly a saved search's tests again with another configuration")
    s.add_argument("search")
    s.add_argument("--config", required=True, help="e.g. improved-no-cbf+rule")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_stress_replay)

    s = sub.add_parser("stress-report", help="compare saved stress searches")
    s.add_argument("files", nargs="+")
    s.add_argument("--out", default="results/STRESS.md")
    s.set_defaults(fn=cmd_stress_report)

    s = sub.add_parser("budget", help="show model spend against the cap, or record spend made elsewhere")
    s.add_argument("--ledger", default=None, help="ledger file (default SWARMLAB_LEDGER or ~/.swarmlab/nebius_ledger.jsonl)")
    s.add_argument("--cap", type=float, default=None, help="cap in USD (default SWARMLAB_BUDGET_USD or 18)")
    s.add_argument("--add-usd", type=float, default=None, help="record spend made outside this tool")
    s.add_argument("--prompt-tokens", type=int, default=0)
    s.add_argument("--completion-tokens", type=int, default=0)
    s.add_argument("--model", default=None)
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_budget)

    s = sub.add_parser("serve", help="start the live dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--results", default="results")
    s.add_argument("--evidence", default="evidence")
    s.add_argument("--model", default=None, help="Nemotron model id, or nemotron-think:<model id> for reasoning on "
                                                  "(default SWARMLAB_SUPERVISOR_MODEL)")
    s.add_argument("--cache", default=None, help="Nemotron call cache: replays recorded runs without a key, records with one")
    s.add_argument("--open", action="store_true", help="open a browser")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("report", help="tables and charts from a benchmark rows file")
    s.add_argument("runs")
    s.add_argument("--out", default="results")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("mission-eval", help="score the mission compiler against the regex baseline")
    s.add_argument("--sets", default="tuning,heldout")
    s.add_argument("--model", default=None, help="Nemotron model id (needs NEBIUS_API_KEY); omit for baseline only")
    s.add_argument("--think", action="store_true", help="turn on the model's reasoning mode")
    s.add_argument("--out", default=None)
    s.add_argument("--ai-log", default=None)
    s.set_defaults(fn=cmd_mission_eval)

    s = sub.add_parser("compile", help="turn a plain-language mission into a mission plan")
    s.add_argument("text")
    s.add_argument("--model", default=None)
    s.add_argument("--ai-log", default=None)
    s.set_defaults(fn=cmd_compile)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
