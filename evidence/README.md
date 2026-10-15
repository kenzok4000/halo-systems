# Evidence: the live end-to-end check

`halo doctor --flight` (then called `swarmlab doctor`, before the project was renamed Halo Systems) wrote these files (it now writes to `evidence/latest/`, so a new check does not overwrite them). Both runs below are real calls to Nebius Token Factory on 12 October 2026, kept as they were written apart from moving them into this folder and shortening the patch's path headers to start at this repository's root (its changes are as recorded). The API key never appears in them.

## `first-attempt/` (22:34 UTC): failed

The model list and model choice passed, then the supervisor call failed with an empty reply after 4 attempts and 43 s. Nemotron 3 reasons by default, the reasoning used the whole token limit, and the client retried a request that could only fail the same way.

## This folder (22:36 UTC): passed

This run used the code of the time plus a small local change, `thinking-off.patch`, which switches reasoning off for the supervisor. The patch applies to that day's code, laid out as `src/swarmlab/` before the rename, not to today's `src/halo_systems/`. Commit dc6cf0d made the same default permanent, with a switch to turn reasoning on, and 53ea3ea stopped the client retrying a reply cut off at the token limit.

| File | What it is |
|---|---|
| `doctor_stdout.txt` | The check's console output |
| `nebius_e2e.json` | Each step with pass or fail, latency, request id and token usage (that build had no prices, so its cost fields are 0) |
| `nebius_calls.jsonl` | Every call in the check, one JSON line each |
| `flight_forest_trail_0.json` | Metrics of the full forest-trail flight with Nemotron supervising |
| `cache_forest_trail_0.jsonl` | That flight's calls as recorded. They replay only with the code of that time (d91cfe6 plus the patch): the prompt has changed since, so today's code finds no match and stops |

The supervisor call took 0.97 s and returned a directive the validator accepted. The full flight made 30 calls with no fallbacks, so the integration worked end to end. The flight itself did not reach the end of the trail: the model kept a grid formation that did not fit the trail. That led to the status report spelling out the corridor and energy checks in words, after which Nano and Super with reasoning off finished every forest flight in tuning rounds 2 and 3 and on the held-out seeds; Nano with reasoning on finished 3 of 5 in round 3 (see `results/`).

## `budget.txt`: the spend ledger total

The output of `halo budget` against the project's shared spend ledger on 14 October 2026: 13,068 entries, 22.3 million prompt tokens, 3.6 million completion tokens and an estimated $8.23 at the price ceilings. It is the source of the project-wide usage figures in the README and the Devpost text.
