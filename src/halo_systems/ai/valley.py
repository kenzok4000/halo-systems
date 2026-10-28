"""Valley mission compiler: an operator's plain words, applied to the night's lightning list, become a checked
squad patrol.

An operator types a request such as "check last night's strikes in the north block with 16 drones". Nemotron
reads the words and answers with a ``ValleySpec``: the drones, the areas the operator named, any drones they put
on an area, the patrol length, and which strikes to check as a filter: when ("last night", "the night before",
"since midnight", "after 10 pm", "in the last 5 hours") and where (in the areas named, outside every patrol area,
or anywhere). Reading those phrasings is the part a keyword parser finds hard.

The model only reads the request. Code does the rest by fixed rules and checks it (``select_strikes``, ``plan``):
it applies the filter to the strike list (``eval.scenarios.valley_strikes``: id, day, time, hours before now,
position and the patrol area each falls in), picks the areas that fly (the ones named, else the ones holding the
chosen strikes, else all three), the drones per area (the operator's numbers, the rest by the default split,
``control.squads.allocate``), each line no wider than its area, at most 64 drones and a patrol of 1 to 15 minutes
(one battery). Anything it changes is listed in ``problems``. In flight each squad starts on the lane nearest its
chosen strikes (``control.squads.cue_lane``), and every directive passes the same safety validator as any other.
The model never flies the drones, never picks a strike by itself and takes no part in alerts.

``regex_baseline`` is the keyword-and-regex parser it is scored against (``halo valley-eval``), with the same
output. Requests outside civilian use are refused by the same keyword guard as the mission compiler
(``ai.mission.harmful``) before any call, or by the model.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

AREAS = ("F", "N", "S")
AREA_NAMES = {"F": "valley floor", "N": "north slopes", "S": "south slopes"}
DEFAULT_DRONES = 16
DEFAULT_PATROL_MIN = 12.0
PATROL_MIN_RANGE = (1.0, 15.0)        # one battery (scenarios.PATROL_S is 12 min; 15 is the dashboard's limit)
MAX_DRONES = 64

NOW_MIN = 8 * 60                    # minutes after midnight when the patrol starts (scenarios.FLIGHT_CLOCK_H)
WHEN = ("all", "last_night", "night_before", "today")       # or "since HH:MM", or "within N" (hours)
WHERE = ("named", "outside", "any")

VALLEY_SYSTEM = """You plan a patrol of a mountain valley by small scout drones that look for wildfires \
(civilian fire detection; the drones carry cameras only). The valley has three patrol areas: \
F = valley floor, N = north slopes, S = south slopes. It is now 08:00 and the patrol starts now. A list of the \
lightning strikes reported over the valley in the last two nights is on file; you do not see it. Code applies \
your strike filter to it.

Reply with one JSON object and nothing else, in this shape:
{"refused": false, "refusal_reason": "",
 "drones": integer or null,
 "areas": list of "F", "N", "S", or null,
 "per_area": {"F": integer, "N": integer, "S": integer} with only the areas given a number, or null,
 "patrol_min": number or null,
 "strikes": null or {"when": "...", "where": "..."},
 "notes": "one short sentence on anything you could not express"}

