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
    """A supervisor's answer, applied ``latency_s`` of simulated time after the request.

    ``directive`` is a ``Directive`` or, from Nemotron, a proposal dict; the
    engine passes either to the safety validator. A non-empty ``meta`` puts
    the decision in the run's ``ai_calls`` log, and ``meta["fallback"]``
    applies the directive with the source ``"fallback"``.
    """

    directive: Directive | None              # or a proposal dict (Nemotron); None = keep the current directive
    latency_s: float = 0.0
    raw: str = ""                            # raw model output, for the audit trail
    meta: dict = field(default_factory=dict)  # model, tokens, cost, request id, errors


class Supervisor:
    """Base supervisor: never changes the plan (``decide`` returns None).

    ``name`` tags the directives a supervisor applies and names it in the
    results; for ``"fixed"`` the engine never calls ``decide``. ``uses_ai``
    makes the dashboard run ``decide`` on a worker thread during a live (not
    replayed) run, so the flight does not wait for the model.
    """

    name = "fixed"
    label = "Fixed plan (no supervisor)"
    uses_ai = False

    def decide(self, summary: dict, current: Directive, t: float) -> Decision | None:
        return None


class FixedSupervisor(Supervisor):
    """Flies the mission's directive unchanged (the no-adaptation baseline)."""
