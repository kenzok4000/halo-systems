"""Crew outputs in the forms crews use: each fire's brief as a map file (GeoJSON or KML), and as a short message.

The station's picture is in local metres (x east, y north). Map files need longitude and latitude, so the valley is
placed at an ``anchor`` (latitude, longitude of the point x = 0, y = 0) on a flat-earth approximation, which is
good to well under a metre across a valley a kilometre wide. The default anchor is 0 N 0 E, so a file from the
simulation can never be mistaken for a real place; an operator would set the incident's own position. Every
feature says it is simulated.

What a file holds, per located fire in the latest forecast: the fire's position (with its location error), the
head's arrow for the next hour, the direction cone, and the named places it reaches with their times.

``sizeup`` writes each brief as a short message in the order of the size-up report in the NWCG Incident Response
Pocket Guide (PMS 461, January 2025, inside front cover): incident type, location/jurisdiction, incident size,
incident status, establish incident commander, incident name, weather conditions, radio frequencies, best access
routes, assets/values at risk, special hazards or concerns, additional resource needs. That report is meant for
the first resource on scene; this one comes from the drones before anyone arrives, so it leaves out what they
cannot know (radio frequencies and access routes) and says no incident commander is on scene yet.
"""

from __future__ import annotations

import json
import math
from xml.sax.saxutils import escape

M_PER_DEG_LAT = 111_320.0
ANCHOR = (0.0, 0.0)


def lonlat(x: float, y: float, anchor: tuple[float, float] = ANCHOR) -> tuple[float, float]:
    lat0, lon0 = anchor
    # On a flat earth a degree of latitude is 111,320 m everywhere, and a degree of longitude is that
    # times the cosine of the anchor's latitude.
    return (round(lon0 + x / (M_PER_DEG_LAT * math.cos(math.radians(lat0))), 7), round(lat0 + y / M_PER_DEG_LAT, 7))


def _cone(origin, bearing_deg: float, cone_deg: float, reach_m: float, k: int = 12) -> list[tuple[float, float]]:
    """A sector from ``origin``: ``cone_deg`` either side of the compass ``bearing_deg`` (the brief's cone is a
    half-width, as its scoring and the dashboard read it), ``reach_m`` long."""
    ox, oy = origin
    pts = [(ox, oy)]
    for j in range(k + 1):
        # Compass bearings run clockwise from north, so x (east) takes the sine and y (north) the cosine.
        b = math.radians(bearing_deg - cone_deg + 2.0 * cone_deg * j / k)
        pts.append((ox + reach_m * math.sin(b), oy + reach_m * math.cos(b)))
    pts.append((ox, oy))
    return pts


def features(fc: dict, anchor: tuple[float, float] = ANCHOR, places=()) -> list[dict]:
    """GeoJSON features for every fire briefed in forecast ``fc`` (a forecast from ``Wildfire.forecasts``);
    ``places`` (the scenario's named places, with x and y) locate the places each brief names."""
    locs = {l["id"]: l for l in fc.get("located", [])}
    where = {p["name"]: (p["x"], p["y"]) for p in places}
    t = float(fc["t"])
    out = []
    for b in fc.get("brief", []):
        loc = locs.get(b["fire"])
        if loc is None:
            continue
        name = f"Fire {b['fire'] + 1}"
        common = {"simulated": True, "fire": b["fire"] + 1, "forecast_t_s": round(t, 1)}
        near = min(where, key=lambda k: math.hypot(where[k][0] - loc["x"], where[k][1] - loc["y"])) if where else None
        out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": lonlat(loc["x"], loc["y"], anchor)},
                    "properties": {**common, "name": name, "kind": "fire", "state": b["state"],
                                   "located_by": b["method"], "location_error_m": round(loc["err_m"], 1),
                                   "area_m2": round(loc["area_m2"], 1), "direction": b["direction"],
                                   "head_bearing_deg": b["head_bearing_deg"], "cone_deg": b["cone_deg"],
                                   "head_m_per_min": b["head_m_per_min"], "area_1h_ha": b["area_1h_ha"],
                                   "area_2h_ha": b["area_2h_ha"], "brief": b["text"],
                                   "radio": sizeup(b, loc, nearest=near)}})
        if b["head_bearing_deg"] is not None and b.get("head_from_xy") and b.get("head_xy"):
            (fx, fy), (hx, hy) = b["head_from_xy"], b["head_xy"]
            out.append({"type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": [lonlat(fx, fy, anchor), lonlat(hx, hy, anchor)]},
                        "properties": {**common, "name": f"{name} head, next hour", "kind": "head",
                                       "head_bearing_deg": b["head_bearing_deg"], "head_m_per_min": b["head_m_per_min"]}})
            if b.get("cone_deg") is not None:
                reach = max(math.hypot(hx - fx, hy - fy), 30.0)
                ring = [lonlat(x, y, anchor) for x, y in _cone((fx, fy), b["head_bearing_deg"], b["cone_deg"], reach)]
                out.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                            "properties": {**common, "name": f"{name} direction cone", "kind": "cone",
                                           "cone_deg": b["cone_deg"]}})
        for p in b.get("places", []):
            if p["name"] not in where:
                continue
            out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": lonlat(*where[p["name"]], anchor)},
                        "properties": {**common, "name": p["name"], "kind": "place", "eta_min": p["eta_min"],
                                       "reached_by": name}})
    return out


