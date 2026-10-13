"""Rule-based supervisor: the strong conventional baseline for Nemotron.

It reads the same state summary Nemotron gets and applies hand-written,
tuned rules that a careful engineer would write for these scenarios:

* Corridor: if the formation will not fit the free corridor ahead (with a
  safety buffer each side), first try tighter spacing, then fly a column.
  Return to the mission plan once the whole formation has room again.
* Turbulence and near misses: widen spacing and raise the safety margin,
  and hold that for a while after it calms down.
* Degraded radio: slow down while many links are stale.
* Energy: when the remaining leg at the mission speed needs more than 90%
  of the mean usable charge, fly the cruise speed with the lowest energy
  per kilometre for the present headwind, until the leg needs less than 60%.

The thresholds were tuned on the tuning seeds (0-9) only (see docs/BENCHMARKS.md).
"""

from __future__ import annotations

from ..control.directive import Directive
from .base import Decision, Supervisor

# CORRIDOR_BUFFER_M and interp_table also feed Simulation._checks, which builds the report's "checks" that
# Nemotron reads. Changing either changes Nemotron's prompt, so its recorded calls would no longer replay.
CORRIDOR_BUFFER_M = 2.0     # free space wanted each side, on top of the safety margin
MIN_SPACING_FRACTION = 0.6  # never squeeze below 60% of the mission spacing to fit a corridor
GUST_LIMIT_MPS = 4.0        # gust peak (m/s, last 5 s) above which spacing and margin widen
STALE_LIMIT = 0.5           # share of stale radio links above which the swarm slows down
HOLD_S = 15.0               # how long the gust and radio responses last after their last trigger
ENERGY_HEADROOM = 0.9       # save energy once the leg needs more than this share of the mean usable charge
ENERGY_RELEASE = 0.6        # stop saving once it needs less than this share


class RuleSupervisor(Supervisor):
    name = "rule"
    label = "Rule-based supervisor"
    uses_ai = False

    def __init__(self):
        self.rough_until = -1.0
        self.radio_until = -1.0
        self.saving = False

    def decide(self, summary: dict, current: Directive, t: float) -> Decision | None:
        mission = dict(summary["mission_directive"])
        cur = summary["current_directive"]
        env, safety, comms, energy = summary["environment"], summary["safety"], summary["comms"], summary["energy"]
        # Every call starts again from the mission plan, so the plan comes back by itself once no rule
        # fires; the only state kept is the two hold timers and the energy flag. The rules run in the
        # order below and a later one overrides an earlier one on the same field: the energy rule's
        # speed replaces the radio slowdown, and the corridor rule can replace the spacing or the shape.
        want = dict(mission)
        reasons = []

        # Weather and near misses: more room between drones.
        gusty = (env.get("gust_peak_mps_last_5s") or 0.0) > GUST_LIMIT_MPS
        close = safety.get("near_misses_last_5s", 0) > 0
        if gusty or close:
            self.rough_until = t + HOLD_S
        if t < self.rough_until:
            want["spacing_m"] = mission["spacing_m"] + 1.5
            want["safety_margin_m"] = min(mission["safety_margin_m"] + 0.5, 2.0)
            reasons.append("gusts or near misses: wider spacing and margin")

        # Badly degraded radio: slow down (drones still hold slots on their own GPS,
        # so re-spacing only adds disruption; measured on the tuning seeds).
        if comms.get("stale_link_fraction", 0.0) > STALE_LIMIT:
            self.radio_until = t + HOLD_S
        if t < self.radio_until:
            want["speed_mps"] = round(mission["speed_mps"] * 0.8, 2)
            reasons.append("stale radio links: slower")

        # Energy: cheapest cruise speed for this headwind when the charge is tight.
        table = energy.get("wh_per_km_by_speed") or {}
        remaining_km = (summary["mission"].get("remaining_m") or 0.0) / 1000.0
        usable = energy.get("usable_wh_mean")
        if table and remaining_km > 0 and usable is not None:
            planned = interp_table(table, mission["speed_mps"])
            if planned * remaining_km > ENERGY_HEADROOM * usable:
                self.saving = True
            elif planned * remaining_km < ENERGY_RELEASE * usable:
                self.saving = False      # hysteresis: only relax once there is clear headroom
            if self.saving:
                best = min(table, key=lambda k: (table[k], float(k)))
                want["speed_mps"] = float(best)
                reasons.append(f"charge tight: {float(best):g} m/s is cheapest per km into this wind")

        # Corridor: fit the formation or fall back to a column.
        free = env.get("free_corridor_width_m")
        if free is not None:
            widths = summary["formation_widths_at_current_spacing_m"]
            per_m = {k: (w / cur["spacing_m"] if cur["spacing_m"] > 0 else 0.0) for k, w in widths.items()}
            need = 2 * (want["safety_margin_m"] + CORRIDOR_BUFFER_M)
            shape = want["formation"]
            fits = per_m[shape] * want["spacing_m"] + need <= free
            if not fits:
                floor = max(summary["limits"]["spacing_floor_m"] + 0.5, MIN_SPACING_FRACTION * mission["spacing_m"])
                squeezed = (free - need) / per_m[shape] if per_m[shape] > 0 else want["spacing_m"]
                if squeezed >= floor:
                    want["spacing_m"] = round(squeezed, 2)
                    reasons.append(f"corridor {free:.0f} m: tighter {shape}")
                else:
                    want["formation"] = "column"
                    reasons.append(f"corridor {free:.0f} m: too narrow for {shape}, flying a column")

        changed = any(_differs(want[k], cur[k]) for k in want)
        if not changed:
            return None
        return Decision(Directive(**want, reason="; ".join(reasons) or "conditions back to normal: mission plan",
                                  source="rule"))


def _differs(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a != b
    return abs(float(a) - float(b)) > 1e-6


def interp_table(table: dict, speed: float) -> float:
    pts = sorted((float(k), v) for k, v in table.items())
    if speed <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if speed <= x1:
            return y0 + (y1 - y0) * (speed - x0) / (x1 - x0)
    return pts[-1][1]
