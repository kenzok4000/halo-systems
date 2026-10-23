# Reproducing the results

Everything here runs on a laptop with Python 3.10 or newer and numpy. Charts need matplotlib (`pip install -e '.[report]'`). Nothing except the Nemotron runs needs a network connection or a key.

## Seeds

* Seeds 0-9 (`--seeds tuning`) were used while developing and tuning the controllers and the rule supervisor.
* Seeds 1000-1019 (`--seeds heldout`) were not looked at until the final runs. Every headline number outside the wildfire valley uses them.
* The wildfire valley has its own seeds: 2000-2011 and 3000-3019 for development, and 4000-4019 held out and flown once with the final code ([WILDFIRE.md](WILDFIRE.md)).
* Each later result has a held-out set of its own, not used before its run: 7000-7019 for which way the fire runs, 8000-8019 for squads per area, 9000-9019 for the pre-signed alerts (T3), 9000-9039 for the first crew (T4: 9000-9019 are T3's pre-signed, automatic flights and 9020-9039 its own), 9040-9087 for the valley compiler (the strike lists of its 48 held-out requests, no flights), 9100-9119 and 9150-9169 for the valley test engineer (T5b), 9200-9219 for the missing hiker (T11), 9300-9319 for the strike checks, and 9400-9419 for the entry's alert design (pre-signed rules, smoulders too) against today's rule. From 8000-8019 on, each results folder keeps the plan written before the run (`PREREG.md`); the fire-direction set is described in [WILDFIRE.md](WILDFIRE.md).
* All these held-out seeds are spent, and so are 5000-5019 and 6000-6019, used in earlier attempts at the fire-direction measurement ([WILDFIRE.md](WILDFIRE.md)); 9120-9149 and 9170-9199 were reserved with T5b and stay unused. The commands below rerun the spent sets to check the committed results; a new measurement needs fresh seeds. The aircraft-inbound demo (seed 3000) and the compute table (3000-3003) use development seeds.

Each seed fixes the wind, sensor noise, radio losses, obstacle layout and initial positions. Every configuration sees the same conditions for a given seed. That is why the report can compare configurations seed by seed (paired comparisons).

These are the commands that produced every file in `results/`, in the order they ran. `bench` is resumable: it skips rows already in its output file, so an interrupted run can be restarted with the same command. `report` writes `REPORT.md`, `summary.json` and the charts from a rows file.

The committed rows files already hold every flight, so rerunning a command into them does nothing. To check a result, give `--out` a new file and compare its rows with the committed ones: apart from compute-time fields they come out identical. A report drawn from the new file has the same means, counts and paired comparisons, but its other bootstrap intervals can move slightly, because the resampling follows the order of the rows and `bench` writes them in the order the flights finish; `halo report` on the committed rows reproduces the committed `REPORT.md` files exactly.

## Conventional benchmark (no key needed)

```bash
ALL=open_transit,formation_changes,forest_trail,urban_canyon,gusty_day,comms_degraded,motor_failures,low_battery,field_survey
halo bench --scenarios $ALL --configs original+fixed,improved+fixed,improved+rule \
  --seeds heldout --workers 4 --out results/runs.jsonl
```

## Ablations, navigation error and scaling

The improved controller's techniques can be switched off one at a time with `improved-no-<feature>`. The features are reassign, blend, pace, consensus, cbf (the safety filter), filter (the state filter) and feedforward.

```bash
halo bench --scenarios formation_changes,forest_trail,gusty_day,motor_failures \
  --configs improved+rule,improved-no-cbf+rule,improved-no-reassign+rule,improved-no-blend+rule,improved-no-pace+rule,improved-no-consensus+rule,improved-no-filter+rule,improved-no-feedforward+rule \
  --seeds heldout --workers 4 --out results/ablation/runs.jsonl
# the safety filter on the other five scenarios
halo bench --scenarios open_transit,urban_canyon,comms_degraded,low_battery,field_survey \
  --configs improved+rule,improved-no-cbf+rule --seeds heldout --workers 4 --out results/ablation/runs.jsonl
halo report results/ablation/runs.jsonl --out results/ablation

for g in 0.5 1 2 3; do
  halo bench --scenarios forest_trail,gusty_day --configs original+fixed,improved+rule \
    --seeds heldout --gps-scale $g --workers 4 --out results/gps/runs.jsonl
done
halo report results/gps/runs.jsonl --out results/gps

halo bench --scenarios open_transit,motor_failures --configs original+fixed,improved+rule \
  --n 8,16,32,64 --seeds heldout --workers 4 --out results/scaling/runs.jsonl
halo report results/scaling/runs.jsonl --out results/scaling
```

## Stress-test search

The searches attack the improved controller with the rule supervisor. Each spends 40 simulated flights, proposed in batches of 4.

```bash
NANO=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B
for s in 0 1 2 3 4; do
  halo stress --searcher random  --budget 40 --batch 4 --seed $s --workers 4 --out results/stress/random_$s.json
  halo stress --searcher cem     --budget 40 --batch 4 --seed $s --workers 4 --out results/stress/cem_$s.json
  halo stress --searcher corners --budget 40 --seed $s --workers 4 --out results/stress/corners_$s.json
  # these two need a key
  halo stress --searcher nemotron:$NANO       --budget 40 --batch 4 --seed $s --workers 4 \
    --ai-log results/stress/nemotron_calls.jsonl --out results/stress/nemotron_$s.json
  halo stress --searcher nemotron-think:$NANO --budget 40 --batch 4 --seed $s --workers 4 \
    --ai-log results/stress/nemotron_calls.jsonl --out results/stress/nemotron-think_$s.json
done
gzip results/stress/nemotron_calls.jsonl
```

To re-fly a search's tests on another configuration, with the same conditions and seeds (here without the safety filter), and then compare everything:

```bash
for f in results/stress/*_[0-4].json; do
  b=$(basename $f .json)
  halo stress-replay $f --config improved-no-cbf+rule --workers 4 --out results/stress/no_safety_filter/${b}_nocbf.json
done
halo stress-report results/stress/random_*.json results/stress/corners_*.json results/stress/cem_*.json \
  results/stress/nemotron_*.json results/stress/nemotron-think_*.json results/stress/no_safety_filter/*.json \
  --out results/STRESS.md
```

The Nemotron searches cannot be replayed from a cache: the model proposes each batch after seeing the last, so a rerun makes new calls and finds different tests. The saved searches hold every test's conditions, seed, the model's reason and the outcome, and `stress-replay` re-flies them exactly.

## Nemotron runs (need a key)

```bash
export NEBIUS_API_KEY=...             # never in a file you commit
halo doctor --flight              # end-to-end check, writes evidence/latest/
NANO=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B; SUPER=nvidia/nemotron-3-super-120b-a12b
halo bench --scenarios $ALL --configs improved+nemotron:$NANO,improved+nemotron:$SUPER --seeds heldout --workers 4 \
  --cache results/nemotron_cache.jsonl --ai-log results/nemotron_calls.jsonl --out results/runs.jsonl
gzip results/nemotron_calls.jsonl
halo report results/runs.jsonl --out results
halo budget                       # spend so far against the cap
```

Spend is capped by the guard described in [NVIDIA_NEBIUS.md](NVIDIA_NEBIUS.md). `improved+nemotron-think:<model id>` turns reasoning on.

The supervisor runs can be replayed exactly with no key and no cost: run the same `bench` command with `--cache-mode replay` (and a new `--out`). Every call is answered from `results/nemotron_cache.jsonl`, so the flights match the published rows apart from timing fields. The dashboard does the same: with no key, `halo serve` replays `results/nemotron_cache.jsonl` with Nemotron 3 Nano.

`results/replay_check/runs.jsonl` holds 24 of these flights replayed this way, with no key. The rows do not record the exact command; this one flies the same 24 flights:

```bash
NANO=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B; SUPER=nvidia/nemotron-3-super-120b-a12b
env -u NEBIUS_API_KEY halo bench --scenarios forest_trail,low_battery,comms_degraded \
  --configs improved+nemotron:$NANO,improved+nemotron:$SUPER --seeds 1000-1003 --workers 4 \
  --cache results/nemotron_cache.jsonl --cache-mode replay --out results/replay_check/runs.jsonl
```

## Mission compiler (needs a key, except for the baseline)

```bash
halo mission-eval --sets heldout --out mission_eval_baseline.json                    # regex baseline only, no key (not committed)
halo mission-eval --sets heldout --model $NANO         --ai-log results/mission_calls.jsonl --out results/mission_eval_nano.json
halo mission-eval --sets heldout --model $NANO --think --ai-log results/mission_calls.jsonl --out results/mission_eval_nano_think.json
halo mission-eval --sets heldout --model $SUPER        --ai-log results/mission_calls.jsonl --out results/mission_eval_super.json
gzip results/mission_calls.jsonl
```

Each committed file also scores the regex baseline on the same prompts, so the baseline's committed score is inside every `mission_eval_*.json`; the baseline-only command is there for a check without a key. The mission compiler has no replay cache; the call log keeps every request and reply.

The regex baseline and both prompt sets were added in the same first commit, and the baseline has not changed since. The model prompt was tuned on the tuning prompts only; the held-out prompts were first sent to a model in the final evaluation. The same author wrote the prompts, the baseline and the model prompt, so the held-out set is unseen by the model, not by its author.

## Wildfire scout (no key needed)

```bash
halo bench --scenarios wildfire_scout --configs improved+rule --seeds heldout --n 1,4,16 --out results/wildfire/runs.jsonl
halo report results/wildfire/runs.jsonl --out results/wildfire
python3 tools/make_figures.py      # the README's figures, from the committed rows
```

## Wildfire valley (no key needed)

The valley's rows come from a sweep script rather than `bench`, because each row records its alert rule and the ablation flies the distance rule. The sweep skips rows already in its output, so an interrupted sweep resumes.

```bash
PYTHONPATH=src python3 tools/wildfire_valley_sweep.py --out results/wildfire_valley/runs.jsonl   # seeds 4000-4019: 1, 4 and 16 drones, and the distance-rule ablation
PYTHONPATH=src python3 tools/wildfire_valley_report.py results/wildfire_valley/runs.jsonl        # writes REPORT.md and summary.json next to the rows
PYTHONPATH=src python3 tools/wildfire_valley_figures.py --seed 4012 --n 16                       # map.png, swarm_size.png, detection.png
```

The second held-out set, for which way the fire runs, was flown once at d4efe78 with the same sweep (80 flights). The spread-timing breakdown flies nothing: it splits the rows of both held-out sets by when each fire starts to spread, with bins picked after those results were known.

```bash
PYTHONPATH=src python3 tools/wildfire_valley_sweep.py --seeds 7000-7019 --out results/wildfire_valley_7000/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_valley_report.py results/wildfire_valley_7000/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_spread_timing.py      # REPORT.md, summary.json and timing.png in results/wildfire_spread_timing/
```

## Squads per area (no key needed)

Flown once from 994550a, the commit that adds the plan (`results/wildfire_valley_squads/PREREG.md`), with the legacy ground station only. Without `--stations legacy` the sweep also flies the tighter station merge ("tight"), which the plan dropped before the run.

```bash
PYTHONPATH=src python3 tools/wildfire_valley_squads_sweep.py --seeds 8000-8019 --arms A,B,D \
  --sizes 4,8,16,32,64 --stations legacy --out results/wildfire_valley_squads/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_valley_squads_report.py --runs results/wildfire_valley_squads/runs.jsonl   # REPORT.md, summary.json, by_size.csv, squads.png
```

Here and below, the sweeps that fly rows (squads, alerts, the first crew's `fly`, the hiker's `fly`, the strike checks) skip rows already in their output. Each report's section headed "What it shows (written after the run)" was written by hand. The report commands write `REPORT.md` whole, so to check a result, fly into a new `--out` and compare the generated part. The valley test engineer's commands, the hiker's `film`, `valley-eval`, the aircraft-inbound demo and the compute table skip nothing: they fly or score everything again and write their `--out` whole, so give them a new `--out` (or folder) too.

## Valley compiler (needs a key, or replays without one)

Nemotron 3 Nano turns an operator's plain words plus the night's strike list into a patrol spec, and code builds the patrol. It was scored once from 994550a, the commit that froze the prompt, the planner and the baseline, on the 48 held-out requests (ids 101-148, strike lists from seeds 9040-9087), with the 16 tuning requests beside them. Those held-out requests are spent.

```bash
NANO=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B
HALO_BUDGET_USD=8.73 halo valley-eval --sets heldout,tuning --think --model $NANO \
  --cache results/valley_compile_cache.jsonl --ai-log results/valley_eval/calls.jsonl --out results/valley_eval/nano_think.json
HALO_BUDGET_USD=8.73 halo valley-eval --sets heldout,tuning --model $NANO \
  --cache results/valley_compile_cache.jsonl --ai-log results/valley_eval/calls.jsonl --out results/valley_eval/nano.json
gzip results/valley_eval/calls.jsonl
```

`HALO_BUDGET_USD` is the spend guard's cap on the shared ledger ([NVIDIA_NEBIUS.md](NVIDIA_NEBIUS.md)); the committed runs set it to 8.73 to stay within the spend the entrant approved. Each file also scores the regex baseline on the same requests, and without `--model` the command scores the baseline only, with no key. With `--replay` (and a new `--out`), every call is answered from `results/valley_compile_cache.jsonl`, with no key and no cost, and the scores come out as committed. `valley-eval` writes only the JSON files; `results/valley_eval/REPORT.md` was written from them.

## Pre-signed alerts and the first crew (no key needed)

T3 flies each valley with 16 drones three times, and only the alerts differ: today's rule, pre-signed rules sent automatically, and pre-signed rules with an official's one-tap approval. T4's first-crew model reads only the pre-signed, automatic flights: T3's on seeds 9000-9019 and its own on 9020-9039. Both were flown from a checkout of 6cb093f, the commit that adds their plans.

```bash
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9000-9019 --out results/wildfire_alerts/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts/runs.jsonl   # REPORT.md and summary.json next to the rows
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9400-9419 --arms today,presigned,presigned-smoulders \
    --out results/wildfire_alerts_smoulders/runs.jsonl   # the entry's alert design on 20 more held-out valleys
PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts_smoulders/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_initial_attack.py fly --seeds 9020-9039 --out results/wildfire_initial_attack/flights.jsonl
PYTHONPATH=src python3 tools/wildfire_initial_attack.py model \
  --flights results/wildfire_alerts/runs.jsonl,results/wildfire_initial_attack/flights.jsonl --out results/wildfire_initial_attack
```

`model` runs each valley's true spread on to 12 h and keeps it in `truth.json.gz`, which a rerun into the same folder reuses.

## Valley test engineer (Nemotron's searches need a key; the rest do not)

Random search, corner search and Nemotron 3 Nano look for conditions where the valley's station fails, with pre-signed rules sent automatically; the drawn day (the valley's own conditions) is the control. Test i of search s flies valley seed 9100 + 50 s + i, so searches 0 and 1 fly the spent seeds 9100-9119 and 9150-9169. The plan and the search tool (`tools/wildfire_valley_stress.py`) are in 5f693a6, and the result files were made from a checkout of it; `tools/wildfire_valley_both_rules.py` was added after the run and run from the same checkout.

