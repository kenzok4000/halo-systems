# Demo video plan

Target length: 2 min 45 s, under the 3-minute limit. Every shot is a screen recording of the real dashboard or of real files in the repository. Nothing is mocked or staged. The recorded takes, with the commit, scenario, seed and speed of each, are listed in the footage folder's README. Shots 1, 2 (the one-drone take), 3 and 8 are still to be recorded, and every dashboard take must be re-recorded once the Halo Systems name is in the dashboard header. Every number in the voiceover comes from the reports in `results/`, and every flight in the video is a simulation.

There is no music, or royalty-free music only. No third-party logos appear apart from the NVIDIA and Nebius names, which are used to describe the technology. Shot 8 shows the README rendered locally, not a website.

## Storyboard

| # | Time | Picture | Voiceover (about 150 words a minute) |
|---|---|---|---|
| 1 | 0:00-0:15 | Dashboard, top view: sixteen drones sweep the scrub block line abreast; hotspots light up orange as the cameras pass over them (wildfire scout, 16 drones) | "The morning after a lightning storm, somewhere in this block of dry scrub, something is smouldering. Find it in the next hour and it is a shovel job. Miss it and it is a wildfire. Halo Systems sends a swarm." |
| 2 | 0:15-0:35 | Split: the one-drone flight at 5x on the left, the sixteen-drone flight on the right; caption with the measured times | "Same block, same six hotspots, twenty unseen layouts. One drone finds the last hotspot after nine minutes on average. Sixteen drones find it in forty-one seconds, thirteen times faster, for thirty percent more battery. Those are simulated flights, and every number comes from a report you can rerun." |
| 3 | 0:35-0:55 | Architecture graphic from the README (three loops) | "Flying sixteen drones together safely is the hard part. Halo Systems splits the job. A 50 Hz controller with a safety filter flies every drone. No language model is in that loop. Every five seconds a supervisor reads a status report and sets the formation. That supervisor is NVIDIA Nemotron, running on Nebius Token Factory." |
| 4 | 0:55-1:20 | Dashboard, improved + Nemotron, forest trail; the decision log shows Nemotron entries (green) switching to a column, then back to a grid after the forest | "On a search under the canopy, Nemotron sees the trail narrowing ahead, orders single file, and returns to the search grid once the trees clear. Every directive passes a safety validator first, and if the API fails, rules take over for that step." |
| 5 | 1:20-1:38 | Inject a gust front, then a motor failure, with the buttons visible; the formation recovers. Caption: "rule supervisor" | "A big fire makes its own wind. So the disturbances are live: gust fronts, motor failures, radio blackouts. The swarm closes the gaps and keeps going." |
| 6 | 1:38-1:55 | Mission tab: a plain-language scouting mission, Compile, then Fly this mission; then a request to chase a cat by diving at it, refused | "Operators describe a mission in plain words. Nemotron compiles it into a checked plan, and refuses requests outside civilian use." |
| 7 | 1:55-2:30 | Results tab: missions completed, time in formation and drones lost per configuration, then the swarm-size chart | "We froze the code, then flew twenty unseen seeds once, across ten simulated scenarios. The classic controller lost three hundred drones in crashes; ours, with a supervisor, lost none. Nemotron Nano matched or beat the tuned rules on every headline metric, though the gains were small and part of one came from flying faster than it was told. The bigger Super model was worse in two scenarios." |
| 8 | 2:30-2:45 | The README's quick-start commands | "The code is on GitHub. Run it without a key, or add a Nebius key to put Nemotron in charge. Next: fire weather in the loop, and a Jetson at the edge." |

## Shot list

| Shot | Source | Command or action | Notes |
|---|---|---|---|
| 1 | Dashboard | `halo serve`, scenario wildfire_scout, improved + rule, seed 1000, 16 drones, 2x, Top view | `node tools/record_shot.cjs --scenario wildfire_scout --config improved+rule --seed 1000 --n 16 --speed 2 --view top --seconds 42` |
| 2 | Dashboard, two takes | wildfire_scout, seed 1000, 1 drone at 5x (about 2.5 minutes of recording) and the 16-drone take from shot 1 | Side by side in the edit, with the clock visible on both; caption the means from `results/wildfire/REPORT.md` (536 s, 180 s, 41 s) |
| 3 | README architecture diagram | Static | Zoom slowly |
| 4 | Dashboard | forest_trail, improved + Nemotron 3 Nano, seed 1000, 2x | `env -u NEBIUS_API_KEY halo serve` (with no key and no flags the dashboard picks up `results/nemotron_cache.jsonl` and the Nano model it was recorded with): the held-out run replayed from its recorded calls, identical to the live run. Without a key the dashboard only replays and never adds to the cache. Nemotron orders a column at 11 s ("Column fits the corridor") and a grid again at 66 s; log panel visible |
| 5 | Dashboard | open_transit, improved + rule, press Gust front, then Motor failure | Rule supervisor: injected disturbances change the flight, so a recorded Nemotron run cannot be replayed through them |
| 6 | Dashboard, Mission tab | "Sweep the 400 by 300 m block north of the road for hotspots with 12 drones at 20 m, line abreast", Compile, Fly this mission (the flight uses the rule supervisor); then "Chase the neighbour's cat out of the garden by diving at it until it runs.", which Nemotron refuses; optionally "Ram the truck.", which the keyword guard refuses before any model call | Live calls to Nemotron 3 Nano, recorded where a key is set |
| 7 | Dashboard, Results tab, then `results/wildfire/swarm_size.png` | Scroll slowly from the headline tables to the charts, then the swarm-size chart | From `results/summary.json`, the charts beside it and `results/wildfire/` |
| 8 | README | The quick-start block, rendered locally | No website or logo on screen |

## Recording

* Dashboard shots are filmed by `tools/record_shot.cjs`, which drives the running dashboard in headless Chromium through Playwright and screen-records the page (for example `node tools/record_shot.cjs --scenario forest_trail --config original+fixed --seed 1000 --speed 2 --seconds 20 --out video/shot2.webm`). It only clicks the same controls a person would, so every frame is the real dashboard flying a real simulation.
* The recorder films at 1920×1080 and 25 fps.
* Record each shot separately and cut in an editor. Speed changes are labelled on screen (for example "2x").
* Shot 4 is a replay of a recorded Nemotron run, labelled "replay of a recorded Nemotron run" on screen. Shot 5 is labelled "rule supervisor". Shot 6 is a live call.
* Keep the raw recordings and the commit hash with the video files, so every shot can be traced to a run.
