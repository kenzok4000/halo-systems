"""Draw the README's results figures from the committed results.

    python tools/make_figures.py      # from the repository folder; needs matplotlib

Writes docs/figures/<name>-light.png and <name>-dark.png. Every number is computed
here from the files in results/, with the statistics the benchmark report uses
(halo_systems.eval.stats: paired differences over the same seeds, 95% percentile
bootstrap with a fixed generator seed), and printed, so each figure can be checked
against the rows behind it.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from halo_systems.ai import tester  # noqa: E402
from halo_systems.eval.report import load_rows  # noqa: E402
from halo_systems.eval.stats import bootstrap_ci, paired, value  # noqa: E402

OUT = ROOT / "docs" / "figures"
NANO = "improved+nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
SUPER = "improved+nemotron:nvidia/nemotron-3-super-120b-a12b"
RULE = "improved+rule"
SCENARIOS = ["open_transit", "formation_changes", "forest_trail", "urban_canyon", "gusty_day",
             "comms_degraded", "motor_failures", "low_battery", "field_survey"]
SCENARIO_LABELS = {"open_transit": "Open transit", "formation_changes": "Formation changes",
                   "forest_trail": "Forest trail", "urban_canyon": "Urban canyon", "gusty_day": "Gusty day",
                   "comms_degraded": "Degraded radio", "motor_failures": "Motor failures",
                   "low_battery": "Low battery", "field_survey": "Field survey"}

# Chart chrome and the two series colours, per theme (validated for colour-vision deficiency on each surface).
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
              "axis": "#c3c2b7", "s1": "#2a78d6", "s2": "#eb6834", "context": "#c3c2b7"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
             "axis": "#383835", "s1": "#3987e5", "s2": "#d95926", "context": "#5a5955"},
}


def style(ax, t, xgrid=True):
    ax.set_facecolor(t["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.tick_params(colors=t["ink2"], length=0, labelsize=9)
    if xgrid:
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        ax.set_axisbelow(True)


def title(ax, t, text, sub=None):
    ax.set_title(text, loc="left", color=t["ink"], fontsize=11, fontweight="bold", pad=22 if sub else 8)
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes, color=t["ink2"], fontsize=8.5, va="bottom")


def save(fig, name, theme):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}-{theme}.png"
    fig.savefig(path, dpi=200, facecolor=fig.get_facecolor(), metadata={"Software": None})
    plt.close(fig)
    return path


# --- data -------------------------------------------------------------------------------------------------

def stress_data():
    searchers = [("random", "Random search"), ("cem", "Cross-entropy method"), ("corners", "Corner search"),
                 ("nemotron-think", "Nemotron 3 Nano, reasoning on"), ("nemotron", "Nemotron 3 Nano")]
    out = []
    for key, label in searchers:
        files = sorted(glob.glob(str(ROOT / "results" / "stress" / f"{key}_[0-4].json")))
        history = [h for f in files for h in json.loads(Path(f).read_text())["history"]]
        s = tester.summarise_search(history)
        fails = [h for h in history if h["outcome"]["failed"]]
        out.append({"key": key, "label": label, "searches": len(files), "flights": len(history),
                    "failures": s["failures"],
                    "distinct": len({json.dumps(tester.clip_params(h["params"]), sort_keys=True)
                                     for h in fails}),
                    "kinds": len(tester.failure_kinds(history)),
                    "mean_stress": s["mean_failure_stress"], "mildest": s["mildest_failure_stress"]})
    return out


def safety_data():
    rows = load_rows(ROOT / "results" / "runs.jsonl")
    configs = [("original+fixed", "Original controller"), ("improved+fixed", "Improved, no supervisor"),
               (RULE, "Improved + rules"), (NANO, "Improved + Nemotron 3 Nano"), (SUPER, "Improved + Nemotron 3 Super")]
    held = [r for r in rows if "error" not in r and 1000 <= r["seed"] <= 1019]
    crashes = []
    for cfg, label in configs:
        rs = [r for r in held if r["config"] == cfg]
        crashes.append({"label": label, "flights": len(rs), "crashes": int(sum(value(r, "crashes") for r in rs)),
                        "near_misses": int(sum(r.get("near_misses", 0) for r in rs))})
    with_filter = without = 0
    fails_with = fails_without = tests = 0
    for f in sorted(glob.glob(str(ROOT / "results" / "stress" / "*_[0-4].json"))):
        base = Path(f).stem
        other = ROOT / "results" / "stress" / "no_safety_filter" / f"{base}_nocbf.json"
        a, b = json.loads(Path(f).read_text())["history"], json.loads(other.read_text())["history"]
        tests += len(a)
        with_filter += sum(tester._lost(h["outcome"]) for h in a)
        without += sum(tester._lost(h["outcome"]) for h in b)
        fails_with += sum(h["outcome"]["failed"] for h in a)
        fails_without += sum(h["outcome"]["failed"] for h in b)
    refly = {"tests": tests, "lost_with": with_filter, "lost_without": without,
             "failures_with": fails_with, "failures_without": fails_without}
    return crashes, refly


def wildfire_data():
    rows = [r for r in load_rows(ROOT / "results" / "wildfire" / "runs.jsonl") if "error" not in r]
    by_n = {}
    for r in rows:
        by_n.setdefault(r["n"], {})[r["seed"]] = r
    sizes = []
    for n in sorted(by_n):
        rs = list(by_n[n].values())
        mean, lo, hi = bootstrap_ci([r["all_found_s"] for r in rs])
        sizes.append({"n": n, "flights": len(rs), "all_found": mean, "lo": lo, "hi": hi,
                      "first_find": bootstrap_ci([r["first_find_s"] for r in rs])[0],
                      "energy_wh": bootstrap_ci([r["energy_wh"] for r in rs])[0],
                      "found_pct": bootstrap_ci([r["targets_found_pct"] for r in rs])[0],
                      "lost": int(sum(value(r, "crashes") for r in rs)),
                      "near_misses": int(sum(r.get("near_misses", 0) for r in rs))})
    # Seed by seed: how many times sooner the largest swarm found the last hotspot than one drone.
    one, big = by_n[min(by_n)], by_n[max(by_n)]
    seeds = sorted(set(one) & set(big))
    ratio = bootstrap_ci([one[s]["all_found_s"] / big[s]["all_found_s"] for s in seeds])
    sooner = bootstrap_ci([(one[s]["all_found_s"] - big[s]["all_found_s"]) / 60.0 for s in seeds])
    energy = sum(big[s]["energy_wh"] for s in seeds) / sum(one[s]["energy_wh"] for s in seeds)
    return sizes, {"seeds": len(seeds), "ratio": ratio, "minutes_sooner": sooner,
                   "one_drone_minutes": bootstrap_ci([one[s]["completion_time_s"] / 60.0 for s in seeds])[0],
                   "energy_ratio": energy}


def supervisor_data():
    rows = load_rows(ROOT / "results" / "runs.jsonl")
    metrics = [("time_formed_pct", "Time in formation", "percentage points", +1, False),
               ("completion_time_s", "Completion time", "seconds", -1, False),
               ("energy_wh_per_km", "Energy per drone-km", "% of the rules' energy", -1, True)]
    out = {}
    for metric, label, unit, better, relative in metrics:
        out[metric] = {"label": label, "unit": unit, "better": better, "rows": []}
        for sc in SCENARIOS:
            for cfg, who in ((NANO, "Nano"), (SUPER, "Super")):
                p = paired(rows, sc, RULE, cfg, metric)
                if p is None:
                    continue
                scale = 100.0 / p["a_mean"] if relative and p["a_mean"] else 1.0
                out[metric]["rows"].append({"scenario": sc, "who": who, "diff": p["mean_diff"] * scale,
                                            "lo": p["lo"] * scale, "hi": p["hi"] * scale, "n": p["n"],
                                            "better": p["b_better"], "ties": p["ties"], "worse": p["b_worse"]})
    return out


# --- figures ----------------------------------------------------------------------------------------------

def fig_stress(data, theme):
    t = THEMES[theme]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.4), gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.08})
    fig.set_facecolor(t["surface"])
    y = list(range(len(data)))
    labels = [d["label"] for d in data]
    colours = [t["s1"] if d["key"] == "nemotron" else (t["s2"] if d["key"] == "nemotron-think" else t["context"])
               for d in data]
    style(a1, t)
    a1.barh(y, [d["distinct"] for d in data], height=0.55, color=colours)
    for yi, d in zip(y, data):
        a1.text(d["distinct"] + 0.6, yi, f'{d["distinct"]}', va="center", color=t["ink"], fontsize=9)
    a1.set_yticks(y, labels)
    a1.set_xlim(0, max(d["distinct"] for d in data) * 1.15)
    title(a1, t, "Distinct failing conditions found", f'{data[0]["searches"]} searches x 40 flights per searcher')

    style(a2, t)
    for yi, d, c in zip(y, data, colours):
        a2.plot([d["mildest"], d["mean_stress"]], [yi, yi], color=t["axis"], linewidth=2, solid_capstyle="round")
        a2.scatter([d["mildest"]], [yi], s=46, facecolor=t["surface"], edgecolor=c if c != t["context"] else t["muted"],
                   linewidth=2, zorder=3)
        a2.scatter([d["mean_stress"]], [yi], s=46, color=c if c != t["context"] else t["muted"], zorder=3,
                   edgecolor=t["surface"], linewidth=1.5)
    a2.set_yticks(y, [""] * len(y))
    a2.set_xlim(0, 1)
    a2.text(0.01, -0.85, "open dot: mildest failure    filled dot: mean of failures", color=t["ink2"], fontsize=8)
    title(a2, t, "How hard the failing conditions were", "stress level: 0 = every condition easy, 1 = every condition hard")
    fig.subplots_adjust(left=0.2, right=0.98, top=0.8, bottom=0.12)
    return save(fig, "stress-search", theme)


def fig_supervisor(data, theme):
    t = THEMES[theme]
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.6), sharey=True, gridspec_kw={"wspace": 0.1})
    fig.set_facecolor(t["surface"])
    for ax, (metric, d) in zip(axes, data.items()):
        style(ax, t)
        ax.axvline(0, color=t["axis"], linewidth=1, zorder=1)
        for r in d["rows"]:
            yi = SCENARIOS.index(r["scenario"]) + (-0.14 if r["who"] == "Nano" else 0.14)
            c = t["s1"] if r["who"] == "Nano" else t["s2"]
            ax.plot([r["lo"], r["hi"]], [yi, yi], color=c, linewidth=2, solid_capstyle="round", zorder=2)
            ax.scatter([r["diff"]], [yi], s=40, color=c, edgecolor=t["surface"], linewidth=1.5, zorder=3)
        lim = max(1e-6, max(max(abs(r["lo"]), abs(r["hi"])) for r in d["rows"])) * 1.15
        ax.set_xlim(-lim, lim)
        arrow = "better →" if d["better"] > 0 else "← better"
        ax.text(0.98 if d["better"] > 0 else 0.02, -0.12, arrow, transform=ax.transAxes, color=t["ink2"], fontsize=8.5,
                ha="right" if d["better"] > 0 else "left")
        title(ax, t, d["label"], d["unit"])
    axes[0].set_yticks(range(len(SCENARIOS)), [SCENARIO_LABELS[s] for s in SCENARIOS])
    axes[0].invert_yaxis()
    handles = [plt.Line2D([], [], color=t["s1"], marker="o", linewidth=2, markersize=6, label="Nemotron 3 Nano minus rules"),
               plt.Line2D([], [], color=t["s2"], marker="o", linewidth=2, markersize=6, label="Nemotron 3 Super minus rules")]
    leg = fig.legend(handles=handles, loc="lower left", ncol=2, frameon=False, fontsize=8.5, bbox_to_anchor=(0.16, 0.0))
    for txt in leg.get_texts():
        txt.set_color(t["ink"])
    fig.subplots_adjust(left=0.13, right=0.98, top=0.84, bottom=0.2)
    return save(fig, "supervisor-vs-rules", theme)


def fig_safety(crashes, refly, theme):
    t = THEMES[theme]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.0), gridspec_kw={"width_ratios": [1.4, 1], "wspace": 0.55})
    fig.set_facecolor(t["surface"])
    style(a1, t)
    y = list(range(len(crashes)))
    colours = [t["s2"] if i == 0 else t["s1"] for i in y]
    a1.barh(y, [c["crashes"] for c in crashes], height=0.55, color=colours)
    top = max(c["crashes"] for c in crashes)
    for yi, c in zip(y, crashes):
        a1.text(c["crashes"] + top * 0.015, yi, f'{c["crashes"]}', va="center", color=t["ink"], fontsize=9)
    a1.set_yticks(y, [c["label"] for c in crashes])
    a1.invert_yaxis()
    a1.set_xlim(0, top * 1.15)
    title(a1, t, "Drones lost in crashes", f'{crashes[0]["flights"]} held-out flights per configuration, nine scenarios')

    style(a2, t)
    vals = [refly["lost_with"], refly["lost_without"]]
    a2.barh([0, 1], vals, height=0.55, color=[t["s1"], t["s2"]])
    for yi, v in zip([0, 1], vals):
        a2.text(v + max(vals) * 0.02, yi, f"{v:,}", va="center", color=t["ink"], fontsize=9)
    a2.set_yticks([0, 1], ["With the safety filter", "Without it"])
    a2.set_ylim(2.0, -1.0)  # inverted, with room so the two bars are as thick as the five on the left
    a2.set_xlim(0, max(vals) * 1.2)
    title(a2, t, "Drones lost in the stress tests", f'the same {refly["tests"]:,} tests flown twice')
    fig.subplots_adjust(left=0.2, right=0.98, top=0.78, bottom=0.12)
    return save(fig, "safety", theme)


def fig_wildfire(sizes, theme):
    t = THEMES[theme]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 2.6), gridspec_kw={"width_ratios": [1.5, 1], "wspace": 0.35})
    fig.set_facecolor(t["surface"])
    y = list(range(len(sizes)))
    labels = [f'{s["n"]} drone' + ("" if s["n"] == 1 else "s") for s in sizes]
    colours = [t["s1"] if s["n"] == max(x["n"] for x in sizes) else t["context"] for s in sizes]
    style(a1, t)
    a1.barh(y, [s["all_found"] for s in sizes], height=0.55, color=colours)
    top = max(s["hi"] for s in sizes)
    for yi, s in zip(y, sizes):
        a1.plot([s["lo"], s["hi"]], [yi, yi], color=t["ink2"], linewidth=1.2, solid_capstyle="round")
        a1.text(s["hi"] + top * 0.02, yi, f'{s["all_found"]:.0f} s', va="center", color=t["ink"], fontsize=9)
    a1.set_yticks(y, labels)
    a1.invert_yaxis()
    a1.set_xlim(0, top * 1.15)
    title(a1, t, "Time to find all six hotspots",
          f'seconds, mean over {sizes[0]["flights"]} held-out blocks with 95% interval; every flight found every hotspot')
    style(a2, t)
    a2.barh(y, [s["energy_wh"] for s in sizes], height=0.55, color=colours)
    etop = max(s["energy_wh"] for s in sizes)
    for yi, s in zip(y, sizes):
        a2.text(s["energy_wh"] + etop * 0.02, yi, f'{s["energy_wh"]:.1f} Wh', va="center", color=t["ink"], fontsize=9)
    a2.set_yticks(y, labels)
    a2.invert_yaxis()
    a2.set_xlim(0, etop * 1.3)
    title(a2, t, "Battery energy for the block", "watt-hours, all drones together")
    fig.subplots_adjust(left=0.1, right=0.98, top=0.74, bottom=0.14)
    return save(fig, "wildfire-swarm-size", theme)


def main() -> int:
    stress = stress_data()
    crashes, refly = safety_data()
    sup = supervisor_data()
    wild, wild_pair = wildfire_data()
    print("Stress search (results/stress/):")
    for d in stress:
        print(f'  {d["label"]:32s} flights {d["flights"]:3d}  failures {d["failures"]:3d}  distinct {d["distinct"]:3d}  '
              f'kinds {d["kinds"]:2d}  mean stress {d["mean_stress"]}  mildest {d["mildest"]}')
    print("Held-out crashes (results/runs.jsonl):")
    for c in crashes:
        print(f'  {c["label"]:30s} flights {c["flights"]}  drones lost {c["crashes"]}  near misses {c["near_misses"]}')
    print(f'Stress tests re-flown without the safety filter: {refly}')
    print("Paired differences against the rules (95% CI):")
    for metric, d in sup.items():
        for r in d["rows"]:
            if abs(r["diff"]) > 1e-9 or abs(r["lo"]) > 1e-9 or abs(r["hi"]) > 1e-9:
                print(f'  {d["label"]:22s} {r["scenario"]:18s} {r["who"]:5s} {r["diff"]:+8.2f} '
                      f'[{r["lo"]:+.2f}, {r["hi"]:+.2f}] {d["unit"]}  better/tie/worse {r["better"]}/{r["ties"]}/{r["worse"]}')
    print("Wildfire scout, swarm size (results/wildfire/runs.jsonl):")
    for s in wild:
        print(f'  {s["n"]:2d} drones  flights {s["flights"]}  hotspots found {s["found_pct"]:.0f}%  '
              f'all found {s["all_found"]:.1f} s [{s["lo"]:.1f}, {s["hi"]:.1f}]  first find {s["first_find"]:.1f} s  '
              f'energy {s["energy_wh"]:.1f} Wh  drones lost {s["lost"]}  near misses {s["near_misses"]}')
    r, m = wild_pair["ratio"], wild_pair["minutes_sooner"]
    print(f'  seed by seed over {wild_pair["seeds"]} seeds: the largest swarm found the last hotspot {r[0]:.2f} times faster '
          f'[{r[1]:.2f}, {r[2]:.2f}], {m[0]:.2f} min earlier [{m[1]:.2f}, {m[2]:.2f}]; one drone takes '
          f'{wild_pair["one_drone_minutes"]:.2f} min to finish; energy ratio {wild_pair["energy_ratio"]:.3f}')
    for theme in THEMES:
        for path in (fig_stress(stress, theme), fig_supervisor(sup, theme), fig_safety(crashes, refly, theme),
                     fig_wildfire(wild, theme)):
            print("wrote", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