def sizeup(b: dict, loc: dict, authority: str = "Valley Fire District", nearest: str | None = None) -> str:
    """One brief as a radio-length message, in the IRPG size-up order (see the module docstring)."""
    d = b.get("drivers", {})
    size = (f"about {loc['area_m2'] / 1e4:.1f} ha" if loc["area_m2"] >= 1e4 else f"about {loc['area_m2']:.0f} m2"
            if b["state"] != "smouldering" else "smouldering, under 50 m2")
    status = (f"head running {b['direction']} at {b['head_m_per_min']:.1f} m/min"
              if b.get("head_bearing_deg") is not None and b["state"] != "smouldering" else
              f"if it takes off, head runs {b['direction']}" if b.get("direction") else b["state"])
    wind = ""
    if d.get("wind_mps") is not None and d.get("wind_from_deg") is not None:
        from .wildfire import compass_of
        wind = f"wind {d['wind_mps']:.0f} m/s from the {compass_of(d['wind_from_deg'])}"
    moist = d.get("fine_fuel_moisture_pct")
    weather = ", ".join(x for x in (wind, None if moist is None else f"fine fuels {moist:.0f}%") if x)
    risk = "; ".join(f"{p['name']} " + ("now" if p["eta_min"] < 1 else f"in {p['eta_min']} min")
                     for p in b.get("places", [])[:3]) or "none named in 3 h"
    hazard = {"wind": "wind-driven", "slope": "running upslope", "wind and slope": "wind and slope together",
              "fuel": "following the fuel", "neither": "slow"}.get(d.get("main"), "")
    parts = [
        "Wildland fire",
        f"{('near ' + nearest + ', ') if nearest else ''}{authority}",
        size,
        status,
        "no IC on scene, drone report",
        f"Fire {b['fire'] + 1}",
        weather or "weather not measured",
        f"at risk: {risk}",
        f"hazard: {hazard}" if hazard else "",
        "initial attack requested",
    ]
    return ". ".join(p for p in parts if p) + ". (Simulated)"


def geojson(fc: dict, anchor: tuple[float, float] = ANCHOR, places=()) -> dict:
    return {"type": "FeatureCollection", "name": "Halo Systems crew brief (simulated)",
            "features": features(fc, anchor, places)}


def kml(fc: dict, anchor: tuple[float, float] = ANCHOR, places=()) -> str:
    """The same features as KML 2.2 placemarks (points, the head line, the cone polygon)."""
    style = {"fire": "fire", "head": "head", "cone": "cone", "place": "place"}
    marks = []
    for f in features(fc, anchor, places):
        p, g = f["properties"], f["geometry"]
        desc = p.get("brief") or ", ".join(f"{k}: {v}" for k, v in p.items() if k not in ("name", "kind"))
        if g["type"] == "Point":
            geom = f"<Point><coordinates>{g['coordinates'][0]},{g['coordinates'][1]},0</coordinates></Point>"
        elif g["type"] == "LineString":
            geom = "<LineString><coordinates>" + " ".join(f"{x},{y},0" for x, y in g["coordinates"]) + \
                   "</coordinates></LineString>"
        else:
            geom = "<Polygon><outerBoundaryIs><LinearRing><coordinates>" + \
                   " ".join(f"{x},{y},0" for x, y in g["coordinates"][0]) + \
                   "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        marks.append(f"<Placemark><name>{escape(str(p['name']))}</name>"
                     f"<description>{escape('SIMULATED. ' + str(desc))}</description>"
                     f"<styleUrl>#{style[p['kind']]}</styleUrl>{geom}</Placemark>")
    styles = ('<Style id="fire"><IconStyle><color>ff0000ff</color></IconStyle></Style>'
              '<Style id="head"><LineStyle><color>ff0000ff</color><width>3</width></LineStyle></Style>'
              '<Style id="cone"><LineStyle><color>ff00a5ff</color></LineStyle><PolyStyle><color>4000a5ff</color>'
              '</PolyStyle></Style><Style id="place"><IconStyle><color>ffffffff</color></IconStyle></Style>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<name>Halo Systems crew brief (simulated), forecast at {fc['t']:.0f} s</name>{styles}"
            + "".join(marks) + "</Document></kml>\n")


def dumps_geojson(fc: dict, anchor: tuple[float, float] = ANCHOR, places=()) -> str:
    return json.dumps(geojson(fc, anchor, places), indent=1)
