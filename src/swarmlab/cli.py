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
    if args.cache:
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
    if args.cache:
        extra["supervisor_kw"] = {"cache_path": args.cache, "cache_mode": args.cache_mode, "log_path": args.ai_log}
    ns = [int(x) for x in args.n.split(",")] if args.n else None
    rows = run_grid(args.scenarios.split(","), _seeds(args.seeds), args.configs.split(","), args.out,
                    n=ns, workers=args.workers, extra=extra)
    errors = [r for r in rows if "error" in r]
    print(f"{len(rows)} rows in {args.out}, {len(errors)} errors")
    return 1 if errors else 0


def cmd_doctor(args) -> int:
    from .ai.doctor import run
    return run(args.evidence, model=args.model, flight=args.flight, seed=args.seed, scenario=args.scenario)


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

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
