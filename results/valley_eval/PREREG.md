# Valley compiler: plan for the held-out score

Written before Nemotron saw any held-out request. The prompt (`VALLEY_SYSTEM` in `src/halo_systems/ai/valley.py`),
the normaliser and its two repairs, the planner and the regex baseline are frozen in the commit that adds this file.

## What is scored

An operator's request becomes a spec (drones, areas named, drones per area, patrol length, and a strike filter:
when and where), and code turns the spec into the patrol (`plan`) by applying the filter to that valley's lightning
list. Each request is scored on:

- **Primary: the same flight.** The plan from the spec flies the same squads for the same time, cued on the same
  strikes, as the plan from the gold spec (`score()["plan_same"]`).
- Every field right (`exact`); per-field accuracy; the strike ids kept (true and false positives, misses); refusals.
- Latency and cost of each call.

## Who is scored

- Nemotron 3 Nano (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`), reasoning on, temperature 0: the headline, and the
  setting the dashboard uses.
- The same model with reasoning off, reported beside it.
- The regex baseline, which has only the keyword guard for refusals.

## The held-out set

`src/halo_systems/ai/valley_eval_heldout.json`: 48 requests (ids 101-148), each on its own valley seed (9040-9087;
the strike lists only, no flights). Three writer agents (a terse radio style, a chatty incident-commander style, and
hard but unambiguous phrasing) wrote 16 requests each with their intents. They were given the areas, the clock, the
intent schema and its conventions, but not the compiler's prompt, its examples, the code or the baseline. Two reader
agents then wrote each request's intent independently, blind to the writer's; a request was kept only if both agreed
with the writer and neither found it ambiguous. All 48 were kept. Gold strike ids are computed from each intent and
its valley's list, never typed. Provenance, with every reading: `heldout_provenance.json`.

## Tuning, disclosed

The prompt was tuned on the 16 tuning requests (`valley_eval_tuning.json`, dev seeds 2000-2003 and 3000-3011). Asked
to pick strike ids from the list itself, Nano without reasoning got 1 of 11 strike sets right; the design changed so
that the model writes a filter and code applies it. With the filter, four examples and two repairs in the normaliser
(one area's number equal to the total is the total; strikes tied to areas when no area is named means anywhere), Nano
scored 13 of 16 without reasoning and 16 of 16 with it on the tuning set. The baseline was written and tuned on the
same 16 requests and scores 14 of 16 there (it misses both refusals).

## Commands

    HALO_BUDGET_USD=8.73 PYTHONPATH=src python3 -m halo_systems.cli valley-eval --sets heldout,tuning --think \
        --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B --cache results/valley_compile_cache.jsonl \
        --ai-log results/valley_eval/calls.jsonl --out results/valley_eval/nano_think.json
    (the same without --think, to results/valley_eval/nano.json)

Every call goes through the shared spend ledger; the cap of Kenzo's approval (USD 0.50 of new calls) is the
HALO_BUDGET_USD above.
