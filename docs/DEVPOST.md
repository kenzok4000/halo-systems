# Devpost submission draft

Draft for review. Before submission, every number marked ⟨…⟩ is replaced from `results/REPORT.md`, and the final audit checks each claim against the code or a log.

## Project name

SwarmLab: supervised drone-swarm formation flight with NVIDIA Nemotron

## Tagline

A 50 Hz safety-filtered swarm controller, with Nemotron on Nebius Token Factory as the slow-loop supervisor, mission compiler and test engineer, measured against strong conventional baselines.

## Track

Physical AI

## Inspiration

Small drone swarms can search a forest for a missing hiker, scout a field or inspect a street faster than one drone can. Holding a formation is the easy part. The hard part is knowing when to change it: when the trail narrows, a gust front hits, the radio degrades or batteries run low. That judgement is usually hand-written rules. We wanted to know whether a language model can make those calls better, and how to let it try without ever putting it between the drones and their motors.

## What it does

* Simulates a swarm of 6 to 64 quadcopters with forward-flight power, wind and gust fronts, noisy GPS, lossy and delayed radio, motor failures, batteries, trees and buildings.
* Flies every drone with a 50 Hz controller that has slot assignment, synchronised transitions, consensus and a control-barrier-function safety filter. No language model is in this loop.
* Every 5 s of simulated time, a supervisor reads a status report and sets the formation, spacing, speed, altitude and safety margin. The supervisor is either tuned rules or Nemotron, and its answer passes a safety validator first.
* Turns plain-language mission requests into checked mission plans, and refuses non-civilian requests.
* Searches for conditions that make the swarm fail, with Nemotron as a test engineer against random search and the cross-entropy method.
* Benchmarks everything on held-out seeds, with confidence intervals and paired comparisons, and shows it live in a 3D dashboard.

## How we built it

* Python and numpy only. The dashboard is a standard-library web server and three.js.
* Nemotron runs on Nebius Token Factory through its OpenAI-compatible API, with a client that handles timeouts, retries, rate limits, response validation, logging and a spend guard.
* Every model call can be recorded and replayed, so the published Nemotron results can be reproduced exactly without a key.
* Seeds 0-9 were used for all tuning. Seeds 1000-1019 were run once at the end.

## Results

⟨Headline table from results/REPORT.md: crashes, time in formation and completion for original, improved, rules and Nemotron on the held-out seeds.⟩

⟨Where Nemotron beat the tuned rules, where it tied, and where it did worse.⟩

⟨Mission compiler: field accuracy, Nemotron vs regex baseline, on the held-out prompts.⟩

⟨Stress search: failures and distinct failure kinds found per 40 flights, for each searcher.⟩

## Challenges

* Nemotron 3 reasons by default. In our first live call, the reasoning used the whole token limit and the answer came back empty, and our client paid for three retries before giving up. We now switch reasoning on or off per role, stop retrying when the limit cuts a reply off, and charge every billed reply to the spend ledger.
* With reasoning off, Nemotron 3 Nano kept a grid formation that was 24 m wide, buffer included, through a 13.7 m forest trail for a whole flight. It repeated its previous answer and reason 30 times. The status report now includes the corridor and energy arithmetic, worked out by code. The rule supervisor sees the same report.
* Our first safety filter measured distances between GPS estimates and ignored GPS error, so two drones touched in one forest run. It now pads its safety distances by two standard deviations of navigation error. Three standard deviations caused tree strikes in narrow gaps.

## Accomplishments

⟨From the final results.⟩

## What we learned

* Put the model where its latency does not matter, and make every output pass a validator.
* Give the model the same report as a strong baseline, and measure both on the same seeds.
* A small model without reasoning needs the arithmetic done for it. With reasoning on, it does the arithmetic itself but takes far longer.

## What's next

* Calibrate the airframe and sensor models against flight logs.
* Run the supervisor against real autopilots through a software-in-the-loop bridge.

## Built with

Python, numpy, matplotlib, three.js, NVIDIA Nemotron 3, Nebius Token Factory

## NVIDIA and Nebius usage

* Model: NVIDIA Nemotron 3 ⟨chosen variant⟩ via Nebius Token Factory (`https://api.tokenfactory.nebius.com/v1`).
* Roles: supervisor, mission compiler, test engineer (see docs/NVIDIA_NEBIUS.md).
* Usage over the whole project: ⟨tokens, calls, estimated cost from the ledger⟩.

## Feedback on Nebius Token Factory and NVIDIA models

* Setup: the OpenAI-compatible endpoint worked with a standard-library client. The model list returned 25 models, 4 of them Nemotron.
* Nemotron 3 Nano with reasoning off answered in about 1 s (p50 0.97 s, p95 1.37 s over 31 calls of about 1,400 prompt tokens), and JSON mode worked.
* With reasoning on, the reasoning comes back in `message.reasoning` and counts against `max_tokens`. When it runs out, `content` is null with `finish_reason: "length"`. A clearer note in the quick-start, or a separate reasoning budget, would save people the paid empty replies we hit.
* ⟨Super and reasoning-mode latency and quality, from the tuning runs.⟩

## Updates during the submission period

SwarmLab was written during the submission period: its first commit is dated 12 October 2026. It reuses two things from the entrant's earlier private drone simulator:

* the airframe parameters (mass, thrust-to-weight, drag area);
* the passive formation law, ported as the "original" baseline controller.

Everything else is new: the simulator engine, wind, sensing and power models, the improved controller and safety filter, the supervisors, the Nemotron integration, the benchmark, the dashboard and the docs.
