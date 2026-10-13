# Reproducing the results

Everything here runs on a laptop with Python 3.10 or newer and numpy. Charts need matplotlib (`pip install -e .[report]`). Nothing except the Nemotron runs needs a network connection or a key.

## Seeds

* Seeds 0-9 (`--seeds tuning`) were used while developing and tuning the controllers and the rule supervisor.
* Seeds 1000-1019 (`--seeds heldout`) were not looked at until the final runs. Every headline number uses them.

Each seed fixes the wind, sensor noise, radio losses, obstacle layout and initial positions. Every configuration sees the same conditions for a given seed. That is why the report can compare configurations seed by seed (paired comparisons).

## Conventional benchmark (no key needed)

```bash
swarmlab bench \
  --scenarios open_transit,formation_changes,forest_trail,urban_canyon,gusty_day,comms_degraded,motor_failures,low_battery,field_survey \
  --configs original+fixed,improved+fixed,improved+rule \
  --seeds heldout --out results/runs.jsonl
swarmlab report results/runs.jsonl --out results
```

`bench` is resumable: it skips rows already in the output file. `report` writes `REPORT.md`, `summary.json` and the charts.

## Ablations, scaling and noise

The improved controller's techniques can be switched off one at a time with `improved-no-<feature>`. The features are reassign, blend, pace, consensus, cbf, filter and feedforward.

```bash
swarmlab bench --scenarios formation_changes,forest_trail,gusty_day,motor_failures \
  --configs improved+rule,improved-no-cbf+rule,improved-no-reassign+rule,improved-no-consensus+rule \
  --seeds heldout --out results/ablation.jsonl
swarmlab bench --scenarios open_transit,motor_failures --configs original+fixed,improved+rule \
  --n 8,16,32,64 --seeds heldout --out results/scaling.jsonl
```

## Mission compiler

```bash
swarmlab mission-eval --out results/mission_eval.json                         # regex baseline only
swarmlab mission-eval --model <nemotron model id> --out results/mission_eval.json
```

The regex baseline was written and frozen before the held-out prompt set was written.

## Stress-test search

```bash
swarmlab stress --searcher random --budget 40 --seed 0 --out results/stress_random_0.json
swarmlab stress --searcher cem --budget 40 --seed 0 --out results/stress_cem_0.json
swarmlab stress --searcher nemotron:<model id> --budget 40 --seed 0 --out results/stress_nemotron_0.json
```

## Nemotron runs

```bash
export NEBIUS_API_KEY=...             # never in a file you commit
swarmlab doctor --flight              # end-to-end check, writes evidence/
swarmlab bench --scenarios ... --configs improved+nemotron:<model id> --seeds heldout \
  --cache results/nemotron_cache.jsonl --out results/runs.jsonl
swarmlab budget                       # spend so far against the cap
```

Spend is capped by the guard described in [NVIDIA_NEBIUS.md](NVIDIA_NEBIUS.md).

To replay the published Nemotron runs exactly with no key and no cost, add `--cache-mode replay`.

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests need no network. Model calls are tested against a fake transport.
