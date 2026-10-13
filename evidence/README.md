# Evidence: the live end-to-end check

`swarmlab doctor --flight` writes these files. Both runs below are real calls to Nebius Token Factory on 12 October 2026, kept as they were written apart from moving them into this folder. The API key never appears in them.

## `first-attempt/` (22:34 UTC): failed

The model list and model choice passed, then the supervisor call failed with an empty reply after 4 attempts and 43 s. Nemotron 3 reasons by default, the reasoning used the whole token limit, and the client retried a request that could only fail the same way.

## This folder (22:36 UTC): passed

This run used the code of the time plus a small local change, `thinking-off.patch`, which switches reasoning off for the supervisor. Commit dc6cf0d made the same default permanent, with a switch to turn reasoning on, and 53ea3ea stopped the client retrying a reply cut off at the token limit.

| File | What it is |
|---|---|
| `doctor_stdout.txt` | The check's console output |
| `nebius_e2e.json` | Each step with pass or fail, latency, request id, usage and cost |
| `nebius_calls.jsonl` | Every call in the check, one JSON line each |
| `flight_forest_trail_0.json` | Metrics of the full forest-trail flight with Nemotron supervising |
| `cache_forest_trail_0.jsonl` | That flight's calls, for replay without a key |

The supervisor call took 0.97 s and returned a directive the validator accepted. The full flight made 30 calls with no fallbacks, so the integration worked end to end. The flight itself did not reach the end of the trail: the model kept a grid formation that did not fit the trail. That led to the status report spelling out the corridor and energy checks in words, after which Nemotron finished every forest flight in tuning and on the held-out seeds (see `results/`).
