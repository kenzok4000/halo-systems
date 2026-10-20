#!/usr/bin/env python3
"""What a day of Halo patrol costs, against what agencies spend today and what an escaped fire costs.

    python tools/patrol_cost.py            # from the repository folder; writes results/patrol_cost/
    python tools/patrol_cost.py --charts   # also draws docs/figures/patrol-*-light.png and -dark.png (needs matplotlib)

Every Halo input is copied from the scout's build sheet (hardware/budget.json and hardware/COSTS.md, prices
checked 2026-10-14) or measured in the simulator (results/wildfire/REPORT.md for the scan rate,
results/wildfire_valley/runs.jsonl and REPORT.md for the valley flights, held-out seeds 4000-4019), or is an
assumption named in ASSUMPTIONS below with its reason. The valley flights' time, energy and fires found are
recomputed from runs.jsonl and checked against the copied values; their other figures are copied from
results/wildfire_valley/REPORT.md. Every real-world figure in COMPARISON carries its source and the words it
was read from; the wage, the lightning network's accuracy and the drone rules are named in BUILD and
ASSUMPTIONS. docs/PATROL_COST.md links each one. Nothing here is tuned to a result, and the
drone has not been built, so every cost is an estimate.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "patrol_cost"
FIGURES = ROOT / "docs" / "figures"

# ---------------------------------------------------------------------------------------------
# Inputs copied from the build sheet (hardware/budget.json and hardware/BOM.md; USD, checked 2026-10-14)
# ---------------------------------------------------------------------------------------------
BUILD = {
    "drone_usd": 1095.33,              # one scout, parts only, no labour
    "capital_per_drone_usd": 1038.35,  # the drone without its battery and props (those are wear lines)
    "battery_usd": 51.99,              # Tattu R-Line 2200 mAh 6S
    "prop_set_usd": 4.99,              # pack of 4
    "ground_station_usd": 932.30,      # radio, antennas, safety handset, 2 chargers, parallel boards, bags
    "spares_per_drone_usd": 2 * 51.99 + 2 * 4.99,   # two spare packs and two spare prop sets per drone
    "swarm16_total_usd": 20280.93,     # 16 drones + ground station + spares, as in BOM.md
    "flight_min_per_charge": 22.4,     # hover endurance with the 5% integration margin
    # cost per drone flight hour, hardware only (amortised drone, battery wear, props, motors, charging), without
    # the build sheet's ground-station share (USD 3.11 a swarm hour, 0.19 a drone hour): the ground station is
    # costed on its own line below. budget.json: base drone_hour_hw 5.57; the 100 h and 1,000 h rows are
    # (swarm_hour_hw - 3.11) / 16 = 12.49 and 3.15.
    "hw_per_drone_hour_usd": {"100 h life": 12.49, "300 h life": 5.57, "1000 h life": 3.15},
    "drone_life_h_base": "300 h life",
    "ground_station_life_squad_h": 300, # the build sheet amortises the ground station over 300 swarm flight hours
    "squad_size": 16,                   # one ground station and one crew per squad of 16, as the build sheet costs it
    "charger_watts_mains": 1000.0,      # two 4-channel chargers, AC 500 W each (BOM.md)
    "battery_wh_usable": 41.51,         # budget.json
    "packs_per_drone": 3,
    "crew_wage_usd_h": 28.50,          # O*NET 33-2011.00 Firefighters, BLS 2025 median hourly wage, wages only
}
BUILD["ground_station_per_squad_hour_usd"] = BUILD["ground_station_usd"] / BUILD["ground_station_life_squad_h"]

# ---------------------------------------------------------------------------------------------
# Measured in the simulator (no tuning; the rates are means over 20 seeds)
# ---------------------------------------------------------------------------------------------
MEASURED = {
    # results/wildfire/REPORT.md, wildfire_scout, 6 ha block, scanning only (launch and transit not counted)
    "scan_rate_ha_per_min": {1: 0.5, 4: 1.6, 16: 6.2},
    # results/wildfire_valley/runs.jsonl, held-out seeds 4000-4019, forecast rule, means over 20 flights
    # (the 500 x 300 m survey block on the floor of a 1.3 x 1.0 km valley; five fires per flight)
    "valley_flight": {
        1:  {"duration_s": 846.5, "energy_wh_total": 34.85,  "fires_located": 3.00, "found_pct": 60, "before_spread_pct": 40,
             "first_before_todays_pct": 40, "lead_over_today_min": 112, "drones_retired_low_battery": 1.0},
        4:  {"duration_s": 570.6, "energy_wh_total": 85.92,  "fires_located": 3.85, "found_pct": 77, "before_spread_pct": 55,
             "first_before_todays_pct": 55, "lead_over_today_min": 74, "drones_retired_low_battery": 0.0},
        16: {"duration_s": 250.9, "energy_wh_total": 154.24, "fires_located": 4.30, "found_pct": 86, "before_spread_pct": 59,
             "first_before_todays_pct": 60, "lead_over_today_min": 69, "drones_retired_low_battery": 0.0},
    },
    "valley_block_km2": 0.5 * 0.3,     # VALLEY_BLOCK = (0, 0, 500, 300) m
    "valley_km2": 1.3 * 1.0,           # VALLEY_EXTENT = (-400, -350, 900, 650) m, ridges included
    "fires_per_flight": 5,
    "smoke_haze_limit_m": 2500.0,      # sim/smoke.py SmokeConfig.visibility_m: the forward camera never sees smoke beyond it
}

# ---------------------------------------------------------------------------------------------
# Assumptions of this model (each named, with its reason; sensitivities below)
# ---------------------------------------------------------------------------------------------
ASSUMPTIONS = {
    "patrol_window_h": (8.0, "one daylight shift, e.g. 08:00 to 16:00; the valley flights start at 08:00"),
    "airtime_share": (0.5, "each drone is airborne half the window: 22-minute flights and battery swaps. Charging "
                           "sets the ceiling: the build sheet's two chargers give 1,000 W on mains, which with the "
                           "three charged packs each drone starts with keeps a 16-drone squad airborne at most about "
                           "60 to 70% of an 8-hour shift (see max_airtime_share), so 0.5 leaves room for weather holds "
                           "and checks; the 75% rows of the sensitivity would need a DC supply"),
    "scan_share": (0.5, "half of airborne time is scanning, the rest is launch, transit, turns and landing; "
                        "the build sheet's own conservative case (COSTS.md)"),
    "scan_rate_per_drone_ha_min": (6.2 / 16, "the 16-drone measured rate divided by 16 (0.39 ha/min); "
                                             "4 drones measured 0.40 per drone, a single drone 0.50"),
    "crew_people_per_16_drones": (2, "a remote pilot and an observer or mission lead per 16 drones, as the build "
                                     "sheet costs its swarm; this needs an FAA one-to-many waiver (Part 107.35) "
                                     "or the proposed Part 108"),
    "smoke_watch_radius_km": (2.5, "the simulator's haze limit: a loitering drone's forward camera notices a "
                                   "spreading fire's plume anywhere within it (the valley flights found the "
                                   "fire across the valley this way); real cameras on towers see much further, "
                                   "so this is conservative"),
    "season_days": (120, "a four-month fire season of daily patrol, for the season totals"),
    "strike_site_block_km2": (0.15, "the valley's 500 x 300 m block, swept in the measured valley flight time"),
    "strike_blocks_per_site": (2, "each strike is checked as two blocks side by side, 500 x 600 m centred on the "
                                  "reported strike. The US lightning network's median location error is about 200 m "
                                  "(Nag et al. 2014); with errors spread evenly in all directions (a circular normal, "
                                  "sigma 170 m), one 500 x 300 m block holds about 54% of strikes and two about 79% "
                                  "(see block_holds_share)"),
    "site_cycle_min_drive_and_fly": (15.0, "drive-and-fly: the crew drives the squad to each site; launch, the measured flight, "
                                           "landing and a battery check, at least 15 minutes a site including a short drive, "
                                           "or the flight plus 8 minutes when that is longer"),
    "site_spacing_km_fly_between": (1.0, "fly-between: sites 1 km apart along a storm track, flown at the scout's 6 m/s"),
    "usable_sortie_min": (20.0, "a sortie of 17 to 22 minutes between battery swaps (hardware/README.md); 20 used"),
}
A = {k: v[0] for k, v in ASSUMPTIONS.items()}


def block_holds_share(width_m: float, height_m: float, median_error_m: float = 200.0) -> float:
    """Share of strikes whose true point lies in a width x height area centred on the reported one, for a
    circular normal location error with the given median (median = sigma * sqrt(2 ln 2))."""
    sigma = median_error_m / math.sqrt(2.0 * math.log(2.0))
    axis = lambda half: math.erf(half / (sigma * math.sqrt(2.0)))  # noqa: E731
    return axis(width_m / 2.0) * axis(height_m / 2.0)


def max_airtime_share(n: int = 16, window_h: float = 8.0) -> float:
    """Most of the shift a squad can stay airborne on the build sheet's chargers at mains power: the packs it
    starts with plus what the chargers put back (charger losses not counted, so this is generous)."""
    hover_w = BUILD["battery_wh_usable"] / (BUILD["flight_min_per_charge"] / 60.0)
    stored = n * BUILD["packs_per_drone"] * BUILD["battery_wh_usable"]
    charged = BUILD["charger_watts_mains"] * math.ceil(n / BUILD["squad_size"]) * window_h
    return min(1.0, (stored + charged) / (n * hover_w * window_h))

SQUADS = [1, 4, 8, 16, 32, 64]
AREAS_KM2 = {
    "the valley survey block (500 x 300 m)": MEASURED["valley_block_km2"],
    "the whole valley (1.3 x 1.0 km)": MEASURED["valley_km2"],
    "a 10 km2 lightning-strike block": 10.0,
    "a 100 km2 district": 100.0,
}


def squad_day(n: int, life: str = BUILD["drone_life_h_base"], crew_people: float | None = None,
              airtime: float = A["airtime_share"], scan_share: float = A["scan_share"],
              window_h: float = A["patrol_window_h"]) -> dict:
    """Cost and coverage of one patrol day for a squad of n drones."""
    hw_rate = BUILD["hw_per_drone_hour_usd"][life]
    flight_h_per_drone = window_h * airtime
    drone_hours = n * flight_h_per_drone
    hardware = drone_hours * hw_rate
    stations = math.ceil(n / BUILD["squad_size"])                              # one per squad of 16
    ground = stations * window_h * BUILD["ground_station_per_squad_hour_usd"]  # each station runs all shift
    if crew_people is None:
        crew_people = A["crew_people_per_16_drones"] * math.ceil(n / 16)
    crew = crew_people * window_h * BUILD["crew_wage_usd_h"]
    rate = MEASURED["scan_rate_ha_per_min"][1] if n == 1 else A["scan_rate_per_drone_ha_min"]
    km2_swept = n * flight_h_per_drone * 60.0 * rate * scan_share / 100.0       # ha -> km2
    total = hardware + ground + crew
    capital = n * BUILD["drone_usd"] + stations * BUILD["ground_station_usd"] + n * BUILD["spares_per_drone_usd"]
    sorties = flight_h_per_drone * 60.0 / BUILD["flight_min_per_charge"]
    return {
        "drones": n, "crew_people": crew_people, "ground_stations": stations, "flight_h_per_drone": round(flight_h_per_drone, 2),
        "sorties_per_drone": round(sorties, 1), "drone_hours": round(drone_hours, 1),
        "hardware_usd": round(hardware, 2), "ground_station_usd": round(ground, 2), "crew_usd": round(crew, 2),
        "total_usd_per_day": round(total, 2), "km2_swept_per_day": round(km2_swept, 3),
        "usd_per_km2_swept": round(total / km2_swept, 2), "usd_per_km2_hardware_only": round((hardware + ground) / km2_swept, 2),
        "capital_usd": round(capital, 2),
    }


def valley_flight_cost(n: int, life: str = BUILD["drone_life_h_base"]) -> dict:
    """What one measured valley flight costs: the squad flies once and lands."""
    m = MEASURED["valley_flight"][n]
    h = m["duration_s"] / 3600.0
    hardware = n * h * BUILD["hw_per_drone_hour_usd"][life]
    ground = math.ceil(n / BUILD["squad_size"]) * h * BUILD["ground_station_per_squad_hour_usd"]
    crew = A["crew_people_per_16_drones"] * math.ceil(n / 16) * h * BUILD["crew_wage_usd_h"]
    total = hardware + ground + crew
    before = MEASURED["fires_per_flight"] * m["before_spread_pct"] / 100.0
    return {
        "drones": n, "flight_min": round(m["duration_s"] / 60.0, 1), "energy_wh": m["energy_wh_total"],
        "hardware_usd": round(hardware, 2), "crew_usd": round(crew, 2), "ground_station_usd": round(ground, 2),
        "total_usd": round(total, 2), "fires_found_of_5": m["fires_located"], "found_pct": m["found_pct"],
        "found_before_spread_pct": m["before_spread_pct"], "usd_per_fire_found": round(total / m["fires_located"], 2),
        "usd_per_fire_found_before_spread": round(total / before, 2),
        "first_before_todays_pct": m["first_before_todays_pct"], "lead_over_today_min": m["lead_over_today_min"],
        "usd_per_km2_of_valley": round(total / MEASURED["valley_km2"], 2),
    }


def strike_sites_per_day(n: int) -> dict:
    """How many lightning strikes a squad checks in a patrol day, each as strike_blocks_per_site measured
    valley-block sweeps (the measured flight time, once per block)."""
    blocks = A["strike_blocks_per_site"]
    site_min = MEASURED["valley_flight"][n]["duration_s"] / 60.0 if n in MEASURED["valley_flight"] else None
    if site_min is None:                                   # 8, 32, 64: 16-drone sweep time, squads of 16 in parallel
        squads = max(1, n // 16)
        site_min = MEASURED["valley_flight"][16]["duration_s"] / 60.0
    else:
        squads = 1
    site_min *= blocks
    window = A["patrol_window_h"]
    # drive-and-fly: one site per cycle; the cycle is the flight plus launch, landing, swap and a short drive
    cycle = max(A["site_cycle_min_drive_and_fly"], site_min + 8.0)
    drive_and_fly = squads * window * 60.0 / cycle
    # fly-between: sites along a storm track, as many per sortie as fit, then a swap
    hop_min = A["site_spacing_km_fly_between"] * 1000.0 / 6.0 / 60.0
    per_sortie = max(1, int((A["usable_sortie_min"] + hop_min) // (site_min + hop_min)))
    sorties = window * A["airtime_share"] * 60.0 / BUILD["flight_min_per_charge"]
    fly_between = squads * sorties * per_sortie
    d = squad_day(n)
    return {"drones": n, "squads_of_16": squads, "blocks_per_site": blocks, "minutes_per_site_flight": round(site_min, 1),
            "km2_swept_on_sites": round(drive_and_fly * blocks * A["strike_site_block_km2"], 2),
            "share_of_strikes_inside": round(block_holds_share(500.0, 300.0 * blocks), 3),
            "sites_per_day_drive_and_fly": round(drive_and_fly), "sites_per_day_fly_between": round(fly_between),
            "sites_per_sortie_fly_between": per_sortie,
            "usd_per_site_drive_and_fly": round(d["total_usd_per_day"] / drive_and_fly, 2),
            "usd_per_site_hardware_only": round((d["hardware_usd"] + d["ground_station_usd"]) / drive_and_fly, 2)}


def smoke_watch_day(drones_aloft: int = 1, crewed: bool = True) -> dict:
    """Keep `drones_aloft` drones loitering all shift as mobile lookouts (model extrapolation, not a measured result)."""
    window = A["patrol_window_h"]
    n = math.ceil(drones_aloft / A["airtime_share"])             # drones needed to keep that many up
    hw = drones_aloft * window * BUILD["hw_per_drone_hour_usd"][BUILD["drone_life_h_base"]]
    ground = math.ceil(n / BUILD["squad_size"]) * window * BUILD["ground_station_per_squad_hour_usd"]
    crew = (A["crew_people_per_16_drones"] * math.ceil(n / 16) * window * BUILD["crew_wage_usd_h"]) if crewed else 0.0
    area = drones_aloft * math.pi * (A["smoke_watch_radius_km"] ** 2)
    total = hw + ground + crew
    return {"drones_aloft": drones_aloft, "drones_in_squad": n, "crewed": crewed, "km2_watched": round(area, 1),
            "total_usd_per_day": round(total, 2), "usd_per_km2_day": round(total / area, 2)}


def build() -> dict:
    out = {"inputs": {"build_sheet": BUILD, "measured": MEASURED,
                      "assumptions": {k: {"value": v[0], "reason": v[1]} for k, v in ASSUMPTIONS.items()}}}
    out["squad_day"] = [squad_day(n) for n in SQUADS]
    out["valley_flight"] = [valley_flight_cost(n) for n in (1, 4, 16)]
    # areas: days to sweep once, cost to sweep once, and the squad that sweeps it every day
    areas = []
    for name, km2 in AREAS_KM2.items():
        row = {"area": name, "km2": km2, "by_squad": []}
        for n in SQUADS:
            d = squad_day(n)
            days = km2 / d["km2_swept_per_day"]
            row["by_squad"].append({"drones": n, "days_to_sweep_once": round(days, 2),
                                    "usd_to_sweep_once": round(days * d["total_usd_per_day"], 2),
                                    "usd_per_day_if_swept_daily": round(km2 * d["usd_per_km2_swept"], 2)})
        per_drone_km2 = squad_day(16)["km2_swept_per_day"] / 16
        row["drones_to_sweep_daily"] = math.ceil(km2 / per_drone_km2)
        areas.append(row)
    out["areas"] = areas
    out["strike_sites"] = [strike_sites_per_day(n) for n in (4, 16, 32, 64)]
    out["smoke_watch"] = [smoke_watch_day(1, True), smoke_watch_day(1, False), smoke_watch_day(4, True), smoke_watch_day(4, False)]
    # sensitivities on the 16-drone squad, USD per km2 swept
    sens = []
    base = squad_day(16)
    for life in BUILD["hw_per_drone_hour_usd"]:
        for crew_label, crew in (("2", None), ("none (automated docks)", 0)):
            for airtime in (0.5, 0.75):
                for scan in (0.5, 1.0):
                    d = squad_day(16, life=life, crew_people=crew, airtime=airtime, scan_share=scan)
                    sens.append({"drone_life": life, "crew": crew_label, "airtime_share": airtime, "scan_share": scan,
                                 "usd_per_day": d["total_usd_per_day"], "km2_per_day": d["km2_swept_per_day"],
                                 "usd_per_km2_swept": d["usd_per_km2_swept"]})
    out["sensitivity_16"] = sens
    lo = min(s["usd_per_km2_swept"] for s in sens)
    hi = max(s["usd_per_km2_swept"] for s in sens)
    out["sensitivity_16_range_usd_per_km2"] = [lo, hi]
    out["max_airtime_share_16_mains"] = round(max_airtime_share(16, A["patrol_window_h"]), 3)
    out["block_holds_share"] = {"one block, 500 x 300 m": round(block_holds_share(500.0, 300.0), 3),
                                "two blocks, 500 x 600 m": round(block_holds_share(500.0, 600.0), 3)}
    # season totals
    out["season"] = {"days": A["season_days"], "by_squad": [
        {"drones": n, "usd_per_season": round(squad_day(n)["total_usd_per_day"] * A["season_days"], 2),
         "km2_sweeps_per_season": round(squad_day(n)["km2_swept_per_day"] * A["season_days"], 1),
         "capital_usd": squad_day(n)["capital_usd"]} for n in SQUADS]}
    return out


def fmt_usd(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:,.2f}"


def tables(m: dict) -> str:
    L = []
    L.append("## A patrol day by squad size\n")
    L.append(f"Window {A['patrol_window_h']:.0f} h, each drone airborne {A['airtime_share']:.0%} of it "
             f"({squad_day(16)['flight_h_per_drone']:.0f} flight hours, about {squad_day(16)['sorties_per_drone']:.0f} battery "
             f"changes), scanning {A['scan_share']:.0%} of its airborne time at {A['scan_rate_per_drone_ha_min']:.2f} ha/min "
             f"per drone (the measured 16-drone rate; a single drone at its own measured 0.50); hardware at the build sheet's "
             f"base rate (300 h drone life); one ground station and a crew of {A['crew_people_per_16_drones']} per 16 drones, "
             f"the crew at USD {BUILD['crew_wage_usd_h']:.2f}/h. The km² columns are for a day spent sweeping open ground; "
             f"a day of strike checks is costed below.\n")
    L.append("| Drones | Crew | Drone flight hours | Hardware (USD) | Ground station (USD) | Crew (USD) | **USD per day** | km² swept per day | **USD per km² swept** | USD per km², hardware only | Capital to buy (USD) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for d in m["squad_day"]:
        L.append(f"| {d['drones']} | {d['crew_people']} | {d['drone_hours']:.0f} | {fmt_usd(d['hardware_usd'])} | {fmt_usd(d['ground_station_usd'])} | "
                 f"{fmt_usd(d['crew_usd'])} | **{fmt_usd(d['total_usd_per_day'])}** | {d['km2_swept_per_day']:.2f} | **{fmt_usd(d['usd_per_km2_swept'])}** | "
                 f"{fmt_usd(d['usd_per_km2_hardware_only'])} | {fmt_usd(d['capital_usd'])} |")
    L.append("\n## One measured valley flight (held-out seeds 4000-4019, means over 20 flights)\n")
    L.append("The squad takes off at 08:00, sweeps the 500 x 300 m block once with thermal cameras, watches the 1.3 km² valley for smoke, and lands. Five fires per flight.\n")
    L.append("| Drones | Flight (min) | Energy (Wh) | Hardware (USD) | Ground station (USD) | Crew (USD) | **Cost of the flight (USD)** | Fires found of 5 | Found before spreading | **USD per fire found** | USD per fire found before it spread | Reported before today's earliest method | Lead (min, median) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for v in m["valley_flight"]:
        L.append(f"| {v['drones']} | {v['flight_min']:.1f} | {v['energy_wh']:.0f} | {v['hardware_usd']:.2f} | {v['ground_station_usd']:.2f} | {v['crew_usd']:.2f} | **{v['total_usd']:.2f}** | "
                 f"{v['fires_found_of_5']:.2f} ({v['found_pct']}%) | {v['found_before_spread_pct']}% | **{v['usd_per_fire_found']:.2f}** | "
                 f"{v['usd_per_fire_found_before_spread']:.2f} | {v['first_before_todays_pct']} of 100 | {v['lead_over_today_min']} |")
    L.append("\n## Lightning strikes checked per patrol day\n")
    holds = m["block_holds_share"]
    L.append(f"Each strike is checked as {A['strike_blocks_per_site']} measured sweeps of a 500 x 300 m block, 500 x 600 m centred on the "
             f"reported strike: with the lightning network's 200 m median location error it holds about {holds['two blocks, 500 x 600 m']:.0%} "
             f"of strikes (one block, about {holds['one block, 500 x 300 m']:.0%}). Drive-and-fly: the crew drives between strikes, at least "
             f"{A['site_cycle_min_drive_and_fly']:.0f} minutes a strike. Fly-between: strikes {A['site_spacing_km_fly_between']:.0f} km apart "
             f"along a storm track, {A['usable_sortie_min']:.0f}-minute sorties between battery swaps. Squads above 16 fly as squads of 16 in parallel, "
             f"each with its own crew and ground station.\n")
    L.append("| Drones | Squads of 16 | Flight per strike (min) | Strikes per day, drive-and-fly | km² swept on them | Strikes per day, fly-between | **USD per strike checked** | USD per strike, hardware only |")
    L.append("|---|---|---|---|---|---|---|---|")
    for s_ in m["strike_sites"]:
        L.append(f"| {s_['drones']} | {s_['squads_of_16']} | {s_['minutes_per_site_flight']} | {s_['sites_per_day_drive_and_fly']} | {s_['km2_swept_on_sites']:.1f} | "
                 f"{s_['sites_per_day_fly_between']} ({s_['sites_per_sortie_fly_between']} a sortie) | **{s_['usd_per_site_drive_and_fly']:.2f}** | {s_['usd_per_site_hardware_only']:.2f} |")
    L.append("\n## Sweeping an area\n")
    L.append("Days for one full thermal sweep, what that sweep costs, and the daily cost of sweeping the whole area every day.\n")
    L.append("| Area | km² | Drones | Days to sweep once | USD to sweep once | USD per day to sweep daily |")
    L.append("|---|---|---|---|---|---|")
    for row in m["areas"]:
        for b in row["by_squad"]:
            if b["drones"] in (4, 16, 64):
                L.append(f"| {row['area']} | {row['km2']:g} | {b['drones']} | {b['days_to_sweep_once']:.2f} | {fmt_usd(b['usd_to_sweep_once'])} | {fmt_usd(b['usd_per_day_if_swept_daily'])} |")
        L.append(f"| {row['area']} | {row['km2']:g} | {row['drones_to_sweep_daily']} (to sweep it all every day) | 1 | | |")
    L.append("\n## Smoke watch (model extrapolation, not a measured result)\n")
    L.append(f"A loitering drone as a mobile lookout: the simulator's forward camera notices a spreading fire's smoke anywhere within its "
             f"{A['smoke_watch_radius_km']:.1f} km haze limit (about {math.pi * A['smoke_watch_radius_km']**2:.0f} km² per drone aloft). "
             f"Keeping one drone aloft all shift takes {smoke_watch_day(1)['drones_in_squad']} drones taking turns.\n")
    L.append("| Drones aloft all shift | Drones in the squad | Crew | km² watched | USD per day | **USD per km² per day** |")
    L.append("|---|---|---|---|---|---|")
    for s in m["smoke_watch"]:
        L.append(f"| {s['drones_aloft']} | {s['drones_in_squad']} | {'2' if s['crewed'] else 'none (automated docks)'} | {s['km2_watched']:.0f} | {fmt_usd(s['total_usd_per_day'])} | **{s['usd_per_km2_day']:.2f}** |")
    L.append("\n## Sensitivity (16 drones, USD per km² swept)\n")
    lo, hi = m["sensitivity_16_range_usd_per_km2"]
    L.append(f"Across drone life 100 to 1,000 h, a crew of 2 or none, airtime 50% or 75%, scanning 50% or 100% of airborne time: "
             f"**USD {lo:,.0f} to {hi:,.0f} per km² swept**. Base case USD {squad_day(16)['usd_per_km2_swept']:,.0f}. "
             f"The 75% rows would need a DC supply: on mains the build sheet's chargers keep the squad airborne at most "
             f"about {m['max_airtime_share_16_mains']:.0%} of the shift.\n")
    L.append("| Drone life | Crew | Airtime | Scanning share | USD per day | km² per day | USD per km² |")
    L.append("|---|---|---|---|---|---|---|")
    for s in m["sensitivity_16"]:
        if s["airtime_share"] == 0.5 or (s["scan_share"] == 1.0 and s["drone_life"] == "300 h life") or s["usd_per_km2_swept"] in (lo, hi):
            L.append(f"| {s['drone_life']} | {s['crew']} | {s['airtime_share']:.0%} | {s['scan_share']:.0%} | {fmt_usd(s['usd_per_day'])} | {s['km2_per_day']:.1f} | {fmt_usd(s['usd_per_km2_swept'])} |")
    L.append(f"\n## A {A['season_days']}-day season\n")
    L.append("| Drones | Capital to buy (USD) | Running cost per season (USD) | km²-sweeps per season |")
    L.append("|---|---|---|---|")
    for s in m["season"]["by_squad"]:
        L.append(f"| {s['drones']} | {fmt_usd(s['capital_usd'])} | {fmt_usd(s['usd_per_season'])} | {s['km2_sweeps_per_season']:,.0f} |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------
# Real-world figures for the comparison (each with its source and the words it was read from)
# ---------------------------------------------------------------------------------------------
REVIEW = ("Spring 2019 Wildfire Review, MNP LLP for the Government of Alberta, November 2020",
          "https://open.alberta.ca/dataset/5fa709d9-d70a-4176-a1b8-e097abafa9fe/resource/"
          "f42b9020-69e0-4d2c-984b-68ad33761f10/download/af-spring-2019-wildfire-review-final-report.pdf")
COMPARISON = {
    "cad_per_usd_2025": (1.3978, "Bank of Canada, annual average exchange rates, 2025",
                         "https://www.bankofcanada.ca/rates/exchange/annual-average-exchange-rates/",
                         "US dollar ... 1.3978"),
    "alberta_aerial_patrol_cad_per_year": (9_638_030, REVIEW[0] + ", table 12 (fiscal years 2015-2019, average)", REVIEW[1],
                                           "Aerial Patrol 12.2% 56.8% $168,529 $9,469,501 $0 $0 $9,638,030"),
    "alberta_aerial_patrol_share_of_detections": (0.122, REVIEW[0] + ", table 12", REVIEW[1], "Aerial Patrol 12.2%"),
    "alberta_lookout_cad_per_year": (7_147_915, REVIEW[0] + ", table 12 (fiscal years 2015-2019, average)", REVIEW[1],
                                     "Lookout 30.2% 42.2% $4,341,600 $2,711,277 $6,499 $88,539 $7,147,915"),
    "alberta_protection_area_km2": (390_000, REVIEW[0], REVIEW[1],
                                    "The Forest Protection Area (FPA) ... covers approximately 39 million hectares"),
    "alberta_fires_per_season": (1_107, "Government of Alberta, Forestry and Parks, Annual Report 2023-2024 "
                                        "(a five-year average given beside the 2023 season; the report does not name "
                                        "the years, most likely 2018-2022)",
                                 "https://open.alberta.ca/dataset/2dedbb29-e005-4802-bf6e-03bf77536add/resource/"
                                 "b86465e8-a8e3-45de-91d8-3fec19c700db/download/fp-annual-report-2023-2024.pdf",
                                 "The five-year average during the same period is 1,107 wildfires"),
    "alberta_fire_season_days": (245, "Forest and Prairie Protection Act (Alberta), s. 17(1)",
                                 "https://kings-printer.alberta.ca/documents/Acts/F19.pdf",
                                 "The period from March 1 to October 31 in each year is the fire season"),
    "alberta_contained_on_time_cad": (20_422, REVIEW[0] + ", table 16 (2011-2019, head fire intensity 3-4, "
                                              "actioned on time, held)", REVIEW[1],
                                      "All Fires On Time $311,202 $15,271 $3,097,996 $20,422 $2,671,818 $29,733"),
    "alberta_escaped_cad": (3_097_996, REVIEW[0] + ", table 16 (2011-2019, head fire intensity 3-4, "
                                       "actioned on time, not held)", REVIEW[1],
                            "All Fires On Time $311,202 $15,271 $3,097,996 $20,422 $2,671,818 $29,733"),
    "alberta_escaped_late_cad": (1_175_276, REVIEW[0] + ", table 16 (2011-2019, head fire intensity 3-4, "
                                            "not actioned on time, not held)", REVIEW[1],
                                 "Not On Time $613,391 $92,487 $1,175,276 $162,193 $2,879,201 $172,813"),
    "alberta_escape_difference_cad": ((1_000_000, 3_000_000), REVIEW[0] + ", p. 114", REVIEW[1],
                                      "Overall, the difference between containment success and failure is between "
                                      "$1 million and $3 million per wildfire at moderate to high hazard levels."),
    "us_federal_suppression_usd_2015_2023": ([2_130_543_000, 1_975_545_000, 2_918_165_000, 3_143_256_000,
                                              1_590_000_000, 2_274_000_000, 4_389_000_000, 3_549_000_000,
                                              3_166_300_000],
                                             "National Interagency Fire Center, federal firefighting costs "
                                             "(suppression only), 2015-2023",
                                             "https://www.nifc.gov/fire-information/statistics/suppression-costs",
                                             "Federal Firefighting Costs (Suppression Only)"),
    "us_fires_2015_2025": ([68_151, 67_743, 71_499, 58_083, 50_477, 58_950, 58_985, 68_988, 56_580, 64_897, 77_850],
                           "National Interagency Fire Center, total wildland fires, 2015-2025",
                           "https://www.nifc.gov/fire-information/statistics/wildfires",
                           "Total Wildland Fires and Acres (1983-2025)"),
    "us_escape_share_and_spend": (((0.01, 0.02), 0.30), "USDA Forest Service, testimony of Victoria C. Christiansen, "
                                                        "3 August 2017",
                                  "https://www.fs.usda.gov/sites/default/files/fs_media/fs_document/"
                                  "usda-testimony-senr-fire-christiansen-08-03-17.pdf",
                                  "These large conflagrations account for 1 to 2 percent of total fires but result "
                                  "in 30 percent or more of fire expenditures."),
    "suppression_share_of_total_cost": (0.09, "Headwaters Economics, The Full Community Costs of Wildfire, May 2018 "
                                              "(five US case-study fires)",
                                        "https://headwaterseconomics.org/wp-content/uploads/full-wildfire-costs-report.pdf",
                                        "suppression costs comprise only nine percent of total wildfire costs"),
}
C = {k: v[0] for k, v in COMPARISON.items()}
HIT_RATES = (20, 100)   # illustrative: one strike site in 20, or in 100, holds a fire; the simulator does not know this share


def check_measured() -> None:
    """The valley flight means copied above must match the committed rows."""
    rows = [json.loads(line) for line in (ROOT / "results" / "wildfire_valley" / "runs.jsonl").open()]
    for n, want in MEASURED["valley_flight"].items():
        got = [r for r in rows if r["n"] == n and r["alert_rule"] == "forecast"]
        assert len(got) == 20, (n, len(got))
        for key, field in (("duration_s", "duration_s"), ("energy_wh_total", "energy_wh"), ("fires_located", "fires_located")):
            mean = sum(r[field] for r in got) / len(got)
            assert abs(mean - want[key]) < 0.05, (n, key, mean, want[key])


def compare(m: dict) -> dict:
    """What a Halo squad costs against Alberta's aerial patrols and against one escaped fire."""
    usd = lambda cad: cad / C["cad_per_usd_2025"]  # noqa: E731
    day16 = squad_day(16)["total_usd_per_day"]
    season16 = day16 * A["season_days"]
    sites16 = next(s for s in m["strike_sites"] if s["drones"] == 16)
    patrol_usd = usd(C["alberta_aerial_patrol_cad_per_year"])
    found = C["alberta_fires_per_season"] * C["alberta_aerial_patrol_share_of_detections"]
    per_day = patrol_usd / C["alberta_fire_season_days"]
    fed = sum(C["us_federal_suppression_usd_2015_2023"]) / len(C["us_federal_suppression_usd_2015_2023"])
    years = len(C["us_federal_suppression_usd_2015_2023"])               # 2015-2023, the same years for both means
    fires = sum(C["us_fires_2015_2025"][:years]) / years
    (lo_share, hi_share), spend_share = C["us_escape_share_and_spend"]
    us_escape = (fed * spend_share / (fires * hi_share), fed * spend_share / (fires * lo_share))
    escapes = [
        ("Alberta, the review's low end (CAD 1 million more than one contained on time)", usd(C["alberta_escape_difference_cad"][0])),
        ("Alberta, attacked late and escaped, against attacked on time and held (table 16)",
         usd(C["alberta_escaped_late_cad"] - C["alberta_contained_on_time_cad"])),
        ("Alberta, attacked on time and still escaped, against attacked on time and held (table 16)",
         usd(C["alberta_escaped_cad"] - C["alberta_contained_on_time_cad"])),
        ("US federal suppression per escaped fire, derived, low", us_escape[0]),
        ("US federal suppression per escaped fire, derived, high", us_escape[1]),
    ]
    # a fire at a strike is found if it lies inside the swept area and the sweep finds it (86% in the valley)
    found16 = MEASURED["valley_flight"][16]["found_pct"] / 100.0 * sites16["share_of_strikes_inside"]
    per_km2_day = lambda cad: usd(cad) / C["alberta_fire_season_days"] / C["alberta_protection_area_km2"]  # noqa: E731
    return {
        "squad16_usd_per_fire_found_if_one_site_in": {str(k): round(sites16["usd_per_site_drive_and_fly"] * k / found16)
                                                      for k in HIT_RATES},
        "alberta_lookout_usd_per_km2_day": round(per_km2_day(C["alberta_lookout_cad_per_year"]), 3),
        "alberta_patrol_usd_per_km2_day": round(per_km2_day(C["alberta_aerial_patrol_cad_per_year"]), 3),
        "squad16_usd_per_km2_swept": squad_day(16)["usd_per_km2_swept"],
        "alberta_escaped_all_costs_usd_if_suppression_is_9pct": round(usd(C["alberta_escaped_cad"]) / C["suppression_share_of_total_cost"]),
        "squad16_usd_per_day": round(day16, 2), "squad16_usd_per_season": round(season16, 2),
        "squad16_share_of_strikes_inside": sites16["share_of_strikes_inside"],
        "squad16_sites_per_day": sites16["sites_per_day_drive_and_fly"], "squad16_usd_per_site": sites16["usd_per_site_drive_and_fly"],
        "alberta_patrol_usd_per_year": round(patrol_usd), "alberta_fires_found_by_patrol_per_year": round(found),
        "alberta_patrol_usd_per_fire_found": round(patrol_usd / found), "alberta_patrol_usd_per_season_day": round(per_day),
        "squad16_days_per_alberta_patrol_day": round(per_day / day16, 1),
        "sites_per_day_for_alberta_patrol_money": round(per_day / day16 * sites16["sites_per_day_drive_and_fly"]),
        "alberta_contained_on_time_usd": round(usd(C["alberta_contained_on_time_cad"])),
        "alberta_escaped_usd": round(usd(C["alberta_escaped_cad"])),
        "alberta_escaped_late_usd": round(usd(C["alberta_escaped_late_cad"])),
        "us_federal_suppression_mean_usd": round(fed), "us_fires_mean": round(fires),
        "us_federal_suppression_per_escape_usd": [round(x) for x in us_escape],
        "breakeven": [{"against": name, "usd_per_escape": round(v),
                       "escapes_avoided_per_season": round(season16 / v, 3),
                       "seasons_per_escape_avoided": round(v / season16, 1),
                       "pays_for_itself_if_one_escape_avoided_every_n_seasons": math.floor(v / season16)} for name, v in escapes],
    }


