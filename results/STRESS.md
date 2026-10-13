# Stress-test search

Each search spends the same number of simulated flights. A failure is any crash or near miss. Distinct failing conditions count the same test conditions once, however often a searcher repeated them. The stress level runs from 0 (every parameter at its easy end) to 1 (every parameter at its hard end); a failure at a low stress level is the more useful find.

## By searcher

| Searcher | Searches | Flights | Failures | Distinct failing conditions | Failure kinds (all searches) | Mildest failure (stress level) | Mean stress level of failures | Random top-ups |
|---|---|---|---|---|---|---|---|---|
| random | 5 | 200 | 2 | 2 | 1 | 0.55 | 0.59 | 0 |
| corners | 5 | 200 | 15 | 15 | 13 | 0.50 | 0.62 | 0 |
| cem | 5 | 200 | 10 | 10 | 8 | 0.50 | 0.53 | 0 |
| nemotron | 5 | 200 | 46 | 39 | 13 | 0.48 | 0.85 | 0 |
| nemotron-think | 5 | 200 | 16 | 16 | 11 | 0.46 | 0.80 | 20 |
| cem (tests re-flown on improved-no-cbf+rule) | 5 | 200 | 69 | 69 | 33 | 0.42 | 0.54 | 0 |
| corners (tests re-flown on improved-no-cbf+rule) | 5 | 200 | 30 | 30 | 24 | 0.29 | 0.61 | 0 |
| nemotron-think (tests re-flown on improved-no-cbf+rule) | 5 | 200 | 63 | 63 | 35 | 0.32 | 0.64 | 20 |
| nemotron (tests re-flown on improved-no-cbf+rule) | 5 | 200 | 71 | 63 | 17 | 0.20 | 0.78 | 0 |
| random (tests re-flown on improved-no-cbf+rule) | 5 | 200 | 56 | 56 | 25 | 0.40 | 0.54 | 0 |

## Same tests, another configuration

Each test re-flown with the same conditions and seed. The system searched was the improved controller with the rule supervisor.

| Tests from | Re-flown on | Flights | Failed as searched | Failed when re-flown | Failed only when re-flown | Failed only as searched | Drones lost as searched | Drones lost when re-flown |
|---|---|---|---|---|---|---|---|---|
| cem | improved-no-cbf+rule | 200 | 10 | 69 | 64 | 5 | 5 | 208 |
| corners | improved-no-cbf+rule | 200 | 15 | 30 | 17 | 2 | 36 | 338 |
| nemotron-think | improved-no-cbf+rule | 200 | 16 | 63 | 49 | 2 | 85 | 427 |
| nemotron | improved-no-cbf+rule | 200 | 46 | 71 | 27 | 2 | 261 | 870 |
| random | improved-no-cbf+rule | 200 | 2 | 56 | 54 | 0 | 4 | 176 |

## By search

