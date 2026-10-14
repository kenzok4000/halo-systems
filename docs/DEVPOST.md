# Devpost submission

Text for the Devpost form, one section per field. The numbers come from the reports and logs in `results/` and `evidence/` (see `results/README.md`), apart from the project-wide usage figures, which come from the spend ledger. All flight results are from simulation.

## Project name

Halo Systems: drone swarms that find wildfire ignitions before they grow

## Tagline

Sixteen drones sweep a block of scrub for smouldering hotspots 13 times faster than one. An NVIDIA Nemotron supervisor sets their plan every 5 s and never touches the motors.

## Track

Physical AI

## Links

* Repository: `https://github.com/kenzok4000/halo-systems` (public, Apache-2.0; created at export, so check the address before submitting).
* Video: the public YouTube address, filled in after upload.
* Everything a judge needs runs from the repository without a key: the README quick start replays the recorded Nemotron flights.

## Inspiration

In 2025 wildfires burnt over a million hectares in the European Union, its worst season on record, with 7,783 fires in 25 countries ([European Commission, ECHO, 31 March 2026](https://civil-protection-humanitarian-aid.ec.europa.eu/news-stories/news/record-number-wildfires-2025-calls-stronger-eu-action-2026-03-31_en); [Copernicus, European State of the Climate 2025](https://climate.copernicus.eu/esotc/2025/wildfires)). The fire season is getting longer: the Joint Research Centre reports serious wildfires both earlier and later than the traditional June to mid-September season ([JRC, 5 December 2025](https://joint-research-centre.ec.europa.eu/jrc-news-and-updates/europes-fire-season-expanding-new-jrc-report-shows-2025-12-05_en)). Most of the damage is done by the few fires nobody reached in time: the US Forest Service told the Senate that it contains 98% of fires at initial attack, and that the 1 to 2% that escape account for 30% or more of its fire spending ([USDA Forest Service testimony, 3 August 2017](https://www.fs.usda.gov/sites/default/files/fs_media/fs_document/usda-testimony-senr-fire-christiansen-08-03-17.pdf)). A 2026 study of 3,693 California ignitions found that a network of drones could detect 97% of them, 74% within the first hour, for about 5% of the state's annual prevention spending ([arXiv 2609.18556](https://arxiv.org/abs/2609.18556)).

A single drone with a thermal camera can scout a block of scrub; it takes it most of a battery. Sixteen drones can do it in under a minute, if they can fly together safely, keep formation in gusts, close the gaps when one fails, and change plan when a trail narrows or the battery runs short. That judgement is usually hand-written rules. We wanted to know whether a language model can make those calls better, and how to let it try without ever putting it between the drones and their motors.

## What it does

* Simulates a swarm of 6 to 64 quadcopters with forward-flight power, wind and gust fronts, noisy GPS, lossy and delayed radio, motor failures, batteries, trees and buildings.
* Flies every drone with a 50 Hz controller that has slot assignment, synchronised transitions, consensus and a control-barrier-function safety filter. No language model is in this loop.
* Every 5 s of simulated time, a supervisor reads a status report and sets the formation, spacing, speed, altitude and safety margin. The supervisor is either tuned rules or Nemotron, and its answer passes a safety validator first.
* Turns plain-language mission requests into checked mission plans, and refuses non-civilian requests.
* Searches for conditions that make the swarm fail, with Nemotron as a test engineer against random search, corner search and the cross-entropy method.
* Benchmarks the controllers and supervisors on held-out seeds, with confidence intervals and paired comparisons, and shows it live in a 3D dashboard.
* Scouts for wildfire ignitions: a thermal sweep of a block of dry scrub for smouldering hotspots, flown with 1, 4 and 16 drones on the same seeds, so the time a swarm saves in finding every hotspot is a measured number, not a claim.

## How we built it

* Python and numpy, with matplotlib for the charts. The dashboard is a standard-library web server and three.js.
* Nemotron runs on Nebius Token Factory through its OpenAI-compatible API, with a client that handles timeouts, retries, rate limits, response validation, logging and a spend guard.
* Every supervisor call is recorded, so the held-out Nemotron flights can be replayed exactly without a key. Mission-compiler and stress-search calls are kept in call logs.
* Seeds 0-9 were used for all tuning. Seeds 1000-1019 were each flown once: the conventional configurations first, then Nemotron, after its setup had been chosen on tuning seeds and committed.

## Results

All from simulated flights on held-out seeds 1000-1019, each flown once, 20 flights per scenario and configuration. The Nemotron setup was chosen on tuning seeds and committed before any Nemotron flight on these seeds.

* **Wildfire scouting, swarm size.** On 20 held-out blocks of 300 × 200 m with six smouldering hotspots each, one drone found the last hotspot after 536 s on average, four drones after 180 s and sixteen after 41 s. Seed by seed, sixteen drones were 13.3 times faster than one [95% interval 12.0 to 14.5], 8.3 minutes sooner on a sweep that takes one drone 11.6 minutes, for 30% more battery energy in total. Every flight found every hotspot with no crashes.
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

* Fire weather in the loop: have the supervisor re-plan the sweep from a wind forecast and send the nearest drone back to confirm a find, and measure whether that beats the fixed lawnmower.
* Calibrate the airframe, battery and thermal-camera models against flight logs and a real camera's detection range.
* Run the supervisor against real autopilots through a software-in-the-loop bridge, then on a Jetson at the edge.

## Built with

Python, numpy, matplotlib, three.js, NVIDIA Nemotron 3, Nebius Token Factory

## NVIDIA and Nebius usage

* Models: NVIDIA Nemotron 3 Nano (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, the main supervisor) and Nemotron 3 Super (`nvidia/nemotron-3-super-120b-a12b`), via Nebius Token Factory (`https://api.tokenfactory.nebius.com/v1`).
* Roles: supervisor, mission compiler, test engineer (see docs/NVIDIA_NEBIUS.md).
* Usage over the whole project, from the spend ledger: about 13,100 requests (13,066 logged by the client and 35 from the first live check, entered by hand), 22.3 million prompt tokens and 3.6 million completion tokens, an estimated $8.23 at price ceilings set above the listed prices. The ledger counts every attempt, retries included, and counts requests that timed out at their worst case.

## Feedback on Nebius Token Factory and NVIDIA models

Answers to the five questions the organisers ask, with the numbers behind them in `evidence/` and `results/`.

* **What we used them for.** Nemotron 3 Nano (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) and Nemotron 3 Super (`nvidia/nemotron-3-super-120b-a12b`) on Nebius Token Factory, in three roles: the swarm's slow-loop supervisor (a directive every 5 s of simulated time), the mission compiler (plain words to a checked plan, with refusals), and the test engineer in the stress search. About 13,100 requests over the project, 22.3 million prompt tokens, 3.6 million completion tokens, an estimated $8.23.
* **What worked well.** The OpenAI-compatible endpoint worked with a standard-library Python client and JSON mode. Nano with reasoning off answered supervisor calls in 1.17 s on average and Super in 1.37 s, with no failed calls in 6,874 on the held-out runs. The price let us measure instead of guess: a supervised flight cost about $0.004, so we could fly 20 held-out seeds per scenario and configuration and still afford tuning rounds and a 1,000-test stress search.
* **What needs work.** Reasoning is on by default, the reasoning comes back in `message.reasoning` and counts against `max_tokens`; when it runs out, `content` is null with `finish_reason: "length"`, which looks like a broken reply. Our first live check failed that way four times in 43 s before we found `enable_thinking`. A note in the quick-start, or a separate reasoning budget, would save newcomers the paid empty replies. Latency has a long tail: in one tuning round 12 of 826 Nano calls timed out at 8 s, and before we added a whole-call deadline 39 of 540 Super calls took over 30 s, the slowest 64 s. A per-request latency hint or a documented p99 would help anyone with a deadline.
* **How onboarding felt, zero to hello world.** Short, with one trap. Key in the environment, the model list returned 25 models (4 Nemotron), and the first supervisor call failed on the reasoning-token default above, four attempts in 43 s (12 October, 22:34 UTC, `evidence/first-attempt/`). With reasoning switched off by a two-line change (`evidence/thinking-off.patch`) the same check passed two minutes later, the supervisor answering in 0.97 s, and a full supervised flight made 30 calls with no fallbacks.
* **Would we build with them again, and why.** Yes. For a decision that can wait a few seconds, a 30B open model at this price and latency is the right tool, and running it on Token Factory meant no GPU to manage and every call logged with a request id for replay. We would keep the same rule: measure it against a conventional baseline on held-out seeds, because on five of nine scenarios the model made no difference, and on the mission compiler it clearly did (97.6% of fields right against 83.9% for the regex baseline).

## Gallery

Six images for the Devpost gallery, in this order, each taken at 1920 × 1080 once the Halo Systems name is in the dashboard header:

1. The wildfire sweep from above with the hotspots lit orange (a frame from `docs/wildfire.gif`).
2. The dashboard in the forest trail with Nemotron's directives in the decision log.
3. `docs/figures/wildfire-swarm-size-light.png`: time to find every hotspot with 1, 4 and 16 drones, with 95% intervals.
4. `docs/architecture.png`: the three loops.
5. The Mission tab with a compiled scouting mission beside a refused request.
6. `results/crashes.png`: drones lost per configuration on the held-out seeds.

## Updates during the submission period

Halo Systems (first named SwarmLab) was written during the submission period: its first commit is dated 12 October 2026. It reuses two things from the entrant's earlier private drone simulator:

* the airframe parameters (mass, thrust-to-weight, drag area, body radius, top speed) and its 17-minute hover endurance, which the power model is tuned to match;
* the passive formation law, ported as the "original" baseline controller.

Everything else is new: the simulator engine, wind, sensing and power models, the improved controller and safety filter, the supervisors, the Nemotron integration, the benchmark, the dashboard and the docs.
