"""Spend guard for paid model calls.

Every call's tokens and estimated cost are appended to a JSON-lines ledger
(``SWARMLAB_LEDGER``, default ``~/.swarmlab/nebius_ledger.jsonl``). All
processes and sessions that point at the same file share one running total.
Before each request the guard adds a pessimistic estimate of that request
(prompt length at three characters per token, plus the full ``max_tokens``)
to the total, and refuses the call if the cap (``SWARMLAB_BUDGET_USD``,
default 18) would be crossed. A benchmark then stops itself instead of
running up a bill.

Costs are estimates. Unless real prices are set with
``NEBIUS_PRICE_INPUT_PER_M`` and ``NEBIUS_PRICE_OUTPUT_PER_M``, they use the
price ceilings below, which are set above the prices listed for these models
so that the ledger over-counts. The provider's billing page is the authority
on what was actually charged.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import jsonl

DEFAULT_CAP_USD = 18.0
DEFAULT_LEDGER = Path.home() / ".swarmlab" / "nebius_ledger.jsonl"

# USD per million tokens (input, output), matched on the model id. Deliberately high: see the module docstring.
PRICE_CEILINGS = (("nano", 0.10, 0.40), ("super", 0.50, 1.50))
UNKNOWN_MODEL_CEILING = (2.00, 6.00)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Pricing:
    """USD per million tokens."""

    input_per_m: float = 0.0
    output_per_m: float = 0.0
    source: str = "given"

    @classmethod
    def for_model(cls, model: str) -> "Pricing":
        """Prices from the environment when both are set, otherwise the ceiling for this model."""
        pin = float(os.environ.get("NEBIUS_PRICE_INPUT_PER_M", "0") or 0)
        pout = float(os.environ.get("NEBIUS_PRICE_OUTPUT_PER_M", "0") or 0)
        if pin > 0 and pout > 0:
            return cls(pin, pout, "env")
        name = (model or "").lower()
        for key, i, o in PRICE_CEILINGS:
            if key in name:
                return cls(i, o, f"ceiling:{key}")
        return cls(*UNKNOWN_MODEL_CEILING, "ceiling:unknown")

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.input_per_m + completion_tokens * self.output_per_m) / 1e6


def estimate_prompt_tokens(messages: list[dict]) -> int:
    """Generous token count for a request (about three characters per token, plus overhead per message)."""
    return sum(len(str(m.get("content", ""))) // 3 + 8 for m in messages)


class Budget:
    """A running total over a shared ledger file, with a hard cap checked before every request."""

    def __init__(self, path: str | os.PathLike | None = None, cap_usd: float | None = None):
        self.path = Path(path or os.environ.get("SWARMLAB_LEDGER") or DEFAULT_LEDGER).expanduser()
        cap = cap_usd if cap_usd is not None else os.environ.get("SWARMLAB_BUDGET_USD")
        self.cap_usd = float(cap) if cap not in (None, "") else DEFAULT_CAP_USD
        self._offset = 0
        self._spent = 0.0
        self._tokens = [0, 0]
        self._entries = 0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        # Read only what other processes appended since the last look.
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read()
        end = chunk.rfind(b"\n") + 1
        for line in chunk[:end].splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            self._spent += float(e.get("cost_usd") or 0.0)
            self._tokens[0] += int(e.get("prompt_tokens") or 0)
            self._tokens[1] += int(e.get("completion_tokens") or 0)
            self._entries += 1
        self._offset += end

    def spent_usd(self) -> float:
        with self._lock:
            self._refresh()
            return self._spent

    def check(self, projected_usd: float) -> None:
        """Raise ``BudgetExceeded`` if a request costing up to ``projected_usd`` could cross the cap."""
        spent = self.spent_usd()
        if spent + projected_usd > self.cap_usd:
            raise BudgetExceeded(f"budget cap reached: ${spent:.4f} spent of ${self.cap_usd:.2f} "
                                 f"(next request could cost up to ${projected_usd:.4f}); see {self.path}")

    def add(self, *, model: str, role: str, prompt_tokens: int = 0, completion_tokens: int = 0, cost_usd: float = 0.0,
            status: str = "ok", note: str = "", call_id: str | None = None, pricing: str = "") -> None:
        entry = {"t": round(time.time(), 3), "call_id": call_id, "model": model, "role": role, "status": status,
                 "prompt_tokens": int(prompt_tokens), "completion_tokens": int(completion_tokens),
                 "cost_usd": round(float(cost_usd), 6), "pricing": pricing, "note": note}
        with self._lock:
            jsonl.append(self.path, entry)

    def summary(self) -> dict:
        spent = self.spent_usd()
        # The home directory is written as ~ so a report can be shared without the user's name in it.
        ledger = str(self.path)
        home = str(Path.home())
        if ledger == home or ledger.startswith(home + os.sep):
            ledger = "~" + ledger[len(home):]
        return {"ledger": ledger, "entries": self._entries, "prompt_tokens": self._tokens[0],
                "completion_tokens": self._tokens[1], "spent_usd": round(spent, 4), "cap_usd": self.cap_usd,
                "remaining_usd": round(self.cap_usd - spent, 4)}