| File | Searcher | Failures | Distinct failing conditions | Failure kinds | Mildest failure | Random top-ups |
|---|---|---|---|---|---|---|
| random_0.json | random | 1 | 1 | 1 | 0.55 | 0 |
| random_1.json | random | 0 | 0 | 0 | n/a | 0 |
| random_2.json | random | 0 | 0 | 0 | n/a | 0 |
| random_3.json | random | 0 | 0 | 0 | n/a | 0 |
| random_4.json | random | 1 | 1 | 1 | 0.63 | 0 |
| corners_0.json | corners | 2 | 2 | 2 | 0.57 | 0 |
| corners_1.json | corners | 4 | 4 | 4 | 0.50 | 0 |
| corners_2.json | corners | 3 | 3 | 3 | 0.57 | 0 |
| corners_3.json | corners | 4 | 4 | 4 | 0.57 | 0 |
| corners_4.json | corners | 2 | 2 | 2 | 0.57 | 0 |
| cem_0.json | cem | 2 | 2 | 1 | 0.50 | 0 |
| cem_1.json | cem | 1 | 1 | 1 | 0.50 | 0 |
| cem_2.json | cem | 2 | 2 | 1 | 0.55 | 0 |
| cem_3.json | cem | 2 | 2 | 2 | 0.50 | 0 |
| cem_4.json | cem | 3 | 3 | 3 | 0.51 | 0 |
| nemotron_0.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 2 | 2 | 2 | 0.67 | 0 |
| nemotron_1.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 10 | 10 | 5 | 0.48 | 0 |
| nemotron_2.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 11 | 6 | 5 | 0.71 | 0 |
| nemotron_3.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 12 | 11 | 6 | 0.74 | 0 |
| nemotron_4.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 11 | 10 | 6 | 0.48 | 0 |
| nemotron-think_0.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 2 | 2 | 2 | 0.72 | 0 |
| nemotron-think_1.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 2 | 2 | 2 | 0.91 | 16 |
| nemotron-think_2.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 6 | 6 | 5 | 0.46 | 0 |
| nemotron-think_3.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 3 | 3 | 3 | 0.78 | 4 |
| nemotron-think_4.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 3 | 3 | 2 | 0.58 | 0 |
| cem_0_nocbf.json | cem (re-flown on improved-no-cbf+rule) | 3 | 3 | 3 | 0.48 | 0 |
| cem_1_nocbf.json | cem (re-flown on improved-no-cbf+rule) | 13 | 13 | 6 | 0.47 | 0 |
| cem_2_nocbf.json | cem (re-flown on improved-no-cbf+rule) | 12 | 12 | 6 | 0.54 | 0 |
| cem_3_nocbf.json | cem (re-flown on improved-no-cbf+rule) | 24 | 24 | 16 | 0.45 | 0 |
| cem_4_nocbf.json | cem (re-flown on improved-no-cbf+rule) | 17 | 17 | 11 | 0.42 | 0 |
| corners_0_nocbf.json | corners (re-flown on improved-no-cbf+rule) | 5 | 5 | 4 | 0.57 | 0 |
| corners_1_nocbf.json | corners (re-flown on improved-no-cbf+rule) | 8 | 8 | 8 | 0.50 | 0 |
| corners_2_nocbf.json | corners (re-flown on improved-no-cbf+rule) | 5 | 5 | 5 | 0.43 | 0 |
| corners_3_nocbf.json | corners (re-flown on improved-no-cbf+rule) | 6 | 6 | 6 | 0.29 | 0 |
| corners_4_nocbf.json | corners (re-flown on improved-no-cbf+rule) | 6 | 6 | 6 | 0.57 | 0 |
| nemotron-think_0_nocbf.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 13 | 13 | 13 | 0.40 | 0 |
| nemotron-think_1_nocbf.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 12 | 12 | 11 | 0.45 | 16 |
| nemotron-think_2_nocbf.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 14 | 14 | 11 | 0.32 | 0 |
| nemotron-think_3_nocbf.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 14 | 14 | 13 | 0.33 | 4 |
| nemotron-think_4_nocbf.json | nemotron-think:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 10 | 10 | 7 | 0.47 | 0 |
| nemotron_0_nocbf.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 8 | 8 | 6 | 0.20 | 0 |
| nemotron_1_nocbf.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 19 | 19 | 8 | 0.30 | 0 |
| nemotron_2_nocbf.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 12 | 7 | 6 | 0.64 | 0 |
| nemotron_3_nocbf.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 20 | 18 | 8 | 0.22 | 0 |
| nemotron_4_nocbf.json | nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (re-flown on improved-no-cbf+rule) | 12 | 11 | 6 | 0.59 | 0 |
| random_0_nocbf.json | random (re-flown on improved-no-cbf+rule) | 7 | 7 | 7 | 0.46 | 0 |
| random_1_nocbf.json | random (re-flown on improved-no-cbf+rule) | 12 | 12 | 11 | 0.40 | 0 |
| random_2_nocbf.json | random (re-flown on improved-no-cbf+rule) | 12 | 12 | 9 | 0.43 | 0 |
| random_3_nocbf.json | random (re-flown on improved-no-cbf+rule) | 12 | 12 | 9 | 0.46 | 0 |
| random_4_nocbf.json | random (re-flown on improved-no-cbf+rule) | 13 | 13 | 10 | 0.44 | 0 |
