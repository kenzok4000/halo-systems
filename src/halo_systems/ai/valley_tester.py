"""Test engineer for the wildfire valley: search the day's weather, fuels and haze for conditions where the
ground station misses a fire, warns people too late, or sends a public alert it should not have.

The system under test is today's flight (16 drones by default, the improved controller with the rule supervisor,
one sweep of the survey block) with the station's spread forecast and Kenzo's chosen alerts: pre-signed trigger
rules, sent automatically. No language model takes part in any alert, card or flight decision; the model only
proposes test conditions, as in ``ai/tester.py``.

A test is a valley seed plus the conditions in ``SPACE``. ``build`` makes the committed valley for that seed and
then changes only what the conditions name, after every random draw, so the default conditions give exactly the
committed valley. Three searchers share the budget and the scoring rule: random search, corner search and
Nemotron as the test engineer (it sees every test so far and proposes the next batch, with a reason for each).
"""

from __future__ import annotations

import dataclasses
import json
import math

import numpy as np

SPACE = {
    # name: (low, high, kind, meaning)
    "drones": (4, 32, "int", "drones on today's flight (one sweep of the 500 x 300 m survey block, line abreast)"),
    "wind_scale": (0.5, 2.0, "float", "the day's wind speed as a multiple of the drawn day's (truth and forecast "
                                      "alike; the drawn day runs from 1.5-3 m/s at take-off up to 9 m/s)"),
    "wind_shift_deg": (-90.0, 90.0, "float", "the true wind turns by this many degrees (positive = anticlockwise) "
                                             "from 1 h after take-off; the forecast does not know"),
    "moisture_scale": (0.6, 1.4, "float", "dead fuel moisture as a multiple of the drawn day's (below 1 is drier; "
                                          "truth and forecast alike)"),
    "forecast_error": (0.0, 3.0, "float", "the weather forecast's errors in wind and fuel moisture as a multiple of "
                                          "the drawn ones (0 = a perfect forecast, 1 = as drawn)"),
    "smoulder_scale": (0.25, 2.0, "float", "how long each fire smoulders before it starts to spread, as a multiple "
                                           "of the drawn time (fires already spreading at take-off are unchanged)"),
    "patchiness": (0.0, 0.8, "float", "unseen patchiness of the fuel: spread rate varies by a log-normal factor "
                                      "with this sigma, which the station's forecast never sees (drawn day 0.3)"),
    "visibility_m": (500.0, 2500.0, "float", "haze: the farthest a forward camera can see smoke"),
    "smoke_range_scale": (0.4, 1.0, "float", "the range at which a small plume is noticed, as a multiple of a "
                                             "clear day's (low-contrast smoke)"),
}
NAMES = list(SPACE)
# The committed valley: build() changes nothing under these conditions.
DEFAULTS = {"drones": 16, "wind_scale": 1.0, "wind_shift_deg": 0.0, "moisture_scale": 1.0, "forecast_error": 1.0,
            "smoulder_scale": 1.0, "patchiness": 0.3, "visibility_m": 2500.0, "smoke_range_scale": 1.0}
# Which end of each range is the harder condition, for the stress level: "low", "high", or "abs" (further from 0
# is harder). Fires that take off sooner or later are each hard for a different reason, so smoulder_scale has no
# hard end and is left out of the stress level.
HARD_END = {"drones": "low", "wind_scale": "high", "wind_shift_deg": "abs", "moisture_scale": "low",
            "forecast_error": "high", "patchiness": "high", "visibility_m": "low", "smoke_range_scale": "low"}
WIND_SHIFT_FROM_S = 3600.0
CONFIG = "improved+rule"
ALERTS = {"alert_rule": "presigned", "approval": "auto"}
# Severity weights (see severity()).
W_PHONE_MISSED, W_FIRE_MISSED, W_DRONE_LOST, W_FALSE_ALARM, W_NEEDLESS_EVAC = 4.0, 10.0, 10.0, 5.0, 3.0
SPREAD_BEFORE_END_S = 300.0       # missed: spreading this long by the end of the flight (time before take-off counts), unlocated
DELIVERY_STREAM = 7708            # the recheck's own stream for alert delivery lags (the station uses 7707)


