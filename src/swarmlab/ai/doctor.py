"""End-to-end check of the Nebius Token Factory integration (``swarmlab doctor``).

Steps, each recorded in ``evidence/``:

1. Environment: is ``NEBIUS_API_KEY`` set (the value is never shown)?
2. Models: list the models the key can use and pick the NVIDIA Nemotron ones.
3. One supervisor call on a real state summary (the forest scenario just
   before the trees), parsed and checked by the safety validator.
4. Optionally (``--flight``), a full simulated flight with Nemotron as the
   supervisor, every call logged and cached for replay.

The command exits non-zero if any step fails. Nothing here is mocked.
"""

from __future__ import annotations

import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

from ..control.improved import ImprovedController
from ..eval.scenarios import make
from ..safety.validator import SafetyValidator
from ..sim.body import DroneBody
from ..sim.engine import Simulation
from .cache import CallCache
from .client import NebiusClient, NebiusError

MODEL_PREFERENCE = ("nemotron-3-nano", "nemotron-nano", "nano", "super", "nemotron")


def pick_model(models: list[str], preference=MODEL_PREFERENCE) -> str | None:
    nemotron = [m for m in models if "nemotron" in m.lower()]
    for p in preference:
        for m in nemotron:
            low = m.lower()
            if p in low and "omni" not in low and "embed" not in low and "safety" not in low and "reward" not in low:
                return m
    return nemotron[0] if nemotron else None


def state_before_forest(seed: int = 0) -> tuple[dict, object]:
    sc = make("forest_trail", seed)
    body = DroneBody()
    sim = Simulation(sc, ImprovedController(body, sc.world), seed=seed, body=body, record_hz=0)
    while sim.t < 22.0:
        sim.step()
    return sim.summary(), sim


