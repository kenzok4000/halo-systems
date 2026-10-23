# Results

Everything here was produced by the commands in [docs/REPRODUCE.md](../docs/REPRODUCE.md); the wildfire scout's are in the [README](../README.md#reproducing-the-results), the wildfire valley's in [its section](#wildfire-valley-held-out-seeds-4000-4019-run-once) below, and the later folders' with their entries under [Later results](#later-results). The reports are generated from the rows files next to them, so a number in a report can be traced to the flights behind it.

## Held-out seed sets so far

| Seeds | Used for | Results |
|---|---|---|
| 1000-1019 | The nine scenarios, the ablation, GPS, scaling, the wildfire scout and the replay check | this folder's top level, `ablation/`, `gps/`, `scaling/`, `wildfire/`, `replay_check/` |
| 4000-4019 | The wildfire valley, flown once at 9c50f70. A sweep of an earlier version was stopped part-way and its rows set aside unread | `wildfire_valley/` |
| 5000-5019 | A first held-out attempt at which way the fire runs, stopped after eight one-drone flights; its rows were set aside unread | not in the repository |
| 6000-6019 | Which way the fire runs, flown once at 5c2c9df; reviews then found faults in that code, so the results come from 7000-7019 | not in the repository |
| 7000-7019 | Which way the fire runs, flown once at d4efe78 | `wildfire_valley_7000/` |
| 8000-8019 | Squads per area | `wildfire_valley_squads/` |
| 9000-9019 | Public alerts under pre-signed rules (T3); their pre-signed, automatic flights also feed the first crew (T4) | `wildfire_alerts/`, `wildfire_initial_attack/` |
| 9020-9039 | The first crew (T4) | `wildfire_initial_attack/` |
| 9040-9087 | Valley compile: the strike list of each held-out request's valley, no flights | `valley_eval/` |
| 9100-9119, 9150-9169 | Valley test engineer (T5b); 9120-9149 and 9170-9199 stay unused | `valley_stress/` |
| 9200-9219 | Missing hiker (T11) | `hiker_search/` |
| 9300-9319 | Strike checks: which lightning strike a check team looks at next | `strike_checks/` |
| 9400-9419 | The entry's alert design (pre-signed rules, smoulders too) against today's rule and the spreading-only rules | `wildfire_alerts_smoulders/` |

Development seeds, not held out: 0-9 for tuning, and 2000-2011 and 3000-3019 for the valley and the later studies ([docs/REPRODUCE.md](../docs/REPRODUCE.md#seeds)). The aircraft-inbound demo (`airspace_hold/`) and the compute table (`compute/`) fly only these seeds.

## Held-out seeds (1000-1019), run once

| File | What it is | Commit |
|---|---|---|
| `REPORT.md`, `summary.json`, `*.png` | Main report: nine scenarios, original and improved controllers, rule supervisor and Nemotron | see below |
| `runs.jsonl` | One row per flight for the main report | |
| `nemotron_cache.jsonl` | Every Nemotron call in the held-out runs, for exact replay without a key | 890b270 to 362c3a6 |
| `nemotron_calls.jsonl.gz` | The call log: latency, attempts, tokens, estimated cost and request id per call | 890b270 to 362c3a6 |
| `mission_eval_*.json`, `mission_calls.jsonl.gz` | Mission compiler on the held-out prompts, per model, against the regex baseline | 362c3a6 |
| `ablation/` | The improved controller with one technique removed at a time, with the rule supervisor | 0456945, and the safety filter on the other five scenarios later with the same simulator and controllers |
| `gps/` | GPS error scaled by 0.5, 1, 2 and 3 on forest_trail and gusty_day | 0456945 |
| `scaling/` | 8, 16, 32 and 64 drones on open_transit and motor_failures | 0456945 |
| `wildfire/` | The wildfire scout (fixed hotspots that do not spread) flown with 1, 4 and 16 drones (improved controller, rule supervisor): hotspots found, time to find them, area covered; the README's figure is drawn from the same rows by `tools/make_figures.py` | 1553df9 |
| `replay_check/runs.jsonl` | 24 held-out Nemotron flights (forest trail, degraded radio and low battery, seeds 1000-1003, Nano and Super) replayed from `nemotron_cache.jsonl` with no key; every metric matches the original rows apart from compute time | 362c3a6 |

The conventional rows (original controller, improved controller, rule supervisor) were flown at commit 0456945, before the Nemotron setup was chosen; that choice used only the tuning seeds. The Nemotron flights started at 890b270, which records the setup, and two commits (01219ac and 362c3a6) landed while they ran. The mission compiler was scored at 362c3a6. None of the commits from 0456945 to 362c3a6 changed the simulator, controllers, rule supervisor, validator, prompts or mission compiler; they changed the stress-test tools, the report, the dashboard, tests and docs.

## Wildfire valley, held-out seeds (4000-4019), run once

`wildfire_valley/` holds the wildfire valley ([docs/WILDFIRE.md](../docs/WILDFIRE.md)): a mountain valley after dry lightning with five fires per flight, one already spreading in open scrub, one under tree crowns, one under a rock overhang no camera looking down can see, one smouldering in the open and one across the valley outside the survey block. The drones sweep with thermal cameras looking down and watch for smoke with cameras looking ahead. The ground station locates the fires, forecasts their spread and sends alerts zone by zone at Japan-style levels (3 prepare, 4 evacuate, 5 emergency) to the phones in each zone, automatically by fixed rules once each passes the alert check. Apart from the distance-rule ablation, these flights used today's rule (the scenario's default), under which Level 3 also counts smouldering fires as if they took off now. Pre-signed trigger rules came later: public alerts go out automatically only under trigger rules the alerting authority signed in advance, and each located fire also goes to fire dispatch as a card. The entrant chose them as the entry's alert design on 20 October 2026, and on 21 October chose that their Level 3 rule also counts smoulders (`smoulder_alerts`, after the valley test engineer's comparison in `valley_stress/`); their results are in `wildfire_alerts/` ([below](#later-results)). Each flight has 63 simulated phones. Unlike the wildfire scout, the fires spread (Rothermel's surface model on land cover, slope and wind). Smoke and heat detection are probability models, and the fire's effect on its own wind is not modelled. No language model takes part, so there is no call log.

| File | What it is | Commit |
|---|---|---|
| `runs.jsonl` | One row per flight: 80 flights, improved controller with the rule supervisor. Each seed flown with 1, 4 and 16 drones under the forecast alert rule (today's rule), and with 16 drones under the distance rule (the ablation). None failed | 9c50f70 |
| `REPORT.md`, `summary.json` | Fires found, drones against today's ways of finding fires (modelled from cited figures with the benefit of the doubt), the spread forecast against the true spread, warnings to phones, and forecast against distance | 9c50f70 |
| `detection.png` | When each fire was reported and how big it was, by the 16-drone flights and by satellites, AI cameras with a clear view and public calls | 9c50f70 |
| `swarm_size.png` | Fires found, 1-hour forecast overlap, arrival error at phones and phones warned in time, by swarm size, drawn from `summary.json` | 9c50f70 |
| `map.png` | One flight re-flown, seed 4012 with 16 drones: the true spread against the forecast, the fires found and the phones alerted | 9c50f70 |

The valley was built on seeds 2000-2011 and 3000-3019, then flown once on seeds 4000-4019 with the code at 9c50f70. A sweep of an earlier version on seeds 4000-4019 was stopped part-way and its rows set aside unread ([docs/WILDFIRE.md](../docs/WILDFIRE.md#how-results-are-scored)). Up to d652f6e, later commits changed the dashboard, its recording tool and the docs; in the simulator, the scenarios and the three scripts below they changed only comments, descriptions and argument parsing, and the scripts still wrote the same files. Commits since then added options and outputs (which way the fire runs, crew updates, squads, pre-signed rules, dispatch cards) to the same simulator; the sections below say which code flew each later result.

To regenerate, from the repository root:

```bash
PYTHONPATH=src python3 tools/wildfire_valley_sweep.py --out check/wildfire_valley/runs.jsonl   # defaults: --seeds 4000-4019 --sizes 1,4,16 --ablation-sizes 16
PYTHONPATH=src python3 tools/wildfire_valley_report.py check/wildfire_valley/runs.jsonl        # writes REPORT.md and summary.json next to the rows
PYTHONPATH=src python3 tools/wildfire_valley_figures.py --seed 4012 --n 16                       # map.png, swarm_size.png, detection.png
```

The sweep skips rows already in its output, so an interrupted sweep resumes; `--workers` sets the number of processes. At d652f6e its rows matched the committed ones apart from the compute-time fields (`wall_s`, `ctrl_ms_mean`, `ctrl_ms_p95`); later code writes extra fields, and the 80 flights have not been re-flown with it to check the rest. The report script run on the committed rows writes the committed `REPORT.md` and `summary.json` unchanged. The figures script reads and writes `results/wildfire_valley/` itself, so it redraws the committed charts in place: it re-flies one flight for `map.png` (pass `--no-map` to skip it) and draws the other two from that folder's `summary.json` and `runs.jsonl`. Its default seed is 3000, one of the seeds the valley was built on; the committed map uses `--seed 4012`.

## Later results

These came after the wildfire valley above, one folder each. A folder with a `PREREG.md` was planned in writing before its held-out seeds or requests were used, and in a report with a section "written after the run", the tool wrote everything above that section. The Commit column gives the code that flew or made the files, or, where that is not recorded, the commit that added them. Commands run from the repository root and write into `results/`. The sweeps skip rows already in their output, and the report tools write `REPORT.md` without the sections written after the run, so to check a folder, point the commands at a new one such as `check/`. Only three need a Nebius key (`NEBIUS_API_KEY`), to call Nemotron live: `valley_eval/`, the Nemotron searches in `valley_stress/` and the Nemotron arm of `strike_checks/`. All three replay from their recorded calls without a key. No language model takes part in any alert, card or crew brief in the valley, or in flight control; the one flight decision a model can make is the optional check team's next strike in `strike_checks/`, and each pick passes the safety validator.

### Which way the fire runs, held-out seeds (7000-7019), run once

`wildfire_valley_7000/` is the wildfire valley flown once more, on fresh seeds with the code at d4efe78, to score the briefs that tell fire crews which way each fire's head runs ([docs/WILDFIRE.md](../docs/WILDFIRE.md#which-way-the-fire-runs)). The flights are those of `wildfire_valley/` (1, 4 and 16 drones under today's rule, and 16 under the distance rule), so the report repeats the other measures on this second set. Seeds 5000-5019 and 6000-6019 were spent on this question first ([above](#held-out-seed-sets-so-far)).

| File | What it is | Commit |
|---|---|---|
| `runs.jsonl`, `REPORT.md`, `summary.json` | 80 flights and their report, with a section on which way the head runs | d4efe78 (added in 96b9444) |
| `direction.png` | Which way the head runs: the briefs against two simple rules (straight downwind, straight upslope) | 96b9444 |
| `map.png`, `swarm_size.png`, `detection.png` | As in `wildfire_valley/`; the map is seed 7006 with 16 drones and the last brief's head arrows and cones | 96b9444 |

```bash
PYTHONPATH=src python3 tools/wildfire_valley_sweep.py --seeds 7000-7019 --out results/wildfire_valley_7000/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_valley_report.py results/wildfire_valley_7000/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_valley_figures.py --seed 7006 --n 16 --brief --out results/wildfire_valley_7000
```

### Spread timing, no new flights

`wildfire_spread_timing/` splits the committed held-out rows of `wildfire_valley/` (seeds 4000-4019) and `wildfire_valley_7000/` (seeds 7000-7019) by when each fire starts to spread, to show how much of the drones' lead over today's methods comes from the smouldering phase. Nothing was flown for it, and the bins were picked after those results were known.

| File | What it is | Commit |
|---|---|---|
| `REPORT.md`, `summary.json`, `timing.png` | Who reported each fire first, the drones or today's methods, by when the fire starts to spread | b57f903 |

```bash
PYTHONPATH=src python3 tools/wildfire_spread_timing.py
```

### Public alerts under pre-signed rules (T3), held-out seeds (9000-9019), run once

`wildfire_alerts/` flies each of 20 fresh valleys with 16 drones (improved+rule) under three alert policies: today's rule, automatic; pre-signed rules, automatic; and pre-signed rules with an official's one-tap approval. The flight is the same in all three; only the alerts differ. Flown from a checkout of the commit that adds `PREREG.md`.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan, measures and predictions, written before the seeds were flown | 6cb093f |
| `runs.jsonl` | One row per flight: 60 flights (20 valleys, three policies) | 6cb093f (added in 5f693a6) |
| `REPORT.md`, `summary.json` | Public alerts, phones warned in time and cards to fire dispatch, paired against today's rule | 6cb093f (added in 5f693a6) |

```bash
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9000-9019 --out results/wildfire_alerts/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts/runs.jsonl
```

### The entry's alert design, held-out seeds (9400-9419), run once

`wildfire_alerts_smoulders/` flies each of 20 fresh valleys with 16 drones (improved+rule) under three alert policies: today's rule, automatic; the spreading-only pre-signed rules, automatic; and the entry's design, the pre-signed rules with smoulders too, automatic. The flight is the same in all three; only the alerts and cards differ. Under the entry's design the public alerts were the same as today's rule's in every valley, alert for alert (140 alerts, 561 of 588 reached phones warned in time, a median lead of 116 minutes), and the dispatch card counts (cards, and cards for false fires) matched the spreading-only rules' in every valley (101 cards, 10 for false fires), as `PREREG.md` predicted. Flown from a checkout of the commit that adds `PREREG.md`. `--report` regenerates the tables and the alert-for-alert section of `REPORT.md`; the "What it shows" section at its end is written by hand after the run, as in `wildfire_alerts/`.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan, measures and predictions, written before the seeds were flown | 4bf9890 |
| `runs.jsonl` | One row per flight: 60 flights (20 valleys, three policies) | 4bf9890 (added in 153afd6) |
| `REPORT.md`, `summary.json` | Public alerts, phones warned in time and cards to fire dispatch, paired against today's rule, and the alert-for-alert comparison | 4bf9890 (added in 153afd6) |

```bash
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9400-9419 --arms today,presigned,presigned-smoulders \
    --out results/wildfire_alerts_smoulders/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts_smoulders/runs.jsonl
```

### First crew on scene (T4), held-out seeds (9000-9039), run once

`wildfire_initial_attack/` models when the first crew reaches each true fire, from today's reports alone or with the drones' cards to fire dispatch added, and how many fires escape initial attack. The flights are 16 drones under pre-signed rules, automatic: seeds 9000-9019 are T3's pre-signed, automatic arm, and 9020-9039 are flown here. The model and the source of each number are in the docstring of `tools/wildfire_initial_attack.py`. Flown and modelled from a checkout of the commit that adds `PREREG.md`.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan, measures and predictions | 6cb093f |
| `flights.jsonl` | The 20 flights on seeds 9020-9039 | 6cb093f (added in 5f693a6) |
| `truth.json.gz` | Each seed's true fires run on to 12 h, and today's report times | 6cb093f (added in 5f693a6) |
| `fires.jsonl` | One row per true fire (200): report times, the crew's response time, and the outcome with today's reports and with the drones' | 6cb093f (added in 5f693a6) |
| `REPORT.md`, `summary.json` | The first crew on scene while the fire smoulders, escapes, and the sensitivity rows | 6cb093f (added in 5f693a6) |

```bash
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9000-9019 --out results/wildfire_alerts/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_initial_attack.py fly --seeds 9020-9039 \
    --out results/wildfire_initial_attack/flights.jsonl
PYTHONPATH=src python3 tools/wildfire_initial_attack.py model \
    --flights results/wildfire_alerts/runs.jsonl,results/wildfire_initial_attack/flights.jsonl \
    --out results/wildfire_initial_attack
```

### Squads per area, held-out seeds (8000-8019), run once

`wildfire_valley_squads/` flies the same drones three ways over each valley: today's single sweep of the floor (A), one squad patrolling the floor (B), and squads per area over the floor and both slopes (D), with 4, 8, 16, 32 and 64 drones. Controller and supervisor improved+fixed, today's alert rule, and the legacy ground station only (a tighter merge tried on the development seeds changed no result, so it was dropped before this run). Flown from a checkout of the commit that adds `PREREG.md`.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan, measures and predictions | 994550a |
| `runs.jsonl` | One row per flight: 300 flights | 994550a (added in d7e8f0d) |
| `REPORT.md`, `summary.json`, `by_size.csv`, `squads.png` | Fires found by their own evidence, found while smouldering, the fire across the valley and false fires, by arm and size, paired by valley | d7e8f0d |

```bash
PYTHONPATH=src python3 tools/wildfire_valley_squads_sweep.py --seeds 8000-8019 --arms A,B,D \
    --sizes 4,8,16,32,64 --stations legacy --out results/wildfire_valley_squads/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_valley_squads_report.py --runs results/wildfire_valley_squads/runs.jsonl
```

### Valley compile (T5a), held-out requests on seeds 9040-9087, scored once

`valley_eval/` scores the valley compiler: Nemotron 3 Nano turns a job in plain words, plus the night's lightning strike list, into a patrol plan (the model writes a short spec; code applies it to the strike list and builds the plan). 48 held-out requests (ids 101-148), each on its own valley seed (9040-9087, strike lists only, no flights), were scored once at 994550a against a regex baseline, with the 16 tuning requests beside them.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan, written before Nemotron saw any held-out request | 994550a |
| `REPORT.md` | The results, with reasoning on and off, against the baseline, written by hand from the JSON files | 6cb093f (scores from 994550a) |
| `nano_think.json`, `nano.json` | Every request with the gold reading, each scorer's spec and its score; reasoning on and off | 994550a (added in 6cb093f) |
| `calls.jsonl.gz` | Every call: request, reply, tokens, cost and latency | 994550a (added in 6cb093f) |
| `heldout_provenance.json` | How each held-out request was written and checked, with every reading | 994550a |
| `../valley_compile_cache.jsonl` | The recorded replies, so the dashboard and `valley-eval --replay` run without a key and give the same answers; also the filmed request on seed 4012 | added in 6cb093f |

Without a key, from the recorded replies (the same without `--think` for `nano.json`); `PREREG.md` gives the live command, which needs a key:

```bash
mkdir -p check
PYTHONPATH=src python3 -m halo_systems.cli valley-eval --sets heldout,tuning --think --replay \
    --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B --cache results/valley_compile_cache.jsonl --out check/nano_think.json
```

### Valley test engineer (T5b), held-out seeds (9100-9119 and 9150-9169)

In `valley_stress/`, three searchers (random, corner and Nemotron 3 Nano) look for conditions of the day under which the valley's ground station fails. The system under test is today's flight (improved controller, rule supervisor) with pre-signed rules, automatic. Each searcher runs two searches of 20 tests on the same 40 valleys; the drawn day (the valley's own conditions) on the same valleys is the control. Plan and tools in 5f693a6, flown from a checkout of it.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan, the failure definitions, the development results and the spend cap | 5f693a6 |
| `random_0.json`, `random_1.json`, `corners_0.json`, `corners_1.json`, `nemotron_0.json`, `nemotron_1.json` | Each search: every test's conditions and outcome, and Nemotron's reason for each of its tests | 5f693a6 |
| `drawn_day.json` | The control: the drawn day on the same 40 valleys | 5f693a6 |
| `calls_cache.jsonl` | Every Nemotron reply, and every call that failed, for replay without a key | 5f693a6 |
| `recheck_forecast.json` | Every failing test re-flown under the pre-signed rules and under today's rule, paired | 5f693a6 |
| `REPORT.md`, `summary.json` | The failures each searcher's conditions caused, every failing test and the re-flights | 5f693a6 |
| `drawn_day_both_rules.json` | Added after the run by `tools/wildfire_valley_both_rules.py`: all 40 drawn-day valleys under both rules, paired | 5f693a6 |

```bash
PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher random --search 0 --out results/valley_stress/random_0.json   # and --search 1; the same for corners
HALO_BUDGET_USD=8.73 PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher nemotron --search 0 \
    --cache results/valley_stress/calls_cache.jsonl --out results/valley_stress/nemotron_0.json   # and --search 1; needs a key, or add --replay to answer from the recorded calls
PYTHONPATH=src python3 tools/wildfire_valley_stress.py drawn-day --out results/valley_stress/drawn_day.json
PYTHONPATH=src python3 tools/wildfire_valley_stress.py recheck results/valley_stress/{random,corners,nemotron}_?.json \
    results/valley_stress/drawn_day.json --out results/valley_stress/recheck_forecast.json
PYTHONPATH=src python3 tools/wildfire_valley_stress.py report results/valley_stress/{random,corners,nemotron}_?.json \
    results/valley_stress/drawn_day.json --recheck results/valley_stress/recheck_forecast.json \
    --out results/valley_stress/REPORT.md
env -u NEBIUS_API_KEY PYTHONPATH=src python3 tools/wildfire_valley_both_rules.py --out results/valley_stress/drawn_day_both_rules.json   # added after the run; reads drawn_day.json
```

### Missing hiker (T11), held-out seeds (9200-9219), run once

`hiker_search/` is search and rescue: one sweep of a 400 x 250 m meadow and forest edge for one missing person, with 1, 4 and 16 drones (today's controller and the rule supervisor), and one drone with the planned speed held (no supervisor). Flown from 5f693a6, the commit that adds `PREREG.md`.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan and the development results (seeds 2000-2005) | 5f693a6 |
| `runs.jsonl` | One row per flight: 80 flights | 5f693a6 (added in 9d0c497) |
| `REPORT.md` | People found, time to find, sweeps completed and speed, by swarm size | 5f693a6 (added in 9d0c497) |
| `film.json` | The Mission tab's hiker request flown on seeds 0-9 to choose a seed for filming; not part of the measurement | 5f693a6 (added in 9d0c497) |

```bash
PYTHONPATH=src python3 tools/hiker_search_sweep.py fly --seeds 9200-9219 --out results/hiker_search/runs.jsonl
PYTHONPATH=src python3 tools/hiker_search_sweep.py report --runs results/hiker_search/runs.jsonl   # REPORT.md and summary.json next to the rows; summary.json is not committed
PYTHONPATH=src python3 tools/hiker_search_sweep.py film --seeds 0-9 --out results/hiker_search/film.json
```

### Strike checks, held-out seeds (9300-9319), run once

`strike_checks/` adds an optional check team of 4 drones that flies to last night's lightning strikes one at a time while the area squads patrol, and compares four ways to fly the same drones for 12 minutes: no team, a team taking the nearest strike first, a team picking by a hand-written triage rule, and a team whose next strike Nemotron 3 Nano picks (8 drones only, to keep within the spend the entrant approved). Every pick passes the safety validator. Controller and supervisor improved+fixed, today's alert rule, report_v2 scoring. Flown from a checkout of ca01f9e, the commit that adds `PREREG.md`.

| File | What it is | Commit |
|---|---|---|
| `PREREG.md` | The plan, the predictions, the development results (seeds 3000-3011) and what changed during development | ca01f9e |
| `runs.jsonl` | One row per flight: 140 flights (no team, nearest and rule at 8 and 16 drones; Nemotron at 8) | ca01f9e (added in f1cc3f8) |
| `nemotron_cache.jsonl` | Every Nemotron reply, for replay without a key | ca01f9e (added in f1cc3f8) |
| `REPORT.md`, `summary.json` | Fires found and found smouldering, the overhang and across-valley fires, false fires, checks, phones warned, safety and the model's calls, by arm and size, paired by valley | ca01f9e (added in f1cc3f8) |

```bash
PYTHONPATH=src python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8,16 \
    --arms none,nearest,rule --out results/strike_checks/runs_rules.jsonl --workers 3
PYTHONPATH=src python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8 --arms nemotron \
    --cache results/strike_checks/nemotron_cache.jsonl --cache-mode replay --out results/strike_checks/runs_nemotron.jsonl
cat results/strike_checks/runs_rules.jsonl results/strike_checks/runs_nemotron.jsonl > results/strike_checks/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_strike_check_report.py results/strike_checks/runs.jsonl --out results/strike_checks/summary.json
```

### Aircraft inbound (T10), development seed 3000, a demonstration

`airspace_hold/` flies development seed 3000 twice with 16 drones (improved+rule): without a hold, and with the operator's "aircraft inbound" hold, ordered at 90 s and resumed 5 minutes later. One seed is a demonstration of the control, not a measurement.

| File | What it is | Commit |
|---|---|---|
| `REPORT.md`, `summary.json` | The orders in the validator's log, how soon every drone was stopped at the hold altitude, the charge used while holding, and the sweep and fires with and without the hold | 5392c8d |

```bash
PYTHONPATH=src python3 tools/airspace_hold_demo.py --out results/airspace_hold
```

### Where each loop runs (T9), development valleys 3000-3003

`compute/` times the controller, the rule supervisor, the station and the spread forecast on development valleys 3000-3003 with 16 drones, flown one at a time. It takes the controller's time with 1, 4 and 16 drones from the committed sweep (seeds 4000-4019) and the Token Factory latencies from `valley_eval/`, and shows what happens when the cloud link drops, with no key and nothing spent. The timings are from the development machine (x86_64, 4 cores), not a drone or a Jetson.

| File | What it is | Commit |
|---|---|---|
| `REPORT.md`, `compute.json` | Where each loop runs, how often, what it costs, and the link-drop check | 5392c8d |

```bash
PYTHONPATH=src python3 tools/compute_table.py --out results/compute
```

## Patrol cost (an estimate)

`patrol_cost/` holds what a patrol day would cost by squad size, against Alberta's patrol flights and the suppression cost of an escaped fire: `model.json` and `TABLES.md`, written by `python3 tools/patrol_cost.py` (no key needed; `--charts` also draws `docs/figures/patrol-*.png`). Its inputs are the scout's build sheet (`hardware/`), the wildfire scout's scan rate and the valley rows above, and the cited figures explained in [docs/PATROL_COST.md](../docs/PATROL_COST.md). It flies nothing, and every cost in it is an estimate.

## Stress-test search

`STRESS.md` compares the searchers. `stress/` holds every search (5 seeds × 40 flights per searcher) with each test's conditions, reason and outcome; `stress/no_safety_filter/` holds the same 1,000 tests re-flown without the safety filter. The system under test is the improved controller with the rule supervisor, unchanged since commit 3a65405, where the random and CEM searches ran. The Nemotron searches ran while commits 955869f to 0456945 landed, which changed the supervisor and mission prompts and the report but not the controller, the rules or the test engineer. The corner search and the re-flights ran later on the same controller and rules.

## Tuning history

`tuning/` keeps the three live tuning rounds on the tuning seeds, including the failures that led to fixes:

| Round | Commit | Seeds | Notes |
|---|---|---|---|
| 1 | 29179cc | 0-4 | 30 of 105 rows failed on a torn line in the shared call cache (fixed in be1cde5); the failed rows are kept with their error. `calls.jsonl.gz` is the round's call log, slow Super calls included |
| 2 | 48c448f | 0-4 | Status report states the corridor and energy checks in words; whole-call deadline |
| 3 | 85ddde6 | 5-9 | Final prompt; picked the Nemotron setup recorded in [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). `conventional_runs.jsonl` holds the rule and fixed-plan flights on the same seeds |
