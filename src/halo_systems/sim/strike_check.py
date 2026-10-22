"""Strike checks: a small team of drones looks at last night's lightning strikes up close, one strike at a time.

The morning after dry lightning, a fire agency has a list of the night's strikes, many more than it can check.
In the wildfire valley the area squads patrol as before; this team (squad code "C", CHECK_K drones plus any the
area split would keep in reserve) flies to one strike, patrols a square round it (CHECK_BOX_M, about the strike
positions' error) for one full circuit with its thermal cameras looking down and its forward cameras watching for
smoke, then moves on to the next. It flies CHECK_LIFT_M above the squads so it can cross their areas, and changes
box the way drones move between squads (at the transit layer, line abreast). Its thermal footprint is held to the
squads' (``Simulation`` caps it), so the extra height buys it no wider view.

What the team checks next is the one decision a picker makes (``supervisor/strikes.py``): a fixed rule, or
Nemotron reading the strike table. Code builds the table (``strike_table``) from what the swarm knows: each
strike's night, its distance from the team, the ground and slope under its reported position, whether an area
squad patrols that ground, how much of the ground around it a thermal camera has already looked at, the nearest
fire the station has located, smoke bearings the station cannot explain that pass near it, and the nearest
people. Code offers only strikes the team can reach and look at before the patrol ends, the safety validator
checks every order (a listed, unchecked strike; a team with a drone flying; no airspace hold), and code flies the
team. While an airspace hold is in force, or no team drone is flying, nothing is picked. The picker never flies a
drone and takes no part in locating fires, forecasts, crews or alerts.

Scoring only (``evaluate``): which fire, if any, each checked strike started (WildfireConfig.strike_fires). The
picker never sees that, nor the sightings' true fire.
"""

from __future__ import annotations

import math

import numpy as np

CHECK_K = 4                 # drones in the team: a sound smoke fix needs bearings from three drones
CHECK_BOX_M = 120.0         # side of the square patrolled round a reported strike (positions carry about 50 m error)
CHECK_LIFT_M = 10.0         # the team flies this far above the squads' altitude
NEAR_FIRE_M = 150.0         # a located fire this close to a strike is taken as that strike's fire (the table, rule)
SMOKE_WINDOW_S = 180.0      # unexplained smoke bearings are counted over the last three minutes
SMOKE_NEAR_M = 80.0         # ... when they pass this close to the strike
SEEN_RADIUS_M = 60.0        # the share already looked at by a thermal camera, over this circle round the strike
JOIN_MARGIN_S = 30.0        # time allowed for the pick and for the team to join its box, in the reachability test
GROUND = {0: "rock", 1: "grass", 2: "scrub", 3: "forest", 4: "bare"}   # terrain cover codes (sim.terrain)


def check_box(x: float, y: float, extent: tuple, box_m: float = CHECK_BOX_M, *, k: int = CHECK_K,
              spacing: float = 14.0, swath: float = 16.6) -> tuple:
    """The square a team patrols round a strike, moved inside the valley (``extent``) when the strike is near
    its edge: far enough in that the team's line, which reaches half its width past the square in turns, stays
    inside it with 10 m to spare."""
    x0, y0, x1, y1 = extent
    h = 0.5 * box_m
    margin = h + 0.5 * max(k - 1, 0) * spacing + 0.5 * swath + 10.0
    cx = float(np.clip(x, x0 + margin, x1 - margin))
    cy = float(np.clip(y, y0 + margin, y1 - margin))
    return (cx - h, cy - h, cx + h, cy + h)


def night_of(strike: dict) -> str:
    return "last night" if strike["hours_ago"] < 20.0 else "night before"


