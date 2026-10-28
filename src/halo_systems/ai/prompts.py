"""The Nemotron supervisor's prompt and the status message built for it.

Everything here is model input and must stay the same byte for byte. The replay cache key
(``ai.cache.request_key``) hashes the exact messages, so a one-character change to
``SUPERVISOR_SYSTEM`` or to the text that ``key_facts`` and ``supervisor_user`` write turns the
stored calls in ``results/nemotron_cache.jsonl`` into misses. ``PROMPT_VERSION`` is recorded with every
supervisor decision; change it when the prompt is changed on purpose. The mission compiler's and the
stress tester's prompts live with their code (``MISSION_SYSTEM`` in ``ai/mission.py``,
``TESTER_SYSTEM`` in ``ai/tester.py``).
"""

PROMPT_VERSION = "2026-10-13.3"

# A backslash at the end of a line inside the string joins it to the next line, so each paragraph
# and each bullet reaches the model as one line. The prompt restates values that live in code, so
# change both together: "3 seconds" is Limits.min_change_interval_s (safety/validator.py),
# "2 x (safety margin + 2 m)" uses CORRIDOR_BUFFER_M (supervisor/rule.py), the shape list is
# control.formations.SHAPES, "over a second" is the stale-link test in Simulation.summary
# (sim/engine.py) and "every few seconds" is Scenario.supervisor_period_s (5 s by default).
SUPERVISOR_SYSTEM = """You supervise a swarm of small quadcopters flying in formation. A fast \
autopilot (50 times a second) keeps each drone in its slot and avoids collisions on its own. \
Every few seconds you receive a JSON status report and choose the swarm's directive: the \
formation shape, the spacing between neighbours, the cruise speed, the altitude and the safety \
margin. A safety validator checks your directive before the swarm uses it: values outside the \
limits are clamped, and a change less than 3 seconds after the previous one is ignored.

What the report fields mean and what your choices cost:
- mission.brief is the operator's goal. mission_directive is the plan the operator asked for; \
fly it whenever conditions allow and return to it when a problem has passed.
- formation_widths_at_current_spacing_m gives each shape's width across the direction of travel \
for the drones still flying. Width grows in proportion to spacing. A column has no width.
- environment.free_corridor_width_m is the width of the lane along the path that is free of \
obstacles, checked from the formation's tail to lookahead_m ahead of its centre (null means no \
obstacles nearby). If the formation's width plus about 2 x (safety margin + 2 m) does not fit, \
drones have to weave between obstacles: the formation breaks up and the swarm slows down. \
Tighter spacing or a narrower shape fixes this. checks.corridor has this arithmetic done for you: \
needed_width_m per shape at the current spacing, the shapes that fit_now, and the \
widest_spacing_that_fits_m per shape. Spacing must stay at or above limits.spacing_floor_m.
- Strong gusts (environment.gust_peak_mps_last_5s) and near misses push drones toward each other: \
more spacing and a larger safety margin give them room, at the cost of a wider formation.
- comms.stale_link_fraction is the share of neighbour links not heard from for over a second. \
When links are stale each drone sees its neighbours late, so more spacing and a lower speed are safer.
- energy.wh_per_km_by_speed is the energy one drone spends per kilometre at each cruise speed in the \
current headwind. energy.usable_wh_mean and usable_wh_min are the charge above the reserve; a drone \
that reaches the reserve leaves the formation. A leg needs about wh_per_km x remaining_km; \
checks.energy gives that for the mission speed, the current speed and the cheapest speed. \
The operator chose the mission speed: fly faster than it only when the charge is not enough at \
that speed. The cheapest speed is a way to save charge, not a target.
- Every change of shape or spacing makes the drones move to new slots for several seconds, during \
which the formation is disordered. Change only when it helps, and avoid switching back and forth.

Each status message starts with key facts: the corridor and energy checks and the recent \
disturbances, written out in words by code from the same report. They are exact; use them rather \
than redoing the arithmetic.

Reply with one JSON object and nothing else:
{"formation": "grid|line|column|v|ring", "spacing_m": number, "speed_mps": number, \
"altitude_m": number, "safety_margin_m": number, "reason": "one short sentence"}
Always give all five values. To keep the current directive, repeat it."""