```bash
for s in 0 1; do
  PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher random  --search $s --out results/valley_stress/random_$s.json
  PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher corners --search $s --out results/valley_stress/corners_$s.json
  # needs a key
  HALO_BUDGET_USD=8.73 PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher nemotron --search $s \
    --cache results/valley_stress/calls_cache.jsonl --out results/valley_stress/nemotron_$s.json
done
PYTHONPATH=src python3 tools/wildfire_valley_stress.py drawn-day --out results/valley_stress/drawn_day.json
PYTHONPATH=src python3 tools/wildfire_valley_stress.py recheck results/valley_stress/{random,corners,nemotron}_?.json \
  results/valley_stress/drawn_day.json --out results/valley_stress/recheck_forecast.json
PYTHONPATH=src python3 tools/wildfire_valley_stress.py report results/valley_stress/{random,corners,nemotron}_?.json \
  results/valley_stress/drawn_day.json --recheck results/valley_stress/recheck_forecast.json --out results/valley_stress/REPORT.md
env -u NEBIUS_API_KEY PYTHONPATH=src python3 tools/wildfire_valley_both_rules.py --out results/valley_stress/drawn_day_both_rules.json   # added after the run
```

The ten Nemotron calls cost USD 0.026. `--replay` answers Nemotron's turns from the recorded calls only, with no key and no spend, and reproduces a recorded search exactly:

```bash
env -u NEBIUS_API_KEY PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher nemotron --search 0 \
  --cache results/valley_stress/calls_cache.jsonl --replay --out nemotron_0_replay.json
```

`recheck` flies every failing test twice more, under the pre-signed rules and under today's rule. `wildfire_valley_both_rules.py` flies all 40 drawn-day valleys from `drawn_day.json` under both rules, paired, and writes its `--out` whole.

## Strike checks (Nemotron's flights need a key, or replay without one)

An optional check team of 4 drones flies to the night's lightning strikes one at a time while the area squads patrol; its next strike is picked nearest first, by a hand-written triage rule or by Nemotron 3 Nano. `halo run` and the dashboard never fly a team. It was flown once from ca01f9e, the commit that adds the plan (`results/strike_checks/PREREG.md`), in a separate checkout, with no supervisor (`improved+fixed`): 140 flights, Nemotron's at 8 drones only. Development used valleys 3000-3011.

```bash
PYTHONPATH=src python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8,16 \
    --arms none,nearest,rule --out results/strike_checks/runs_rules.jsonl --workers 3
HALO_LEDGER=<the project's spend ledger> HALO_BUDGET_USD=8.71 PYTHONPATH=src \
    python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8 --arms nemotron \
    --cache results/strike_checks/nemotron_cache.jsonl --out results/strike_checks/runs_nemotron.jsonl --workers 1
cat results/strike_checks/runs_rules.jsonl results/strike_checks/runs_nemotron.jsonl > results/strike_checks/runs.jsonl
PYTHONPATH=src python3 tools/wildfire_strike_check_report.py results/strike_checks/runs.jsonl \
    --out results/strike_checks/summary.json
```

