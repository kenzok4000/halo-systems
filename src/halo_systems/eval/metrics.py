"""Run metrics, all computed from ground truth recorded by the engine.

Definitions (also in docs/BENCHMARKS.md), with the result keys they fill:

* tracking error: distance from each flying drone to its own slot (RMS over
  the swarm) after removing the swarm's mean offset ("shape error"), so a
  formation blown sideways in one piece still tracks well. Reported as
  ``shape_error_*``; ``formation_rms_mean_m`` is the same distance with no
  offset removed.
* formation error (integrity): RMS distance from the flying drones to the
  ideal formation for their number, with the best matching of drones to
  positions. It does not care which slot a controller gave each drone, so
  holes left by lost drones and half-finished changes both count. This is
  the headline formation-quality number. Reported as ``formation_error_*``
  and ``time_formed_pct``.
* formed: formation error under ``FORMED_M`` for at least ``HOLD_S`` seconds.
* directive change: any accepted change of plan, including a speed or
  altitude change that keeps the formation (``directive_changes``).
* settle time: from the start (``settle_time_s``) or from a directive change
  (``transition_settle_s``) until formed. ``settle_time_s`` is None if the
  swarm never forms; a change after which it never forms again counts the
  rest of the flight.
* recovery time: from a disturbance (gust front, motor failure, radio
  blackout) until formed again; 0 if the shape did not break within 10 s of
  the disturbance, and the rest of the flight if it never forms again.
* steady state (``*_steady_m``): from the first settle (or the start, if the
  swarm never forms), leaving out 10 s after each disturbance or change.
* collision: two bodies' centres closer than two body radii at any instant
  of a step (swept, so fast crossings count). Near miss: closer than two
  radii plus 0.5 m, counted once per encounter.
* energy: electrical energy from the thrust-based power model, per drone
  and per kilometre flown.
* scouting (scenarios with a ground sensor): targets found, time to the
  first and the last find, share of the survey area whose cells have been
  inside a camera footprint, and the time to cover 90% of it.
* wildfire (the wildfire valley): fires located and how, smoke location
  error, the last forecast against the true spread, and warning lead times
  and false alarms at the phones; ``Wildfire.evaluate`` and docs/WILDFIRE.md.
* airspace holds (runs with an "aircraft inbound" order only): each hold's
  record (``Simulation.airspace_order``), the total time held and the longest
  time from a hold order until every flying drone was stopped at the hold
  altitude (``airspace_holds``, ``airspace_held_s``, ``airspace_clear_s_max``).
"""

from __future__ import annotations

import math

import numpy as np

FORMED_M = 1.0
HOLD_S = 2.0


def _first_formed(t: np.ndarray, err: np.ndarray, start: float, end: float | None = None) -> float | None:
    """Earliest time >= start at which err stays under FORMED_M for HOLD_S."""
    mask = t >= start
    if end is not None:
        mask &= t <= end + HOLD_S
    idx = np.nonzero(mask)[0]
    if len(idx) == 0:
        return None
    ok = err[idx] < FORMED_M
    run_start = None
    for k, i in enumerate(idx):
        if ok[k]:
            if run_start is None:
                run_start = t[i]
            if t[i] - run_start >= HOLD_S:
                return float(run_start)
        else:
            run_start = None
    return None