class StrikeChecker:
    """Flies the check team (squad code "C") and asks its picker for the next strike after each look."""

    def __init__(self, sim, cfg: dict):
        from ..supervisor.strikes import make_picker
        plan = sim.ctrl.plan
        codes = [sq.code for sq in plan.squads]
        if "C" not in codes:
            raise ValueError("strike checks need a check squad (code C)")
        if not sim.sc.strikes:
            raise ValueError("strike checks need the night's strike list")
        self.cfg = dict(cfg)
        self.sq = codes.index("C")
        self.k = len(plan.squads[self.sq].drones)
        self.strikes = {int(k["id"]): k for k in sim.sc.strikes}
        self.picker = make_picker(self.cfg)
        self.extent = tuple(sim.sc.world.geofence_lo[:2]) + tuple(sim.sc.world.geofence_hi[:2])
        self.patrolled = [sq.rect for sq in plan.squads if sq.code != "C"]
        self.checked: dict[int, dict] = {}      # strike id -> its check record
        self.refused: set[int] = set()          # strikes the validator ruled out for good (not offered again)
        self.current: dict | None = None
        self.pending: dict | None = None        # a pick waiting for its call's latency to pass
        self.finished = False
        self.next_t = 0.0
        self.wait_until = 0.0
        self.decisions: list[dict] = []

    # -- the loop, once a second --------------------------------------------------------------------------
    def tick(self, sim, obs) -> None:
        t = sim.t
        if t < self.next_t:
            return
        self.next_t = t + 1.0
        plan = sim.ctrl.plan
        if self.pending is not None and t >= self.pending["t_apply"]:
            pick, self.pending = self.pending, None
            self._apply(sim, obs, pick)
        cur = self.current
        part = plan.parts[self.sq]
        if cur is not None and cur.get("t_look") is None and self._arrived(plan, sim):
            cur["t_look"] = round(t, 2)
            cur["s0"] = float(part.s)
            sim.event("strike_check", None, state="looking", strike=cur["strike"])
        if self.finished or self.pending is not None or t < self.wait_until:
            return
        if sim._hold is not None or not self._flying(plan, sim):
            return                               # nothing is picked during a hold or with no team drone flying
        # A look ends when the team's circuit has gone once round (progress stops during a hold).
        looked = cur is not None and cur.get("t_look") is not None and part.s - cur["s0"] >= part.path.length
        if cur is None or "t_done" in cur or looked:
            if cur is not None and "t_done" not in cur:
                self._close(sim, cur)
            self._decide(sim)

    def _team(self, plan) -> list[int]:
        members = [int(d) for d in plan.parts[self.sq].members]
        return members + [d for d, tr in plan.transit.items() if tr["to"] == self.sq]

    def _flying(self, plan, sim) -> int:
        act = sim.active_mask()
        return sum(1 for d in self._team(plan) if act[d])

    def _arrived(self, plan, sim) -> bool:
        act = sim.active_mask()
        moving = [d for d, tr in plan.transit.items() if tr["to"] == self.sq and act[d]]
        joined = [d for d in plan.parts[self.sq].members if act[d]]
        return not moving and bool(joined)

    def _close(self, sim, cur: dict) -> None:
        cur["t_done"] = round(sim.t, 2)
        k = self.strikes[cur["strike"]]
        near = nearest_located(sim, k["x"], k["y"])
        cur["result"] = "fire located nearby" if near is not None and near <= NEAR_FIRE_M else "nothing located nearby"
        sim.event("strike_check", None, state="done", strike=cur["strike"], result=cur["result"])

    def _decide(self, sim) -> None:
        table, ctx = strike_table(sim, self)
        if not table:
            self.finished = True
            sim.event("strike_check", None, state="finished", reason="no strike left that the team can reach in time")
            return
        got = self.picker.pick(table, ctx)
        rec = {"t": round(sim.t, 2), "offered": [r["id"] for r in table], **{k: v for k, v in got.items() if k != "record"}}
        if got.get("record") is not None:
            r = got["record"]
            rec["call"] = {k: r.get(k) for k in ("call_id", "model", "latency_s", "prompt_tokens", "completion_tokens",
                                                 "cost_usd", "status")}
        self.decisions.append(rec)
        sid = got.get("strike")
        latency = float(got.get("latency_s", 0.0))
        if sid is None:
            if got.get("valid", True):
                self.finished = True
                sim.event("strike_check", None, state="finished", reason=got.get("why", "picker stopped"))
            else:
                # An unusable reply: the team keeps looking where it is and the picker is asked again a circuit
                # after the reply came.
                self.wait_until = sim.t + latency + float(sim.ctrl.plan.squads[self.sq].pass_s or 60.0)
            return
        self.pending = {"strike": int(sid), "why": got.get("why", ""), "t_apply": sim.t + latency,
                        "t_pick": round(sim.t, 2)}

    def _apply(self, sim, obs, pick: dict) -> None:
        plan = sim.ctrl.plan
        order = {"op": "check", "strike": pick["strike"]}
        bad = sim.validator.check_strike(order, set(self.strikes), set(self.checked) | self.refused,
                                         self._flying(plan, sim), sim.t, self.picker.source,
                                         airspace_hold=sim._hold is not None)
        if bad:
            # Only a refusal about the strike itself rules it out for good (a hold or a grounded team passes).
            if any(b.startswith(("no strike", "unknown strike order")) or b.endswith("already checked") for b in bad):
                self.refused.add(pick["strike"])
            sim.event("strike_check_rejected", None, by=self.picker.source, order=order, reasons=bad)
            return
        k = self.strikes[pick["strike"]]
        rect = check_box(k["x"], k["y"], self.extent, float(self.cfg.get("box_m", CHECK_BOX_M)), k=self.k,
                         spacing=plan.directive.spacing_m, swath=plan.swath)
        drones = plan.retarget(self.sq, rect, obs.p_est, sim.t)
        self.current = {"strike": pick["strike"], "t_pick": pick["t_pick"], "t_order": round(sim.t, 2),
                        "why": pick["why"], "box": [round(v, 1) for v in rect], "drones": drones}
        self.checked[pick["strike"]] = self.current
        sim.event("strike_check", None, state="ordered", by=self.picker.source, strike=pick["strike"],
                  box=self.current["box"], why=pick["why"], drones=drones)

    # -- scoring ------------------------------------------------------------------------------------------
    def evaluate(self, sim) -> dict:
        fires = sim.wildfire.fires
        truth = sim.wildfire.cfg.strike_fires
        rows = []
        for sid, c in self.checked.items():
            j = truth.get(sid)
            rows.append({**{key: c.get(key) for key in ("strike", "t_pick", "t_order", "t_look", "t_done", "result",
                                                          "why")},
                         "night": night_of(self.strikes[sid]), "fire": j,
                         "fire_shelter": fires[j].shelter if j is not None else None,
                         "fire_located_before": (j is not None and _located_by(sim, j, c.get("t_look")))})
        calls = [d["call"] for d in self.decisions if "call" in d]
        done = [r for r in rows if r["t_done"] is not None]
        return {
            "strike_picker": self.picker.name,
            "strike_team": self.k,
            "strike_checks": len(done),
            "strike_checks_cut_off": sum(1 for r in rows if r["t_done"] is None),
            "strike_checks_fire": sum(1 for r in done if r["fire"] is not None),
            "strike_checks_fire_unfound": sum(1 for r in done if r["fire"] is not None and not r["fire_located_before"]),
            "strike_checks_overhang": sum(1 for r in done if r["fire_shelter"] == "overhang"),
            "strike_check_table": rows,
            "strike_decisions": self.decisions,
            "strike_invalid": sum(1 for d in self.decisions if d.get("valid") is False),
            "strike_calls": len(calls),
            "strike_cost_usd": round(sum(float(c.get("cost_usd") or 0.0) for c in calls), 6),
            "strike_latency_s_median": round(float(np.median([c["latency_s"] or 0.0 for c in calls])), 2) if calls else None,
        }


