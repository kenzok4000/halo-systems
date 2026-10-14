# Documentation

Read in this order:

1. [RESULTS.md](RESULTS.md): every result in full, with confidence intervals, seed-by-seed comparisons, figures, and where the AI did worse.
2. [WILDFIRE.md](WILDFIRE.md): the wildfire valley: terrain, fire spread, smoke and heat detection, the forecast, the zone alerts, the models of today's ways of finding fires, every source, and what is not modelled.
3. [ARCHITECTURE.md](ARCHITECTURE.md): the three loops, the simulator, the controllers, the supervisors, the safety validator, and known simplifications.
4. [NVIDIA_NEBIUS.md](NVIDIA_NEBIUS.md): how Nemotron and Nebius Token Factory are used, why the model is not in the control loop, reliability, reasoning on or off, the spend guard and exact replay.
5. [BENCHMARKS.md](BENCHMARKS.md): the scenarios, configurations, protocol, metrics and the stress-test search.
6. [REPRODUCE.md](REPRODUCE.md): every command behind `results/`, and how to check a result against the committed rows.
7. [CODE_TOUR.md](CODE_TOUR.md): the source in the order a flight runs.

The scout drone's priced design (CAD model, parts list, running costs and build guide) is in [hardware/](../hardware/README.md).

Submission material: [DEVPOST.md](DEVPOST.md) is the Devpost text and [VIDEO.md](VIDEO.md) the demo video's script and shot list.
