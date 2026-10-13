"""Supervisor interface: the slow loop that sets the swarm's directive.

A supervisor sees only the compact state summary (the same one for rules
and for Nemotron, so comparisons are fair) and returns a decision. The
engine applies a decision ``latency_s`` of simulated time after it was
requested, so a slow model pays for its slowness, and only after the
safety validator accepts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..control.directive import Directive


@dataclass
class Decision:
    directive: Directive | None              # None = keep the current directive
    latency_s: float = 0.0
    raw: str = ""                            # raw model output, for the audit trail
    meta: dict = field(default_factory=dict)  # model, tokens, cost, request id, errors


class Supervisor:
    name = "fixed"
    label = "Fixed plan (no supervisor)"
    uses_ai = False

    def decide(self, summary: dict, current: Directive, t: float) -> Decision | None:
        return None


class FixedSupervisor(Supervisor):
    """Flies the mission's directive unchanged (the no-adaptation baseline)."""
