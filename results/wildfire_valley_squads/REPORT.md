# Squads per area: fires found by squad size

Seeds 8000-8019 (20 valleys), flown once after the plan in `PREREG.md` was written; 300 flights. Made by `tools/wildfire_valley_squads_sweep.py` and `tools/wildfire_valley_squads_report.py`.

Three ways to fly the same number of drones over the same valleys:

- **A**, today's flight (one sweep of the floor).
- **B**, floor patrol (one squad).
- **D**, squads per area (floor and both slopes).

The squads patrol for 12 minutes, inside one battery. Drones that do not fit across an area stay in reserve at the launch point (spares for battery swaps). Each valley has five fires: four in or beside the valley floor and one across the valley on a slope. A fire counts as found when the ground station placed a fix from its own hotspot or from bearings to its own plume.

## Ground station: legacy

| Drones | D: drones per area (reserve) | Found: A / B / D | Found while smouldering: A / B / D | Across-valley fire: A / B / D | False fires per flight: A / B / D | D: time to see each area once |
|---|---|---|---|---|---|---|
| 4 | F4-N0-S0-R0 | 78% / 72% / 72% | 53% / 50% / 50% | 35% / 15% / 15% | 0.20 / 0.55 / 0.55 | F 9.6 min |
| 8 | F4-N4-S0-R0 | 76% / 74% / 84% | 53% / 51% / 58% | 30% / 20% / 75% | 0.00 / 0.40 / 1.00 | F 9.6 min, N 9.7 min |
| 16 | F4-N6-S6-R0 | 84% / 80% / 88% | 58% / 55% / 62% | 75% / 50% / 100% | 0.40 / 0.35 / 1.05 | F 9.6 min, N 7.1 min, S 7.1 min |
| 32 | F6-N12-S12-R2 | 86% / 80% / 90% | 62% / 55% / 64% | 75% / 50% / 100% | 0.50 / 0.35 / 1.50 | F 7.0 min, N 4.3 min, S 4.3 min |
| 64 | F11-N12-S12-R29 | 87% / 80% / 91% | 61% / 55% / 65% | 100% / 50% / 100% | 0.70 / 0.35 / 0.85 | F 4.2 min, N 4.3 min, S 4.3 min |

Paired by valley (same seeds): extra fires found per flight, mean and 95% bootstrap interval over valleys.

| Drones | D minus B: found | D minus B: found while smouldering | D minus A: found | D minus A: found while smouldering |
|---|---|---|---|---|
| 4 | +0.00 (+0.00 to +0.00) | +0.00 (+0.00 to +0.00) | -0.30 (-0.65 to -0.05) | -0.15 (-0.40 to +0.00) |
| 8 | +0.50 (+0.20 to +0.85) | +0.35 (+0.00 to +0.70) | +0.40 (+0.10 to +0.70) | +0.25 (+0.00 to +0.55) |
| 16 | +0.40 (+0.10 to +0.70) | +0.35 (+0.00 to +0.70) | +0.20 (-0.10 to +0.50) | +0.20 (-0.10 to +0.50) |
| 32 | +0.50 (+0.20 to +0.80) | +0.45 (+0.10 to +0.75) | +0.20 (-0.15 to +0.50) | +0.10 (-0.20 to +0.40) |
| 64 | +0.55 (+0.35 to +0.75) | +0.50 (+0.25 to +0.75) | +0.20 (-0.05 to +0.45) | +0.20 (-0.05 to +0.45) |

Why the others were missed (D, all valleys): 
- 4 drones: 17 never in view, 8 in view but hidden, 3 confirmed by a camera but no fix came from it, 22 found only after spreading.
- 8 drones: 5 never in view, 9 in view but hidden, 2 confirmed by a camera but no fix came from it, 26 found only after spreading.
- 16 drones: 0 never in view, 10 in view but hidden, 2 confirmed by a camera but no fix came from it, 26 found only after spreading.
- 32 drones: 0 never in view, 8 in view but hidden, 2 confirmed by a camera but no fix came from it, 26 found only after spreading.
- 64 drones: 0 never in view, 7 in view but hidden, 2 confirmed by a camera but no fix came from it, 26 found only after spreading.

Safety and energy, every flight: 4 drones: 0 crashes, 0 drones retired; 8 drones: 0 crashes, 0 drones retired; 16 drones: 0 crashes, 0 drones retired; 32 drones: 0 crashes, 0 drones retired; 64 drones: 0 crashes, 0 drones retired.

## What it shows (written after the run)

| Prediction in `PREREG.md` | Outcome |
|---|---|
| D finds more fires than B at 8 drones | Yes: 84% against 74%, +0.50 fires per flight (+0.20 to +0.85) |
| ...and the same as B from 16 drones up | No: D kept finding more, +0.40 to +0.55 per flight at 16, 32 and 64, every interval above zero. B levels off at 80%: one squad on the floor finds the fire across the valley in half the valleys at most |
| D finds the across-valley fire more often than A up to 16 drones | Yes from 8 drones: 75% against 30% at 8, 100% against 75% at 16. At 4 drones D is the floor squad alone (15% against 35%) |
| At 32 and 64 drones A matches or beats D on fires found | Matches: D 90% and 91%, A 86% and 87%; +0.20 per flight, with intervals that include zero |
| No arm finds the fire under the overhang from above | Yes: no camera saw it in any of the 300 flights. Its smoke escapes the overhang, and every arm placed it by smoke while it still smouldered in about 60% of flights (176 of 300) |

- **More fires, from 8 drones up.** Squads per area found 84% to 91% of fires (8 to 64 drones), against 74% to 80%
  for one squad on the floor, and the fire across the valley in every valley from 16 drones. Against today's single
  sweep the gain is clear at 8 drones (+0.40 per flight, +0.10 to +0.70) and smaller from 16 up (+0.20, intervals
  that include zero).
- **At 4 drones, one sweep beats a patrol**: the single line found 78% against 72% (-0.30 per flight, -0.65 to
  -0.05).
- **The cost: more false fires.** D placed 0.85 to 1.50 false fires per flight from 8 drones up, against 0.35 to
  0.40 for B, above the +0.3 guard at every size from 8. Under the committed (today's) alert rule, false fires
  led to public alerts in 5 of the 20 valleys at 8 drones (17 alerts in all), in one valley each at 32 and 64
  drones, and in one valley for A at 4 drones; never for B. These flights did not use the pre-signed rules, so how
  those rules treat the false fires was not measured here.
- **Not the same flight time.** The squads patrol for 12 minutes on one battery; today's sweep ends when the line
  has crossed the floor once (about 10 minutes at 4 drones, 4 at 16, under 2 at 32 and 64). Part of D's gain
  over A is that it keeps looking for longer.
- **Phones warned in time did not change**: 26.9 per flight in every arm and size. The fires that reach phones were
  found in time by all three flights; the squads' extra finds are fires that never reached a phone.
- No crashes and no drones retired in any of the 300 flights.

The table and the paired differences above come from the report tool unchanged; the alert counts in this section
were read from `runs.jsonl` after the run (`alerts_from_false_fires`, `phones_warned_in_time`), the overhang
counts from each fire's `camera_t` and `own_by`.