def _located_by(sim, fire: int, t: float | None) -> bool:
    """Scoring only: whether ``fire`` was already credited to a fix by time t (its first fix's time)."""
    if t is None:
        return False
    made = sim.wildfire.located + sim.wildfire.merged
    return any(l["truth"] == fire and l["t"] <= t for l in made)


def nearest_located(sim, x: float, y: float) -> float | None:
    wf = sim.wildfire
    if not wf.located:
        return None
    return float(min(math.hypot(l["x"] - x, l["y"] - y) for l in wf.located))


def _unexplained(wf, t: float) -> list[tuple]:
    """Camera bearings from the last SMOKE_WINDOW_S that no located fire's plume explains (the station's own
    test, ``Wildfire._on_plume`` with each fire's estimated plume): (x, y, bearing, drone)."""
    from .smoke import plume_top
    cfg = wf.cfg.smoke
    sigma = math.radians(cfg.bearing_sigma_deg)
    wind = getattr(wf, "_wind_now", None)
    if wind is None:
        wind = np.zeros(2)
    out = []
    for s in reversed(wf.sightings):
        if t - s[0] > SMOKE_WINDOW_S:
            break
        if s[7] != "camera":
            continue
        _t, d, x, y, _z, b = s[:6]
        if any(wf._on_plume(loc, float(plume_top(max(loc["area_m2"], 1.0), cfg)), x, y, b, wind, sigma, 7)
               for loc in wf.located):
            continue
        out.append((x, y, b, d))
    return out