def _num(x) -> str:
    # Numbers are written with :g, so 6.0 reads as 6. The format is part of the message text, and so of the
    # cache key, like every other character here.
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def key_facts(summary: dict) -> list[str]:
    """The report's checks in plain sentences. No new information: everything comes from ``summary``.

    On the tuning seeds, Nano without reasoning read ``"fits_now": ["column"]`` and still answered
    that its grid "fits the corridor", so the verdicts are spelled out here.
    """
    cur, plan = summary["current_directive"], summary["mission_directive"]
    checks = summary.get("checks") or {}
    env, safety, comms = summary["environment"], summary["safety"], summary["comms"]
    facts = []
    cor = checks.get("corridor")
    if cor is None:
        if env.get("free_corridor_width_m") is None:
            facts.append(f"No obstacles within {_num(env.get('lookahead_m'))} m ahead: every shape fits.")
    else:
        shape, sp = cur["formation"], cur["spacing_m"]
        need = cor["needed_width_m"][shape]
        fits = shape in cor["fits_now"]
        facts.append(f"The obstacle-free lane ahead is {_num(cor['free_width_m'])} m wide. The current {shape} at "
                     f"{_num(sp)} m spacing needs {_num(need)} m, so it {'fits' if fits else 'does NOT fit'}.")
        facts.append("Shapes that fit at the current spacing: " + (", ".join(cor["fits_now"]) or "none") + ".")
        widest = cor["widest_spacing_that_fits_m"].get(shape)
        floor = summary["limits"]["spacing_floor_m"]
        if not fits and widest is not None:
            if widest >= floor:
                facts.append(f"A {shape} would fit at {_num(widest)} m spacing or less (floor {_num(floor)} m).")
            else:
                facts.append(f"A {shape} would need {_num(widest)} m spacing to fit, below the {_num(floor)} m floor, "
                             f"so no {shape} fits here.")
        if "mission_plan_fits" in cor:
            facts.append(f"The mission plan ({plan['formation']} at {_num(plan['spacing_m'])} m) needs "
                         f"{_num(cor['mission_plan_needs_m'])} m and {'fits' if cor['mission_plan_fits'] else 'does not fit'} now.")
    en = checks.get("energy")
    if en:
        usable, low = en["usable_wh_mean"], summary["energy"].get("usable_wh_min", en["usable_wh_mean"])

        def verdict(wh):
            if wh <= low:
                return "enough for every drone"
            if wh <= usable:
                return "enough on average, not for the weakest drone"
            return "NOT enough"
        km = (summary["mission"].get("remaining_m") or 0.0) / 1000.0
        facts.append(f"{km:.2f} km left. Usable charge per drone: {_num(usable)} Wh on average, {_num(low)} Wh lowest.")
        facts.append(f"Energy needed per drone: {_num(en['wh_needed_at_mission_speed'])} Wh at the mission speed "
                     f"{_num(plan['speed_mps'])} m/s ({verdict(en['wh_needed_at_mission_speed'])}); "
                     f"{_num(en['wh_needed_at_current_speed'])} Wh at the current {_num(cur['speed_mps'])} m/s "
                     f"({verdict(en['wh_needed_at_current_speed'])}); {_num(en['wh_needed_at_cheapest_speed'])} Wh at the "
                     f"cheapest {_num(en['cheapest_speed_mps'])} m/s ({verdict(en['wh_needed_at_cheapest_speed'])}).")
    facts.append(f"Last 5 s: gusts up to {_num(env.get('gust_peak_mps_last_5s'))} m/s, "
                 f"{safety.get('near_misses_last_5s', 0)} near misses, closest pair {_num(safety.get('closest_pair_m_last_5s'))} m, "
                 f"{round(100 * (comms.get('stale_link_fraction') or 0))}% of radio links stale.")
    diff = [k for k in ("formation", "spacing_m", "speed_mps", "altitude_m", "safety_margin_m") if cur[k] != plan[k]]
    if diff:
        facts.append("The current directive differs from the mission plan in: "
                     + ", ".join(f"{k} ({_num(cur[k])} now, {_num(plan[k])} planned)" for k in diff) + ".")
    else:
        facts.append("The current directive is the mission plan.")
    return facts


def supervisor_user(summary: dict, history: list[dict]) -> str:
    """The user message: the key facts as a list, the whole report as compact JSON, then up to four of
    the model's most recent directives (with their times, without reasons). Its exact text is part of
    the cache key."""
    import json
    lines = ["Key facts:"] + [f"- {f}" for f in key_facts(summary)]
    lines += ["Status report:", json.dumps(summary, separators=(",", ":"))]
    if history:
        lines.append("Your recent directives (oldest first):")
        lines.append(json.dumps([{k: v for k, v in h.items() if k != "reason"} for h in history[-4:]],
                                separators=(",", ":")))
    lines.append("Choose the directive now.")
    return "\n".join(lines)
