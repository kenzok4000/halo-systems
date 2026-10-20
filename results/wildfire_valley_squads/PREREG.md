# Squads per area: plan for the held-out measurement

Written before seeds 8000-8019 were flown. The code that flies them is the commit that adds this file; the sweep
runs from a separate checkout of that commit, so later edits cannot reach it.

## Question

Kenzo asked for bigger squads, sized to their areas, that find more fires. Does flying the swarm as squads, one per
area of the valley (the floor and both slopes), find more of the valley's fires than the same drones flown as
today's single sweep of the floor, or as one squad patrolling the floor alone? And how does that change with the
number of drones?

## Arms (each valley flown once per arm and size)

- **A**, today's flight: one line abreast sweeping the valley floor once (the committed results' flight).
- **B**, floor patrol: one squad patrolling the valley floor for 12 minutes ("auto:F").
- **D**, squads per area: the default split ("auto", `control.squads.allocate`) over the floor and both slopes,
  each squad patrolling its own area for 12 minutes.

Sizes 4, 8, 16, 32 and 64 drones. Where two arms fly identical flights (the same split; at 4 drones B and D are
both the floor's single squad), the flight is flown once and its row is shared. Drones that do not fit across an
area stay in reserve and do not fly. Controller and supervisor: improved+fixed, as the earlier sweeps. Ground
station: legacy (the committed one). Alerts: the committed forecast rule.

## Measures

- **Primary:** fires found by their own evidence per flight (report_v2: a fix from the fire's own hotspot, or the
  majority of a smoke fix's bearings from its own plume), paired by valley, **D minus B** at each size, with a
  95% bootstrap interval over valleys (10,000 resamples).
- **Co-primary:** the same for fires found while still smouldering.
- **Secondary:** D minus A on both; the across-valley fire found (the fire type B and A cannot reach); time for
  each area to be seen once; the nearest-fire rule of the committed reports (`fires_located`) beside the primary.
- **Guard:** false fires per flight. A difference of more than +0.3 false fires per flight for D over B at a size
  is reported as a cost against any gain there.
- **Safety:** crashes, drones retired and near misses in every flight.

## What the development seeds showed (3000-3003, four valleys, not held out)

D found 90% of fires at 8 drones and above, against 70% for B at 8 drones and 90% at 16 and above. A, the single
line, found 85% at 4 to 16 drones and 95% at 32 and 64, where its line is wide enough to overfly both slopes. D
found the across-valley fire in every valley from 16 drones (A in half of them at 4 to 16). The fire under the
rock overhang stayed hidden from every arm. No crashes. Twenty fires per arm and size is too few to read the
differences; the held-out run has 100.

Predictions written now: D finds more fires than B at 8 drones and the same from 16 up; D finds the across-valley
fire more often than A up to 16 drones; at 32 and 64 A matches or beats D on fires found, because its line covers
the slopes in one pass; no arm finds the fire under the overhang from above.

## Dropped before this run

A tighter ground-station merge ("tight") was tried on the development seeds. It changed no flight's result, so the
held-out run flies the legacy station only, and the write-up says so.

## Seeds

Held out: 8000-8019, reserved for this on 16 October and never flown before. Earlier held-out sets (4000-4019,
5000-5019, 6000-6019, 7000-7019) are not used here.

## Command

    PYTHONPATH=src python3 tools/wildfire_valley_squads_sweep.py --seeds 8000-8019 --arms A,B,D \
        --sizes 4,8,16,32,64 --stations legacy --out results/wildfire_valley_squads/runs.jsonl --workers 4
    PYTHONPATH=src python3 tools/wildfire_valley_squads_report.py --runs results/wildfire_valley_squads/runs.jsonl
