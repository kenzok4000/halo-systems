# Strike checks: plan for the held-out measurement

Written before seeds 9300-9319 were flown. The code that flies them is the commit that adds this file; the sweep
runs from a separate checkout of that commit, so later edits cannot reach it.

## Question

On 22 October the entrant asked for a measured Nemotron job in the wildfire valley: Nemotron chooses which of the
night's lightning strikes a small team of drones checks next. The morning after dry lightning an agency has more
strikes than it can check, and a strike can smoulder for hours before it shows. Does a check team that lets
Nemotron pick its next strike find more of the valley's fires, and more of them while they still smoulder, than
the same team picking by a hand-written triage rule? And does a check team help at all, against putting every
drone in the area squads?

## The job

The area squads patrol the valley floor and slopes as in the squads measurement
([../wildfire_valley_squads/PREREG.md](../wildfire_valley_squads/PREREG.md)). A check team of 4 drones (squad
code C; `sim/strike_check.py`) flies to one reported strike at a time, patrols a 120 m square round it for one
full circuit with thermal cameras looking down and forward cameras watching for smoke, then moves on. It flies
20 m above the squads, so it can cross their areas, and keeps that height between strikes; its drones move in
step, at up to 8 m/s, to points in a line beside the new square, and join the square together once all have
settled there. It is never moved between squads by hand (the validator refuses it). Its thermal footprint is held
to the squads' size, so the extra height buys no wider view. The next strike is picked 40 s before the current
look ends.

Each valley's strike list (`eval/scenarios._strike_list`, its own random stream) has one strike near each of the
5 fires (reported about 50 m off, one standard deviation), 6 to 10 more from last night that started nothing, and
3 to 6 from the night before, spread uniformly over the valley. Code builds the table a picker reads (night, hours
ago, distance from the team, ground and slope, whether an area squad patrols it, the share of the ground round it
a thermal camera has already looked at, the nearest fire the station has located, smoke bearings the station
cannot explain that pass near it, and the nearest people) and offers only strikes the team can reach and look at
before the patrol ends. Every pick passes the safety validator (`check_strike`: a listed, unchecked strike, a team
drone flying, no airspace hold). Which fire a strike started is used only for scoring; no picker sees it.

## Arms (each valley flown once per arm and size; 12-minute patrol)

