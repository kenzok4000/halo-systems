# Results

Everything here was produced by the commands in [docs/REPRODUCE.md](../docs/REPRODUCE.md). The reports are generated from the rows files next to them, so a number in a report can be traced to the flights behind it.

## Held-out seeds (1000-1019), run once

| File | What it is | Commit |
|---|---|---|
| `REPORT.md`, `summary.json`, `*.png` | Main report: nine scenarios, original and improved controllers, rule supervisor and Nemotron | see below |
| `runs.jsonl` | One row per flight for the main report | |
| `nemotron_cache.jsonl` | Every Nemotron call in the held-out runs, for exact replay without a key | dccae5b |
| `nemotron_calls.jsonl.gz` | The call log: latency, attempts, tokens, estimated cost and request id per call | dccae5b |
| `mission_eval_*.json`, `mission_calls.jsonl.gz` | Mission compiler on the held-out prompts, per model, against the regex baseline | dccae5b |
| `ablation/` | The improved controller with one technique removed at a time, with the rule supervisor | fbdf402 |
| `gps/` | GPS error scaled by 0.5, 1, 2 and 3 on forest_trail and gusty_day | fbdf402 |
| `scaling/` | 8, 16, 32 and 64 drones on open_transit and motor_failures | fbdf402 |

The conventional rows (original controller, improved controller, rule supervisor) were flown at commit fbdf402 and the Nemotron rows at dccae5b. The simulator, controllers, rule supervisor, validator and prompts are identical between the two; the commits in between changed only the stress-test tools, docs and report.

## Stress-test search

`STRESS.md` compares the searchers. `stress/` holds every search (5 seeds × 40 flights per searcher) with each test's conditions, reason and outcome; `stress/no_safety_filter/` holds the same 1,000 tests re-flown without the safety filter. The system under test is the improved controller with the rule supervisor, unchanged since commit 191a11a, where the random, CEM and Nemotron searches ran. The corner search and the re-flights ran later on the same controller and rules.

## Tuning history

`tuning/` keeps the three live tuning rounds on the tuning seeds, including the failures that led to fixes:

| Round | Commit | Seeds | Notes |
|---|---|---|---|
| 1 | dc6cf0d | 0-4 | 30 of 105 rows failed on a torn line in the shared call cache (fixed in 0548b83); the failed rows are kept with their error |
| 2 | bcfc174 | 0-4 | Status report states the corridor and energy checks in words; whole-call deadline |
| 3 | eb5ef30 | 5-9 | Final prompt; picked the Nemotron setup recorded in [docs/BENCHMARKS.md](../docs/BENCHMARKS.md). `conventional_runs.jsonl` holds the rule and fixed-plan flights on the same seeds |