def clip_params(p: dict) -> dict:
    """Make proposed conditions valid. Every searcher's proposals pass through here, the model's included.

    Keys outside ``SPACE`` are dropped; a missing, non-numeric (true/false included) or non-finite value becomes the
    default (the committed valley's); every value is clamped to its range; integers are rounded and floats kept to
    3 decimals.
    """
    out = {}
    for k, (lo, hi, kind, _) in SPACE.items():
        v = p.get(k) if isinstance(p, dict) else None
        v = _number(v)
        if v is None:
            v = float(DEFAULTS[k])
        v = min(max(v, lo), hi)
        out[k] = int(round(v)) if kind == "int" else round(v, 3)
    return out


def _number(v) -> float | None:
    """``v`` as a finite float, or None when it is not a number (true and false are not numbers here)."""
    if isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def stress_level(params: dict) -> float:
    """How hard a test's conditions are, from 0 (every parameter at its easy end) to 1 (all at the hard end),
    over the parameters in ``HARD_END``. A failure found at a low stress level tells an engineer more."""
    p = clip_params(params)
    h = []
    for k, end in HARD_END.items():
        lo, hi = SPACE[k][0], SPACE[k][1]
        if end == "abs":
            h.append(abs(p[k]) / max(abs(lo), abs(hi)))
        else:
            u = (p[k] - lo) / (hi - lo)
            h.append(1.0 - u if end == "low" else u)
    return round(float(np.mean(h)), 3)


def _turn(w: tuple, deg: float, scale: float = 1.0) -> tuple:
    a = math.radians(deg)
    return (scale * (w[0] * math.cos(a) - w[1] * math.sin(a)), scale * (w[0] * math.sin(a) + w[1] * math.cos(a)))


def build(params: dict, seed: int):
    """The valley for ``seed`` under the test conditions: the committed valley (today's flight, pre-signed rules,
    automatic, report_v2 scoring), then each condition that differs from ``DEFAULTS`` applied in turn. Nothing
    here draws a random number, so the fires, terrain, phones and the forecast's drawn errors stay those of the
    seed."""
    from ..eval.scenarios import make
    from ..sim.fire import Moisture, Period
    p = clip_params(params)
    sc = make("wildfire_valley", seed, n=p["drones"], options={**ALERTS, "report_v2": True})
    wf = sc.wildfire
    truth, told = list(wf.weather), list(wf.weather_forecast)
    s, ms, e, shift = p["wind_scale"], p["moisture_scale"], p["forecast_error"], p["wind_shift_deg"]
    if (s, ms, e) != (1.0, 1.0, 1.0):
        # The forecast's drawn error per period: a turn and a speed factor on the wind, an offset on moisture.
        # The new forecast keeps that error, multiplied by e, around the new (unshifted) truth.
        new_truth, new_told = [], []
        for t0, f0 in zip(truth, told):
            w, wt = t0.wind_mps, f0.wind_mps
            turn = math.degrees(math.atan2(w[0] * wt[1] - w[1] * wt[0], w[0] * wt[0] + w[1] * wt[1]))
            k = math.hypot(*wt) / max(math.hypot(*w), 1e-9)
            m, mt = t0.moisture, f0.moisture
            m2 = Moisture(m.d1 * ms, m.d10 * ms, m.d100 * ms, m.live)
            w2 = (w[0] * s, w[1] * s)
            new_truth.append(Period(t0.start_s, w2, m2))
            new_told.append(Period(f0.start_s, _turn(w2, e * turn, k ** e),
                                   Moisture(max(0.03, m2.d1 + e * (mt.d1 - m.d1)), max(0.03, m2.d10 + e * (mt.d10 - m.d10)),
                                            max(0.03, m2.d100 + e * (mt.d100 - m.d100)), mt.live)))
        truth, told = new_truth, new_told
    if shift != 0.0:
        truth = [Period(t0.start_s, _turn(t0.wind_mps, shift), t0.moisture) if t0.start_s >= WIND_SHIFT_FROM_S else t0
                 for t0 in truth]
    fires = wf.fires
    if p["smoulder_scale"] != 1.0:
        fires = [dataclasses.replace(f, t_active_s=f.t_active_s * p["smoulder_scale"]) if f.t_active_s > 0.0 else f
                 for f in fires]
    smoke = wf.smoke
    if (p["visibility_m"], p["smoke_range_scale"]) != (DEFAULTS["visibility_m"], DEFAULTS["smoke_range_scale"]):
        smoke = dataclasses.replace(smoke, visibility_m=float(p["visibility_m"]),
                                    r50_ref_m=smoke.r50_ref_m * p["smoke_range_scale"])
    wf = dataclasses.replace(wf, weather=truth, weather_forecast=told, fires=fires, smoke=smoke,
                             heterogeneity_sigma=float(p["patchiness"]))
    wind = sc.wind
    if s != 1.0:
        wind = dataclasses.replace(wind, mean_mps=tuple(s * c for c in wind.mean_mps),
                                   gust_sigma_mps=wind.gust_sigma_mps * s, local_sigma_mps=wind.local_sigma_mps * s)
    return dataclasses.replace(sc, wildfire=wf, wind=wind, description=json.dumps(p))


