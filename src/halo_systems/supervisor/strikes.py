"""Pickers for the strike-check team (sim/strike_check.py): which of the night's lightning strikes it checks next.

Each picker reads the same table that code builds (``strike_table``) and names one strike id, or none to stop.

* ``nearest``: last night's strikes before the night before's, the nearest first. What a crew does by default.
* ``rule``: a hand-written triage rule. Strikes a located fire already explains (one within 150 m) go last; of
  the rest, those with smoke the station cannot explain, seen by two or more drones, go first, then last night's,
  then the night before's; the nearest first within each group. (A first version also put unpatrolled ground,
  and ground no thermal camera had cleared, first; on the development valleys it spent most of its checks on
  strikes that started nothing, so it was dropped before the held-out run.)
* ``nemotron``: Nemotron reads the table and the patrol's state and answers with an id and a reason. A reply that
  names no offered strike, or a call that fails (cut off, empty, a server error after retries), is not replaced
  by a rule: the team keeps looking where it is, the model is asked again a circuit later, and the reply is
  counted as unusable. It may also answer "strike": null to stop checking; the team then keeps patrolling its last
  square. A fatal error (no key, the budget cap, a replay with no recorded reply) stops the flight.

Code offers only strikes the team can reach and look at in time, and the safety validator checks every order.
"""

from __future__ import annotations

import json
import os

NEAR_FIRE_M = 150.0
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def explained(r: dict) -> bool:
    return r["fire_near_m"] is not None and r["fire_near_m"] <= NEAR_FIRE_M


class NearestPicker:
    name = source = "nearest"

    def pick(self, table: list[dict], ctx: dict) -> dict:
        r = min(table, key=lambda r: (r["night"] != "last night", r["dist_m"], r["id"]))
        return {"strike": r["id"], "why": "nearest of the remaining strikes", "latency_s": 0.0, "how": "rule"}


class RulePicker:
    name = source = "rule"

    @staticmethod
    def group(r: dict) -> int:
        if explained(r):
            return 3
        if r["smoke_drones"] >= 2:
            return 0
        return 1 if r["night"] == "last night" else 2

    WHY = ["unexplained smoke passes near it", "last night's, no fire located near it", "the night before's",
           "a located fire is near it"]

    def pick(self, table: list[dict], ctx: dict) -> dict:
        r = min(table, key=lambda r: (self.group(r), r["dist_m"], r["id"]))
        return {"strike": r["id"], "why": self.WHY[self.group(r)], "latency_s": 0.0, "how": "rule"}


SYSTEM = """\
You direct a small check team of drones over a mountain valley the morning after dry lightning. Other drone squads \
patrol the valley's areas. This team flies to one reported lightning strike at a time, patrols a 120 m square \
round it for one circuit with thermal cameras looking down and cameras watching for smoke, then moves on. Your \
only decision is which strike the team checks next. Code flies the drones, checks your choice and does \
everything else: locating fires, forecasts and alerts. You never fly a drone.

Each row is a strike the team can still reach and check before the patrol ends:
- id; night: "last night" (19:00 to 04:00) or "night before"; hours_ago.
- patrolled: whether an area squad's patrol covers the ground at its reported position.
- dist_m: distance from the team now. The team transits at about 8 m/s; each check takes look_min minutes.
- ground, slope_pct: the ground at the reported position. Reported positions are off by about 50 m.
- seen_pct: share of the ground within 60 m already looked at from above by a thermal camera. A camera looking \
down sees a smouldering spot in the open; tree crowns or a rock overhang can hide one, though its smoke may \
still show.
- fire_near_m: distance to the nearest fire the station has already located (null: none yet). A strike within \
about 150 m of a located fire is probably that fire's strike.
- smoke_lines, smoke_drones: smoke bearings from the last 3 minutes that no located fire explains and that pass \
within 80 m of this strike, and how many drones saw them: smoke the swarm has seen but not yet placed.
- people_m: distance to the nearest people (homes, campground, trail, road or ranch).

Goal: find the fires the patrol has not found yet, while they are still smouldering. The team has only a few \
checks left and travel costs time, so spend each one where a fire that has not been found yet is most likely.

Answer with JSON only: {"strike": <an id from the table>, "why": "<one short sentence>"}, or \
{"strike": null, "why": "<one short sentence>"} to stop checking."""


def user_message(table: list[dict], ctx: dict) -> str:
    done = "; ".join(f"strike {d['id']}: {d['result']}" for d in ctx["done"]) or "none yet"
    head = (f"Patrol time {ctx['t_min']} min, {ctx['left_min']} min left. Team: {ctx['team_flying']} drones flying; "
            f"one check takes about {ctx['look_min']} min after arriving. Fires located so far: {ctx['located']}. "
            f"Checked so far: {done}.")
    cols = ("id", "night", "hours_ago", "patrolled", "dist_m", "ground", "slope_pct", "seen_pct", "fire_near_m",
            "smoke_lines", "smoke_drones", "people_m")
    rows = "\n".join(json.dumps({c: r[c] for c in cols}, separators=(",", ":")) for r in table)
    return f"{head}\nStrikes the team can check:\n{rows}"


