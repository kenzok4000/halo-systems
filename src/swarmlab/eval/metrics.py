"""Run metrics, all computed from ground truth recorded by the engine.

Definitions (also in docs/BENCHMARKS.md):

* tracking error: distance from each flying drone to its own slot (RMS over
  the swarm) after removing the swarm's mean offset ("shape error"), so a
  formation blown sideways in one piece still tracks well.
* formation error (integrity): RMS distance from the flying drones to the
  ideal formation for their number, with the best matching of drones to
  positions. It does not care which slot a controller gave each drone, so
  holes left by lost drones and half-finished changes both count. This is
  the headline formation-quality number.
* formed: formation error under ``FORMED_M`` for at least ``HOLD_S`` seconds.
* settle time: from the start (or a formation change) until formed.
* recovery time: from a disturbance (gust front, motor failure, radio
  blackout) until formed again; 0 if the shape never broke.
* collision: two bodies' centres closer than two body radii at any instant
  of a step (swept, so fast crossings count). Near miss: closer than two
  radii plus 0.5 m, counted once per encounter.
* energy: electrical energy from the thrust-based power model, per drone
  and per kilometre flown.
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
    s = {k: np.asarray(v, dtype=float) for k, v in sim.series.items()}
    t = s["t"]
    shape = np.nan_to_num(s["shape_error"], nan=0.0)
    rms = np.nan_to_num(s["rms_error"], nan=0.0)
    form = np.nan_to_num(s["integrity_error"], nan=0.0)
    duration = float(t[-1]) if len(t) else 0.0
    events = sim.events

    settle = _first_formed(t, form, 0.0)
    # Formation changes ordered by an operator or a supervisor.
    change_times = [e["t"] for e in events if e["kind"] == "directive"]
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
        "mean_power_w_per_drone": round(energy * 3600.0 / flying_drone_s, 2) if flying_drone_s > 0 else None,
        "drones_lost": int(np.sum((statuses == CRASHED) | (statuses == DOWN))),
        "drones_retired": int(np.sum(statuses == RETIRED)),
        "drones_flying_end": int(s["active"][-1]) if len(s["active"]) else 0,
        "ctrl_ms_mean": round(float(ctrl.mean()), 3) if len(ctrl) else None,
        "ctrl_ms_p95": round(float(np.percentile(ctrl, 95)), 3) if len(ctrl) else None,
        "directive_changes": len(change_times),
        "directives_rejected": sum(1 for e in events if e["kind"] == "directive_rejected"),
        "comms_delivery_rate": round(sim.sensing.delivery_rate, 3),
        "ai_calls": len(ai),
        "ai_latency_mean_s": round(float(np.mean(lat)), 3) if lat else None,
        "ai_latency_p95_s": round(float(np.percentile(lat, 95)), 3) if lat else None,
        "ai_fallbacks": sum(1 for c in ai if c.get("fallback")),
        "ai_tokens": int(sum(c.get("total_tokens", 0) for c in ai)),
        "ai_cost_usd": round(float(sum(c.get("cost_usd", 0.0) for c in ai)), 5),
    }
    return metrics