def _outcome(sim, m: dict) -> dict:
    """The test's outcome from a finished flight: what went wrong, and the numbers severity() reads."""
    from ..eval.scenarios import VALLEY_BLOCK
    wf = sim.wildfire
    bx0, by0, bx1, by1 = VALLEY_BLOCK
    end = float(m["duration_s"])
    missed = [i for i, (f, row) in enumerate(zip(wf.fires, m["fire_table"]))
              if bx0 <= f.x <= bx1 and by0 <= f.y <= by1 and f.t_active_s <= end - SPREAD_BEFORE_END_S
              and not row["located"]]
    # A Level 4 or 5 alert (leave now) for a zone no true fire comes near (within the danger radius of any of its
    # phones) in the 4 hours the truth runs.
    needless = []
    for a in m.get("alert_table", []):
        if a["level"] < 4:
            continue
        k = wf._zone_index(a["zone"])
        members = np.nonzero(wf.zone_of_phone == k)[0]
        reach = float(wf.phone_truth_s[members].min()) if len(members) else math.inf
        if not math.isfinite(reach):
            needless.append(a["zone"])
    lost = int(m["drones_lost"])        # every crashed or downed drone, collisions and strikes included
    out = {"phones_reached_3h": m["phones_reached_3h"], "phones_warned_in_time": m["phones_warned_in_time"],
           "phones_missed": m["phones_missed"], "lead_time_min_p10": m["lead_time_min_p10"],
           "fires_spreading_missed": missed, "fires_located": m["fires_located"],
           "false_locations_standing": m["false_locations_standing"],
           "alerts_delivered": m["alerts_delivered"], "alerts_not_justified": m.get("alerts_not_justified", 0),
           "alerts_from_false_fires": m.get("alerts_from_false_fires", 0),
           "alerts_for_smoulders": m.get("alerts_for_smoulders", 0), "needless_evacuations": needless,
           "dispatch_cards": m.get("dispatch_cards", 0), "drones_lost": lost, "flight_s": round(end, 1)}
    out["kinds"] = sorted(what(out))
    out["failed"] = bool(out["kinds"])
    out["severity"] = round(severity(out), 3)
    return out


def what(o: dict) -> set:
    """What went wrong in a test: any of late-warning (a phone the fire reached within 3 h, with signal, warned
    late or never), missed-fire (a fire in the survey block had been spreading for 5 minutes or more by the end of
    the flight, time before take-off included, and was never located), false-alarm (a public alert resting only on fires that do not exist), needless-evacuation
    (a Level 4 or 5 alert for a zone no fire comes near in 4 h) and drone-lost. Empty: the test passed."""
    out = set()
    if o["phones_missed"] > 0:
        out.add("late-warning")
    if o["fires_spreading_missed"]:
        out.add("missed-fire")
    if o["alerts_from_false_fires"] > 0:
        out.add("false-alarm")
    if o["needless_evacuations"]:
        out.add("needless-evacuation")
    if o["drones_lost"] > 0:
        out.add("drone-lost")
    return out