class NemotronPicker:
    name = "nemotron"
    source = "nemotron"

    def __init__(self, model: str = NANO, think: bool = True, cache_path: str | None = None,
                 cache_mode: str = "record", client=None):
        from ..ai.cache import CallCache
        if cache_mode == "replay" and not cache_path:
            raise ValueError("replay needs the reply cache (cache path)")
        self.model = model
        self.think = think
        self.cache = CallCache(cache_path, cache_mode) if cache_path else None
        self.client = client

    def _client(self):
        if self.client is None:
            from ..ai.client import NebiusClient
            self.client = NebiusClient()
        return self.client

    def pick(self, table: list[dict], ctx: dict) -> dict:
        from ..ai.cache import request_key
        from ..ai.client import NebiusError, reasoning
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_message(table, ctx)}]
        max_tokens = 6000 if self.think else 400
        extra = reasoning(self.think)
        key = request_key(self.model, messages, 0.0, {"extra_body": extra, "max_tokens": max_tokens})
        hit = self.cache.get(key) if self.cache is not None and self.cache.mode != "off" else None
        if hit is not None and (hit.get("content") is not None or hit.get("error")):
            content, record, how = hit.get("content"), dict(hit["record"]), "replayed"
            if content is None:                  # a recorded failure replays as the same unusable reply
                return self._unusable(f"call failed: {hit['error']}", record, how)
        elif self.cache is not None and self.cache.mode == "replay":
            raise NebiusError("this request was not recorded, and replay makes no live calls", fatal=True)
        else:
            try:
                result = self._client().chat(self.model, messages, role="strike-picker", temperature=0.0,
                                             max_tokens=max_tokens, extra_body=extra)
            except NebiusError as exc:
                if exc.fatal or exc.record is None:
                    raise
                record = exc.record.to_dict()
                if self.cache is not None:
                    self.cache.put(key, None, record, error=str(exc)[:300])
                return self._unusable(f"call failed: {exc}", record, "live")
            content, record, how = result.content, result.record.to_dict(), "live"
            if self.cache is not None:
                self.cache.put(key, content, record)
        return parse_reply(content, {r["id"] for r in table}, record, how)

    @staticmethod
    def _unusable(why: str, record: dict, how: str) -> dict:
        return {"strike": None, "valid": False, "why": why[:200], "latency_s": float(record.get("latency_s") or 0.0),
                "how": how, "record": record}


def parse_reply(content: str | None, offered: set, record: dict, how: str) -> dict:
    """The picked strike from a reply: an offered id, or none (to stop) when the reply says "strike": null.
    Anything else is unusable."""
    from ..ai.client import extract_json
    latency = float(record.get("latency_s") or 0.0)
    try:
        obj = extract_json(content or "")
    except Exception as exc:                     # malformed or pathological JSON: unusable, never a crash
        return NemotronPicker._unusable(f"unparsable reply: {str(exc)[:120]}", record, how)
    if not isinstance(obj, dict):
        return NemotronPicker._unusable("the reply is not a JSON object", record, how)
    sid, why = obj.get("strike"), str(obj.get("why", ""))[:200]
    if sid is None and "strike" in obj:
        return {"strike": None, "valid": True, "why": why or "stopped", "latency_s": latency, "how": how,
                "record": record}
    if isinstance(sid, str) and sid.strip().isascii() and sid.strip().isdigit() and len(sid.strip()) <= 6:
        sid = int(sid.strip())
    elif isinstance(sid, float) and sid.is_integer() and abs(sid) < 1e6:
        sid = int(sid)
    if not isinstance(sid, int) or isinstance(sid, bool) or sid not in offered:
        return NemotronPicker._unusable(f"named no offered strike ({repr(sid)[:60]})", record, how)
    return {"strike": sid, "valid": True, "why": why, "latency_s": latency, "how": how, "record": record}


def make_picker(cfg: dict):
    kind = cfg.get("picker", "rule")
    if kind == "rule":
        return RulePicker()
    if kind == "nearest":
        return NearestPicker()
    if kind == "nemotron":
        return NemotronPicker(model=cfg.get("model", NANO), think=bool(cfg.get("think", True)),
                              cache_path=cfg.get("cache") or os.environ.get("HALO_STRIKE_CACHE"),
                              cache_mode=cfg.get("cache_mode", "record"), client=cfg.get("client"))
    raise ValueError(f"unknown strike picker {kind!r}; use rule, nearest or nemotron")
