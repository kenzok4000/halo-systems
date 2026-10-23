# Strike checks: which lightning strike to check next

Seeds 9300-9319 (20 valleys), flown once after the plan in [PREREG.md](PREREG.md) was written; 140 flights. Made
by `tools/wildfire_strike_check_sweep.py` and `tools/wildfire_strike_check_report.py` from the commit that adds
the plan (ca01f9e), in a separate checkout.

The morning after dry lightning, a team of 4 drones (squad C) flies to one reported strike at a time and patrols a
120 m square round it for one circuit, while the area squads patrol as before. Four ways to fly the same drones
over the same valleys for 12 minutes:

- **none**: no check team; every drone in the area squads (F4-N4 at 8 drones, F4-N6-S6 at 16).
- **nearest**: check team, last night's strikes first, the nearest first.
- **rule**: check team, a hand-written triage rule (strikes a located fire explains go last; smoke the station
  cannot explain, seen by two or more drones, first; then last night's, then the night before's; nearest first).
- **nemotron**: check team, Nemotron 3 Nano with reasoning reads the strike table and picks the next strike, with
  a one-sentence reason. Every pick passes the safety validator. No picker sees which strike started a fire.

With a team the area squads are F4 at 8 drones and F4-N4-S4 at 16. Nemotron flew at 8 drones only, to stay within
the USD 0.40 the entrant approved. A fire counts as found when the ground station placed a fix from its own
hotspot or from bearings to its own plume (report_v2).

## 8 drones

| Arm | Squads | Found | Found smouldering | Under the overhang | Across the valley | False fires per flight | Checks per flight | Checks on a fire's strike (fire not yet found) | Phones warned in time |
|---|---|---|---|---|---|---|---|---|---|
| none | F4-N4 | 90% | 63% | 16 of 20 | 14 of 20 | 1.50 | - | - | 95.1% |
| nearest | F4 + C4 | 95% | 68% | 18 of 20 | 19 of 20 | 0.70 | 4.7 | 61 of 94 (14) | 95.1% |
| rule | F4 + C4 | 96% | 69% | 18 of 20 | 20 of 20 | 0.60 | 4.0 | 27 of 80 (10) | 95.1% |
| nemotron | F4 + C4 | 96% | 69% | 18 of 20 | 19 of 20 | 0.65 | 3.8 | 27 of 76 (6) | 95.1% |

Paired by valley: extra fires found per flight (and extra false fires, the guard), mean and 95% bootstrap interval
over valleys (10,000 resamples), with the number of valleys better and worse.

| Comparison | Found | Found smouldering | False fires |
|---|---|---|---|
| **nemotron minus rule (primary)** | **+0.00 (-0.20 to +0.20); 2 better, 2 worse** | **+0.00 (-0.20 to +0.20); 2 better, 2 worse** | +0.05 (-0.45 to +0.50) |
| nemotron minus nearest | +0.05 (-0.15 to +0.25); 3 better, 2 worse | +0.05 (-0.15 to +0.25) | -0.05 (-0.50 to +0.35) |
| nemotron minus none | +0.30 (+0.00 to +0.60); 8 better, 1 worse | +0.30 (-0.05 to +0.60) | -0.85 (-1.45 to -0.25) |
| rule minus none | +0.30 (+0.00 to +0.60); 9 better, 3 worse | +0.30 (+0.00 to +0.60) | -0.90 (-1.45 to -0.35) |
| nearest minus none | +0.25 (-0.10 to +0.55); 9 better, 4 worse | +0.25 (-0.10 to +0.55) | -0.80 (-1.30 to -0.30) |
| rule minus nearest | +0.05 (+0.00 to +0.15); 1 better, 0 worse | +0.05 (+0.00 to +0.15) | -0.10 (-0.25 to +0.00) |

## 16 drones