The second command made Nemotron's calls, with a key; `HALO_BUDGET_USD` is the spend guard's cap on the shared ledger, as for the valley compiler. Of the rows files only `runs.jsonl` is committed, so to check a result give each command a new output file. The report tool prints the tables and writes `summary.json`; `REPORT.md` was written from them.

The 77 Nemotron calls cost USD 0.086. With `--cache-mode replay` and a new `--out` file, every pick is answered from the recorded replies, with no key and no spend:

```bash
env -u NEBIUS_API_KEY PYTHONPATH=src python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8 \
  --arms nemotron --cache results/strike_checks/nemotron_cache.jsonl --cache-mode replay --out strike_nemotron_replay.jsonl
```

Seed 9300 replayed this way matched its committed row apart from compute-time fields and the fields that mark each reply as replayed.

## Missing hiker (no key needed)

Flown once from 5f693a6, as planned in `results/hiker_search/PREREG.md`: 1, 4 and 16 drones on each seed, plus one drone with the planned speed held. `film` flies the Mission tab's hiker request on seeds 0-9 to choose a seed for filming; those seeds are not part of the measurement.

```bash
PYTHONPATH=src python3 tools/hiker_search_sweep.py fly --seeds 9200-9219 --out results/hiker_search/runs.jsonl
PYTHONPATH=src python3 tools/hiker_search_sweep.py report --runs results/hiker_search/runs.jsonl   # REPORT.md and summary.json next to the rows (summary.json is not committed)
PYTHONPATH=src python3 tools/hiker_search_sweep.py film --seeds 0-9 --out results/hiker_search/film.json
```

