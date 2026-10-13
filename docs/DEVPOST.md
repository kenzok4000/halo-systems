# Devpost submission

Text for the Devpost form, one section per field. The numbers come from the reports and logs in `results/` and `evidence/` (see `results/README.md`), apart from the project-wide usage figures, which come from the spend ledger. All flight results are from simulation.

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
* Searches for conditions that make the swarm fail, with Nemotron as a test engineer against random search, corner search and the cross-entropy method.
* Benchmarks the controllers and supervisors on held-out seeds, with confidence intervals and paired comparisons, and shows it live in a 3D dashboard.

## How we built it

* Python and numpy, with matplotlib for the charts. The dashboard is a standard-library web server and three.js.
* Nemotron runs on Nebius Token Factory through its OpenAI-compatible API, with a client that handles timeouts, retries, rate limits, response validation, logging and a spend guard.
* Every supervisor call is recorded, so the held-out Nemotron flights can be replayed exactly without a key. Mission-compiler and stress-search calls are kept in call logs.
* Seeds 0-9 were used for all tuning. Seeds 1000-1019 were each flown once: the conventional configurations first, then Nemotron, after its setup had been chosen on tuning seeds and committed.

## Results

All from simulated flights on held-out seeds 1000-1019, each flown once, 20 flights per scenario and configuration. The Nemotron setup was chosen on tuning seeds and committed before any Nemotron flight on these seeds.

* **Safety.** Over nine scenarios, the original formation controller lost 300 drones in crashes in 180 flights. The improved controller, supervised by the rules or by Nemotron, had no crashes and no near misses in 180 flights each. (Drones that drop out with a scripted motor failure are not counted as crashes.)
* **Supervision.** Without a supervisor, the improved controller finished 5% of forest-trail flights and 55% of urban-canyon flights, because its safety filter will not squeeze a formation through a gap it does not fit. With the rules or Nemotron it finished all of them.
* **Nemotron 3 Nano against the tuned rules:** on the five headline metrics, better beyond noise in four of nine scenarios and worse in none, but the gains are small and two come with a caveat. It held formation slightly better in the forest (86% against 85% of the time) and used 1.8% less energy in the urban canyon. When the radio degraded it finished in 66 s against 70 s, because the rules slowed down and Nano kept the plan, as flying with no supervisor does. On a low-battery leg it finished in 128 s against 138 s on 5% less energy, partly because it kept a higher speed than planned in 90 of 150 updates where its instructions said the planned speed was enough. It also took longer than the rules to settle after formation changes. In five scenarios no supervisor made a difference above the report's 1% threshold.
* **Nemotron 3 Super**, run alongside as a declared comparison, did worse than the rules in two scenarios: less time in formation in the urban canyon, and a slower, costlier low-battery leg that retired more drones.
* **Mission compiler** on 20 held-out requests: the regex baseline got 83.9% of fields right and 9 requests fully right. Nemotron 3 Nano got 97.6% and 18. Nano with reasoning on and Super both got 100% and 20. Of the 3 harmful requests, the keyword guard refused one before any model call, and all three models refused the other two.
* **Stress testing**, 200 flights per searcher: Nemotron found 46 failures in 39 distinct conditions, against 15 for corner search, 10 for the cross-entropy method and 2 for random search. It found as many kinds of failure as corner search (13 each, only 3 of them in common), mostly by pushing every condition near its limit.
* **Safety filter ablation.** Re-flying all 1,000 stress tests without the safety filter raised the failures from 89 to 289 and the drones lost from 391 to 2,019.

## Challenges

* Nemotron 3 reasons by default. In our first live call, the reasoning used the whole token limit and the answer came back empty, and our client paid for three retries before giving up. We now switch reasoning on or off per role, stop retrying when the limit cuts a reply off, and charge every billed reply to the spend ledger.
* With reasoning off, Nemotron 3 Nano kept a grid formation that was 24 m wide, buffer included, through a 13.7 m forest trail for a whole flight. It repeated its previous answer and reason 30 times. The status report now includes the corridor and energy arithmetic, worked out by code. The rule supervisor sees the same report.
* Our first safety filter measured distances between GPS estimates and ignored GPS error, so drones still collided and had near misses in forest and canyon runs on the tuning seeds. It now pads its safety distances by two standard deviations of navigation error. Three standard deviations caused tree strikes in narrow gaps.

## Accomplishments

* A supervisor that a 3-billion-active-parameter model runs for about $0.004 a simulated flight, with 3,400 calls and no fallbacks on the held-out seeds, and that matched or beat tuned rules on the headline metrics there.
* The held-out Nemotron flights can be replayed exactly without a key: 24 of them replayed from the published call cache match the originals apart from compute time (`results/replay_check/`).
* The evidence includes the failures: the first live call that returned nothing, the run where reasoning was switched off and the model ignored the corridor check, and the scenarios where Nemotron made no difference or did worse.

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

* Models: NVIDIA Nemotron 3 Nano (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, the main supervisor) and Nemotron 3 Super (`nvidia/nemotron-3-super-120b-a12b`), via Nebius Token Factory (`https://api.tokenfactory.nebius.com/v1`).
* Roles: supervisor, mission compiler, test engineer (see docs/NVIDIA_NEBIUS.md).
* Usage over the whole project, from the spend ledger: about 13,100 requests (13,066 logged by the client and 35 from the first live check, entered by hand), 22.3 million prompt tokens and 3.6 million completion tokens, an estimated $8.23 at price ceilings set above the listed prices. The ledger counts every attempt, retries included, and counts requests that timed out at their worst case.

## Feedback on Nebius Token Factory and NVIDIA models

* Setup: the OpenAI-compatible endpoint worked with a standard-library client. The model list returned 25 models, 4 of them Nemotron.
* Nemotron 3 Nano with reasoning off answered in about 1 s (p50 0.97 s, p95 1.36 s over 31 calls of about 1,400 prompt tokens), and JSON mode worked.
* With reasoning on, the reasoning comes back in `message.reasoning` and counts against `max_tokens`. When it runs out, `content` is null with `finish_reason: "length"`. A clearer note in the quick-start, or a separate reasoning budget, would save people the paid empty replies we hit.
* On the held-out runs, Nano answered supervisor calls in 1.18 s on average and Super in 1.37 s, with no failed calls in 6,874. In the last tuning round, 12 of 826 Nano calls timed out at 8 s on the first attempt and the retry ran past the 15 s deadline, so the rules took those steps. In the first tuning round, before the whole-call deadline existed, 39 of 540 Super calls took over 30 s, the slowest 64 s.
* For the mission compiler, Super was the fastest (median 1.3 s) and, with Nano with reasoning on, the most accurate. Nano took 5 s with reasoning off and 12 s with it on, because a mission plan is a longer answer.

## Updates during the submission period

SwarmLab was written during the submission period: its first commit is dated 12 October 2026. It reuses two things from the entrant's earlier private drone simulator:

* the airframe parameters (mass, thrust-to-weight, drag area, body radius, top speed) and its 17-minute hover endurance, which the power model is tuned to match;
* the passive formation law, ported as the "original" baseline controller.

Everything else is new: the simulator engine, wind, sensing and power models, the improved controller and safety filter, the supervisors, the Nemotron integration, the benchmark, the dashboard and the docs.