def severity(o: dict) -> float:
    """Higher is closer to failure: 4 per phone warned late or never, 10 per missed spreading fire, 10 per drone
    lost, 5 per public alert from false fires only, 3 per needless evacuation alert, 1 per alert the fire did not
    justify in its level's window, 1 per false fire still standing, and up to 3 as the earliest tenth of
    warnings gets under 30 minutes of lead."""
    p10 = o.get("lead_time_min_p10")
    return (W_PHONE_MISSED * o["phones_missed"] + W_FIRE_MISSED * len(o["fires_spreading_missed"])
            + W_DRONE_LOST * o["drones_lost"] + W_FALSE_ALARM * o["alerts_from_false_fires"]
            + W_NEEDLESS_EVAC * len(o["needless_evacuations"]) + o["alerts_not_justified"]
            + o["false_locations_standing"] + (max(0.0, 30.0 - p10) / 10.0 if p10 is not None else 0.0))


def _own_delivery_stream(wf, seed: int) -> None:
    """Draw this flight's alert delivery lags from a stream of their own. The station draws its forecast noise,
    thermal mapping and delivery lags from one stream, and the alert rule decides how many lags are drawn, so
    after the first alert that differs between two rules every later forecast would be redrawn. With the lags on
    their own stream, the station's picture (fires located, forecasts) is the same under either rule and only the
    alerts differ. Used for the recheck's paired flights only; the searches fly the committed station."""
    own = np.random.default_rng(np.random.SeedSequence([seed, DELIVERY_STREAM]))
    broadcast = wf._broadcast

    def _broadcast(*a, **kw):
        shared, wf.rng = wf.rng, own
        try:
            return broadcast(*a, **kw)
        finally:
            wf.rng = shared
    wf._broadcast = _broadcast


def evaluate(params: dict, seed: int, alert_rule: str | None = None, paired: bool = False) -> dict:
    """Fly one test and score it. ``alert_rule`` flies the same test under another alert rule. ``paired`` draws
    alert delivery lags from their own stream (``_own_delivery_stream``), so that two flights of one test under
    two rules see the same station picture; the recheck flies both rules that way. The drones fly the same under
    any rule."""
    from ..eval.runner import make_controller, make_supervisor, split_config
    from ..sim.body import DroneBody
    from ..sim.engine import Simulation
    sc = build(params, seed)
    if alert_rule is not None:
        sc = dataclasses.replace(sc, wildfire=dataclasses.replace(
            sc.wildfire, alerts=dataclasses.replace(sc.wildfire.alerts, rule=alert_rule)))
    ctrl, sup = split_config(CONFIG)
    body = DroneBody()
    sim = Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body, record_hz=0)
    if paired:
        _own_delivery_stream(sim.wildfire, seed)
    m = sim.run().metrics
    return _outcome(sim, m)


def causes(p: dict) -> list[str]:
    """Coarse labels for the conditions behind a failure, from hand-set thresholds (they only group failures
    into kinds for counting variety, and are reported with the results)."""
    p = clip_params(p)
    out = []
    if p["drones"] < 10:
        out.append("few-drones")
    if p["wind_scale"] > 1.4:
        out.append("windy")
    if abs(p["wind_shift_deg"]) > 30.0:
        out.append("wind-shift")
    if p["moisture_scale"] < 0.8:
        out.append("dry")
    if p["forecast_error"] > 2.0:
        out.append("bad-forecast")
    if p["smoulder_scale"] < 0.6:
        out.append("early-takeoff")
    if p["smoulder_scale"] > 1.5:
        out.append("late-takeoff")
    if p["patchiness"] > 0.5:
        out.append("patchy-fuel")
    if p["visibility_m"] < 1200.0 or p["smoke_range_scale"] < 0.6:
        out.append("haze")
    return out


def failure_kinds(history: list[dict]) -> set:
    """Distinct failure modes, ``what:cause+cause`` for each thing that went wrong in each failing test."""
    kinds = set()
    for h in history:
        for w in h["outcome"]["kinds"]:
            kinds.add(w + ":" + "+".join(causes(h["params"]) or ["other"]))
    return kinds