def compare_tables(c: dict) -> str:
    sites16_inside = c["squad16_share_of_strikes_inside"]
    L = ["## Against Alberta's aerial patrols and one escaped fire\n",
         f"USD at the Bank of Canada's 2025 average, {C['cad_per_usd_2025']} CAD per USD. Sources in `COMPARISON` above and in docs/PATROL_COST.md.\n",
         "| What | Value |", "|---|---|",
         f"| 16-drone squad, crew of two: one patrol day | USD {c['squad16_usd_per_day']:,.0f} |",
         f"| Lightning strikes it checks in that day / USD per strike | {c['squad16_sites_per_day']} / USD {c['squad16_usd_per_site']:,.2f} |",
         f"| 16-drone squad: a {A['season_days']}-day season, crewed | USD {c['squad16_usd_per_season']:,.0f} |",
         f"| Alberta's aerial patrols, a year (2015-2019 average) | USD {c['alberta_patrol_usd_per_year']:,} |",
         f"| Fires they find a year (12.2% of {C['alberta_fires_per_season']:,}) / USD per fire found | {c['alberta_fires_found_by_patrol_per_year']} / USD {c['alberta_patrol_usd_per_fire_found']:,} |",
         f"| Their spend per day of the {C['alberta_fire_season_days']}-day season | USD {c['alberta_patrol_usd_per_season_day']:,} |",
         f"| Crewed 16-drone squad-days that buys / strikes checked | {c['squad16_days_per_alberta_patrol_day']} / {c['sites_per_day_for_alberta_patrol_money']:,} |",
         f"| Alberta, suppression at moderate hazard: attacked on time and held / on time and still escaped / late and escaped | USD {c['alberta_contained_on_time_usd']:,} / USD {c['alberta_escaped_usd']:,} / USD {c['alberta_escaped_late_usd']:,} |",
         f"| US federal suppression per escaped fire, derived | USD {c['us_federal_suppression_per_escape_usd'][0]:,} to {c['us_federal_suppression_per_escape_usd'][1]:,} |",
         f"| A Halo fire found, if one strike in 20 / in 100 left a fire (illustrative; the fire inside the swept area {sites16_inside:.0%} of the time, found 86% of the time when inside) | USD {c['squad16_usd_per_fire_found_if_one_site_in']['20']:,} / USD {c['squad16_usd_per_fire_found_if_one_site_in']['100']:,} |",
         f"| Per km² per day: Alberta's lookouts / aerial patrols over the protection area; a 16-drone squad per km² it sweeps | USD {c['alberta_lookout_usd_per_km2_day']} / USD {c['alberta_patrol_usd_per_km2_day']}; USD {c['squad16_usd_per_km2_swept']:,.0f} |",
         f"| Alberta escaped fire, all costs, if suppression is 9% of the total | USD {c['alberta_escaped_all_costs_usd_if_suppression_is_9pct']:,} |",
         "\n## Break-even, as escapes avoided\n",
         "| Against the suppression cost of one escaped fire | USD per escape | Escapes avoided per season to break even | One escape avoided pays for | So the squad pays for itself if it avoids one escape every |",
         "|---|---|---|---|---|"]
    for b in c["breakeven"]:
        L.append(f"| {b['against']} | {b['usd_per_escape']:,} | {b['escapes_avoided_per_season']} | {b['seasons_per_escape_avoided']:.1f} seasons | "
                 f"{b['pays_for_itself_if_one_escape_avoided_every_n_seasons']} seasons |")
    return "\n".join(L) + "\n"


