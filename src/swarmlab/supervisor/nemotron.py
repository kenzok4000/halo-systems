"""Nemotron supervisor: an NVIDIA Nemotron model on Nebius Token Factory sets the directive.

The model sees exactly the summary the rule supervisor sees, plus its own
last few directives, and answers with a JSON directive. Nothing it says
reaches the motors directly: the engine applies the answer only after the
measured API latency has passed in simulated time, and only through the
safety validator.

If the call fails (network, HTTP error, no answer within the deadline,
unparsable or incomplete JSON, or a replay-cache miss), the supervisor falls
back to the rule supervisor's decision for that step and records why, so the
swarm never waits long on the API and every fallback is counted in the
results. Failed calls are cached too, with their latency, so a replayed run
falls back at the same moments the live run did. With
``strict=True`` (benchmarks) an error that would hit every later call too
(no key, key rejected, budget cap) stops the run instead, so a flight that
was really flown by the rules is never reported as a Nemotron result.
"""

from __future__ import annotations

import os

from ..ai.cache import CallCache, request_key
from ..ai.client import NebiusClient, NebiusError, extract_json, reasoning
from ..ai.prompts import PROMPT_VERSION, SUPERVISOR_SYSTEM, supervisor_user
from ..control.directive import DIRECTIVE_FIELDS, Directive
from .base import Decision, Supervisor
from .rule import RuleSupervisor


# Per-attempt timeout and whole-call deadline, from the tuning-seed call logs. Without reasoning,
# Nano answered within 3 s every time and Super's slow attempts were all-or-nothing (they timed out
# at 20 s, then the retry answered in about 2 s), so a slow attempt is cut at 8 s and retried, and
# the call gives up at 15 s: by then the report it answers is 75 m out of date at 5 m/s. With
# reasoning, answers took 14 s at the median and 48 s at the 95th percentile, so one 60 s attempt.
TIMEOUTS = {False: dict(timeout_s=8.0, max_retries=2, deadline_s=15.0),
            True: dict(timeout_s=60.0, max_retries=0, deadline_s=60.0)}


