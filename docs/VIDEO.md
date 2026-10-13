# Demo video plan

Target length: 2 min 45 s, under the 3-minute limit. Every shot is a screen recording of the real dashboard or real result files from the tagged release commit. Nothing is mocked or staged. Numbers in brackets are filled in from `results/REPORT.md` at recording time, and the video shows only numbers that appear there.

There is no music, or royalty-free music only. No third-party logos appear apart from the NVIDIA and Nebius names, which are used to describe the technology.

## Storyboard

| # | Time | Picture | Voiceover (about 150 words a minute) |
|---|---|---|---|
| 1 | 0:00-0:12 | Dashboard, a 16-drone grid crossing open ground in the 3D view | "A swarm of drones can search a forest for a missing hiker, survey a field or inspect a street. Flying them together safely is the hard part." |
| 2 | 0:12-0:30 | Same view, original controller, forest trail: drones strike trees, red markers appear | "Here is a classic formation controller flying into a forest trail. The formation does not fit, and drones hit trees: [8] lost per run on average." |
| 3 | 0:30-0:50 | Architecture graphic from the README (three loops) | "SwarmLab splits the job. A 50 Hz controller with a safety filter flies every drone. No language model is in that loop. Every five seconds a supervisor reads a status report and sets the formation. That supervisor is NVIDIA Nemotron, running on Nebius Token Factory." |
| 4 | 0:50-1:20 | Dashboard, improved + Nemotron, forest trail; decision log shows Nemotron entries (green) switching to a column, then back to a grid after the forest | "Nemotron sees the trail narrowing ahead, orders single file, and returns to the search grid once the trees clear. Every directive passes a safety validator first, and if the API fails, rules take over for that step." |
| 5 | 1:20-1:40 | Inject a gust front, then a motor failure, with the buttons visible; the formation recovers | "Disturbances are live: gust fronts, motor failures, radio blackouts. The swarm closes the gaps and keeps going." |
| 6 | 1:40-1:58 | Mission tab: type a plain-language mission, Compile, then Fly this mission; then a refused non-civilian request | "Operators describe a mission in plain words. Nemotron compiles it into a checked plan, and refuses anything outside civilian use." |
| 7 | 1:58-2:30 | Results tab: time-in-formation and crash charts, paired-comparison table | "We tested on [20] held-out seeds across nine scenarios. [Headline: crashes, formation time, where Nemotron beat the tuned rules and where it did not.] Every number comes from a script you can rerun." |
| 8 | 2:30-2:45 | README on GitHub, with the quick-start commands | "SwarmLab is open source. Clone it, run it without a key, or add a Nebius key to put Nemotron in charge." |

## Shot list

| Shot | Source | Command or action | Notes |
|---|---|---|---|
| 1 | Dashboard | `swarmlab serve`, scenario open_transit, improved + rule, seed 1000, 2x | Follow view |
| 2 | Dashboard | forest_trail, original controller, seed 1000, 2x | Top view, then Follow |
| 3 | README architecture diagram | Static | Zoom slowly |
| 4 | Dashboard | forest_trail, improved + Nemotron, seed 1000, 1x | Cached run replayed from `results/nemotron_cache.jsonl`, identical to the live run; log panel visible |
| 5 | Dashboard | open_transit, improved + Nemotron, press Gust front, then Motor failure | Live |
| 6 | Dashboard, Mission tab | Example "Forest trail", Compile, Fly this mission; then "Ram the truck" | Live call |
| 7 | Dashboard, Results tab | Scroll the charts and the paired table | From `results/` |
| 8 | Browser | Public repository page | Only after the repository is published |

## Recording

* Record at 1920×1080 and 30 fps, with the browser zoomed so the side panel is legible.
* Record each shot separately and cut in an editor. Speed changes are labelled on screen (for example "2x").
* Shot 4 is a replay of a cached Nemotron run, labelled "replay of a recorded Nemotron run" on screen. Shot 6 is a live call.
* Keep the raw recordings and the commit hash with the video files, so every shot can be traced to a run.