# ------------------------------------------------------------------ searchers
def _unit_to_params(u) -> dict:
    return clip_params({k: SPACE[k][0] + float(x) * (SPACE[k][1] - SPACE[k][0]) for k, x in zip(NAMES, u)})


class RandomSearch:
    name = "random"

    def __init__(self, seed: int, stream: int = 5101):
        self.rng = np.random.default_rng([seed, stream])

    def propose(self, history: list[dict], k: int) -> list[dict]:
        return [{"params": _unit_to_params(self.rng.uniform(0, 1, len(NAMES))), "why": "uniform random"}
                for _ in range(k)]


class CornerSearch:
    name = "corners"

    def __init__(self, seed: int):
        self.rng = np.random.default_rng([seed, 5102])

    def propose(self, history: list[dict], k: int) -> list[dict]:
        return [{"params": _unit_to_params(self.rng.integers(0, 2, len(NAMES)).astype(float)), "why": "random corner"}
                for _ in range(k)]


TESTER_SYSTEM = """You are a test engineer hunting for conditions under which a wildfire scouting system fails. \
The morning after dry lightning, five fires smoulder in a mountain valley: one already spreading in open scrub, \
one under tree crowns, one under a rock overhang no camera looking down can see, one smouldering in the open, and \
one across the valley outside the survey block. A swarm of drones sweeps the 500 x 300 m survey block once, line \
abreast, with thermal cameras looking down and forward cameras watching for smoke. A ground station locates fires \
from both, forecasts their spread 3 hours ahead from a weather forecast, and sends zone alerts to simulated phones \
(Level 3 prepare, Level 4 leave, Level 5 emergency) under trigger rules signed in advance: a public alert only for \
fires it believes are spreading; a fire found still smouldering goes to fire dispatch instead.

You choose the test conditions. Parameters, their ranges and meaning:
{space}
The drawn day is {defaults}.

A test fails if any of these happens:
- late-warning: a phone the fire reached within 3 h (with signal) got no alert before the fire arrived;
- missed-fire: a fire in the survey block had been spreading for 5 minutes or more by the end of the flight \
(time before take-off counts) and was never located;
- false-alarm: a public alert rested only on fires that do not exist;
- needless-evacuation: a Level 4 or 5 alert for a zone no fire comes near in 4 h;
- drone-lost: a drone crashed.
Severity = 4 x phones warned late + 10 x missed fires + 10 x drones lost + 5 x false alarms + 3 x needless \
evacuations + 1 x alerts the fire did not justify in time + 1 x false fires still standing + up to 3 when the \
earliest tenth of warnings has under 30 minutes of lead. Higher is closer to failure. Each test also gets its own \
valley (terrain, fire positions, people), so the same conditions can pass in one valley and fail in another.

Some valleys fail even under the drawn day's conditions. Your tests are scored on failures the conditions cause: \
a failure counts when the same valley under the drawn day does not fail that way. You will not know in advance \
which valley a test gets. Aim, in this order: failures the conditions cause; kinds of failure not yet found; \
then the mildest conditions (closest to the drawn day) that still cause one.

You will see every test so far with its outcome. Reply with one JSON object and nothing else:
{{"tests": [{{"params": {{...all parameters...}}, "why": "one short sentence"}}, ...]}}"""


def _readable(p) -> bool:
    """A proposal's conditions can be read: a dict naming at least one condition with a finite number."""
    return isinstance(p, dict) and any(k in SPACE and _number(v) is not None for k, v in p.items())