def charts(m: dict, c: dict) -> None:
    """Two figures for docs/PATROL_COST.md, in the README figures' light and dark themes."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    from make_figures import THEMES, save, style, title  # the README figures' chrome
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    def money(x: float) -> str:
        for div, unit in ((1e6, "M"), (1e3, "K")):
            if x >= div:
                v = x / div
                return f"${v:,.3g}{unit}" if v < 100 else f"${v:,.0f}{unit}"
        return f"${x:,.0f}" if x >= 100 else f"${x:,.2f}"

    def tick(v: float, _=None) -> str:
        return money(v) if v >= 100 or not float(v).is_integer() else f"${v:,.0f}"

    rows = [r for r in m["squad_day"] if r["drones"] >= 4]
    lo, hi = c["us_federal_suppression_per_escape_usd"]
    bars = [
        ("One 16-drone valley flight (4.2 min)", m["valley_flight"][2]["total_usd"], "s1"),
        (f"One 16-drone patrol day (about {c['squad16_sites_per_day']} strikes)", c["squad16_usd_per_day"], "s1"),
        ("Buying 16 drones, a ground station and spares", BUILD["swarm16_total_usd"], "s1"),
        (f"A {A['season_days']}-day season of patrol, crewed", c["squad16_usd_per_season"], "s1"),
        ("Alberta's aerial patrols, one season day", c["alberta_patrol_usd_per_season_day"], "context"),
        ("Alberta's aerial patrols, per fire they find", c["alberta_patrol_usd_per_fire_found"], "context"),
        ("Alberta: suppressing a fire attacked on time and held", c["alberta_contained_on_time_usd"], "context"),
        ("US federal suppression per escaped fire (derived)", lo, "s2"),
        ("Alberta: a fire attacked on time that still escaped", c["alberta_escaped_usd"], "s2"),
    ]
    for theme, t in THEMES.items():
        fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), facecolor=t["surface"], gridspec_kw={"wspace": 0.35})
        for ax, (head, key, fmt) in zip(axes, (("USD per patrol day", "total_usd_per_day", money),
                                               ("USD per km\u00b2 swept", "usd_per_km2_swept", money))):
            style(ax, t, xgrid=False)
            x = list(range(len(rows)))
            vals = [r[key] for r in rows]
            ax.bar(x, vals, width=0.5, color=t["s1"], zorder=3)
            ax.grid(axis="y", color=t["grid"], linewidth=0.8)
            ax.set_axisbelow(True)
            ax.set_xticks(x, [str(r["drones"]) for r in rows])
            ax.set_xlabel("drones in the squad", color=t["ink2"], fontsize=9)
            ax.yaxis.set_major_formatter(FuncFormatter(tick))
            ax.set_ylim(0, max(vals) * 1.2)
            for xi, v in zip(x, vals):
                ax.text(xi, v, money(v), ha="center", va="bottom", color=t["ink2"], fontsize=8, parse_math=False)
            title(ax, t, head)
        fig.suptitle("A patrol day by squad size: 8 h shift, drones airborne half of it, a crew of 2 per 16 drones (estimate)",
                     color=t["ink2"], fontsize=9, x=0.01, ha="left", y=0.98)
        fig.subplots_adjust(top=0.8, bottom=0.17, left=0.08, right=0.98)
        save(fig, "patrol-day", theme)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 0.36 * len(bars) + 1.2), facecolor=t["surface"])
        fig.subplots_adjust(left=0.37, right=0.97, top=0.88, bottom=0.14)
        style(ax, t)
        y = list(range(len(bars)))[::-1]
        ax.barh(y, [b[1] for b in bars], height=0.55, color=[t[b[2]] for b in bars], zorder=3)
        ax.set_yticks(y, [b[0] for b in bars], color=t["ink"], fontsize=9)
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(LogLocator(base=10, numticks=10))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.xaxis.set_major_formatter(FuncFormatter(tick))
        ax.set_xlim(5, 2e7)
        for yi, b in zip(y, bars):
            label = money(b[1]) + (f" to {money(hi)}" if "(derived)" in b[0] else "")
            ax.text(b[1] * 1.12, yi, label, va="center", color=t["ink2"], fontsize=8, parse_math=False)
        ax.set_xlabel("USD (log scale)", color=t["ink2"], fontsize=9)
        title(ax, t, "A Halo squad (blue), what Alberta spends today (grey), one escaped fire (orange)")
        save(fig, "patrol-vs-escape", theme)
        plt.close(fig)


def main() -> None:
    check_measured()
    m = build()
    c = compare(m)
    m["comparison"] = c
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "model.json").write_text(json.dumps(m, indent=1) + "\n")
    (OUT / "TABLES.md").write_text("# Patrol cost: tables\n\nGenerated by `tools/patrol_cost.py`; "
                                   "docs/PATROL_COST.md explains the model and its sources. Every cost is an estimate.\n\n"
                                   + tables(m) + "\n" + compare_tables(c))
    print(f"wrote results/patrol_cost/; 16-drone day USD {c['squad16_usd_per_day']:,.0f}, "
          f"{c['squad16_sites_per_day']} strikes, USD {c['squad16_usd_per_site']:.2f} a strike; "
          f"season USD {c['squad16_usd_per_season']:,.0f}; Alberta patrols USD {c['alberta_patrol_usd_per_fire_found']:,} per fire found")
    if "--charts" in sys.argv:
        charts(m, c)
        print("wrote docs/figures/patrol-day-*.png and patrol-vs-escape-*.png")


if __name__ == "__main__":
    main()