class NemotronSupervisor(Supervisor):
    name = "nemotron"
    label = "Nemotron supervisor"
    uses_ai = True

    def __init__(self, client: NebiusClient | None = None, model: str | None = None, cache: CallCache | None = None,
                 fallback: str = "rule", temperature: float = 0.2, max_tokens: int = 1200,
                 extra_body: dict | None = None, system_prefix: str = "", strict: bool = False, think: bool = False,
                 deadline_s: float | None = None):
        t = TIMEOUTS[bool(think)]
        self.client = client or NebiusClient(timeout_s=t["timeout_s"], max_retries=t["max_retries"])
        self.deadline_s = deadline_s if deadline_s is not None else t["deadline_s"]
        self.model = model or os.environ.get("SWARMLAB_SUPERVISOR_MODEL", "")
        if not self.model:
            raise ValueError("no model id: pass model= or set SWARMLAB_SUPERVISOR_MODEL (see `swarmlab doctor`)")
        self.cache = cache
        self.fallback = RuleSupervisor() if fallback == "rule" else None
        self.temperature = temperature
        self.think = think
        # Reasoning is off unless asked for: it multiplies latency and tokens (see ai/client.reasoning).
        self.max_tokens = max(max_tokens, 4096) if think else max_tokens
        self.extra_body = extra_body if extra_body is not None else reasoning(think)
        self.system = (system_prefix + "\n" + SUPERVISOR_SYSTEM).strip() if system_prefix else SUPERVISOR_SYSTEM
        self.history: list[dict] = []
        self.strict = strict

    @classmethod
    def from_spec(cls, spec: str, cache_path: str | None = None, cache_mode: str = "record",
                  log_path: str | None = None, **kw) -> "NemotronSupervisor":
        """``nemotron[:<model id>]`` (reasoning off) or ``nemotron-think[:<model id>]`` (reasoning on)."""
        kind, _, model = spec.partition(":")
        think = kind == "nemotron-think"
        if kind not in ("nemotron", "nemotron-think"):
            raise ValueError(f"unknown supervisor {spec!r}")
        t = TIMEOUTS[think]
        client = NebiusClient(timeout_s=t["timeout_s"], max_retries=t["max_retries"], log_path=log_path)
        cache = CallCache(cache_path, cache_mode) if cache_path else None
        return cls(client=client, model=model or None, cache=cache, think=think, **kw)

    def _messages(self, summary: dict) -> list[dict]:
        return [{"role": "system", "content": self.system},
                {"role": "user", "content": supervisor_user(summary, self.history)}]

    def decide(self, summary: dict, current: Directive, t: float) -> Decision | None:
        rule_decision = self.fallback.decide(summary, current, t) if self.fallback else None
        messages = self._messages(summary)
        key = request_key(self.model, messages, self.temperature,
                          {"extra_body": self.extra_body, "max_tokens": self.max_tokens})
        meta = {"role": "supervisor", "model": self.model, "prompt_version": PROMPT_VERSION, "cached": False,
                "fallback": False}
        content, latency = None, 0.0
        try:
            hit = self.cache.get(key) if self.cache and self.cache.mode != "off" else None
            if hit is not None:
                content = hit["content"]
                rec = dict(hit["record"])
                meta.update({k: rec.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_usd",
                                                    "request_id", "attempts")})
                meta["cached"] = True
                latency = float(rec.get("latency_s") or 0.0)
                if hit.get("error"):
                    raise NebiusError(hit["error"])     # the live call failed here: fail the same way
            elif self.cache is not None and self.cache.mode == "replay":
                raise NebiusError("replay cache miss (run in record mode with a key to fill it)")
            else:
                result = self.client.chat(self.model, messages, role="supervisor", temperature=self.temperature,
                                          max_tokens=self.max_tokens, extra_body=self.extra_body,
                                          deadline_s=self.deadline_s)
                content = result.content
                rec = result.record.to_dict()
                meta.update({k: rec.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_usd",
                                                    "request_id", "attempts")})
                latency = result.record.latency_s
                if self.cache is not None:
                    self.cache.put(key, content, rec)
            proposal = parse_directive(content)
        except (NebiusError, ValueError) as exc:
            if self.strict and getattr(exc, "fatal", False):
                raise
            err = getattr(exc, "record", None)
            if err is not None:
                latency = err.latency_s
                meta.update({k: getattr(err, k, None) for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                                                                "cost_usd", "request_id", "attempts")})
                if self.cache is not None and not getattr(exc, "fatal", False):
                    self.cache.put(key, None, err.to_dict(), error=str(exc))
            meta.update({"fallback": True, "error": str(exc)[:300]})
            fb = rule_decision.directive if rule_decision is not None else None
            return Decision(fb, latency_s=latency, raw=(content or "")[:2000], meta=meta)
        meta["reason"] = proposal.get("reason", "")
        self.history.append({"t": round(t, 1), **{k: proposal[k] for k in DIRECTIVE_FIELDS}, "reason": proposal.get("reason", "")})
        proposal["source"] = "nemotron"
        return Decision(proposal, latency_s=latency, raw=content[:2000], meta=meta)


def parse_directive(content: str) -> dict:
    """Validate the model's JSON reply into a directive dict (bounds are the validator's job)."""
    obj = extract_json(content)
    missing = [k for k in DIRECTIVE_FIELDS if k not in obj]
    if missing:
        raise ValueError(f"reply is missing {', '.join(missing)}")
    out = {"formation": str(obj["formation"]).strip().lower()}
    for k in DIRECTIVE_FIELDS[1:]:
        try:
            out[k] = float(obj[k])
        except (TypeError, ValueError):
            raise ValueError(f"{k} is not a number: {obj[k]!r}")
    out["reason"] = str(obj.get("reason", ""))[:300]
    return out