## Aircraft inbound and the compute table (no key needed)

Both use development seeds and come from 5392c8d. The aircraft-inbound demo flies seed 3000 with 16 drones twice, without and with the operator's hold; one seed is a demonstration of the control, not a measurement. The compute table flies development valleys 3000-3003 one at a time and times each loop, so its timings belong to the machine it runs on; it also reads the committed valley sweep and valley-compile results. Its check of a dropped cloud link uses a simulated dead link: nothing leaves the machine, and no key is needed.

```bash
PYTHONPATH=src python3 tools/airspace_hold_demo.py --out results/airspace_hold   # REPORT.md and summary.json
PYTHONPATH=src python3 tools/compute_table.py --out results/compute             # REPORT.md and compute.json
```

## Patrol cost (no key needed)

The cost model reads the build sheet's figures, copied into the script, and checks its valley-flight means against `results/wildfire_valley/runs.jsonl`. The real-world figures it compares with are typed beside their sources ([PATROL_COST.md](PATROL_COST.md)).

```bash
python3 tools/patrol_cost.py --charts   # results/patrol_cost/ and docs/figures/patrol-*.png
```

## Tuning rounds

`results/tuning/` keeps the three rounds on the tuning seeds. They used the same `bench` and `mission-eval` commands with `--seeds 0-4` (rounds 1 and 2) or `--seeds 5-9` (round 3), `--sets tuning`, and the configurations `improved+nemotron:$NANO`, `improved+nemotron:$SUPER` and `improved+nemotron-think:$NANO`, on seven scenarios in rounds 1 and 2 and all nine in round 3. See [results/README.md](../results/README.md) for what each round changed.

## Tests

```bash
python3 -m unittest discover -s tests -v                    # after pip install -e .
PYTHONPATH=src python3 -m unittest discover -s tests -v     # or without installing
```

The tests need no key and no internet connection: model calls go to a fake transport, and two tests start a local server on 127.0.0.1. The suite takes one to two minutes on a laptop, and CI runs it on Python 3.10 to 3.13 ([tests.yml](../.github/workflows/tests.yml)).