- **none**: no check team; every drone in the area squads, the default split (`"auto"`): F4-N4 at 8 drones,
  F4-N6-S6 at 16. The strike list is attached but cues nothing (the squads measurement's arm D).
- **nearest**: check team, last night's strikes before the night before's, the nearest first.
- **rule**: check team, a hand-written triage rule: strikes a located fire already explains (one within 150 m)
  go last; of the rest, those with smoke the station cannot explain, seen by two or more drones, go first, then
  last night's, then the night before's; the nearest first within each group (`supervisor/strikes.RulePicker`).
- **nemotron**: check team, Nemotron 3 Nano (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) with reasoning, temperature
  0, up to 6,000 tokens, reads the table and answers with a strike id and a one-sentence reason, or with no strike
  to stop checking (the team then keeps patrolling its last square; it never did so on the development valleys).
  A reply that names no offered strike, or a call that fails, is counted as unusable and never replaced by the
  rule: the team keeps looking where it is and the model is asked again a circuit later. Every reply is recorded,
  so the flights replay.

With a team the other drones take the default split of what is left: F4 + C4 at 8 drones, F4-N4-S4 + C4 at 16.
Every arm flies all n drones. Nemotron flies at 8 drones only, to keep this job within the USD 0.40 the entrant
approved (total Nebius spend under USD 8.74; the development flights used USD 0.17 of it). Controller and
supervisor improved+fixed, ground station legacy, report_v2 scoring, as the squads measurement.

## Measures

- **Primary:** fires found by their own evidence per flight (report_v2), paired by valley, **nemotron minus rule
  at 8 drones**, with a 95% bootstrap interval over valleys (10,000 resamples, generator seed 9300).
- **Co-primary:** the same for fires found while still smouldering (the station's own class).
- **Secondary:** nemotron minus nearest and nemotron minus none at 8 drones; rule minus none, nearest minus none
  and rule minus nearest at 8 and 16 drones; the fire under the overhang and the fire across the valley found;
  checks per flight; checks on a fire's strike, and on the strike of a fire not yet found when the team got there;
  phones warned in time; Nemotron's calls, cost, unusable replies and median latency.
- **Guard:** false fires per flight. More than +0.3 per flight over the arm compared is reported as a cost against
  any gain.
- **Safety:** crashes, near misses and the closest approach between drones in every flight.

## What the development valleys showed (3000-3011, twelve valleys, not held out)

Twelve valleys per arm and size. Nearest and rule were flown with the code of this commit; none with earlier code
that flies it identically (a none valley flown again with this commit matched in every measure but timing); the
Nemotron flights were flown before the last changes below (items 4 to 6) and are shown for what they are.

| Drones | Arm | Found | Found smouldering | Across-valley fire | Under the overhang | False fires per flight | Checks per flight (on a fire's strike; of those, fires not yet found) |
|---|---|---|---|---|---|---|---|
| 8 | none (F4-N4) | 85% | 55% | 9 of 12 | 7 of 12 | 0.92 | - |
| 8 | nearest | 90% | 58% | 11 of 12 | 7 of 12 | 0.67 | 4.8 (44 of 57; 9) |
| 8 | rule | 90% | 60% | 12 of 12 | 6 of 12 | 0.75 | 4.2 (17 of 50; 7) |
| 8 | nemotron (earlier code) | 88% | 58% | 12 of 12 | 6 of 12 | 0.67 | 3.9 (15 of 47; 5) |
| 16 | none (F4-N6-S6) | 88% | 58% | 12 of 12 | 6 of 12 | 0.67 | - |
| 16 | nearest | 90% | 60% | 12 of 12 | 6 of 12 | 0.33 | 4.8 (45 of 58; 7) |
| 16 | rule | 90% | 60% | 12 of 12 | 6 of 12 | 0.33 | 4.0 (13 of 48; 5) |

Paired by valley, rule minus none was +0.25 fires per flight at 8 drones (95% interval -0.08 to +0.58) and +0.08
at 16 (-0.17 to +0.33); rule minus nearest was 0 at both sizes (-0.25 to +0.25). Nemotron made 51 calls over its
12 flights: one reply was unusable, the cost was USD 0.073 and the median call took 11.7 s; it never answered
"stop". No flight crashed or logged a near miss, and no check was cut off by the patrol's end; the closest
approach between two drones was 6.6 to 8.1 m in team flights with the final code, against 10.9 to 11.2 m with no
team. Twelve valleys are too few to read differences this small; the held-out run has twenty.

Predictions written now: nemotron and rule find the same number of fires within the noise at 8 drones (the
interval spans zero), and the same while smouldering. With a team, the fire across the valley, which F4-N4 does not
patrol, is found more often than with none at 8 drones, but fires found in all are within the noise of none at
both sizes. Nearest spends the most checks on strikes of fires already found and also reaches the most fires not
yet found; nemotron and rule spend most of their checks on strikes that started nothing. No arm finds the fire
under the overhang more often than none.

## Changed during development (all before this run)

1. A first pass with a 3-drone team was flown on the development valleys and discarded: review found bugs in it
   (in when a look ended and in how refused picks were handled). The team became 4 drones, so it can make a sound
   smoke fix (three drones' bearings) on its own.
2. The first triage rule put strikes on unpatrolled ground, and ground no thermal camera had cleared, first; the
   first prompt nudged the model the same way and put strikes near people first. Both spent most of their checks
   on far strikes that started nothing, so both were revised to the versions above.
3. The 40 s lookahead was added, so the model's thinking time overlaps the look instead of idling the team.
4. After the last development flights the closest approach between two drones was 2.3 m in team flights, against
   about 11 m with no team (no near misses were logged). Traces showed three causes, all fixed: the team's drones
   joined a new square one by one, across each other's paths (they now join together); they chased a square
   that was already moving along its circuit (it now waits at its start until they join); and where the team
   crossed a squad at a forest edge, the followed surface stepped by about 15 m and both drones lagged it, the
   team climbing and the squad descending, until they were at the same height (the team now flies 20 m above the
   squads instead of 10; a smoothed surface for the team was tried first and dropped after review, because on
   steep slopes it lifted the team past 120 m above the ground and held it above the ordered height in an
   airspace hold). A re-check then showed 3.4 m right after take-off, when the first square lay beside the team's
   starting square: the drones now move to their points in step, by the assignment with the least total squared
   distance, and join once each has held its point 3 s. The development flights of nearest and rule were flown
   again with these changes (above); the Nemotron development flights were flown before them and not again, to
   save the budget.
5. A review before this run found that the table's seen_pct counted only ground inside the area squads' block, so
   a strike outside it always showed 0% seen, even after the team had looked there. The team now keeps its own
   record of the ground any thermal camera has seen over the whole valley. Only Nemotron reads that column (the
   rule and nearest do not); the Nemotron development flights read the old column.
6. The same review found that a strike picked during the lookahead was offered without counting the rest of the
   current look, so a last check could be cut off by the patrol's end, and that a pick held through an airspace
   hold was applied without checking the time again. The time test now counts the rest of the look (with a
   margin for slow corners), a pick that can no longer be finished in time is dropped and made again
   (strike_picks_expired), and moves of the team by hand are refused.

## Seeds

Held out: 9300-9319, reserved for this job on 22 October and never flown before. Earlier held-out sets are not used.

## Commands

    PYTHONPATH=src python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8,16 \
        --arms none,nearest,rule --out results/strike_checks/runs_rules.jsonl --workers 3
    HALO_LEDGER=<the project's spend ledger> HALO_BUDGET_USD=8.71 PYTHONPATH=src \
        python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8 --arms nemotron \
        --cache results/strike_checks/nemotron_cache.jsonl --out results/strike_checks/runs_nemotron.jsonl --workers 1
    cat results/strike_checks/runs_rules.jsonl results/strike_checks/runs_nemotron.jsonl > results/strike_checks/runs.jsonl
    PYTHONPATH=src python3 tools/wildfire_strike_check_report.py results/strike_checks/runs.jsonl \
        --out results/strike_checks/summary.json

The Nemotron flights replay without a key from the recorded replies: the same command with `--cache-mode replay`
and a new `--out` file (the sweep skips flights already in its output).