class NemotronSearch:
    """Nemotron as the test engineer. Each call sends the whole history so far (each test's conditions, what went
    wrong, severity and the main numbers). When the model proposes too few tests, a test whose conditions cannot
    be read, or a call fails, the gap is filled by random search (its own stream) and counted as a random top-up.
    ``cache`` (an ``ai.cache.CallCache``) records every reply, and every call that failed for a reason a retry
    might not share, so in "replay" mode a recorded search re-runs exactly, with no key. ``salt`` (for example the
    search number and first seed) goes into each request's cache key without changing the prompt, so two searches
    whose first prompts are the same text get a call each. With ``strict`` any failure that is not the model's own
    reply (no key, budget, a replay miss, an HTTP error, a network failure or a timeout) stops the search, so it
    cannot run on as random search; a reply that was cut off, empty or unreadable is topped up and counted."""

    name = "nemotron"

    def __init__(self, client, model: str, seed: int = 0, temperature: float = 0.7, think: bool = True,
                 strict: bool = False, cache=None, salt: dict | None = None):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.think = think
        self.strict = strict
        self.cache = cache
        self.salt = dict(salt or {})
        # Top-ups draw from a stream of their own, so they never copy the random searcher's tests.
        self.fallback = RandomSearch(seed, stream=5103)
        self.calls: list[dict] = []
        space = "\n".join(f"- {k}: {lo} to {hi} ({kind}), {meaning}" for k, (lo, hi, kind, meaning) in SPACE.items())
        self.system = TESTER_SYSTEM.format(space=space, defaults=json.dumps(DEFAULTS))

    def _ask(self, user: str) -> str:
        from .cache import request_key
        from .client import NebiusError, reasoning
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        max_tokens = 12000 if self.think else 2500
        extra = reasoning(self.think)
        key = request_key(self.model, messages, self.temperature,
                          {"extra_body": extra, "max_tokens": max_tokens, **({"salt": self.salt} if self.salt else {})})
        hit = self.cache.get(key) if self.cache is not None and self.cache.mode != "off" else None
        if hit is not None and hit.get("error"):
            # A recorded failure: fail the same way, so the batch is topped up as it was in the live run.
            self.calls.append({**(hit.get("record") or {}), "status": "error", "error": hit["error"][:200],
                               "replayed": True})
            exc = NebiusError(hit["error"])
            exc.logged = True
            raise exc
        if hit is not None and hit.get("content") is not None:
            self.calls.append({**hit["record"], "replayed": True})
            return hit["content"]
        if self.cache is not None and self.cache.mode == "replay":
            raise NebiusError("this request was not recorded, and replay makes no live calls", fatal=True)
        if self.client is None:
            raise NebiusError("no client for a live call", fatal=True)
        try:
            res = self.client.chat(self.model, messages, role="valley-test-engineer", temperature=self.temperature,
                                   max_tokens=max_tokens, extra_body=extra)
        except NebiusError as exc:
            rec = exc.record.to_dict() if getattr(exc, "record", None) is not None else {}
            # A billed call that failed still cost money: keep its record.
            self.calls.append({**rec, "status": "error", "error": str(exc)[:200]})
            exc.logged = True
            if self.strict and getattr(exc, "status", None) != 200:
                # Not the model's own reply (an HTTP error, a network failure or a timeout): stop the search
                # rather than let it run on as random search; a re-run replays the calls already recorded.
                exc.fatal = True
            if self.cache is not None and not exc.fatal:
                # The model's reply was cut off, empty or unreadable: record it, so a replay tops up the same way.
                self.cache.put(key, None, rec, error=str(exc))
            raise
        self.calls.append(res.record.to_dict())
        if self.cache is not None:
            self.cache.put(key, res.content, res.record.to_dict())
        return res.content

    def propose(self, history: list[dict], k: int) -> list[dict]:
        from .client import NebiusError, extract_json
        rows = [{"params": h["params"], "failed": h["outcome"]["kinds"], "severity": h["outcome"]["severity"],
                 "phones_reached_3h": h["outcome"]["phones_reached_3h"], "phones_warned_late": h["outcome"]["phones_missed"],
                 "missed_fires": len(h["outcome"]["fires_spreading_missed"]),
                 "false_alarms": h["outcome"]["alerts_from_false_fires"],
                 "needless_evacuations": len(h["outcome"]["needless_evacuations"]),
                 "alerts_not_justified": h["outcome"]["alerts_not_justified"],
                 "lead_p10_min": h["outcome"]["lead_time_min_p10"]} for h in history]
        user = (f"Tests so far ({len(rows)}):\n{json.dumps(rows, separators=(',', ':'))}\n\n"
                f"Propose exactly {k} new tests.")
        out = []
        try:
            obj = extract_json(self._ask(user))
            tests = (obj.get("tests") or []) if isinstance(obj, dict) else []
            if not isinstance(tests, list):
                tests = []
            for t in tests[:k]:
                if not isinstance(t, dict) or not _readable(t.get("params")):
                    continue                      # unreadable: its slot is topped up below and counted
                p = t["params"]
                out.append({"params": clip_params(p), "why": " ".join(str(t.get("why", "")).split())[:200],
                            "defaulted": sorted(k2 for k2 in SPACE if _number(p.get(k2)) is None)})
        except (NebiusError, ValueError, OverflowError) as exc:
            if self.strict and getattr(exc, "fatal", False):
                raise
            if not getattr(exc, "logged", False):     # a failed call is logged where it fails, with its cost
                self.calls.append({"status": "error", "error": str(exc)[:200]})
        while len(out) < k:
            out.append({**self.fallback.propose(history, 1)[0],
                        "why": "random top-up (model gave too few or unreadable tests)"})
        return out


