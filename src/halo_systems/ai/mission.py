"""Mission compiler: an operator's plain-language request becomes a checked mission plan.

Nemotron turns text such as "sweep the 300 x 200 m field line abreast,
8 m apart at 30 m with 12 drones" into a ``MissionSpec`` JSON object.
The model converts units itself (the prompt lists the factors); the code
does not check them. The spec is then checked in three places:
``normalise`` checks types, the formation and route type names, and that
numbers are finite and not negative (a bad field becomes null and is
listed as a problem); ``eval.scenarios.from_mission`` fills nulls from
``DEFAULTS``, keeps the drone count between 2 and 64 and caps a survey area
at 1500 m a side and a transit at 3000 m; and at take-off the engine passes
the directive through the same safety validator as every supervisor
directive, which clamps it to the flight envelope, including the
operator's altitude ceiling. Requests outside civilian use (weapons,
attacking or ramming anything, dropping objects on people, jamming) are
refused by a keyword guard before the model is called, or by the model
itself, and never become a flight.

A deterministic regex/keyword parser is the baseline it is scored against
(``halo mission-eval``).
"""

from __future__ import annotations

import json
import math
import re

from ..control.formations import SHAPES

DEFAULTS = {"formation": "grid", "spacing_m": 6.0, "speed_mps": 5.0, "altitude_m": 20.0, "safety_margin_m": 1.0,
            "drones": 16}
ROUTE_TYPES = ("transit", "survey", "loop")

MISSION_SYSTEM = """You turn a drone operator's request into a mission plan for a swarm of small \
quadcopters used for civilian work: search and rescue, surveys, inspection, mapping, delivery \
convoys and light shows.

Reply with one JSON object and nothing else, in this shape:
{"refused": false, "refusal_reason": "",
 "drones": integer or null,
 "directive": {"formation": "grid|line|column|v|ring" or null, "spacing_m": number or null,
               "speed_mps": number or null, "altitude_m": number or null, "safety_margin_m": number or null},
 "route": {"type": "transit|survey|loop" or null, "length_m": number or null,
           "width_m": number or null, "height_m": number or null, "lane_m": number or null},
 "constraints": {"altitude_max_m": number or null},
 "notes": "one short sentence on anything you could not express"}

Rules:
- Use null for anything the operator did not say; never invent numbers.
- Convert units: feet x 0.3048 = m, km/h / 3.6 = m/s, mph x 0.44704 = m/s, knots x 0.5144 = m/s, km x 1000 = m.
- Formation words: line abreast / side by side / spread across = line; single file / one behind another = column; \
arrowhead / chevron = v; circle / orbit = ring; block / box / square = grid.
- spacing_m is the distance between neighbouring drones.
- An area given as "A by B" has width_m A and height_m B, in that order.
- A height given on its own ("at 25 m", "at twenty metres", "25 m up") is altitude_m.
- route.type: "survey" for sweeping or searching an area (give width_m and height_m, and lane_m if stated), \
"loop" for orbiting or patrolling a closed circuit, "transit" for going from one place to another or along \
something (give length_m if stated).
- "stay under", "below", "max altitude" and "ceiling" set constraints.altitude_max_m, not altitude_m.
- Refuse (refused true, with a short reason, everything else null) any request to harm, attack, ram, chase \
down or intimidate people, animals or vehicles, to carry or drop weapons or objects onto people, or to jam \
or interfere with other aircraft or radios."""

