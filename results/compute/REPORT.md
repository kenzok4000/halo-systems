# Where each loop runs

Measured on x86_64, 4 cores, Python 3.13.16 (the development machine, not a drone or a Jetson). The controller, the rule supervisor and the station were timed on development valleys 3000, 3001, 3002, 3003 (16 drones), flown one at a time (1-minute load average 0.16 before, 1.0 after). The controller's time with 1, 4 and 16 drones also comes from the committed valley sweep, which flew its flights in parallel (below). Token Factory latencies are from the scored valley-compile calls (results/valley_eval).

| Loop | Where it runs | How often | Measured here | When the cloud link drops |
|---|---|---|---|---|
| Flight control: each drone's controller with its collision-avoidance safety filter | On each drone (simulated here, all 16 in one process) | 50 Hz (every 20 ms) | All 16 drones in one vectorised call: 3.33 ms per step (p95 4.57 ms). In the parallel sweep (below), one drone's controller alone: 3.2 ms per step (p95 5.05 ms), against 5.5 ms for all 16 | Unaffected: it never uses the link |
| Rule supervisor: formation, spacing and speed, every change through the safety validator | Ground station | Every 5 s | Decision: median 0.035 ms, p95 0.063 ms (198 calls); building the state summary it reads: median 1.551 ms, p95 2.484 ms; validator check: median 0.028 ms, p95 0.045 ms (156 checks) | Unaffected |
| Station: smoke bearings, thermal finds, located fires, cards to fire dispatch | Ground station (a Jetson Orin Nano is the candidate) | Once a second | Median 2.56 ms, p95 17.59 ms per step, steps that ran a forecast included (busy 3.56% of the flight) | Unaffected |
| Spread forecast (Rothermel ensemble), crew briefs, pre-signed alert checks | Ground station | Once a fire is located: every 2 min, sooner when the picture changes (at least 30 s apart) | Median 1620.1 ms, p95 1790.3 ms, longest 1875.5 ms per forecast (23 forecasts) | Unaffected: no model in any alert or card |
| Valley compile: plain words to a patrol plan (Nemotron 3 Nano, reasoning) | Nebius Token Factory | Once per job | Median 4.7 s, p95 10.44 s (47 calls) | The regex baseline reads the request (below); the operator checks the plan |
| Test engineer: proposes stress tests (Nemotron) | Token Factory, before deployment | Per batch of tests | Not timed here (results/valley_stress) | Not in flight |

## Controller time per step

The controller computes every drone's command in one vectorised call per 20 ms step.

| Drones | Flights | Where from | ms per step (median of flight means) | p95 (median of flight p95s) |
|---|---|---|---|---|
| 16 | 4 | development valleys 3000, 3001, 3002, 3003, flown one at a time here | 3.33 | 4.57 |
| 1 | 20 | committed sweep, seeds 4000-4019, alert rule forecast, flown in parallel | 3.2 | 5.05 |
| 4 | 20 | committed sweep, seeds 4000-4019, alert rule forecast, flown in parallel | 4.51 | 7.05 |
| 16 | 40 | committed sweep, seeds 4000-4019, alert rules distance, forecast, flown in parallel | 5.5 | 8.86 |

The sweep (tools/wildfire_valley_sweep.py) flew its flights in parallel, one process per core by default, so its times depend on the machine's load: compare them with each other, not with the flights timed one at a time. In the sweep one drone alone took 3.2 ms per step and sixteen 5.5 ms, so the 16-drone time divided by 16 is not one drone's cost.

## The link drops

- Valley compile: the call failed: network error: OSError: network unreachable (simulated link drop); the regex baseline read the request ("We had dry lightning last night. Put 16 drones over the whole valley for 12 minutes and check last night's strikes.") as squads F4-N6-S6-R0 and strikes [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19].
- Nemotron supervisor on the forest trail: 18 calls, 18 fell back to the rule supervisor; completed True, drones lost 0, collisions 0.