def strike_table(sim, checker: StrikeChecker) -> tuple[list[dict], dict]:
    """The strikes the team could check next, with what the swarm knows about each, and the patrol's state."""
    plan = sim.ctrl.plan
    wf = sim.wildfire
    t = sim.t
    act = sim.active_mask()
    team = [d for d in checker._team(plan) if act[d]]
    pos = sim.obs.p_est if sim.obs is not None else sim.p
    here = pos[team, :2].mean(axis=0) if team else np.array(plan.parts[checker.sq].centre()[:2])
    from ..control.squads import TRANSIT_SPEED_MPS
    look_s = float(plan.squads[checker.sq].pass_s or 120.0)
    left_s = float((sim.sc.patrol_s or sim.sc.duration_s) - t)
    smoke = _unexplained(wf, t)
    terrain = wf.terrain
    phones = np.array([[p.x, p.y] for p in wf.cfg.phones], dtype=float) if wf.cfg.phones else np.zeros((0, 2))
    cov = sim.coverage
    rows = []
    for sid, k in sorted(checker.strikes.items()):
        if sid in checker.checked or sid in checker.refused:
            continue
        x, y = float(k["x"]), float(k["y"])
        dist = float(math.hypot(x - here[0], y - here[1]))
        if dist / TRANSIT_SPEED_MPS + JOIN_MARGIN_S + look_s > left_s:
            continue                                     # cannot be reached and looked at before the patrol ends
        near = nearest_located(sim, x, y)
        lines, drones = 0, set()
        for sx, sy, b, d in smoke:
            rx, ry = x - sx, y - sy
            along = rx * math.cos(b) + ry * math.sin(b)
            if along <= 0.0:
                continue
            if abs(rx * math.sin(b) - ry * math.cos(b)) <= SMOKE_NEAR_M:
                lines += 1
                drones.add(d)
        seen = 0.0
        if cov is not None:
            mask = ((sim._cover_x[:, None] - x) ** 2 + (sim._cover_y[None, :] - y) ** 2) <= SEEN_RADIUS_M ** 2
            total = math.pi * SEEN_RADIUS_M ** 2 / (sim.sc.sensor.cell_m ** 2)
            seen = min(1.0, float(np.count_nonzero(cov & mask)) / total)
        gx, gy = terrain.gradient(x, y)
        rows.append({
            "id": sid, "night": night_of(k), "hours_ago": round(float(k["hours_ago"]), 1),
            "patrolled": any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in checker.patrolled),
            "dist_m": int(round(dist, -1)), "ground": GROUND.get(int(terrain.cover_at(x, y)), "?"),
            "slope_pct": int(round(100.0 * float(np.hypot(gx, gy)))),
            "seen_pct": int(round(100.0 * seen)),
            "fire_near_m": None if near is None else int(round(near, -1)),
            "smoke_lines": lines, "smoke_drones": len(drones),
            "people_m": int(round(float(np.min(np.hypot(phones[:, 0] - x, phones[:, 1] - y))), -1)) if len(phones) else None,
        })
    ctx = {"t_min": round(t / 60.0, 1), "left_min": round(left_s / 60.0, 1), "team_flying": len(team),
           "look_min": round(look_s / 60.0, 1), "transit_mps": TRANSIT_SPEED_MPS,
           "done": [{"id": c["strike"], "result": c.get("result", "being checked")} for c in checker.checked.values()],
           "located": len(wf.located)}
    return rows, ctx