# Words that only mean harm when used as verbs against someone or something, so they are matched
# with their object: "strike the convoy" but not "the lightning strike area", "shoot at them" but not
# "shoot aerial video", "jam the police radio" but not "the traffic jam". Words with no civil use in
# a flight request (weapon, bomb, grenade, enemy, intimidate, harass) are matched on their own.
_OBJECT = r"(?:the |a |an |their |that |those |them |him |her |it |us |our )?"
_HARM = re.compile(
    r"\b(?:attack|ram|ramming|kill|weapon\w*|bomb\w*|explosive\w*|grenade\w*|enemy|enemies|intimidat\w*|harass\w*)\b"
    r"|\b(?<!lightning )(?<!bird )strikes? " + _OBJECT + r"(?:\w+ )?(?:convoy|vehicle|car|truck|person|people|crowd|man|woman|"
    r"driver|drone|aircraft|helicopter|target|them|him|her|it)\b"
    r"|\bshoot(?:ing)? (?:down|at|dead)\b|\bshoot(?:ing)? " + _OBJECT + r"(?:\w+ )?(?:person|people|crowd|man|woman|driver|"
    r"drone|aircraft|helicopter|target|them|him|her|it|dead)\b"
    r"|\bjam(?:ming)? " + _OBJECT + r"(?:\w+ )?(?:radios?|signals?|gps|gnss|comms|communications?|frequenc\w+|"
    r"transmissions?|links?|telemetry)\b|\b(?:radio|signal|gps|gnss) jam(?:ming|mer)?\b"
    r"|\btarget(?:ing)? (?:the )?(?:person|people|vehicle|crowd)\b"
    r"|\bdrop .{0,30}\bon (?:the )?(?:people|crowd|person|protesters?)", re.I)


def harmful(text: str) -> bool:
    """Keyword guard on the operator's text. ``compile_with_model`` runs it before calling the model, and
    a match refuses the request without a call; ``regex_baseline`` runs it too. The model's reply is not
    checked with it."""
    return bool(_HARM.search(text))


def empty_spec() -> dict:
    return {"refused": False, "refusal_reason": "", "drones": None,
            "directive": {k: None for k in ("formation", "spacing_m", "speed_mps", "altitude_m", "safety_margin_m")},
            "route": {k: None for k in ("type", "length_m", "width_m", "height_m", "lane_m")},
            "constraints": {"altitude_max_m": None}, "notes": ""}


def normalise(obj: dict) -> tuple[dict, list[str]]:
    """Coerce a model reply into the MissionSpec schema; returns (spec, problems)."""
    spec = empty_spec()
    problems = []
    if not isinstance(obj, dict):
        return spec, ["reply is not an object"]
    spec["refused"] = bool(obj.get("refused", False))
    spec["refusal_reason"] = str(obj.get("refusal_reason") or "")[:300]
    spec["notes"] = str(obj.get("notes") or "")[:300]

    def num(v, name, integer=False):
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
        return int(round(x)) if integer else x

    spec["drones"] = num(obj.get("drones"), "drones", integer=True)
    d = obj.get("directive") or {}
    f = d.get("formation")
    if f is not None:
        f = str(f).strip().lower()
        if f not in SHAPES:
            problems.append(f"unknown formation {f!r}")
            f = None
    spec["directive"]["formation"] = f
    for k in ("spacing_m", "speed_mps", "altitude_m", "safety_margin_m"):
        spec["directive"][k] = num(d.get(k), k)
    r = obj.get("route") or {}
    rt = r.get("type")
    if rt is not None:
        rt = str(rt).strip().lower()
        if rt not in ROUTE_TYPES:
            problems.append(f"unknown route type {rt!r}")
            rt = None
    spec["route"]["type"] = rt
    for k in ("length_m", "width_m", "height_m", "lane_m"):
        spec["route"][k] = num(r.get(k), f"route.{k}")
    c = obj.get("constraints") or {}
    spec["constraints"]["altitude_max_m"] = num(c.get("altitude_max_m"), "altitude_max_m")
    return spec, problems