| Arm | Squads | Found | Found smouldering | Under the overhang | Across the valley | False fires per flight | Checks per flight | Checks on a fire's strike (fire not yet found) | Phones warned in time |
|---|---|---|---|---|---|---|---|---|---|
| none | F4-N6-S6 | 95% | 68% | 15 of 20 | 20 of 20 | 1.50 | - | - | 95.0% |
| nearest | F4-N4-S4 + C4 | 96% | 68% | 17 of 20 | 20 of 20 | 0.95 | 4.8 | 61 of 95 (11) | 95.1% |
| rule | F4-N4-S4 + C4 | 96% | 68% | 17 of 20 | 20 of 20 | 1.00 | 4.0 | 20 of 80 (7) | 95.1% |

| Comparison | Found | Found smouldering | False fires |
|---|---|---|---|
| rule minus none | +0.05 (-0.15 to +0.25); 3 better, 2 worse | +0.00 (-0.25 to +0.25) | -0.50 (-1.00 to +0.00) |
| nearest minus none | +0.05 (-0.15 to +0.25); 3 better, 2 worse | +0.00 (-0.25 to +0.25) | -0.55 (-1.10 to -0.05) |
| rule minus nearest | +0.00 (+0.00 to +0.00) | +0.00 (+0.00 to +0.00) | +0.05 (+0.00 to +0.15) |

## Safety and the model

| Drones | Arm | Crashes | Near misses | Closest approach between two drones, worst flight (median flight) | Checks cut off by the patrol's end | Picks dropped as too late |
|---|---|---|---|---|---|---|
| 8 | none | 0 | 0 | 10.9 m (12.7 m) | - | - |
| 8 | nearest | 0 | 0 | 8.5 m (10.1 m) | 0 | 1 |
| 8 | rule | 0 | 0 | 5.1 m (9.8 m) | 0 | 0 |
| 8 | nemotron | 0 | 0 | 5.4 m (10.0 m) | 0 | 1 |
| 16 | none | 0 | 0 | 11.0 m (12.5 m) | - | - |
| 16 | nearest | 0 | 0 | 7.6 m (10.0 m) | 0 | 0 |
| 16 | rule | 0 | 0 | 5.3 m (10.0 m) | 0 | 0 |

Nemotron made 77 calls over its 20 flights: none unusable, none answered "stop", every pick named an offered strike
and passed the validator. The calls cost USD 0.086 at the price ceiling the ledger uses (about USD 0.004 a flight), including nine attempts
that timed out at 30 s and were retried, each counted at its worst case.
The median call took 10.8 s (9 in 10 under 21 s, the slowest 156 s) and used 1,586 completion tokens (most 2,573, of
6,000 allowed). The team picks its next strike 40 s before a look ends: 55 of the 57 calls made during a look
finished before it ended. During a slow call the team keeps patrolling its square: the take-off pick in valley 9317
took 143 s, and in valley 9311 a 156 s call came back too late to finish in the patrol, so the pick was dropped
and that flight made no further check.

A sample of its reasons, as written (seed, strike):

- 9304, 13: "close unchecked strike with strong smoke detections suggests an active fire not yet found"
- 9306, 10: "High unexplained smoke, far from known fires and people, likely a new fire"
- 9312, 10: "Closest recent last-night strike in scrub near people, high chance of an undiscovered fire."
- 9303, 18: "Most recent, unpatrolled, far from known fires, low prior inspection, likely a new smoldering fire."

## What it shows (written after the run)

