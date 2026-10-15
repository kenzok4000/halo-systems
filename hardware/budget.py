"""Mass, power, endurance and cost budget for the Halo scout.

Reads bom.csv (one row per part, prices and masses with sources) and
exports/mass_properties.json (written by cad/halo_scout.py), then writes:

    BOM.md        the parts table and the totals against the simulator
    COSTS.md      cost per flight hour and per hectare, with the crewed comparison
    budget.json   every computed number, for anything else that wants them

Run from anywhere with any Python that has numpy (the simulator's only
dependency). From the repository root:

    python3 hardware/budget.py

The simulator's DroneBody is imported read-only. Its parameters are never
changed: the build's figures go into a copy made with dataclasses.replace, so
the endurance below uses exactly the formula in src/halo_systems/sim/body.py.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True          # leave nothing behind under src/
sys.path.insert(0, str(HERE.parent / "src"))
from halo_systems.sim.body import DroneBody  # noqa: E402  (read-only use)

# ----------------------------------------------------------------------------
# Assumptions. Each one is named here and repeated in the generated files.
# ----------------------------------------------------------------------------
CHECKED = "2026-10-14"
SWARM_SIZE = 16
SCOUT_SPEED_MPS = 6.0            # src/halo_systems/eval/scenarios.py, SCOUT_SPEED_MPS

BATTERY_AH = 2.2                 # Tattu R-Line 2200 mAh 6S
BATTERY_V_NOMINAL = 22.2         # 6 cells x 3.7 V
USABLE_FRACTION = 0.85           # land at about 3.5 V per cell under load

FC_REGULATOR_EFF = 0.85          # Kakute H7's own 5 V regulator; no maker figure, assumed
BEC_EFF_TYPICAL = 0.92           # Matek BEC12S-Pro, listed 88% to 96%
BEC_EFF_PEAK = 0.88              # low end of the listed range, used for the peak case
INTEGRATION_MARGIN = 0.05        # wires, glue, labels and the parts nobody weighs
MOTOR_ESC_EFF = 0.80             # sensitivity only: motor times ESC efficiency in hover

DRONE_LIFE_H = 300               # flight hours before the drone is written off
DRONE_LIFE_SENSITIVITY_H = (100, 1000)
GROUND_LIFE_SWARM_H = 300        # swarm flight hours for the ground station
BATTERY_CYCLES_CITED = 300       # low end of 300 to 500, batteryskills.com
BATTERY_CYCLES_LOW = 150         # half of that, for deep discharge at high current
PROP_LIFE_H = 10                 # one set of four props per 10 flight hours
MOTOR_SET_LIFE_H = 150           # one set of four motors per 150 flight hours (bearings)
CHARGER_EFF = 0.90
ELECTRICITY_USD_KWH = 0.30       # deliberately high; the line is tiny either way
CREW = 2                         # remote pilot plus observer or mission lead
CREW_WAGE_USD_H = 28.50          # O*NET 33-2011.00 Firefighters, BLS 2025 median wage
SCAN_FRACTIONS = (1.0, 0.5)      # share of airborne time spent scanning

COVERAGE_HA_MIN = {1: 0.5, 16: 6.2}   # README.md wildfire table, 20 seeds, 6 ha block

SOURCES = {
    "batteryskills": "https://batteryskills.com/how-long-do-lipo-batteries-last/",
    "onet_ff": "https://www.onetonline.org/link/summary/33-2011.00",
    "usfs2017": "https://www.fs.usda.gov/sites/default/files/2019-07/cy2017_usfs_aviation_report_final_20190702.pdf",
    "mt2022": "https://archive.legmt.gov/content/Publications/fiscal/2023-Interim/IBC-C/Aviation.pdf",
    "nirops2026": "https://gacc.nifc.gov/oscc/docs/2026/2026_IAA-and-NIROPS_FAQs.pdf",
}

# Crewed aircraft figures, copied from the cited documents (checked 2026-10-14).
CREWED = [
    # name, role, total USD, hours, flight-only USD or None, per-hour USD or None, basis, source key
    ("USFS contract Type 3 (light) helicopters, CY2017", "light helicopters on fire and resource missions",
     49.2e6, 14572, 22.2e6, None, "Table 18: availability plus flight, all contracts", "usfs2017"),
    ("USFS contract fixed-wing aircraft, FY2017", "air attack 52% of hours, lead plane 13%, detection 8%, smokejumpers 5% (Table 9)",
     67.7e6, 27078, 46.3e6, None, "Table 10: availability plus flight, all contracts", "usfs2017"),
    ("Montana DNRC Cessna 182/185, 2021-22", "fire patrol and detection, transport",
     None, None, None, 175, "agency operating cost per hour", "mt2022"),
    ("Montana DNRC Bell 206A, 2021-22", "light helicopter",
     None, None, None, 515, "agency operating cost per hour", "mt2022"),
    ("Montana DNRC Bell UH-1H, 2021-22", "initial attack helicopter",
     None, None, None, 1650, "agency operating cost per hour", "mt2022"),
]


def read_bom():
    with open(HERE / "bom.csv", newline="") as f:
        return list(csv.DictReader(f))


def fill_printed(rows, props):
    """Printed rows carry 'cad' for quantity and mass; fill them from the CAD."""
    grams = {"petg": props["printed_mass_g"]["PETG"], "tpu": props["printed_mass_g"]["TPU"]}
    for r in rows:
        if r["qty"] == "cad":
            g = grams[r["id"]]
            r["qty"] = f"{g / 1000:.4f}"
            r["unit_mass_g"] = "1000"                  # grams per kg of filament
            r["qty_label"] = f"{g:.1f} g"
            r["mass_status"] = "CAD"
    return rows


def money(x):
    return f"{x:,.2f}"


def totals(rows, props):
    drone = [r for r in rows if r["section"] == "drone"]
    ground = [r for r in rows if r["section"] == "ground"]
    spares = [r for r in rows if r["section"] == "spares"]
    line = lambda r: float(r["qty"]) * float(r["unit_usd"])
    mass = lambda r: float(r["qty"]) * float(r["unit_mass_g"])

    drone_usd = sum(line(r) for r in drone)
    ground_usd = sum(line(r) for r in ground)
    spares_usd = sum(line(r) for r in spares)
    est_usd = sum(line(r) for r in drone if r["price_status"] != "checked")
    mass_g = sum(mass(r) for r in drone)

    rails = {"fc5v": [0.0, 0.0], "bec5v": [0.0, 0.0]}
    for r in drone:
        if r["rail"] in rails:
            rails[r["rail"]][0] += float(r["qty"]) * float(r["power_w"])
            rails[r["rail"]][1] += float(r["qty"]) * float(r["power_max_w"])
    av_typ = rails["fc5v"][0] / FC_REGULATOR_EFF + rails["bec5v"][0] / BEC_EFF_TYPICAL
    av_peak = rails["fc5v"][1] / FC_REGULATOR_EFF + rails["bec5v"][1] / BEC_EFF_PEAK

    nominal_wh = BATTERY_AH * BATTERY_V_NOMINAL
    usable_wh = USABLE_FRACTION * nominal_wh

    sim = DroneBody()
    m_build = mass_g / 1000
    m_margin = m_build * (1 + INTEGRATION_MARGIN)

    def case(label, mass_kg, avionics_w, battery_wh, fom=sim.figure_of_merit):
        b = dataclasses.replace(sim, mass_kg=mass_kg, avionics_power_w=avionics_w,
                                battery_wh=battery_wh, figure_of_merit=fom)
        rotor = float(b.rotor_power_w(b.mass_kg * 9.81))
        cruise = float(b.level_power_w(SCOUT_SPEED_MPS))
        return {"case": label, "mass_kg": round(mass_kg, 4), "avionics_w": round(avionics_w, 2),
                "battery_wh": round(battery_wh, 2), "figure_of_merit": fom,
                "rotor_w": round(rotor, 1), "hover_w": round(b.hover_power_w, 1),
                "hover_min": round(b.hover_endurance_min, 1),
                "cruise_6mps_w": round(cruise, 1),
                "cruise_6mps_min": round(60 * battery_wh / cruise, 1)}

    fom_elec = round(sim.figure_of_merit * MOTOR_ESC_EFF, 3)
    cases = [
        case("Simulator defaults (body.py)", sim.mass_kg, sim.avionics_power_w, sim.battery_wh),
        case("Build as modelled in CAD", m_build, av_typ, usable_wh),
        case(f"Build plus {INTEGRATION_MARGIN:.0%} integration margin", m_margin, av_typ, usable_wh),
        case("Build plus margin, avionics at peak", m_margin, av_peak, usable_wh),
        case(f"Build plus margin, FoM {fom_elec} (motor and ESC losses)", m_margin, av_typ,
             usable_wh, fom_elec),
    ]

    motor_thrust_g = 2640  # retailer figure, see the motor row's note
    twr = 4 * motor_thrust_g / (m_margin * 1000)

    return {
        "checked": CHECKED,
        "drone_usd": round(drone_usd, 2),
        "drone_usd_estimated_part": round(est_usd, 2),
        "ground_usd": round(ground_usd, 2),
        "spares_usd": round(spares_usd, 2),
        "swarm_drones_usd": round(SWARM_SIZE * drone_usd, 2),
        "swarm_total_usd": round(SWARM_SIZE * drone_usd + ground_usd + spares_usd, 2),
        "mass_g_bom": round(mass_g, 1),
        "mass_g_cad": round(props["mass_kg"] * 1000, 1),
        "mass_g_with_margin": round(m_margin * 1000, 1),
        "rails_w": {k: [round(v[0], 3), round(v[1], 3)] for k, v in rails.items()},
        "avionics_w_typical": round(av_typ, 2),
        "avionics_w_peak": round(av_peak, 2),
        "battery_wh_nominal": round(nominal_wh, 2),
        "battery_wh_usable": round(usable_wh, 2),
        "endurance": cases,
        "thrust_to_weight": round(twr, 1),
        "motor_thrust_g": motor_thrust_g,
    }


def costs(rows, t):
    by_id = {r["id"]: r for r in rows}
    usd = lambda i: float(by_id[i]["qty"]) * float(by_id[i]["unit_usd"])
    flight_min = next(c for c in t["endurance"] if "margin" in c["case"] and "peak" not in c["case"]
                      and "FoM" not in c["case"])["hover_min"]
    capital = t["drone_usd"] - usd("battery") - usd("props")

    def per_drone_hour(life_h, cycles):
        cycles_per_h = 60 / flight_min
        lines = {
            "Drone hardware, amortised": capital / life_h,
            "Battery wear": usd("battery") / cycles * cycles_per_h,
            "Propellers": usd("props") / PROP_LIFE_H,
            "Motor replacement": usd("motor") / MOTOR_SET_LIFE_H,
            "Charging electricity": t["battery_wh_nominal"] / CHARGER_EFF / 1000
                                    * ELECTRICITY_USD_KWH * cycles_per_h,
        }
        return lines

    ground_per_swarm_h = t["ground_usd"] / GROUND_LIFE_SWARM_H
    crew_h = CREW * CREW_WAGE_USD_H

    def summary(life_h, cycles):
        d = per_drone_hour(life_h, cycles)
        drone_h = sum(d.values())
        swarm_hw = SWARM_SIZE * drone_h + ground_per_swarm_h
        single_hw = drone_h + ground_per_swarm_h / SWARM_SIZE
        return {"lines": {k: round(v, 3) for k, v in d.items()},
                "drone_hour_hw": round(drone_h, 2),
                "swarm_hour_hw": round(swarm_hw, 2),
                "swarm_hour_with_crew": round(swarm_hw + crew_h, 2),
                "single_hour_hw": round(single_hw, 2),
                "single_hour_with_crew": round(single_hw + crew_h, 2)}

    base = summary(DRONE_LIFE_H, BATTERY_CYCLES_LOW)
    sens = []
    for life in (DRONE_LIFE_SENSITIVITY_H[0], DRONE_LIFE_H, DRONE_LIFE_SENSITIVITY_H[1]):
        for cyc in (BATTERY_CYCLES_LOW, BATTERY_CYCLES_CITED):
            s = summary(life, cyc)
            sens.append({"life_h": life, "cycles": cyc, "drone_hour_hw": s["single_hour_hw"],
                         "swarm_hour_hw": s["swarm_hour_hw"],
                         "swarm_hour_with_crew": s["swarm_hour_with_crew"]})

    per_ha = []
    for n, rate in COVERAGE_HA_MIN.items():
        hw = base["swarm_hour_hw"] if n == SWARM_SIZE else base["single_hour_hw"]
        crewed = base["swarm_hour_with_crew"] if n == SWARM_SIZE else base["single_hour_with_crew"]
        for f in SCAN_FRACTIONS:
            ha_h = rate * 60 * f
            per_ha.append({"drones": n, "ha_min_scan": rate, "scan_fraction": f,
                           "ha_per_hour": round(ha_h, 1),
                           "usd_ha_hw": round(hw / ha_h, 3), "usd_ha_with_crew": round(crewed / ha_h, 3)})

    crewed = []
    for name, role, total, hours, flight_only, rate, basis, src in CREWED:
        all_in = rate if rate is not None else total / hours
        crewed.append({"name": name, "role": role, "usd_h": round(all_in),
                       "usd_h_flight_only": round(flight_only / hours) if flight_only else None,
                       "basis": basis, "source": SOURCES[src],
                       "hours": hours, "total_usd": total})

    return {"flight_min_per_cycle": flight_min, "capital_per_drone_usd": round(capital, 2),
            "ground_per_swarm_hour": round(ground_per_swarm_h, 2), "crew_per_hour": crew_h,
            "base": base, "sensitivity": sens, "per_hectare": per_ha, "crewed": crewed}


# ----------------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------------
def bom_md(rows, t):
    out = []
    w = out.append
    w("# Bill of materials: Halo scout\n")
    w(f"Generated by `budget.py` from `bom.csv` and `exports/mass_properties.json`. Edit the CSV, then run "
      f"`python3 hardware/budget.py`. Prices are in US dollars and were checked on {CHECKED} "
      "at the linked page, excluding shipping and tax. Masses marked *est.* are estimates; *CAD* means "
      "the printed mass from the CAD volumes at the stated infill. Power is the draw at 5 V before regulator "
      "losses: typical / peak.\n")
    sections = [("drone", "Per drone"), ("ground", "Ground station (one per swarm)"),
                ("spares", "Spares (for a 16-drone swarm)")]
    notes = []
    for sec, title in sections:
        w(f"## {title}\n")
        w("| # | Part | Product | Qty | Unit USD | Line USD | Unit g | Line g | Power W | Source | Checked |")
        w("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            if r["section"] != sec:
                continue
            q = float(r["qty"])
            line = q * float(r["unit_usd"])
            price = money(float(r["unit_usd"]))
            if r["price_status"] != "checked":
                price += " (estimated)"
            if r["id"] in ("petg", "tpu"):
                price += " per kg"
            qty = r.get("qty_label") or (r["qty"] if q != int(q) else str(int(q)))
            if sec == "drone":
                um = float(r["unit_mass_g"])
                if r["id"] in ("petg", "tpu"):
                    ug, lg = "", f"{q * um:.1f}"
                else:
                    ug, lg = f"{um:g}", f"{q * um:.1f}"
                tag = {"estimated": " *est.*", "CAD": " *CAD*"}.get(r["mass_status"], "")
                lg += tag
                pw = (f"{float(r['power_w']):g} / {float(r['power_max_w']):g}"
                      if float(r["power_max_w"]) > 0 else "")
            else:
                ug = lg = pw = ""
            src = f"[link]({r['source']})" if r["source"] else "no single source"
            n = ""
            if r["source_note"].strip():
                notes.append((r["id"], r["source_note"].strip()))
                n = f" [{len(notes)}]"
            w(f"| {r['id']}{n} | {r['item']} | {r['product']} | {qty} | {price} | {money(line)} | {ug} | {lg} "
              f"| {pw} | {src} | {r['checked']} |")
        w("")

    e = {c["case"]: c for c in t["endurance"]}
    sim = t["endurance"][0]
    build = t["endurance"][1]
    margin = t["endurance"][2]
    peak = t["endurance"][3]
    fom = t["endurance"][4]
    w("## Totals\n")
    w("| Figure | This design | Simulator (`body.py`) | Verdict |")
    w("|---|---|---|---|")
    w(f"| Cost per drone | USD {money(t['drone_usd'])} (of which USD {money(t['drone_usd_estimated_part'])} estimated) | n/a | |")
    w(f"| 16 drones | USD {money(t['swarm_drones_usd'])} | n/a | |")
    w(f"| Ground station | USD {money(t['ground_usd'])} | n/a | |")
    w(f"| Spares: 32 batteries, 32 prop sets | USD {money(t['spares_usd'])} | n/a | |")
    w(f"| **16-drone swarm, ground station and spares** | **USD {money(t['swarm_total_usd'])}** | n/a | |")
    w(f"| All-up mass | {t['mass_g_bom']:.0f} g from this table ({t['mass_g_cad']:.0f} g in the CAD); "
      f"{t['mass_g_with_margin']:.0f} g with a {INTEGRATION_MARGIN:.0%} integration margin | 1,200 g | "
      f"{1200 - t['mass_g_with_margin']:.0f} g under with the margin |")
    w(f"| Avionics power | {t['avionics_w_typical']:.1f} W typical, {t['avionics_w_peak']:.1f} W peak, at the battery | "
      f"8 W | typical {8 - t['avionics_w_typical']:.1f} W under; peak {t['avionics_w_peak'] - 8:.1f} W over |")
    w(f"| Battery usable energy | {t['battery_wh_usable']:.1f} Wh ({USABLE_FRACTION:.0%} of {t['battery_wh_nominal']:.2f} Wh nominal) | 41 Wh | match |")
    w(f"| Hover power | {margin['hover_w']:.1f} W (with margin) | {sim['hover_w']:.1f} W | |")
    w(f"| Hover endurance | {margin['hover_min']:.1f} min with margin; {build['hover_min']:.1f} min as modelled | "
      f"{sim['hover_min']:.1f} min | longer than simulated, see below |")
    w(f"| Thrust-to-weight | about {t['thrust_to_weight']:.0f} (4 x {t['motor_thrust_g']:,} g retailer figure) | 2.5 | far more; limit it in the autopilot |")
    w("")
    w("### Power by rail\n")
    fc, bec = t["rails_w"]["fc5v"], t["rails_w"]["bec5v"]
    w("| Rail | Loads | 5 V typical / peak | Regulator efficiency | At battery, typical / peak |")
    w("|---|---|---|---|---|")
    w(f"| Flight controller 5 V | FC, GNSS, RC receiver, Remote ID | {fc[0]:.2f} / {fc[1]:.2f} W | "
      f"{FC_REGULATOR_EFF:.0%} (assumed) | {fc[0] / FC_REGULATOR_EFF:.2f} / {fc[1] / FC_REGULATOR_EFF:.2f} W |")
    w(f"| Matek BEC 5 V | companion, thermal, RGB, smoke sensor, swarm radio | {bec[0]:.2f} / {bec[1]:.2f} W | "
      f"{BEC_EFF_TYPICAL:.0%} / {BEC_EFF_PEAK:.0%} | {bec[0] / BEC_EFF_TYPICAL:.2f} / {bec[1] / BEC_EFF_PEAK:.2f} W |")
    w(f"| **Total** | | | | **{t['avionics_w_typical']:.2f} / {t['avionics_w_peak']:.2f} W** |")
    w("")
    w("The peak case adds every maximum at once: the radio sending at 1 W all the time, the companion at "
      "full load, a thermal shutter event and the smoke sensor's fan start. That does not happen in "
      "practice, but it shows the 8 W budget has no room for a continuous 1 W link. Keep the swarm radio "
      "at 100 to 250 mW over a 6 ha block.\n")
    w("### Endurance by the simulator's formula\n")
    w("`budget.py` imports `DroneBody` from `src/halo_systems/sim/body.py` and replaces mass, avionics power and "
      "battery energy in a copy. Hover power is `rotor_power_w(m g) + avionics_power_w`, with induced power "
      "`T v_h / FoM` from momentum theory over the 0.0993 m^2 disk; endurance is `60 battery_wh / hover_power_w`. "
      f"Cruise is `level_power_w` at the scouting speed of {SCOUT_SPEED_MPS:g} m/s with the simulator's 0.035 m^2 drag area.\n")
    w("| Case | Mass kg | Avionics W | Battery Wh | FoM | Rotor W | Hover W | Hover min | Cruise W at 6 m/s | Cruise min |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for c in t["endurance"]:
        w(f"| {c['case']} | {c['mass_kg']:.3f} | {c['avionics_w']:.2f} | {c['battery_wh']:.2f} | {c['figure_of_merit']:g} | "
          f"{c['rotor_w']:.1f} | {c['hover_w']:.1f} | **{c['hover_min']:.1f}** | {c['cruise_6mps_w']:.1f} | {c['cruise_6mps_min']:.1f} |")
    w("")
    w(f"The simulator's figure of merit, 0.6, is a rotor figure, but `body.py` adds avionics to it and treats "
      f"the sum as battery power. Real motors and ESCs lose energy as well. The last row multiplies 0.6 by an "
      f"assumed {MOTOR_ESC_EFF:.0%} motor-and-ESC efficiency; hover then lands at {fom['hover_min']:.1f} minutes, "
      f"close to the simulator's {sim['hover_min']:.1f}. Measure hover current on the first build and fit the "
      "figure of merit to it (BUILD.md, step 12).\n")
    w("## Notes\n")
    for i, (rid, note) in enumerate(notes, 1):
        w(f"{i}. **{rid}**: {note}")
    w("")
    w("## What could not be sourced\n")
    w("- Assorted hardware (wire, screws, standoffs, heat-shrink): priced as a range, USD 8 to 15, marked estimated.")
    w("- Masses of the PureThermal Mini Pro board, its cable, the OTG adapter, the SD card, the camera ribbon, "
      "the battery strap, the XT60 lead and the assorted hardware are not published; they are estimates (*est.*).")
    w("- No maker current figure was found for the RC receiver, the Remote ID module, the Camera Module 3 or the "
      "Kakute H7 itself; those powers are estimates and are marked so in `bom.csv`.")
    w("- The GEPRC Mark4 7-inch frame was out of stock at the maker on the day checked; a European shop had it.")
    w("")
    return "\n".join(out)


def costs_md(t, c):
    b = c["base"]
    out = []
    w = out.append
    w("# Running costs: Halo scout\n")
    w(f"Generated by `budget.py` from `bom.csv` and the assumptions at the top of that script. Prices checked {CHECKED}. "
      "Every number below is either computed from `BOM.md` or carries a link. Assumptions are labelled as such.\n")
    w("## Assumptions\n")
    w("| Assumption | Value | Basis |")
    w("|---|---|---|")
    w(f"| Drone life | {DRONE_LIFE_H} flight hours | assumption; {DRONE_LIFE_SENSITIVITY_H[0]} h and {DRONE_LIFE_SENSITIVITY_H[1]:,} h shown below. Covers airframe, electronics and sensors. |")
    w(f"| Ground station life | {GROUND_LIFE_SWARM_H} swarm flight hours | assumption |")
    w(f"| Battery cycle life | {BATTERY_CYCLES_LOW} cycles (used below); {BATTERY_CYCLES_CITED} shown as a sensitivity | "
      f"[{BATTERY_CYCLES_CITED} to 500 cycles is the usual LiPo range]({SOURCES['batteryskills']}); halved because each flight uses {USABLE_FRACTION:.0%} of the pack at high current |")
    w(f"| Flight per charge | {c['flight_min_per_cycle']:.1f} min | hover endurance with the integration margin, from BOM.md |")
    w(f"| Propellers | one set of 4 per {PROP_LIFE_H} flight hours | assumption; sooner after any contact with the ground |")
    w(f"| Motors | one set of 4 per {MOTOR_SET_LIFE_H} flight hours | assumption (bearing wear in dust) |")
    w(f"| Charging | {CHARGER_EFF:.0%} charger efficiency, USD {ELECTRICITY_USD_KWH:.2f}/kWh | assumption, set high on purpose |")
    w(f"| Crew | {CREW} people at USD {CREW_WAGE_USD_H:.2f}/h | [O*NET 33-2011.00 Firefighters, median wage 2025 (BLS data)]({SOURCES['onet_ff']}); wages only, no overheads |")
    w("")
    w("## Cost per flight hour\n")
    w(f"Drone capital excludes the battery and props, which are counted as wear: USD {money(c['capital_per_drone_usd'])} per drone.\n")
    w("| Line | Per drone flight hour (USD) | Per swarm hour, 16 drones (USD) |")
    w("|---|---|---|")
    for k, v in b["lines"].items():
        w(f"| {k} | {v:.2f} | {SWARM_SIZE * v:.2f} |")
    w(f"| Ground station, amortised | {c['ground_per_swarm_hour'] / SWARM_SIZE:.2f} | {c['ground_per_swarm_hour']:.2f} |")
    w(f"| **Hardware subtotal** | **{b['single_hour_hw']:.2f}** | **{b['swarm_hour_hw']:.2f}** |")
    w(f"| Crew of {CREW} | {c['crew_per_hour']:.2f} | {c['crew_per_hour']:.2f} |")
    w(f"| **Total with crew** | **{b['single_hour_with_crew']:.2f}** | **{b['swarm_hour_with_crew']:.2f}** |")
    w("")
    w("Sensitivity to drone life and battery cycles (hardware only, then with crew):\n")
    w("| Drone life (h) | Battery cycles | Per drone flight hour | Per swarm hour | Per swarm hour with crew |")
    w("|---|---|---|---|---|")
    for s in c["sensitivity"]:
        w(f"| {s['life_h']:,} | {s['cycles']} | {s['drone_hour_hw']:.2f} | {s['swarm_hour_hw']:.2f} | {s['swarm_hour_with_crew']:.2f} |")
    w("")
    big = max(b["lines"], key=b["lines"].get)
    w(f"The largest line is {big.lower()}, USD {b['lines'][big]:.2f} per drone flight hour. Battery wear comes next "
      f"at USD {b['lines']['Battery wear']:.2f}: a USD 51.99 pack lasting {BATTERY_CYCLES_LOW} flights of "
      f"{c['flight_min_per_cycle']:.0f} minutes. Gentler use (landing with 30% left, storing packs at storage "
      "voltage) is the cheapest saving. Crew time dominates a single drone's hour; the swarm spreads it over 16.\n")
    w("## Cost per hectare\n")
    w("Coverage rates are the simulator's measured rates on the 6 ha wildfire block, means over 20 seeds "
      "(`README.md`, wildfire table): one drone 0.5 ha/min, 16 drones 6.2 ha/min. They count scanning time only. "
      "Launch, transit to the block, battery swaps and landing add airborne time without coverage, so the table "
      "also shows a case where only half of the airborne time is scanning.\n")
    w("| Drones | Scan rate (ha/min) | Scanning share of flight | Hectares per hour | USD per ha, hardware | USD per ha, with crew |")
    w("|---|---|---|---|---|---|")
    for p in c["per_hectare"]:
        w(f"| {p['drones']} | {p['ha_min_scan']} | {p['scan_fraction']:.0%} | {p['ha_per_hour']:.0f} | {p['usd_ha_hw']:.2f} | {p['usd_ha_with_crew']:.2f} |")
    w("")
    one = next(p for p in c["per_hectare"] if p["drones"] == 1 and p["scan_fraction"] == 1.0)
    six = next(p for p in c["per_hectare"] if p["drones"] == 16 and p["scan_fraction"] == 1.0)
    w(f"Per hectare of hardware, one drone is cheaper ({one['usd_ha_hw']:.2f} against {six['usd_ha_hw']:.2f} USD): "
      f"16 drones cover {six['ha_per_hour'] / SWARM_SIZE:.0f} ha per drone-hour against {one['ha_per_hour']:.0f} for one drone, "
      "close to the 30% extra energy the simulator "
      "reports. What the swarm buys is time. It covers 90% of the block in 44 s instead of 620 s and finds the "
      f"last hotspot 13 times sooner. With crew counted the swarm is cheaper per hectare "
      f"({six['usd_ha_with_crew']:.2f} against {one['usd_ha_with_crew']:.2f} USD), because the same two people "
      f"cover {six['ha_per_hour'] / one['ha_per_hour']:.0f} times the ground per hour.\n")
    w("## Crewed aircraft, for comparison\n")
    w("| Aircraft | Role | USD per flight hour | What it includes | Source |")
    w("|---|---|---|---|---|")
    for a in c["crewed"]:
        inc = a["basis"]
        if a["usd_h_flight_only"]:
            inc += f"; flight and miscellaneous alone USD {a['usd_h_flight_only']:,}/h"
            inc += f" (USD {a['total_usd'] / 1e6:.1f} M over {a['hours']:,} h)"
        w(f"| {a['name']} | {a['role']} | {a['usd_h']:,} | {inc} | [link]({a['source']}) |")
    w(f"| NIROPS night infrared mapping flight | large fire perimeter mapping | about USD 3,000 per incident, not per hour | the 2026 FAQ's average, billed to the incident | [link]({SOURCES['nirops2026']}) |")
    w("")
    heli = c["crewed"][0]["usd_h"]
    fixed = c["crewed"][1]["usd_h"]
    cessna = c["crewed"][2]["usd_h"]
    w(f"A swarm hour with crew, USD {b['swarm_hour_with_crew']:,.0f}, is about {b['swarm_hour_with_crew'] / heli:.0%} of the cost of "
      f"a contract light helicopter hour (USD {heli:,}) and {b['swarm_hour_with_crew'] / fixed:.0%} of a contract fixed-wing hour "
      f"(USD {fixed:,}). It is close to the Montana DNRC's operating cost for a Cessna patrol hour, USD {cessna}, "
      "which is not an all-in figure.\n")
    w("Read these with care:\n")
    w("- **Not like for like.** A crewed patrol aircraft covers far more ground per hour, flies in wind and "
      "terrain a 1 kg drone cannot, and carries people and water. The swarm's job is narrower: a close thermal "
      "sweep (about 0.14 m per pixel at 20 m) of a small block after lightning, to find smouldering spots a "
      "high pass can miss. It is a complement for ground crews, not a replacement for aircraft.")
    w("- **USFS figures are 2017 dollars**, averaged over every contract of that type, with availability "
      "(standby) included. Flight-only figures are shown alongside. No inflation adjustment is applied.")
    w("- **Montana DNRC figures are the agency's own operating cost per hour** as reported to the legislature "
      "in June 2022; the report does not say what they include.")
    w("- **The swarm figures leave out** training, insurance, regulatory approval, a vehicle, software "
      "development, data links beyond the BOM, and crashes beyond what the 300-hour life absorbs. The 100-hour "
      "row above is a rough stand-in for a hard first season.")
    w("")
    return "\n".join(out)


def main():
    props = json.loads((HERE / "exports" / "mass_properties.json").read_text())
    rows = fill_printed(read_bom(), props)
    t = totals(rows, props)
    c = costs(rows, t)
    (HERE / "BOM.md").write_text(bom_md(rows, t))
    (HERE / "COSTS.md").write_text(costs_md(t, c))
    (HERE / "budget.json").write_text(json.dumps({"totals": t, "costs": c}, indent=2) + "\n")
    print(f"drone USD {t['drone_usd']:.2f}; swarm USD {t['swarm_total_usd']:.2f}")
    print(f"mass {t['mass_g_bom']:.1f} g (CAD {t['mass_g_cad']:.1f} g), with margin {t['mass_g_with_margin']:.1f} g")
    print(f"avionics {t['avionics_w_typical']:.2f} W typical, {t['avionics_w_peak']:.2f} W peak; usable {t['battery_wh_usable']:.2f} Wh")
    for e in t["endurance"]:
        print(f"  {e['case']}: hover {e['hover_w']} W, {e['hover_min']} min; cruise {e['cruise_6mps_min']} min")
    b = c["base"]
    print(f"per drone hour {b['single_hour_hw']}, swarm hour {b['swarm_hour_hw']} (+crew {b['swarm_hour_with_crew']})")
    for p in c["per_hectare"]:
        print(f"  {p}")


if __name__ == "__main__":
    main()
