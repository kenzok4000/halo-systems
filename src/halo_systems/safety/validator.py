"""Safety validation for directives, whoever proposes them.

Every directive from a supervisor (rules, Nemotron or an operator) passes
through ``SafetyValidator.check`` before the controller sees it:

1. Schema: a missing field keeps its current value (noted); an unknown
   formation, a non-number or a non-finite number rejects the proposal.
2. Bounds: values outside the flight envelope are clamped and the clamp is
   recorded; nothing outside the envelope reaches the controller.
3. Feasibility: spacing never drops below what the drone size, safety
   margin and navigation uncertainty require.
4. Rate limit: plan changes closer together than ``min_change_interval_s``
   are rejected, so a chattering supervisor cannot thrash the formation.

A rejected or malformed proposal leaves the last safe directive in force.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..control.directive import DIRECTIVE_FIELDS, Directive
from ..control.formations import SHAPES


@dataclass
class Limits:
    # Each tuple is (lo, hi). The simulator replaces the altitude ceiling with the scenario's. The
    # supervisor prompt (ai/prompts.py) states the 3 s change interval, so a new value needs the
    # prompt changed to match, and recorded Nemotron calls then no longer replay.
    spacing_m: tuple = (2.0, 15.0)
    speed_mps: tuple = (0.5, 12.0)
    altitude_m: tuple = (5.0, 120.0)
    safety_margin_m: tuple = (0.5, 4.0)
    min_change_interval_s: float = 3.0


@dataclass
class ValidationResult:
    action: str                       # accepted | clamped | rejected
    directive: Directive              # what is now in force
    violations: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.action != "rejected"

    def to_dict(self) -> dict:
        return {"action": self.action, "violations": self.violations, "directive": self.directive.to_dict()}


class SafetyValidator:
    def __init__(self, body_radius_m: float, nav_sigma_m: float = 0.3, limits: Limits | None = None):
        self.body_radius_m = body_radius_m
        self.nav_sigma_m = nav_sigma_m
        self.limits = limits or Limits()
        self.last_change_t = -math.inf
        self.log: list[dict] = []

    def spacing_floor(self, margin: float) -> float:
        """Smallest spacing the controller can hold safely: bodies + margin + 3 sigma relative nav error."""
        rel_sigma = math.sqrt(2.0) * self.nav_sigma_m
        return max(self.limits.spacing_m[0], 2 * self.body_radius_m + margin + 3 * rel_sigma)

    def check(self, proposal, current: Directive, t: float, source: str) -> ValidationResult:
        """Check ``proposal`` (a Directive or a dict) against ``current`` at time ``t``.

        The result's ``directive`` is what is now in force: the checked proposal,
        or ``current`` if it was rejected. It carries ``source`` from this call;
        a ``source`` in the proposal is ignored. The rate limit applies only to
        plan changes, so repeating the current plan, with a new reason say, is
        never held back by it.
        """
        violations: list[str] = []
        if isinstance(proposal, Directive):
            data = proposal.to_dict()
        elif isinstance(proposal, dict):
            data = dict(proposal)
        else:
            return self._reject(current, t, ["proposal is not an object"], source)

        # 1. Schema.
        merged = current.to_dict()
        for key in DIRECTIVE_FIELDS:
            if key not in data:
                violations.append(f"missing field {key!r}; kept current value")
                continue
            merged[key] = data[key]
        if merged["formation"] not in SHAPES:
            return self._reject(current, t, [f"unknown formation {merged['formation']!r}"], source)
        for key in DIRECTIVE_FIELDS[1:]:
            try:
                value = float(merged[key])
            except (TypeError, ValueError):
                return self._reject(current, t, [f"{key} is not a number"], source)
            if not math.isfinite(value):
                return self._reject(current, t, [f"{key} is not finite"], source)
            merged[key] = value

        # 2. Bounds.
        clamped = False
        for key in DIRECTIVE_FIELDS[1:]:
            lo, hi = getattr(self.limits, key)
            if merged[key] < lo or merged[key] > hi:
                violations.append(f"{key} {merged[key]:g} outside [{lo:g}, {hi:g}]; clamped")
                merged[key] = min(max(merged[key], lo), hi)
                clamped = True

        # 3. Feasibility.
        floor = self.spacing_floor(merged["safety_margin_m"])
        if merged["spacing_m"] < floor:
            violations.append(f"spacing {merged['spacing_m']:.2f} m below safe floor {floor:.2f} m; raised")
            merged["spacing_m"] = math.ceil(floor * 100.0) / 100.0
            clamped = True

        reason = str(data.get("reason", ""))[:300]
        directive = Directive(formation=merged["formation"], spacing_m=merged["spacing_m"], speed_mps=merged["speed_mps"],
                              altitude_m=merged["altitude_m"], safety_margin_m=merged["safety_margin_m"],
                              reason=reason, source=source)

        # 4. Rate limit (only for real plan changes).
        if not directive.same_plan(current) and t - self.last_change_t < self.limits.min_change_interval_s:
            return self._reject(current, t, violations + [
                f"plan change {t - self.last_change_t:.1f} s after the previous one (minimum {self.limits.min_change_interval_s:g} s)"], source)

        if not directive.same_plan(current):
            self.last_change_t = t
        action = "clamped" if clamped else "accepted"
        result = ValidationResult(action, directive, violations)
        self.log.append({"t": round(t, 2), "source": source, **result.to_dict()})
        return result

    def _reject(self, current: Directive, t: float, violations: list, source: str) -> ValidationResult:
        result = ValidationResult("rejected", current, violations)
        self.log.append({"t": round(t, 2), "source": source, **result.to_dict()})
        return result

    # -- squad orders (squad runs only) --------------------------------------------------------------------------
    SQUAD_ORDER_GAP_S = 10.0

    def check_squads(self, order: dict, flying_by_squad: dict, caps: dict, t: float, source: str,
                     incoming: dict | None = None) -> list[str]:
        """Check an order to move drones between squads; returns the violations (empty: accepted).

        ``order`` is {"op": "move", "from": code, "to": code, "k": drones}; ``flying_by_squad`` maps each squad's
        code to its drones flying now, ``incoming`` to drones already on their way to it, and ``caps`` to the most
        drones one line fits across its area. A move needs k of at least 1, the source to keep at least one flying
        drone, the receiving squad (with those on their way) to stay within its cap, and orders at least
        SQUAD_ORDER_GAP_S apart. Logged like a directive, with ``kind`` "squads".
        """
        incoming = incoming or {}
        bad = []
        op, src, dst = order.get("op"), order.get("from"), order.get("to")
        k = order.get("k")
        if op != "move":
            bad.append(f"unknown squad order {op!r}")
        if src not in flying_by_squad:
            bad.append(f"no squad {src!r}")
        if dst not in flying_by_squad:
            bad.append(f"no squad {dst!r}")
        if src == dst:
            bad.append("source and destination are the same squad")
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            bad.append("k must be a whole number of drones, at least 1")
        if not bad:
            if flying_by_squad[src] - k < 1:
                bad.append(f"squad {src} has {flying_by_squad[src]} flying; it must keep at least one")
            after = flying_by_squad[dst] + incoming.get(dst, 0) + k
            if after > caps[dst]:
                bad.append(f"squad {dst} would have {after}, more than fit across its area ({caps[dst]})")
        last = getattr(self, "_last_squad_t", -math.inf)
        if t - last < self.SQUAD_ORDER_GAP_S:
            bad.append(f"squad orders must be {self.SQUAD_ORDER_GAP_S:.0f} s apart")
        if not bad:
            self._last_squad_t = t
        self.log.append({"t": round(t, 2), "source": source, "kind": "squads", "order": dict(order),
                         "action": "accepted" if not bad else "rejected", "violations": bad})
        return bad