def _nanmean(x):
    x = [v for v in x if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return float(np.mean(x)) if x else None


def compute_metrics(sim) -> dict:
    # The series hold one value per engine step. A step with no drone flying records NaN for the formation
    # errors (it is the last step, since the run then ends), and that NaN counts as zero here.
    s = {k: np.asarray(v, dtype=float) for k, v in sim.series.items()}
    t = s["t"]
    shape = np.nan_to_num(s["shape_error"], nan=0.0)
    rms = np.nan_to_num(s["rms_error"], nan=0.0)
    form = np.nan_to_num(s["integrity_error"], nan=0.0)
    duration = float(t[-1]) if len(t) else 0.0
    events = sim.events

    settle = _first_formed(t, form, 0.0)
    # Plan changes ordered by an operator or a supervisor (formation, spacing, speed, altitude or margin).
    change_times = [e["t"] for e in events if e["kind"] == "directive"]
    if getattr(sim, "squads", False):
        # Moving drones between squads re-forms both squads, like a change of plan.
        change_times = sorted(change_times + [e["t"] for e in events if e["kind"] == "squad_changed"])
    transition = []
    for ct in change_times:
        f = _first_formed(t, form, ct)
        transition.append((f - ct) if f is not None else duration - ct)
    # Disturbances.
    onsets = [e["t"] for e in events if e["kind"] in ("gust_front", "motor_failure", "comms_blackout")]
    recovery, peak = [], []
    for on in onsets:
        window = (t >= on) & (t <= on + 10.0)
        peak.append(float(form[window].max()) if window.any() else 0.0)
        broke = (t >= on) & (t <= on + 10.0) & (form >= FORMED_M)
        if not broke.any():
            recovery.append(0.0)
            continue
        first_break = float(t[np.argmax(broke)])
        f = _first_formed(t, form, first_break)
        recovery.append((f - on) if f is not None else duration - on)

    # Steady state: after the initial settle, excluding 10 s after each disturbance or change.
    steady = t >= (settle if settle is not None else 0.0)
    for on in onsets + change_times:
        steady &= ~((t >= on) & (t <= on + 10.0))
    min_sep = s["min_sep"]
    finite_sep = min_sep[np.isfinite(min_sep)]
    ctrl = s["ctrl_ms"][s["ctrl_ms"] > 0]
    distance_km = float(np.sum(sim.distance_m)) / 1000.0
    energy = float(np.sum(sim.energy_wh))
    flying_drone_s = float(np.sum(s["active"]) * sim.dt)
    ai = sim.ai_calls
    lat = [c["latency_s"] for c in ai if c.get("latency_s") is not None]
    statuses = sim.status

    from ..sim.engine import CRASHED, DOWN, RETIRED  # avoid an import cycle at module load

    metrics = {
        "duration_s": round(duration, 2),
        # A looped circuit has no end, so completion does not apply.
        "completed": None if sim.sc.loop else sim.done_t is not None,
        "completion_time_s": round(sim.done_t, 2) if sim.done_t is not None else None,
        "progress_m": round(float(sim.ctrl.plan.s), 1),
        # collisions counts touching pairs, while obstacle and ground crashes count drones. The report's crashes
        # are drones destroyed, so each collision counts twice there (eval/stats.value).
        "collisions": int(sim.collisions),
        "obstacle_crashes": int(sim.obstacle_crashes),
        "ground_crashes": int(sim.ground_crashes),
        "any_collision": bool(sim.collisions + sim.obstacle_crashes + sim.ground_crashes > 0),
        "near_misses": int(sim.near_miss_events),
        "min_separation_m": round(float(finite_sep.min()), 3) if len(finite_sep) else None,
        "formation_rms_mean_m": round(float(rms.mean()), 3) if len(rms) else None,
        "shape_error_mean_m": round(float(shape.mean()), 3) if len(shape) else None,
        "shape_error_p95_m": round(float(np.percentile(shape, 95)), 3) if len(shape) else None,
        "shape_error_steady_m": round(float(shape[steady].mean()), 3) if steady.any() else None,
        "formation_error_mean_m": round(float(form.mean()), 3) if len(form) else None,
        "formation_error_p95_m": round(float(np.percentile(form, 95)), 3) if len(form) else None,
        "formation_error_steady_m": round(float(form[steady].mean()), 3) if steady.any() else None,
        "time_formed_pct": round(100.0 * float(np.mean(form < FORMED_M)), 1) if len(form) else None,
        "settle_time_s": round(settle, 2) if settle is not None else None,
        "transition_settle_s": round(_nanmean(transition), 2) if transition else None,
        "recovery_time_s": round(_nanmean(recovery), 2) if recovery else None,
        "disturbance_peak_error_m": round(_nanmean(peak), 3) if peak else None,
        "energy_wh": round(energy, 3),
        "energy_wh_per_drone": round(energy / sim.n, 3),
        "energy_wh_per_km": round(energy / distance_km, 3) if distance_km > 0.01 else None,
        # Energy in joules (Wh x 3600) over the drone-seconds flown: the mean power of one flying drone.
        "mean_power_w_per_drone": round(energy * 3600.0 / flying_drone_s, 2) if flying_drone_s > 0 else None,
        "drones_lost": int(np.sum((statuses == CRASHED) | (statuses == DOWN))),
        "drones_retired": int(np.sum(statuses == RETIRED)),
        "drones_flying_end": int(s["active"][-1]) if len(s["active"]) else 0,
        "ctrl_ms_mean": round(float(ctrl.mean()), 3) if len(ctrl) else None,
        "ctrl_ms_p95": round(float(np.percentile(ctrl, 95)), 3) if len(ctrl) else None,
        "directive_changes": len(change_times),
        # Share of the flight spent faster than the operator's planned speed (supervisors may slow down
        # for safety, and may speed up when the charge would not last; anything else ignores the plan).
        "time_faster_than_plan_pct": round(100.0 * float(np.mean(np.asarray(s["speed_over_plan"]) > 0.05)), 1)
        if len(s.get("speed_over_plan", [])) else None,
        "directives_rejected": sum(1 for e in events if e["kind"] == "directive_rejected"),
        "comms_delivery_rate": round(sim.sensing.delivery_rate, 3),
        "ai_calls": len(ai),
        "ai_latency_mean_s": round(float(np.mean(lat)), 3) if lat else None,
        "ai_latency_p95_s": round(float(np.percentile(lat, 95)), 3) if lat else None,
        # A fallback is a supervisor call that failed or gave an unusable reply. The rule supervisor's answer
        # took its place or, with no answer from the rules, the directive in force stayed.
        "ai_fallbacks": sum(1 for c in ai if c.get("fallback")),
        "ai_tokens": int(sum(c.get("total_tokens", 0) for c in ai)),
        "ai_cost_usd": round(float(sum(c.get("cost_usd", 0.0) for c in ai)), 5),
    }
    if getattr(sim, "squads", False):
        metrics.update(squad_metrics(sim))
    if sim.sc.sensor.enabled:
        metrics.update(scouting_metrics(sim, t, duration))
    if getattr(sim, "wildfire", None) is not None:
        metrics.update(sim.wildfire.evaluate(sim))
        if getattr(sim, "checker", None) is not None:
            metrics.update(sim.checker.evaluate(sim))
    if getattr(sim, "airspace", False):
        metrics.update(airspace_metrics(sim))
    return metrics


def airspace_metrics(sim) -> dict:
    """Runs with an airspace order only: every hold, the time held in all and the slowest clear (None: no hold
    cleared). A hold still in force at the end counts until the end."""
    holds = [dict(h) for h in sim.holds]
    held = sum((h["to_s"] if h["to_s"] is not None else sim.t) - h["from_s"] for h in holds)
    clears = [h["clear_s"] for h in holds if h["clear_s"] is not None]
    return {"airspace_holds": holds, "airspace_held_s": round(float(held), 2),
            "airspace_clear_s_max": max(clears) if clears else None}


def scouting_metrics(sim, t: np.ndarray, duration: float) -> dict:
    """Targets found and area covered, for scenarios with a ground sensor."""
    found_t = sim.target_found_t
    total = len(found_t)
    found = found_t[~np.isnan(found_t)]
    cov = np.asarray(sim.series.get("coverage_pct", []), dtype=float)
    out = {
        "targets_total": total,
        "targets_found": int(len(found)),
        "targets_found_pct": round(100.0 * len(found) / total, 1) if total else None,
        "first_find_s": round(float(found.min()), 1) if len(found) else None,
        "all_found_s": round(float(found.max()), 1) if total and len(found) == total else None,
        "find_time_mean_s": round(float(found.mean()), 1) if len(found) else None,
        "area_covered_pct": None,
        "coverage_90pct_s": None,
        "coverage_ha_per_min": None,
    }
    if sim.coverage is not None and len(cov):
        area_ha = sim.coverage.size * sim.sc.sensor.cell_m ** 2 / 10000.0
        out["area_covered_pct"] = round(float(cov[-1]), 1)
        hit = np.nonzero(cov >= 90.0)[0]
        out["coverage_90pct_s"] = round(float(t[hit[0]]), 1) if len(hit) else None
        if duration > 0:
            out["coverage_ha_per_min"] = round(float(cov[-1]) / 100.0 * area_ha / (duration / 60.0), 3)
    return out


def squad_metrics(sim) -> dict:
    """Squad runs only: the split flown, and per squad its drones, circuits flown and predicted circuit time."""
    from ..control.squads import canonical
    plan = sim.ctrl.plan
    act = sim.active_mask()
    per = []
    for sq, part, m in zip(plan.squads, plan.parts, plan.part_masks()):
        per.append({"code": sq.code, "start_n": len(sim.sc.squads[sq.id].drones), "end_flying": int((m & act).sum()),
                    "lanes": sq.lanes, "pass_s": round(sq.pass_s, 1),
                    "passes": round(float(part.s / part.path.length), 2) if part.path.length > 0 else None})
    return {"squads": canonical(sim.sc.squad_counts), "patrol_s": sim.sc.patrol_s,
            "reserve": int((sim.sc.squad_counts or {}).get("R", 0)),
            "squad_changes": sum(1 for e in sim.events if e["kind"] == "squad_changed"),
            "squad_table": per}

