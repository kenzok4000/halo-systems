# Missing hiker: plan for the held-out search (T11)

Written before any of seeds 9200-9219 was flown. The code is `hiker_search` in `src/halo_systems/eval/scenarios.py`
and `tools/hiker_search_sweep.py` in the commit that adds this file.

## Question

Search and rescue has been "next" in the entry until its own result is measured. How often, and how soon, does a
swarm find a missing person in one sweep of a meadow and forest edge, with 1, 4 and 16 drones?

## Scenario

A 400 x 250 m meadow and forest edge. One person, a heat signature 0.8 m across, lies somewhere in it: in the open
in half the draws, otherwise under 30 to 80% tree crown. The drones fly one sweep line abreast at 20 m with thermal
cameras looking down; lanes are split across the swarm, so fewer drones fly more lanes each. A find needs half a
second of the person in some camera's view, longer when partly hidden. Today's controller (improved) and the rule
supervisor; no language model in the loop. The seed fixes the person's place and cover, the same for every swarm
size.

The rule supervisor may speed a lone drone up to save charge: when the rest of the sweep at the planned 6 m/s would
need more than 90% of the usable charge, it orders the cruise speed that costs least per kilometre, which can be
faster (up to 12 m/s). A faster pass gives a person under tree cover less time in view. So one secondary row flies
1 drone with the planned speed held (today's controller, no supervisor) on the same seeds, to tell the number of
lanes apart from the speed-up.

## Measures

For each swarm size (1, 4 and 16 drones with the rule supervisor), on the same 20 seeds:

- **Primary:** people found in one sweep (of 20).
- **Secondary:** found in the open and under trees; median and 90th-percentile time to find, over all 20 flights
  with a miss counted as never found (nearest rank; "not reached" when it lands on a miss); sweeps completed; the
  median of each flight's mean speed along the sweep path, and the fastest speed any plan ordered; median flight
  time; drones retired at the battery reserve and drones lost.
- **Secondary row:** 1 drone with the planned speed held (no supervisor), on the same 20 seeds, with the same
  measures. Whether one drone at 6 m/s finishes the sweep on one charge, or retires before the end, is reported as
  flown.

No threshold is set in advance; the table is reported as flown.

## What the development seeds showed

Seeds 2000-2005, flown on 20 October (6 searches each with 1, 4 and 16 drones, and the speed-held row):

| Drones | Found | In the open | Under trees | Time to find (median) | 90% found within | Sweeps completed | Mean speed (median) | Flight took (median) |
|---|---|---|---|---|---|---|---|---|
| 1 | 4 of 6 | 2 of 2 | 2 of 4 | 7.5 min | not reached | 6 of 6 | 8.8 m/s | 12.5 min |
| 4 | 6 of 6 | 2 of 2 | 4 of 4 | 2.5 min | 5.7 min | 6 of 6 | 5.9 m/s | 6.2 min |
| 16 | 6 of 6 | 2 of 2 | 4 of 4 | 0.2 min | 2.3 min | 6 of 6 | 5.0 m/s | 3.1 min |
| 1, speed held | 6 of 6 | 2 of 2 | 4 of 4 | 9.4 min | 15.6 min | 0 of 6 | 5.9 m/s | 16.5 min |

With the rule supervisor the lone drone flew at up to 12 m/s and missed two of the four people under tree crowns
(59% and 72% hidden).
With the planned 6 m/s held it found all six, but reached its battery reserve before finishing the sweep every
time (88.5% of the area covered, median), so on another seed the person could lie in the part it never reached.

The first development run gave the time to find over found people only and had neither the speed-held row nor the
speed fields; a code review of the tool, before any held-out flight, added them. Nothing in the flight, the
scenario or the supervisor was changed, and nothing was tuned after these flights.

The Mission tab's hiker request ("Search the 400 by 250 m meadow for a missing hiker with 12 drones line abreast,
8 m apart, at 30 m.") was flown on seeds 0-9 to choose a seed for filming (`film`); those seeds are not part of this
measurement.

## Seeds

9200-9219, reserved on 20 October and never flown before. Development: 2000-2005.

## Commands

    PYTHONPATH=src python3 tools/hiker_search_sweep.py fly --seeds 9200-9219 --out results/hiker_search/runs.jsonl
    PYTHONPATH=src python3 tools/hiker_search_sweep.py report --runs results/hiker_search/runs.jsonl