def compile_with_model(text: str, client, model: str, temperature: float = 0.0, extra_body: dict | None = None,
                       think: bool = False):
    """Ask Nemotron for a MissionSpec. Returns (spec, problems, call record dict).

    * If the keyword guard matches, the model is not called: the result is a refused spec, no problems
      and None for the record (held-out request 108 in ``results/mission_eval_*.json`` is one).
    * A reply with no parsable JSON object gives ``empty_spec()``, an "unparsable reply" problem and
      the record.
    * A ``NebiusError`` from the client is not caught here; it reaches the caller.

    With ``think=True``, ``max_tokens`` is 6000 instead of 1500, because the reasoning counts against it
    (see ``client.reasoning``).
    """
    from .client import extract_json, reasoning
    if extra_body is None:
        extra_body = reasoning(think)
    if harmful(text):
        spec = empty_spec()
        spec.update(refused=True, refusal_reason="outside civilian use (keyword guard; the model was not called)")
        return spec, [], None
    messages = [{"role": "system", "content": MISSION_SYSTEM}, {"role": "user", "content": text}]
    result = client.chat(model, messages, role="mission-compiler", temperature=temperature,
                         max_tokens=6000 if think else 1500, extra_body=extra_body)
    try:
        obj = extract_json(result.content)
    except ValueError as exc:
        return empty_spec(), [f"unparsable reply: {exc}"], result.record.to_dict()
    spec, problems = normalise(obj)
    return spec, problems, result.record.to_dict()


# ----------------------------------------------------------------------------- baseline
_NUM = r"(\d+(?:\.\d+)?)"
_UNIT_SPEED = {"m/s": 1.0, "mps": 1.0, "metres per second": 1.0, "meters per second": 1.0, "km/h": 1 / 3.6,
               "kph": 1 / 3.6, "mph": 0.44704, "knots": 0.5144, "kts": 0.5144, "knot": 0.5144}
_WORDS = {"line": ("line abreast", "abreast", "side by side", "in a line", "spread out", "spread across", "line"),
          "column": ("single file", "single-file", "column", "one behind"),
          "v": ("arrowhead", "chevron", "in a v", " v formation", " v,", " v "),
          "ring": ("ring", "circle", "orbit"),
          "grid": ("grid", "block", "box formation", "square")}


