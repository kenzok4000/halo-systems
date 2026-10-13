# Demo video plan

Target length: 2 min 45 s, under the 3-minute limit. Every shot is a screen recording of the real dashboard or real result files from the release commit. Nothing is mocked or staged. Every number in the voiceover appears in `results/REPORT.md`.

There is no music, or royalty-free music only. No third-party logos appear apart from the NVIDIA and Nebius names, which are used to describe the technology.

## Storyboard

| # | Time | Picture | Voiceover (about 150 words a minute) |
|---|---|---|---|
| 1 | 0:00-0:12 | Dashboard, a 16-drone grid crossing open ground in the 3D view | "A swarm of drones can search a forest for a missing hiker, survey a field or inspect a street. Flying them together safely is the hard part." |
| 2 | 0:12-0:30 | Same view, original controller, forest trail: drones strike trees, red markers appear | "Here is a classic formation controller flying into a forest trail. The formation does not fit, and drones hit trees: about eight lost per flight on average." |
| 3 | 0:30-0:50 | Architecture graphic from the README (three loops) | "SwarmLab splits the job. A 50 Hz controller with a safety filter flies every drone. No language model is in that loop. Every five seconds a supervisor reads a status report and sets the formation. That supervisor is NVIDIA Nemotron, running on Nebius Token Factory." |
| 4 | 0:50-1:20 | Dashboard, improved + Nemotron, forest trail; decision log shows Nemotron entries (green) switching to a column, then back to a grid after the forest | "Nemotron sees the trail narrowing ahead, orders single file, and returns to the search grid once the trees clear. Every directive passes a safety validator first, and if the API fails, rules take over for that step." |
| 5 | 1:20-1:40 | Inject a gust front, then a motor failure, with the buttons visible; the formation recovers. Caption: "rule supervisor" | "Disturbances are live: gust fronts, motor failures, radio blackouts. The swarm closes the gaps and keeps going." |
| 6 | 1:40-1:58 | Mission tab: type a plain-language mission, Compile, then Fly this mission; then a refused non-civilian request | "Operators describe a mission in plain words. Nemotron compiles it into a checked plan, and refuses anything outside civilian use." |
| 7 | 1:58-2:30 | Results tab: time-in-formation and crash charts, paired-comparison table | "We froze everything, then tested once on twenty unseen seeds across nine scenarios. The classic controller lost three hundred drones; ours, with a supervisor, lost none. Nemotron Nano beat the tuned rules in four scenarios and was never worse. The bigger Super model was worse in two. Every number comes from a script you can rerun." |
| 8 | 2:30-2:45 | README on GitHub, with the quick-start commands | "SwarmLab is open source. Clone it, run it without a key, or add a Nebius key to put Nemotron in charge." |

## Shot list

| Shot | Source | Command or action | Notes |
|---|---|---|---|
| 1 | Dashboard | `swarmlab serve`, scenario open_transit, improved + rule, seed 1000, 2x | Follow view |
| 2 | Dashboard | forest_trail, original controller, seed 1000, 2x | Top view, then Follow |
| 3 | README architecture diagram | Static | Zoom slowly |
| 4 | Dashboard | forest_trail, improved + Nemotron 3 Nano, seed 1000, 1x | `swarmlab serve --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B --cache results/nemotron_cache.jsonl`: the held-out run replayed from its recorded calls, identical to the live run. Nemotron orders a column at 11 s ("Column fits the corridor") and a grid again at 66 s; log panel visible |
| 5 | Dashboard | open_transit, improved + rule, press Gust front, then Motor failure | Rule supervisor: injected disturbances change the flight, so a recorded Nemotron run cannot be replayed through them |
| 6 | Dashboard, Mission tab | Example "Forest trail", Compile, Fly this mission; then "Ram the truck" | Live call, recorded where a key is set |
| 7 | Dashboard, Results tab | Scroll the charts and the paired table | From `results/` |
| 8 | Browser | Public repository page | Only after the repository is published |

## Recording

* Dashboard shots are filmed by `tools/record_shot.cjs`, which drives the running dashboard in headless Chromium through Playwright and screen-records the page (for example `node tools/record_shot.cjs --scenario forest_trail --config original+fixed --seed 1000 --speed 2 --seconds 20 --out video/shot2.webm`). It only clicks the same controls a person would, so every frame is the real dashboard flying a real simulation.
* Record at 1920×1080 and 30 fps, with the browser zoomed so the side panel is legible.
* Record each shot separately and cut in an editor. Speed changes are labelled on screen (for example "2x").
* Shot 4 is a replay of a recorded Nemotron run, labelled "replay of a recorded Nemotron run" on screen. Shot 5 is labelled "rule supervisor". Shot 6 is a live call.
* Keep the raw recordings and the commit hash with the video files, so every shot can be traced to a run.