def valley_seed(search: int, i: int, base: int = 9100) -> int:
    """The valley seed of test i in search number ``search``: the same for every searcher, so searchers are
    compared on the same valleys (a search uses at most 50 tests)."""
    if not 0 <= i < 50:
        raise ValueError("a search has at most 50 tests")
    return base + 50 * search + i


def run_search(searcher, budget: int = 20, batch: int = 4, search: int = 0, base: int = 9100, workers: int = 4,
               progress=None) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor
    history: list[dict] = []
    with ProcessPoolExecutor(workers) as ex:
        while len(history) < budget:
            k = min(batch, budget - len(history))
            props = searcher.propose(history, k)
            seeds = [valley_seed(search, len(history) + i, base) for i in range(k)]
            outcomes = list(ex.map(evaluate, [p["params"] for p in props], seeds))
            for prop, s, o in zip(props, seeds, outcomes):
                history.append({"i": len(history), "params": prop["params"], "why": prop["why"], "seed": s,
                                **({"defaulted": prop["defaulted"]} if prop.get("defaulted") else {}),
                                "outcome": o})
            if progress:
                progress(f"{searcher.name}: {len(history)}/{budget}, "
                         f"failures so far {sum(h['outcome']['failed'] for h in history)}")
    return history


def caused(h: dict, drawn: dict | None) -> set:
    """What went wrong in a test that the same valley under the drawn day did not have (``drawn`` maps a seed to
    its drawn-day kinds); every kind when ``drawn`` is None."""
    return set(h["outcome"]["kinds"]) - set((drawn or {}).get(h["seed"], ()))


def summarise_search(history: list[dict], drawn: dict | None = None) -> dict:
    """Totals for one search. With ``drawn`` (seed -> the drawn day's kinds on that valley) it also counts the
    failures the conditions caused: tests that went wrong in a way the drawn day on the same valley did not."""
    sev = [h["outcome"]["severity"] for h in history]
    fails = [h for h in history if h["outcome"]["failed"]]
    own = [h for h in history if caused(h, drawn)] if drawn is not None else None
    levels = [stress_level(h["params"]) for h in fails]
    by_what = {w: sum(w in h["outcome"]["kinds"] for h in history)
               for w in ("late-warning", "missed-fire", "false-alarm", "needless-evacuation", "drone-lost")}
    return {"tests": len(history), "random_top_ups": sum(h["why"].startswith("random top-up") for h in history),
            "failures": len(fails), "by_what": by_what,
            "distinct_failure_kinds": len(failure_kinds(history)),
            "first_failure_at": next((h["i"] + 1 for h in history if h["outcome"]["failed"]), None),
            "mildest_failure_stress": min(levels) if levels else None,
            "mean_failure_stress": round(float(np.mean(levels)), 3) if levels else None,
            "phones_warned_late": sum(h["outcome"]["phones_missed"] for h in history),
            "max_severity": max(sev) if sev else None,
            "mean_severity": round(float(np.mean(sev)), 3) if sev else None,
            **({"caused_failures": len(own),
                "caused_kinds": len({w + ":" + "+".join(causes(h["params"]) or ["other"]) for h in own
                                     for w in caused(h, drawn)}),
                "mildest_caused_stress": min((stress_level(h["params"]) for h in own), default=None)}
               if own is not None else {})}