| Prediction in `PREREG.md` | Outcome |
|---|---|
| Nemotron and the rule find the same number of fires within the noise at 8 drones | Yes: 96% each, +0.00 per flight (-0.20 to +0.20), 2 valleys better and 2 worse |
| ...and the same while smouldering | Yes: 69% each, +0.00 (-0.20 to +0.20) |
| With a team the fire across the valley is found more often than with none at 8 drones | Yes: 19 or 20 of 20 against 14 of 20 (6 valleys better, at most 1 worse) |
| ...but fires found in all are within the noise of none at both sizes | At 16 drones, yes (+0.05, -0.15 to +0.25). At 8 drones, on the edge: rule and Nemotron +0.30 per flight, intervals from +0.00 to +0.60; nearest +0.25 (-0.10 to +0.55) |
| Nearest spends the most checks on strikes of fires already found | Yes: 47 of 94 checks, against 17 of 80 for the rule and 21 of 76 for Nemotron |
| ...and reaches the most fires not yet found | Yes: 14 checks, against 10 for the rule and 6 for Nemotron |
| Nemotron and the rule spend most of their checks on strikes that started nothing | Yes: 49 of 76 and 53 of 80 |
| No arm finds the fire under the overhang more often than none | No, narrowly: 18 of 20 with each team against 16 with none at 8 drones, 17 against 15 at 16 (3 valleys better, 1 worse, in each case; no interval computed). Every find of it was by smoke, and all but two (one each for nearest and rule at 16 drones) while it smouldered |

- **No measurable difference between Nemotron and the hand-written rule.** On 20 unseen valleys both found 96 of
  the 100 fires; valley by valley they differed by one fire in 4 of 20 (2 each way), +0.00 per flight (-0.20 to
  +0.20). False fires were within the noise (0.65 against 0.60 per flight, +0.05, -0.45 to +0.50). Nemotron reached
  fewer fires not yet found on its checks (6 against 10; paired by valley -0.20 per flight, -0.55 to +0.10, worked
  out after the run). Its reasons cite the table's columns; 20 of the 77 mention people (18 because a strike is near
  them, 2 because it is far from them), which the table offers but the rule does not use.
- **A check team probably helps at 8 drones, mostly by reaching the fire across the valley**, which the F4-N4
  split does not patrol: about +0.3 fires per flight with the rule or Nemotron, but the gain sits at the edge of the
  noise (intervals from +0.00, and from -0.05 for Nemotron's fires found smouldering; nearest's spans zero). At 16 drones
  the area squads already cover the whole valley, and the team adds nothing measurable.
- **Fewer false fires with a team**: 0.60 to 0.70 per flight at 8 drones against 1.50, and 0.95 to 1.00 at 16
  against 1.50, every interval below or touching zero. The team takes drones out of the area squads, and in the squads
  measurement false fires rose when slope squads flew (B against D in
  [../wildfire_valley_squads/REPORT.md](../wildfire_valley_squads/REPORT.md)); that this is the cause here is
  inferred, not measured. Under today's alert rule, false fires led to public alerts in 2 valleys with Nemotron
  (7 alerts), 3 with the rule (12) and 4 each with nearest (13) and none (18), at 8 drones.
- **Phones warned in time did not change**: 568 of 597 in every arm and size (567 for none at 16).
- **The team flies closer to other drones.** No flight crashed or logged a near miss, but the closest approach
  between two drones in the worst team flight was 5.1 to 5.4 m (rule and Nemotron) and 7.6 to 8.5 m (nearest),
  against 10.9 to 11.0 m with no team; the median flight was about 10 m against 12.5 to 12.7 m. On the development
  valleys the worst team flight was 6.6 to 8.1 m. Where the close passes on these held-out flights come from was
  not traced; tracing them, and any fix, would need new valleys.
- **Cost**: USD 0.086 for Nemotron's 77 calls; the rule and nearest cost nothing to run.

The tables come from the report tool unchanged. The overhang and across-valley pairs, the false-fire alert counts,
the safety columns and Nemotron's latency and token figures were read from `runs.jsonl` after the run
(`fire_table`, `alerts_from_false_fires`, `min_separation_m`, `strike_picks_expired`, `strike_decisions`).

## Files

- `runs.jsonl`: one row per flight (140), all four arms.
- `nemotron_cache.jsonl`: every Nemotron reply, so the Nemotron flights replay without a key
  (`--cache-mode replay`, command in [PREREG.md](PREREG.md)).
- `summary.json`: the report tool's output.
