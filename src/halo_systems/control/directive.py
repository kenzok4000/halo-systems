"""The high-level directive a supervisor (rules or Nemotron) gives the swarm.

A directive never touches motors: it sets the formation shape, spacing,
cruise speed, altitude and safety margin that the 50 Hz controller then
flies. Every directive passes the safety validator before it is applied.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace

from .formations import SHAPES

DIRECTIVE_FIELDS = ("formation", "spacing_m", "speed_mps", "altitude_m", "safety_margin_m")


@dataclass(frozen=True)
class Directive:
    formation: str = "grid"
    spacing_m: float = 6.0
    speed_mps: float = 5.0
    altitude_m: float = 20.0
    safety_margin_m: float = 1.0
    reason: str = ""
    source: str = "mission"          # mission | rule | nemotron | operator | fallback

    def __post_init__(self):
        if self.formation not in SHAPES:
            raise ValueError(f"formation must be one of {SHAPES}")

    def to_dict(self) -> dict:
        return asdict(self)

    def with_(self, **changes) -> "Directive":
        return replace(self, **changes)

    def same_plan(self, other: "Directive") -> bool:
        # Reason and source are not part of the plan, so a new reason alone is not a change: the validator's
        # rate limit ignores it and the controller does not rebuild its slots.
        return all(getattr(self, f) == getattr(other, f) for f in DIRECTIVE_FIELDS)