def _usage_from_log(log_path: Path, since: float, roles: tuple = ("supervisor",)) -> dict:
    """Tokens and cost of the calls in a call log made at or after ``since`` (epoch seconds) in the given roles."""
    out = {"calls": 0, "ok": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    if not log_path.exists():
        return out
    for line in log_path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("role") not in roles or float(rec.get("started_at") or 0.0) < since - 1.0:
            continue
        out["calls"] += 1
        if rec.get("status") == "ok":
            out["ok"] += 1
            out["prompt_tokens"] += int(rec.get("prompt_tokens") or 0)
            out["completion_tokens"] += int(rec.get("completion_tokens") or 0)
        out["cost_usd"] += float(rec.get("cost_usd") or 0.0)
    out["cost_usd"] = round(out["cost_usd"], 6)
    return out


def run(evidence_dir: str = "evidence", model: str | None = None, flight: bool = False, seed: int = 0,
        scenario: str = "forest_trail", print_fn=print) -> int:
    ev = Path(evidence_dir)
    ev.mkdir(parents=True, exist_ok=True)
    log_path = ev / "nebius_calls.jsonl"
    client = NebiusClient(timeout_s=30.0, max_retries=3, log_path=log_path)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "base_url": client.base_url, "python": platform.python_version(), "steps": []}

    def step(name, ok, **detail):
        report["steps"].append({"step": name, "ok": ok, **detail})
        print_fn(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail.get('note')}" if detail.get("note") else ""))
        return ok

    started_epoch = time.time()

    def finish(code):
        report["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        usage = client.usage_summary()
        # The flight's supervisor calls go through the supervisor's own client, so they are read back
        # from the shared call log; "This run" then covers the whole check, not just the doctor's calls.
        flight_usage = _usage_from_log(log_path, since=started_epoch, roles=("supervisor",))
        usage["flight"] = flight_usage
        usage["run_prompt_tokens"] = usage["prompt_tokens"] + flight_usage["prompt_tokens"]
        usage["run_completion_tokens"] = usage["completion_tokens"] + flight_usage["completion_tokens"]
        usage["run_cost_usd"] = round(usage["cost_usd"] + flight_usage["cost_usd"], 6)
        report["usage"] = usage
        report["passed"] = code == 0
        (ev / "nebius_e2e.json").write_text(json.dumps(report, indent=2))
        b = usage["budget"]
        print_fn(f"This run: {usage['run_prompt_tokens']} prompt + {usage['run_completion_tokens']} completion tokens "
                 f"over {usage['calls'] + flight_usage['calls']} calls ({flight_usage['calls']} in the flight), about "
                 f"${usage['run_cost_usd']:.4f}. Ledger total ${b['spent_usd']:.4f} of the ${b['cap_usd']:.2f} cap ({b['ledger']}).")
        print_fn(f"Evidence written to {ev / 'nebius_e2e.json'} and {log_path}")
        return code

    if not step("NEBIUS_API_KEY is set", client.configured,
                note=None if client.configured else "set it in the environment; never paste it in chat or a file"):
        return finish(2)

    try:
        models = client.list_models()
    except (NebiusError, OSError, ValueError) as exc:
        step("list models", False, note=str(exc)[:300])
        return finish(3)
    nemotron = [m for m in models if "nemotron" in m.lower()]
    step("list models", True, note=f"{len(models)} models, {len(nemotron)} Nemotron", nemotron_models=nemotron)
    chosen = model or os.environ.get("SWARMLAB_SUPERVISOR_MODEL") or pick_model(models)
    if not step("choose a Nemotron model", bool(chosen) and "nemotron" in (chosen or "").lower(), note=chosen,
                model=chosen):
        return finish(4)

    from ..supervisor.nemotron import NemotronSupervisor, parse_directive
    summary, sim = state_before_forest(seed)
    sup = NemotronSupervisor(client=client, model=chosen, fallback="none")
    t0 = time.perf_counter()
    try:
        result = client.chat(chosen, sup._messages(summary), role="doctor-supervisor", temperature=0.2,
                             max_tokens=sup.max_tokens, extra_body=sup.extra_body)
    except NebiusError as exc:
        step("supervisor call", False, note=exc.reason[:300], http_status=exc.status)
        return finish(5)
    step("supervisor call", True, note=f"{result.record.latency_s:.2f} s, {result.record.total_tokens} tokens",
         latency_s=result.record.latency_s, request_id=result.record.request_id,
         finish_reason=result.record.finish_reason, response_format=result.record.response_format)
    try:
        proposal = parse_directive(result.content)
    except ValueError as exc:
        step("reply is a valid directive", False, note=str(exc), reply=result.content[:1500])
        return finish(6)
    validator = SafetyValidator(DroneBody().radius_m)
    checked = validator.check(proposal, sim.directive, 100.0, "nemotron")
    step("reply is a valid directive", checked.ok, note=f"{checked.action}: {proposal}", reply=result.content[:1500],
         validation=checked.to_dict(), wall_s=round(time.perf_counter() - t0, 2))
    if not checked.ok:
        return finish(7)

    if flight:
        from ..eval.runner import run_one
        cache = ev / f"cache_{scenario}_{seed}.jsonl"
        cache.unlink(missing_ok=True)   # a live check: never answer from an earlier run's recording
        try:
            row = run_one(scenario, seed, f"improved+nemotron:{chosen}",
                          supervisor_kw={"cache_path": str(cache), "cache_mode": "record", "log_path": str(log_path)})
        except NebiusError as exc:   # a dead key or the budget cap stops the flight: record it as a failed step
            step("full flight with Nemotron supervising", False, note=exc.reason[:300], http_status=exc.status)
            return finish(8)
        (ev / f"flight_{scenario}_{seed}.json").write_text(json.dumps(row, indent=2))
        ok = row["ai_calls"] > 0 and row["ai_fallbacks"] < row["ai_calls"]
        step("full flight with Nemotron supervising", ok,
             note=f"{row['ai_calls']} calls, {row['ai_fallbacks']} fallbacks, completed={row['completed']}",
             metrics=row)
        if not ok:
            return finish(8)
    return finish(0)