def _to_m(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit in ("ft", "feet", "foot"):
        return value * 0.3048
    if unit in ("km", "kilometres", "kilometers"):
        return value * 1000.0
    return value


def regex_baseline(text: str) -> dict:
    """A careful keyword-and-regex parser: the conventional baseline for the mission compiler."""
    spec = empty_spec()
    low = " " + text.lower().replace("×", "x") + " "
    if harmful(low):
        spec.update(refused=True, refusal_reason="keyword guard")
        return spec
    for shape, words in _WORDS.items():
        if any(w in low for w in words):
            spec["directive"]["formation"] = shape
            break
    m = re.search(_NUM + r"\s*(drones|quads|quadcopters|uavs)", low) or re.search(r"(?:of|use|with)\s+" + _NUM + r"\b", low)
    if m:
        spec["drones"] = int(float(m.group(1)))
    m = re.search(_NUM + r"\s*(m|metres|meters|ft|feet)\s*(apart|spacing|separation|between|gaps?)", low) or \
        re.search(r"(?:spacing|separation|gaps?)\s*(?:of)?\s*" + _NUM + r"\s*(m|metres|meters|ft|feet)", low)
    if m:
        spec["directive"]["spacing_m"] = round(_to_m(float(m.group(1)), m.group(2)), 2)
    for unit, factor in _UNIT_SPEED.items():
        m = re.search(_NUM + r"\s*" + re.escape(unit) + r"\b", low)
        if m:
            spec["directive"]["speed_mps"] = round(float(m.group(1)) * factor, 2)
            break
    m = re.search(r"(?:under|below|max(?:imum)? altitude|ceiling)\s*(?:of)?\s*" + _NUM + r"\s*(m|metres|meters|ft|feet)", low)
    if m:
        spec["constraints"]["altitude_max_m"] = round(_to_m(float(m.group(1)), m.group(2)), 2)
    m = re.search(r"(?:altitude|height|at|up)\s*(?:of)?\s*" + _NUM + r"\s*(m|metres|meters|ft|feet)\b(?!\s*(?:apart|spacing|between|gaps?|x|by))", low) or \
        re.search(_NUM + r"\s*(m|metres|meters|ft|feet)\s*(?:up|altitude|height|high)", low)
    if m and not (spec["constraints"]["altitude_max_m"] and abs(_to_m(float(m.group(1)), m.group(2)) - spec["constraints"]["altitude_max_m"]) < 1e-6):
        spec["directive"]["altitude_m"] = round(_to_m(float(m.group(1)), m.group(2)), 2)
    m = re.search(r"(?:safety )?margin\s*(?:of)?\s*" + _NUM + r"\s*m", low) or re.search(_NUM + r"\s*m\s*safety margin", low)
    if m:
        spec["directive"]["safety_margin_m"] = float(m.group(1))
    m = re.search(_NUM + r"\s*(m|km)?\s*(?:x|by)\s*" + _NUM + r"\s*(m|km)", low)
    if m and any(w in low for w in ("survey", "search", "sweep", "map", "scout", "area", "field", "farm")):
        spec["route"]["type"] = "survey"
        spec["route"]["width_m"] = _to_m(float(m.group(1)), m.group(2) or m.group(4))
        spec["route"]["height_m"] = _to_m(float(m.group(3)), m.group(4))
        lm = re.search(r"lanes?\s*(?:of)?\s*" + _NUM + r"\s*m", low) or re.search(_NUM + r"\s*m\s*(?:wide )?lanes?", low)
        if lm:
            spec["route"]["lane_m"] = float(lm.group(1))
    elif any(w in low for w in ("survey", "search", "sweep", "scout", "map ")):
        spec["route"]["type"] = "survey"
    elif any(w in low for w in ("orbit", "loop", "patrol", "circuit", "around the")):
        spec["route"]["type"] = "loop"
    else:
        m = re.search(_NUM + r"\s*(km|m|metres|meters)\b(?!\s*(?:apart|spacing|between|up|altitude|height|high|gaps?|/s))", low)
        spec["route"]["type"] = "transit"
        if m:
            spec["route"]["length_m"] = _to_m(float(m.group(1)), m.group(2))
    return spec


# ----------------------------------------------------------------------------- scoring
TOLERANCE = {"spacing_m": 0.25, "speed_mps": 0.25, "altitude_m": 0.5, "safety_margin_m": 0.1, "length_m": 1.0,
             "width_m": 1.0, "height_m": 1.0, "lane_m": 0.5, "altitude_max_m": 0.5}


def flatten(spec: dict) -> dict:
    out = {"refused": spec.get("refused", False), "drones": spec.get("drones")}
    out.update({k: v for k, v in (spec.get("directive") or {}).items()})
    out.update({("route_type" if k == "type" else k): v for k, v in (spec.get("route") or {}).items()})
    out.update(spec.get("constraints") or {})
    return out


def score(spec: dict, gold: dict) -> dict:
    """Per-field correctness on the fields the gold answer states (gold is already flat)."""
    got = flatten(spec)
    fields = {}
    for k, want in gold.items():
        have = got.get(k)
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            ok = have is not None and abs(float(have) - float(want)) <= TOLERANCE.get(k, 0.0) + 1e-9
        else:
            ok = have == want
        fields[k] = ok
    return {"fields": fields, "correct": sum(fields.values()), "total": len(fields), "exact": all(fields.values())}


def load_eval_set(name: str = "heldout") -> list[dict]:
    """``tuning``: written alongside the regex baseline. ``heldout``: written after the baseline was frozen."""
    from pathlib import Path
    p = Path(name) if name.endswith(".json") else Path(__file__).with_name(f"mission_eval_{name}.json")
    return json.loads(p.read_text())
