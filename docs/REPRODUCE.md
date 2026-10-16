# Reproducing the results

Everything here runs on a laptop with Python 3.10 or newer and numpy. Charts need matplotlib (`pip install -e '.[report]'`). Nothing except the Nemotron runs needs a network connection or a key.

## Seeds

* Seeds 0-9 (`--seeds tuning`) were used while developing and tuning the controllers and the rule supervisor.
* Seeds 1000-1019 (`--seeds heldout`) were not looked at until the final runs. Every headline number outside the wildfire valley uses them.
* The wildfire valley has its own seeds: 2000-2011 and 3000-3019 for development, and 4000-4019 held out and flown once with the final code ([WILDFIRE.md](WILDFIRE.md)).

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

## Tuning rounds

`results/tuning/` keeps the three rounds on the tuning seeds. They used the same `bench` and `mission-eval` commands with `--seeds 0-4` (rounds 1 and 2) or `--seeds 5-9` (round 3), `--sets tuning`, and the configurations `improved+nemotron:$NANO`, `improved+nemotron:$SUPER` and `improved+nemotron-think:$NANO`, on seven scenarios in rounds 1 and 2 and all nine in round 3. See [results/README.md](../results/README.md) for what each round changed.

## Tests

```bash
python3 -m unittest discover -s tests -v                    # after pip install -e .
PYTHONPATH=src python3 -m unittest discover -s tests -v     # or without installing
```

The tests need no key and no internet connection: model calls go to a fake transport, and two tests start a local server on 127.0.0.1. The suite takes one to two minutes on a laptop, and CI runs it on Python 3.10 to 3.13 ([tests.yml](../.github/workflows/tests.yml)).
