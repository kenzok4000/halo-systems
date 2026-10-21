# Valley compiler: held-out results

Scored once on 20 October 2026, from commit 939fadc (the commit that froze the prompt, the planner, the baseline
and the plan in `PREREG.md`), on the 48 held-out requests (ids 101-148, strike lists from seeds 9040-9087), with
the 16 tuning requests run beside them. Nemotron 3 Nano (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`), temperature 0.

## Headline

An operator's plain words become the same patrol as the gold reading in **47 of 48** held-out requests with
Nemotron Nano reasoning, against **20 of 48** for the hand-written rules (the regex baseline).

| Held-out, 48 requests | Nano, reasoning on | Nano, reasoning off | Rules (baseline) |
|---|---|---|---|
| Same flight as the gold reading (primary) | **47** | 39 | 20 |
| Every field right | 47 | 36 | 17 |
| Fields right | 99.2% | 95.0% | 85.3% |
| Strike lists right (of 32 requests that ask for strikes) | 31 | 28 | 24 |
| Strikes chosen: right / extra / missed | 138 / 0 / 17 | 136 / 2 / 19 | 138 / 65 / 17 |
| Requests outside civilian use refused (of 6) | 6 | 6 | 0 |
| Median time per request | 4.7 s | 1.1 s | under 0.01 s |
| Cost of the calls (47 of the 48 requests; the guard refused one before any call) | USD 0.0164 | USD 0.0061 | none |

Tuning set (16 requests, dev seeds, seen while the prompt was written): reasoning on 16 of 16, reasoning off 12 of
16, rules 14 of 16. Reasoning off scored 13 of 16 on the same requests during tuning: the model is not fully
repeatable at temperature 0.

## Where each one goes wrong

- **Nano, reasoning on: one miss, not the model's.** Request 145, "Go through the lightning list for me, every
  single strike on it, no matter where it landed.", was refused by the keyword guard that runs before any call
  ("strike on it" reads to the guard like an order to strike something), so Nemotron never saw it. The guard is
  shared with the mission compiler and errs on the side of refusing; it is left as it is, and this miss stays in
  the score. Its 17 strikes are every "missed" strike in the table.
- **Nano, reasoning off: nine misses.** Five name areas when the request names none (three name all three; the
  patrol then differs from the default split, which follows the strikes); three read the clock or the list wrongly ("half past two this morning" as 14:30,
  "since half past ten last night" as 21:30, "every strike on file" as the night before only); and request 145, as above.
- **Rules: twenty-eight misses.** Seventeen patrol lengths in words ("an hour and a quarter", "a ninety-minute patrol", "half hr")
  read wrongly, six requests that should be refused let through (the rules have only the keyword guard), request
  145, and four with the drones, areas or strikes wrong. The rules also add 65 strikes nobody asked for.

## What this shows, and what it does not

The model reads the words; code does everything else. Nemotron writes a short spec (how many drones, which areas,
how long, which strikes by when and where); the planner checks it and builds the squads, and the strike filter is
applied by code to the valley's own list, so the model never picks a strike or a lane itself. Every plan still
passes the safety validator before a drone moves, and no language model takes part in any alert or crew decision.

48 requests is a small set: one request is two points. The requests were written by three writer agents and checked
by two blind readers (see `PREREG.md` and `heldout_provenance.json`), not by fire crews, so they show how the
compiler handles varied phrasing, not how real dispatchers talk.

## Files

- `nano_think.json`, `nano.json`: every request with the gold reading, each scorer's spec and its score.
- `calls.jsonl.gz`: every call (request, reply, tokens, cost, latency), also in the project's spend ledger.
- `../valley_compile_cache.jsonl`: the recorded replies, so the dashboard and `valley-eval --replay` run without a
  key and give the same answers.

Spend: the held-out and tuning runs cost USD 0.029 together; with the 16 tuning calls made while the prompt was
written, the valley compiler has used USD 0.047 of the USD 0.50 approved.
