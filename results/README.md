# Results

Everything here was produced by the commands in [docs/REPRODUCE.md](../docs/REPRODUCE.md); the wildfire scout's are in the [README](../README.md#reproducing-the-results), and the wildfire valley's in [its section](#wildfire-valley-held-out-seeds-4000-4019-run-once) below. The reports are generated from the rows files next to them, so a number in a report can be traced to the flights behind it.

## Held-out seeds (1000-1019), run once

| File | What it is | Commit |
|---|---|---|
| `REPORT.md`, `summary.json`, `*.png` | Main report: nine scenarios, original and improved controllers, rule supervisor and Nemotron | see below |
| `runs.jsonl` | One row per flight for the main report | |
| `nemotron_cache.jsonl` | Every Nemotron call in the held-out runs, for exact replay without a key | dccae5b to 2ec7c41 |
| `nemotron_calls.jsonl.gz` | The call log: latency, attempts, tokens, estimated cost and request id per call | dccae5b to 2ec7c41 |
| `mission_eval_*.json`, `mission_calls.jsonl.gz` | Mission compiler on the held-out prompts, per model, against the regex baseline | 2ec7c41 |
| `ablation/` | The improved controller with one technique removed at a time, with the rule supervisor | fbdf402, and the safety filter on the other five scenarios later with the same simulator and controllers |
| `gps/` | GPS error scaled by 0.5, 1, 2 and 3 on forest_trail and gusty_day | fbdf402 |
| `scaling/` | 8, 16, 32 and 64 drones on open_transit and motor_failures | fbdf402 |
| `wildfire/` | The wildfire scout (fixed hotspots that do not spread) flown with 1, 4 and 16 drones (improved controller, rule supervisor): hotspots found, time to find them, area covered; `swarm_size.png` is the report's own chart; the README's figure is drawn from the same rows by `tools/make_figures.py` | 9e9b14a |
| `replay_check/runs.jsonl` | 24 held-out Nemotron flights (forest trail, degraded radio and low battery, seeds 1000-1003, Nano and Super) replayed from `nemotron_cache.jsonl` with no key; every metric matches the original rows apart from compute time | 2ec7c41 |

The conventional rows (original controller, improved controller, rule supervisor) were flown at commit fbdf402, before the Nemotron setup was chosen; that choice used only the tuning seeds. The Nemotron flights started at dccae5b, which records the setup, and two commits (7bddbc3 and 2ec7c41) landed while they ran. The mission compiler was scored at 2ec7c41. None of the commits from fbdf402 to 2ec7c41 changed the simulator, controllers, rule supervisor, validator, prompts or mission compiler; they changed the stress-test tools, the report, the dashboard, tests and docs.

## Wildfire valley, held-out seeds (4000-4019), run once

`wildfire_valley/` holds the wildfire valley ([docs/WILDFIRE.md](../docs/WILDFIRE.md)): a mountain valley after dry lightning with five fires per flight, one already spreading in open scrub, one under tree crowns, one under a rock overhang no camera looking down can see, one smouldering in the open and one across the valley outside the survey block. The drones sweep with thermal cameras looking down and watch for smoke with cameras looking ahead. The ground station locates the fires, forecasts their spread and sends alerts zone by zone at Japan-style levels (3 prepare, 4 evacuate, 5 emergency) to the phones in each zone, automatically by fixed rules once each passes the alert check. Each flight has 63 simulated phones. Unlike the wildfire scout, the fires spread (Rothermel's surface model on land cover, slope and wind). Smoke and heat detection are probability models, and the fire's effect on its own wind is not modelled. No language model takes part, so there is no call log.

| File | What it is | Commit |
|---|---|---|
| `runs.jsonl` | One row per flight: 80 flights, improved controller with the rule supervisor. Each seed flown with 1, 4 and 16 drones under the forecast alert rule, and with 16 drones under the distance rule (the ablation). None failed | 4d892bd |
| `REPORT.md`, `summary.json` | Fires found, drones against today's ways of finding fires (modelled from cited figures with the benefit of the doubt), the spread forecast against the true spread, warnings to phones, and forecast against distance | 4d892bd |
| `detection.png` | When each fire was reported and how big it was, by the 16-drone flights and by satellites, AI cameras with a clear view and public calls | 4d892bd |
| `swarm_size.png` | Fires found, 1-hour forecast overlap, arrival error at phones and phones warned in time, by swarm size, drawn from `summary.json` | 4d892bd |
| `map.png` | One flight re-flown, seed 4012 with 16 drones: the true spread against the forecast, the fires found and the phones alerted | 4d892bd |

The valley was built on seeds 2000-2011 and 3000-3019, then flown once on seeds 4000-4019 with the code at 4d892bd. A sweep of an earlier version on seeds 4000-4019 was stopped part-way and its rows set aside unread ([docs/WILDFIRE.md](../docs/WILDFIRE.md#how-results-are-scored)). Later commits changed the dashboard, its recording tool and the docs; in the simulator, the scenarios and the three scripts below they changed only comments, descriptions and argument parsing, and the scripts still write the same files.

To regenerate, from the repository root:

```bash
PYTHONPATH=src python3 tools/wildfire_valley_sweep.py --out check/wildfire_valley/runs.jsonl   # defaults: --seeds 4000-4019 --sizes 1,4,16 --ablation-sizes 16
PYTHONPATH=src python3 tools/wildfire_valley_report.py check/wildfire_valley/runs.jsonl        # writes REPORT.md and summary.json next to the rows
PYTHONPATH=src python3 tools/wildfire_valley_figures.py --seed 4012 --n 16                       # map.png, swarm_size.png, detection.png
```

The sweep skips rows already in its output, so an interrupted sweep resumes; `--workers` sets the number of processes. Its rows match the committed ones apart from the compute-time fields (`wall_s`, `ctrl_ms_mean`, `ctrl_ms_p95`), and the report script run on the committed rows writes the committed `REPORT.md` and `summary.json` unchanged. The figures script reads and writes `results/wildfire_valley/` itself, so it redraws the committed charts in place: it re-flies one flight for `map.png` (pass `--no-map` to skip it) and draws the other two from that folder's `summary.json` and `runs.jsonl`. Its default seed is 3000, one of the seeds the valley was built on; the committed map uses `--seed 4012`.

## Stress-test search

`STRESS.md` compares the searchers. `stress/` holds every search (5 seeds × 40 flights per searcher) with each test's conditions, reason and outcome; `stress/no_safety_filter/` holds the same 1,000 tests re-flown without the safety filter. The system under test is the improved controller with the rule supervisor, unchanged since commit 191a11a, where the random and CEM searches ran. The Nemotron searches ran while commits c59fa42 to fbdf402 landed, which changed the supervisor and mission prompts and the report but not the controller, the rules or the test engineer. The corner search and the re-flights ran later on the same controller and rules.

## Tuning history

`tuning/` keeps the three live tuning rounds on the tuning seeds, including the failures that led to fixes:

| Round | Commit | Seeds | Notes |
|---|---|---|---|
| 1 | dc6cf0d | 0-4 | 30 of 105 rows failed on a torn line in the shared call cache (fixed in 0548b83); the failed rows are kept with their error. `calls.jsonl.gz` is the round's call log, slow Super calls included |
| 2 | bcfc174 | 0-4 | Status report states the corridor and energy checks in words; whole-call deadline |
| 3 | eb5ef30 | 5-9 | Final prompt; picked the Nemotron setup recorded in [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). `conventional_runs.jsonl` holds the rule and fixed-plan flights on the same seeds |
