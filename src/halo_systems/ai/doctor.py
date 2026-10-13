"""End-to-end check of the Nebius Token Factory integration (``halo doctor``).

Steps, each recorded in the evidence directory (``halo doctor`` writes to
``evidence/latest`` by default; the committed check is in ``evidence/``):

1. Environment: is ``NEBIUS_API_KEY`` set (the value is never shown)?
2. Models: list the models the key can use and pick the NVIDIA Nemotron ones.
3. One supervisor call on a real state summary (the forest_trail scenario
   at 22 s, just before the trees), parsed and checked by the safety
   validator. ``--scenario`` does not change this step.
4. Optionally (``--flight``), a full simulated flight of ``--scenario`` with
   Nemotron as the supervisor, every call logged and cached for replay.

The command exits non-zero if any step fails: 2 no key, 3 listing the
models failed, 4 no Nemotron model, 5 the supervisor call failed, 6 the
reply is not a valid directive, 7 the safety validator rejected it, 8 no
call in the flight was used without a fallback. Nothing here is mocked.
"""

from __future__ import annotations

import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

from ..control.improved import ImprovedController
from ..eval.scenarios import make
from ..safety.validator import SafetyValidator
from ..sim.body import DroneBody
from ..settings import env
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

    def finish(code):
        report["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        report["usage"] = client.usage_summary()
        report["passed"] = code == 0
        (ev / "nebius_e2e.json").write_text(json.dumps(report, indent=2))
        b = report["usage"]["budget"]
        print_fn(f"This run: {report['usage']['prompt_tokens']} prompt + {report['usage']['completion_tokens']} completion "
                 f"tokens, about ${report['usage']['cost_usd']:.4f}. Ledger total ${b['spent_usd']:.4f} of the "
                 f"${b['cap_usd']:.2f} cap ({b['ledger']}).")
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
    chosen = model or env("SUPERVISOR_MODEL") or pick_model(models)
    if not step("choose a Nemotron model", bool(chosen) and "nemotron" in (chosen or "").lower(), note=chosen,
                model=chosen):
        return finish(4)

    from ..supervisor.nemotron import NemotronSupervisor, parse_directive
    summary, sim = state_before_forest(seed)
    sup = NemotronSupervisor(client=client, model=chosen, fallback="none")
    # Call the client directly with the supervisor's messages and settings instead of sup.decide():
    # decide() turns a failure into a fallback, while this step reports the HTTP status, finish reason
    # and raw reply. The call uses this client's 30 s timeout and 3 retries and no deadline, so it can
    # wait longer than the supervisor does in flight (8 s per attempt, 15 s in all).
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
        row = run_one(scenario, seed, f"improved+nemotron:{chosen}",
                      supervisor_kw={"cache_path": str(cache), "cache_mode": "record", "log_path": str(log_path)})
        (ev / f"flight_{scenario}_{seed}.json").write_text(json.dumps(row, indent=2))
        ok = row["ai_calls"] > 0 and row["ai_fallbacks"] < row["ai_calls"]
        step("full flight with Nemotron supervising", ok,
             note=f"{row['ai_calls']} calls, {row['ai_fallbacks']} fallbacks, completed={row['completed']}",
             metrics=row)
        if not ok:
            return finish(8)
    return finish(0)