Rules:
- Use null for anything the operator did not say. Never invent numbers or areas.
- drones: how many drones the operator asks for in all.
- areas: the patrol areas the operator names. North slopes, north side, north block, north ridge = N; south \
slopes, south side, south block = S; valley floor, valley bottom, the floor, the flats = F; the whole valley, \
everywhere, all areas = ["F", "N", "S"]. null if they name no area: do not fill in all three, and strikes \
alone never add an area (code flies the areas that hold the chosen strikes).
- per_area: only numbers the operator puts on a named area ("six on the floor" gives {"F": 6}). If they give one \
total ("16 drones on the north slopes"), that is drones, and per_area is null.
- patrol_min: how long to patrol, in minutes (hours x 60). "In the last 5 hours" is a strike filter, not a patrol.
- strikes: null when the request does not ask to check strikes or lightning. Otherwise:
  when: "last_night" (last night, overnight, last night's storm), "night_before" (the night before, two \
nights ago, the night before last), "today" (since midnight, after midnight, this morning), "since HH:MM" for a \
clock time they give, in 24-hour time ("after 10 pm" = "since 22:00", "from 9:30 last night" = "since 21:30"), \
"within N" for the last N hours ("in the past 5 hours" = "within 5"), or "all" when they give no time.
  where: "named" when they tie the strikes to the areas they name ("strikes on the north slopes", "the strikes \
there", "on those two"), "outside" for strikes outside the patrol areas, "any" otherwise.
- Refuse (refused true, a short reason, everything else null) any request to harm, chase, follow, watch or \
intimidate people, animals or vehicles, to drop anything on people, or to jam radios or other aircraft.

Examples (other fields null):
"Fly the north slopes with 10 drones for 12 minutes" -> {"drones": 10, "areas": ["N"], "patrol_min": 12}
"Five on the flats, seven on the south side, and look at the strikes on both from after 1 am" -> \
{"areas": ["F", "S"], "per_area": {"F": 5, "S": 7}, "strikes": {"when": "since 01:00", "where": "named"}}
"Check every strike from the storm two nights ago" -> {"strikes": {"when": "night_before", "where": "any"}}
"20 drones over the whole valley, and check last night's lightning" -> \
{"drones": 20, "areas": ["F", "N", "S"], "strikes": {"when": "last_night", "where": "any"}}"""


def user_message(text: str) -> str:
    return f"Request: {text.strip()}"


def empty_spec() -> dict:
    return {"refused": False, "refusal_reason": "", "drones": None, "areas": None, "per_area": None,
            "patrol_min": None, "strikes": None, "notes": ""}


def _area_code(v) -> str | None:
    s = str(v).strip().upper()
    if s in AREAS:
        return s
    low = str(v).strip().lower()
    for code, name in AREA_NAMES.items():
        if low in (name, name.split()[0], code.lower()):
            return code
    return None


def _filter(v, problems: list[str]) -> dict | None:
    """A strike filter {"when", "where"} from a reply, or None; a bad part is listed and replaced by its default."""
    if v is None or v is False:
        return None
    if not isinstance(v, dict):
        problems.append(f"strikes is not an object: {v!r}")
        return {"when": "all", "where": "any"}
    when = str(v.get("when") or "all").strip().lower().replace(" ", "_") if v.get("when") is not None else "all"
    # Spaces became underscores so that "last night" reads as "last_night"; the two forms that carry a value
    # get their space back.
    if when.startswith("since_"):
        when = "since " + when[6:]
    elif when.startswith("within_"):
        when = "within " + when[7:]
    ok = when in WHEN
    if when.startswith("since "):
        m = re.fullmatch(r"since (\d{1,2}):(\d{2})", when)
        ok = bool(m) and int(m.group(1)) < 24 and int(m.group(2)) < 60
        if ok:
            when = f"since {int(m.group(1)):02d}:{m.group(2)}"
    elif when.startswith("within "):
        try:
            h = float(when[7:])
            ok = math.isfinite(h) and h > 0
            when = f"within {h:g}"
        except ValueError:
            ok = False
    if not ok:
        problems.append(f"unknown strike time {v.get('when')!r}: all strikes")
        when = "all"
    where = str(v.get("where") or "any").strip().lower()
    if where not in WHERE:
        problems.append(f"unknown strike place {v.get('where')!r}: anywhere")
        where = "any"
    return {"when": when, "where": where}


def normalise(obj: dict) -> tuple[dict, list[str]]:
    """Coerce a model reply into the ValleySpec schema; returns (spec, problems)."""
    spec = empty_spec()
    problems: list[str] = []
    if not isinstance(obj, dict):
        return spec, ["reply is not an object"]
    spec["refused"] = bool(obj.get("refused", False))
    spec["refusal_reason"] = str(obj.get("refusal_reason") or "")[:300]
    spec["notes"] = str(obj.get("notes") or "")[:300]

    def count(v, name):
        if v is None or v == "":
            return None
        try:
            x = float(v)
        except (TypeError, ValueError):
            problems.append(f"{name} is not a number: {v!r}")
            return None
        if not math.isfinite(x) or x < 0:
            problems.append(f"{name} must be a non-negative number")
            return None
        return x

    d = count(obj.get("drones"), "drones")
    spec["drones"] = None if d is None else int(round(d))
    areas = obj.get("areas")
    if areas is not None:
        if isinstance(areas, str):
            areas = list(AREAS) if areas.strip().lower() in ("all", "everywhere", "whole valley") else [areas]
        got = []
        for v in areas if isinstance(areas, list) else []:
            c = _area_code(v)
            if c is None and str(v).strip().lower() in ("all", "everywhere", "whole valley"):
                got += [c for c in AREAS if c not in got]
            elif c is None:
                problems.append(f"unknown area {v!r}")
            elif c not in got:
                got.append(c)
        spec["areas"] = [c for c in AREAS if c in got] or None
    per = obj.get("per_area")
    if isinstance(per, dict):
        out = {}
        for k, v in per.items():
            c = _area_code(k)
            x = count(v, f"per_area.{k}")
            if c is None:
                problems.append(f"unknown area {k!r}")
            elif x is not None:
                out[c] = int(round(x))
        spec["per_area"] = {c: out[c] for c in AREAS if c in out} or None
    spec["patrol_min"] = count(obj.get("patrol_min"), "patrol_min")
    spec["strikes"] = _filter(obj.get("strikes"), problems)
    # Two readings the schema rules out, repaired the same way whoever wrote the spec.
    per = spec["per_area"]
    if per and len(per) == 1 and spec["drones"] is not None and next(iter(per.values())) == spec["drones"]:
        problems.append("one area's number is the total: read as drones in all")
        spec["per_area"] = None
    if spec["strikes"] and spec["strikes"]["where"] == "named" and not spec["areas"]:
        problems.append("strikes tied to areas, but no area named: strikes anywhere")
        spec["strikes"]["where"] = "any"
    return spec, problems


def select_strikes(spec: dict, strikes: list[dict]) -> list[int] | None:
    """The ids of the listed strikes a spec's filter keeps (None when it asks for none). Clock times are read as the
    latest such time before now (08:00 today), within the last 24 hours."""
    want = spec.get("strikes")
    if not want or spec.get("refused"):
        return None
    when, where = want.get("when", "all"), want.get("where", "any")
    keep = list(strikes)
    if when == "last_night":
        keep = [k for k in keep if k["hours_ago"] < 24]
    elif when == "night_before":
        keep = [k for k in keep if k["hours_ago"] >= 24]
    elif when == "today":
        keep = [k for k in keep if k["day"] == "today"]
    elif when.startswith("since "):
        hh, mm = (int(v) for v in when[6:].split(":"))
        start = hh * 60 + mm
        # A clock time after 08:00 has not come yet today, so it means yesterday's (1440 min earlier).
        start = start if start <= NOW_MIN else start - 1440
        keep = [k for k in keep if NOW_MIN - round(k["hours_ago"] * 60) >= start and k["hours_ago"] < 24]
    elif when.startswith("within "):
        keep = [k for k in keep if k["hours_ago"] <= float(when[7:])]
    elif when != "all":
        raise ValueError(f"unknown strike time filter {when!r}")
    if where == "named":
        keep = [k for k in keep if k["area"] in (spec.get("areas") or [])]
    elif where == "outside":
        keep = [k for k in keep if k["area"] == "-"]
    elif where != "any":
        raise ValueError(f"unknown strike place filter {where!r}")
    return [k["id"] for k in keep]


def compile_with_model(text: str, client, model: str, *, temperature: float = 0.0, think: bool = False, cache=None):
    """Ask Nemotron for a ValleySpec. Returns (spec, problems, call record dict or None, how), ``how`` being
    "guard" (refused before any call), "live" or "replayed".

    ``cache`` (an ``ai.cache.CallCache``) records each live reply, or in "replay" mode answers from the record
    only: a request it has not seen raises a fatal ``NebiusError`` instead of calling. ``client`` may be None in
    replay mode.
    """
    from .cache import request_key
    from .client import NebiusError, extract_json, reasoning
    from .mission import harmful
    if harmful(text):
        spec = empty_spec()
        spec.update(refused=True, refusal_reason="outside civilian use (keyword guard; the model was not called)")
        return spec, [], None, "guard"
    messages = [{"role": "system", "content": VALLEY_SYSTEM}, {"role": "user", "content": user_message(text)}]
    max_tokens = 6000 if think else 800
    extra = reasoning(think)
    key = request_key(model, messages, temperature, {"extra_body": extra, "max_tokens": max_tokens})
    # Only a recorded reply is served. A failed call is not stored here: the client's NebiusError reaches
    # the caller.
    hit = cache.get(key) if cache is not None and cache.mode != "off" else None
    if hit is not None and hit.get("content") is not None:
        content, record, how = hit["content"], dict(hit["record"]), "replayed"
    elif cache is not None and cache.mode == "replay":
        raise NebiusError("this request was not recorded, and replay makes no live calls", fatal=True)
    else:
        if client is None:
            raise NebiusError("no client for a live call", fatal=True)
        result = client.chat(model, messages, role="valley-compiler", temperature=temperature,
                             max_tokens=max_tokens, extra_body=extra)
        content, record, how = result.content, result.record.to_dict(), "live"
        # Stored before it is parsed, so an unparsable reply replays the same way.
        if cache is not None:
            cache.put(key, content, record)
    try:
        obj = extract_json(content)
    except ValueError as exc:
        return empty_spec(), [f"unparsable reply: {exc}"], record, how
    spec, problems = normalise(obj)
    return spec, problems, record, how


# ----------------------------------------------------------------------------- the patrol from a spec
def plan(spec: dict, strikes: list[dict], *, default_drones: int = DEFAULT_DRONES,
         default_patrol_min: float = DEFAULT_PATROL_MIN) -> dict:
    """The patrol a spec asks for, by fixed rules, with every change listed in ``problems``.

    Returns {"refused", "drones", "areas", "counts" ({code: drones} with "R" for the reserve), "squads" (e.g.
    "F0-N12-S0-R4"), "patrol_min", "strike_ids" (chosen by the filter and cued), "uncovered" (chosen strikes no
    squad flies over), "options" (the body fields of the dashboard's POST /api/runs; ``scenario_options`` gives
    ``eval.scenarios.make``'s), "problems"}.
    """
    from ..control.directive import Directive
    from ..control.squads import allocate, canonical, cap_for
    from ..eval.scenarios import (PATROL_S, SCOUT_ALTITUDE_M, SCOUT_SPACING_M, SCOUT_SPEED_MPS, valley_areas,
                                  wildfire_valley)
    if spec.get("refused"):
        return {"refused": True, "reason": spec.get("refusal_reason") or "outside civilian use", "problems": []}
    problems: list[str] = []
    by_id = {k["id"]: k for k in strikes}
    ids = select_strikes(spec, strikes) or []
    chosen = [by_id[i] for i in ids]
    if spec.get("strikes") and not ids:
        problems.append("no strike on the list matches the request")
    per = {c: int(v) for c, v in (spec.get("per_area") or {}).items() if c in AREAS and v is not None}
    named = list(spec.get("areas") or [])
    # The areas that fly: those named (with any given a number), else those given a number, else those
    # holding the chosen strikes, else all three.
    if named:
        fly = [c for c in AREAS if c in named or c in per]
    elif per:
        fly = [c for c in AREAS if c in per]
    elif chosen:
        fly = [c for c in AREAS if any(k["area"] == c for k in chosen)]
        if not fly:
            problems.append("none of the chosen strikes is in a patrol area: patrolling the whole valley")
            fly = list(AREAS)
    else:
        fly = list(AREAS)
    n = spec.get("drones")
    if n is None:
        n = sum(per.values()) if per else default_drones
    if n > MAX_DRONES:
        problems.append(f"{n} drones asked for: at most {MAX_DRONES}")
        n = MAX_DRONES
    if n < 1:
        problems.append("no drones asked for: flying the default")
        n = default_drones
    patrol = spec.get("patrol_min")
    patrol = default_patrol_min if patrol is None else float(patrol)
    lo, hi = PATROL_MIN_RANGE
    if not lo <= patrol <= hi:
        problems.append(f"a {patrol:g}-minute patrol is outside one battery ({lo:g} to {hi:g} min): "
                        f"{min(max(patrol, lo), hi):g} min")
        patrol = min(max(patrol, lo), hi)
    # A one-drone valley, built only to read the camera's footprint at the scouting height: the swath is the
    # width of ground one drone sees. Any seed would do.
    sc = wildfire_valley(0, n=1)
    swath = 2.0 * float(sc.sensor.footprint_radius(SCOUT_ALTITUDE_M))
    directive = Directive("line", SCOUT_SPACING_M, SCOUT_SPEED_MPS, SCOUT_ALTITUDE_M, 1.0, source="mission")
    areas = {a.code: a for a in valley_areas()}
    counts = {c: 0 for c in AREAS}
    for c, k in per.items():
        cap = cap_for(areas[c].height, SCOUT_SPACING_M, swath)
        if k > cap:
            problems.append(f"{k} drones in one line do not fit across the {AREA_NAMES[c]}: {cap} fly there")
            k = cap
        counts[c] = k
    if sum(counts.values()) > n:
        problems.append(f"the numbers per area add up to {sum(counts.values())}, more than the {n} asked for: "
                        "flying the numbers per area")
        n = sum(counts.values())
    # The operator's numbers per area, capped above, stand. The drones left go to the other flying areas by
    # the default split, and whatever it does not place stays in reserve ("R").
    rest = [areas[c] for c in fly if c not in per]
    left = n - sum(counts.values())
    if rest and left > 0:
        got = allocate(left, rest, directive, swath, min(PATROL_S, patrol * 60.0))
        for a in rest:
            counts[a.code] = got.get(a.code, 0)
        reserve = got["R"]
    else:
        reserve = left
    counts["R"] = reserve
    if not any(counts[c] for c in AREAS):
        problems.append("too few drones for any squad: all of them fly the first area")
        counts[fly[0]] = min(n, cap_for(areas[fly[0]].height, SCOUT_SPACING_M, swath))
        counts["R"] = n - counts[fly[0]]
    flown = [c for c in AREAS if counts[c] > 0]
    uncovered = [k["id"] for k in chosen if k["area"] not in flown]
    return {"refused": False, "drones": n, "areas": flown, "counts": counts, "squads": canonical(counts),
            "patrol_min": patrol, "strike_ids": ids, "uncovered": uncovered,
            "options": {"squads": canonical(counts), "patrol_min": patrol, "strikes": ids},
            "problems": problems}


def scenario_options(p: dict) -> dict:
    """``eval.scenarios.make`` options for the wildfire valley from a plan."""
    o = p["options"]
    return {"squads": o["squads"], "patrol_s": 60.0 * float(o["patrol_min"]), "strikes": list(o["strikes"])}


# ----------------------------------------------------------------------------- baseline
# Frozen with the prompt before the held-out set was written (see load_set), rough edges included, so its
# scores stay a fair comparison for the model.
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "twenty-four": 24, "thirty": 30,
             "thirty-two": 32, "forty": 40, "forty-eight": 48, "sixty-four": 64, "a dozen": 12, "dozen": 12,
             "half a dozen": 6, "a couple of": 2, "a pair of": 2}
_NUMWORD_RE = "|".join(sorted((re.escape(w) for w in _NUMWORDS), key=len, reverse=True))
_N = rf"(\d+|{_NUMWORD_RE})"
_AREA_WORDS = {"N": ("north",), "S": ("south",), "F": ("floor", "valley bottom", "bottom of the valley", "flats")}
_ALL_WORDS = ("whole valley", "entire valley", "everywhere", "all areas", "all three areas", "every area")
_STRIKE_WORDS = ("strike", "lightning", "bolt", "flash")


def _num(s: str) -> int:
    s = s.strip().lower()
    return int(s) if s.isdigit() else _NUMWORDS[s]


def _areas_in(low: str) -> list[str]:
    if any(w in low for w in _ALL_WORDS):
        return list(AREAS)
    return [c for c in AREAS if any(w in low for w in _AREA_WORDS[c])]


_PLACE = r"(?:north|south|floor|valley bottom|flats)"
_STRIKE = r"(?:strikes?|lightning|bolts?|flashes)"


def _tied(low: str) -> bool:
    """Whether the request ties the strikes to the areas it names: "strikes on the north slopes", "south-side
    strikes", "the strikes there", "on those two"."""
    return bool(re.search(_STRIKE + r"[^.;!?]{0,40}?\b(?:in|on|over|at|across|from)\s+(?:the\s+)?(?:\w+\s+)?" + _PLACE, low)
                or re.search(_PLACE + r"[\w\s-]{0,15}?\b" + _STRIKE, low)
                or re.search(_STRIKE + r"[^.;!?]{0,40}?\b(?:there|on those|in those|on them|in them|on both)\b", low))


def regex_baseline(text: str) -> dict:
    """A careful keyword-and-regex parser: the conventional baseline for the valley compiler, with the same output."""
    from .mission import harmful
    spec = empty_spec()
    low = " " + text.lower().replace("’", "'").replace("night-before", "night before") + " "
    if harmful(low):
        spec.update(refused=True, refusal_reason="keyword guard")
        return spec
    per, spans = {}, []
    for m in re.finditer(_N + r"\s+(?:drones\s+|of them\s+|scouts\s+)?(?:on|over|to|for|in|across)\s+(?:the\s+)?"
                         r"(north|south|valley floor|floor|valley bottom|flats)", low):
        code = "N" if m.group(2) == "north" else "S" if m.group(2) == "south" else "F"
        per[code] = _num(m.group(1))
        spans.append(m.span())
    # One number on one area and no other count is the total ("16 drones on the north slopes").
    totals = [m for m in re.finditer(_N + r"\s+(?:\w+\s+)?(?:drones|quads|scouts|aircraft|uavs)\b", low)
              if not any(a <= m.start() < b for a, b in spans)]
    if totals:
        spec["drones"] = _num(totals[0].group(1))
    elif len(per) == 1 and re.search(_N + r"\s+(?:drones|scouts)\s+(?:on|over|to|for|in|across)", low):
        spec["drones"], per = next(iter(per.values())), {}
    spec["per_area"] = per or None
    areas = _areas_in(low)
    spec["areas"] = areas or None
    # A duration straight after "last" or "past" is meant as a strike filter ("in the last 5 hours"), not the
    # patrol. The look-behind guards only a one-digit number: "last 12 hours" is still read from its 2, as a
    # 2-hour patrol, one of the frozen baseline's rough edges.
    m = re.search(r"(?<!last )(?<!past )(\d+(?:\.\d+)?)\s*(?:-\s*)?(min|mins|minutes|minute|hours?|hrs?)\b", low)
    if m:
        v = float(m.group(1))
        spec["patrol_min"] = v * 60.0 if m.group(2).startswith("h") else v
    elif "half an hour" in low:
        spec["patrol_min"] = 30.0
    elif "quarter of an hour" in low or "quarter hour" in low:
        spec["patrol_min"] = 15.0
    if any(w in low for w in _STRIKE_WORDS):
        when = "all"
        if "night before" in low or "two nights ago" in low or "previous night" in low:
            when = "night_before"
        elif "since midnight" in low or "after midnight" in low or "this morning" in low:
            when = "today"
        elif (m := re.search(r"(?:last|past)\s+" + _N + r"\s+hours", low)):
            when = f"within {_num(m.group(1))}"
        elif (m := re.search(r"(?:after|since|from)\s+(\d{1,2})(?::(\d\d))?\s*(am|pm|a\.m\.|p\.m\.)?"
                             r"(?!\s*(?:drones|min|minutes|m\b|hours))", low)):
            h, mer = int(m.group(1)), (m.group(3) or "").replace(".", "") or None
            # A bare hour is read as the night's: 7 to 11 in the evening, up to 6 in the morning.
            if mer is None and h < 12:
                mer = "pm" if h >= 7 else "am"
            if mer == "pm" and h < 12:
                h += 12
            if mer == "am" and h == 12:
                h = 0
            when = f"since {h:02d}:{int(m.group(2) or 0):02d}"
        elif "last night" in low or "overnight" in low or "tonight" in low:
            when = "last_night"
        where = "outside" if "outside" in low else "named" if areas and _tied(low) else "any"
        spec["strikes"] = {"when": when, "where": where}
    return spec


# ----------------------------------------------------------------------------- scoring
def flatten(spec: dict, strikes: list[dict] | None = None) -> dict:
    out = {"refused": bool(spec.get("refused")), "drones": spec.get("drones"),
           "areas": sorted(spec["areas"]) if spec.get("areas") else None,
           "per_area": dict(sorted(spec["per_area"].items())) if spec.get("per_area") else None,
           "patrol_min": spec.get("patrol_min"), "strikes": spec.get("strikes")}
    if strikes is not None:
        ids = select_strikes(spec, strikes)
        out["strike_ids"] = sorted(ids) if ids is not None else None
    return out


def score(spec: dict, gold: dict, strikes: list[dict]) -> dict:
    """Per-field correctness on every field, and whether the patrol that would fly (``plan``) is the gold one.
    Strikes are scored on the ids the filter keeps from this valley's list, not on how the filter is written."""
    got, want = flatten(spec, strikes), flatten(gold, strikes)
    fields = {}
    for k in ("refused", "drones", "areas", "per_area", "patrol_min", "strike_ids"):
        if k == "patrol_min" and want[k] is not None and got[k] is not None:
            fields[k] = abs(float(got[k]) - float(want[k])) <= 0.5
        elif k == "drones" and want[k] is None and want["per_area"] and got[k] is not None:
            fields[k] = got[k] == sum(want["per_area"].values())      # "six on the floor": six in all, said or not
        else:
            fields[k] = got[k] == want[k]
    if want["refused"]:
        fields = {"refused": got["refused"]}
    p_got, p_want = plan(spec, strikes), plan(gold, strikes)
    # The same flight: the same squads and patrol, cued on the same strikes (strikes no squad flies over only differ
    # in what the plan lists as uncovered).
    same = all(p_got.get(k) == p_want.get(k) for k in ("refused", "counts", "patrol_min")) and \
        _cued(p_got) == _cued(p_want)
    out = {"fields": fields, "correct": sum(fields.values()), "total": len(fields), "exact": all(fields.values()),
           "plan_same": same}
    if want["strike_ids"] is not None:
        g, w = set(got["strike_ids"] or []), set(want["strike_ids"])
        out["strikes"] = {"tp": len(g & w), "fp": len(g - w), "fn": len(w - g)}
    return out


def _cued(p: dict) -> list[int]:
    return [i for i in p.get("strike_ids") or [] if i not in (p.get("uncovered") or [])]


def load_set(name: str) -> list[dict]:
    """``tuning`` (written with the regex baseline and the prompt) or ``heldout`` (written after both were frozen),
    or a path. Each item is {id, seed, text, intent}; the intent is a spec (refused, drones, areas, per_area,
    patrol_min, strikes), and the gold strike ids are computed from it and the seed's list, never typed."""
    p = Path(name) if name.endswith(".json") else Path(__file__).with_name(f"valley_eval_{name}.json")
    return json.loads(p.read_text())


def gold_spec(item: dict) -> dict:
    """The gold spec of an eval item."""
    it = item["intent"]
    if it.get("refused"):
        return {**empty_spec(), "refused": True}
    return {**empty_spec(), **{k: it.get(k) for k in ("drones", "areas", "per_area", "patrol_min", "strikes")}}
