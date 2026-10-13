"""Benchmark report: markdown tables and charts from runner rows.

``swarmlab report runs.jsonl --out results/`` writes ``results/REPORT.md``,
``results/summary.json`` and PNG charts (charts need the optional
``matplotlib`` extra; tables do not). Every number comes from the rows
file; nothing is typed in by hand.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from .. import jsonl
from .stats import METRICS, paired, summarise

CONFIG_LABELS = {
    "original+fixed": "Original controller",
    "improved+fixed": "Improved controller",
    "improved+rule": "Improved + rule supervisor",
    "original+rule": "Original + rule supervisor",
}
TABLE_METRICS = ("time_formed_pct", "formation_error_mean_m", "crashes", "near_misses", "completed",
                 "completion_time_s", "energy_wh_per_km", "drones_retired", "time_faster_than_plan_pct")
COLORS = ["#8c8c8c", "#4c78a8", "#f58518", "#54a24b", "#b279a2", "#e45756", "#72b7b2", "#eeca3b"]


def label(config: str) -> str:
    if config in CONFIG_LABELS:
        return CONFIG_LABELS[config]
    ctrl, _, sup = config.partition("+")
    if ctrl.startswith("improved-no-"):
        ctrl_label = "Improved without " + ", ".join(ctrl[len("improved-no-"):].split("-"))
    else:
        ctrl_label = {"improved": "Improved", "original": "Original"}.get(ctrl, ctrl)
    if sup.startswith("nemotron"):
        kind, _, model = sup.partition(":")
        detail = ", ".join(x for x in (model.split("/")[-1] if model else "",
                                       "reasoning on" if kind == "nemotron-think" else "") if x)
        return f"{ctrl_label} + Nemotron supervisor" + (f" ({detail})" if detail else "")
    return f"{ctrl_label} + " + {"rule": "rule supervisor", "fixed": "fixed plan"}.get(sup, sup)


def short_label(config: str) -> str:
    """A label short enough for a chart legend with five entries in a row."""
    ctrl, _, sup = config.partition("+")
    if ctrl.startswith("improved-no-"):
        names = {"cbf": "CBF", "filter": "state filter"}
        ctrl_label = "No " + ", ".join(names.get(f, f) for f in ctrl[len("improved-no-"):].split("-"))
    else:
        ctrl_label = {"improved": "Improved", "original": "Original"}.get(ctrl, ctrl)
    if sup.startswith("nemotron"):
        kind, _, model = sup.partition(":")
        name = model.split("/")[-1]
        size = re.search(r"nano|super|ultra", name, re.I)
        name = f"Nemotron {size.group(0).capitalize()}" if size else (name or "Nemotron")
        return f"{ctrl_label} + {name}" + (", reasoning" if kind == "nemotron-think" else "")
    return f"{ctrl_label} + " + {"rule": "rules", "fixed": "fixed plan"}.get(sup, sup)


def split_variants(rows: list[dict]) -> list[dict]:
    """Rows of one scenario flown with different swarm sizes or GPS error get their own table rows.

    The scaling and noise sweeps vary these within a scenario; merging them would average unlike runs.
    """
    sizes, scales = {}, {}
    for r in rows:
        sizes.setdefault(r["scenario"], set()).add(r.get("n"))
        scales.setdefault(r["scenario"], set()).add(r.get("gps_scale", 1.0))
    out = []
    for r in rows:
        sc = r["scenario"]
        name = sc
        if len(sizes[sc]) > 1:
            name += f", {r.get('n')} drones"
        if len(scales[sc]) > 1:
            name += f", GPS error x{r.get('gps_scale', 1.0):g}"
        out.append(r if name == sc else {**r, "scenario": name, "base_scenario": sc})
    return out


def load_rows(path) -> list[dict]:
    skipped = []
    rows = jsonl.read(path, skipped)
    if skipped:
        print(f"warning: skipped {len(skipped)} unreadable line(s) in {path}", file=sys.stderr)
    return rows


def _fmt(cell, metric) -> str:
    if cell is None or cell.get("mean") is None:
        return "n/a"
    m, lo, hi = cell["mean"], cell["lo"], cell["hi"]
    digits = 0 if metric in ("time_formed_pct", "completed", "completion_time_s", "time_faster_than_plan_pct") else 2
    if lo is None or hi is None or abs(hi - lo) < 10 ** -digits / 2:
        return f"{m:.{digits}f}"
    return f"{m:.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"


def tables(rows, summary) -> str:
    scenarios = sorted({r["scenario"] for r in rows if "error" not in r}, key=_scenario_order)
    configs = _config_order({r["config"] for r in rows if "error" not in r})
    out = []
    for sc in scenarios:
        seeds = sorted({r["seed"] for r in rows if r.get("scenario") == sc and "error" not in r})
        out.append(f"### {sc}\n\nMean over {len(seeds)} seeds, with 95% bootstrap intervals.\n")
        out.append("| Configuration | " + " | ".join(METRICS[m][0] for m in TABLE_METRICS) + " |")
        out.append("|" + "---|" * (len(TABLE_METRICS) + 1))
        for cfg in configs:
            cell = summary.get((sc, cfg))
            if not cell:
                continue
            out.append(f"| {label(cfg)} | " + " | ".join(_fmt(cell.get(m), m) for m in TABLE_METRICS) + " |")
        out.append("")
    return "\n".join(out)


def comparisons(rows, pairs) -> str:
    scenarios = sorted({r["scenario"] for r in rows if "error" not in r}, key=_scenario_order)
    out = ["| Scenario | Comparison | Metric | A | B | B - A [95% CI] | B better / tie / worse |", "|---|---|---|---|---|---|---|"]
    for sc in scenarios:
        for a, b in pairs:
            for m in ("time_formed_pct", "crashes", "completion_time_s", "energy_wh_per_km"):
                p = paired(rows, sc, a, b, m)
                if p is None or p["n"] == 0:
                    continue
                ci = f"{p['mean_diff']:+.2f} [{p['lo']:+.2f}, {p['hi']:+.2f}]"
                out.append(f"| {sc} | {label(b)} vs {label(a)} | {METRICS[m][0]} | {p['a_mean']:.2f} | {p['b_mean']:.2f} | {ci} | "
                           f"{p['b_better']} / {p['ties']} / {p['b_worse']} |")
    return "\n".join(out)


_ORDER = ["open_transit", "formation_changes", "forest_trail", "urban_canyon", "gusty_day", "comms_degraded",
          "motor_failures", "low_battery", "field_survey"]


def _scenario_order(name):
    base, _, rest = name.partition(", ")
    nums = [float(x) for x in "".join(c if (c.isdigit() or c == ".") else " " for c in rest).split()]
    return (_ORDER.index(base) if base in _ORDER else len(_ORDER), base, nums)


def _config_order(configs):
    pref = ["original+fixed", "improved+fixed", "original+rule", "improved+rule"]
    return sorted(configs, key=lambda c: (pref.index(c) if c in pref else len(pref), "nemotron" in c, c))


def ai_usage(rows) -> str:
    """Model calls, fallbacks, latency, tokens and cost per Nemotron configuration, from the run rows."""
    ai = [r for r in rows if r.get("ai_calls")]
    if not ai:
        return ""
    configs = _config_order({r["config"] for r in ai})
    out = ["| Configuration | Runs | Calls per run | Fallbacks to rules | Latency, mean (s) | Latency, 95th pct (s) "
           "| Tokens per run | Cost per run (USD) | Cost, all runs (USD) |", "|---|---|---|---|---|---|---|---|---|"]
    for cfg in configs:
        rs = [r for r in ai if r["config"] == cfg]
        calls = sum(r["ai_calls"] for r in rs)

        def mean(k):
            vals = [r[k] for r in rs if r.get(k) is not None]
            return sum(vals) / len(vals) if vals else float("nan")
        fb = sum(r.get("ai_fallbacks") or 0 for r in rs)
        cost = sum(r.get("ai_cost_usd") or 0.0 for r in rs)
        out.append(f"| {label(cfg)} | {len(rs)} | {calls / len(rs):.1f} | {fb} of {calls} ({100 * fb / max(calls, 1):.1f}%) "
                   f"| {mean('ai_latency_mean_s'):.2f} | {mean('ai_latency_p95_s'):.2f} | {mean('ai_tokens'):,.0f} "
                   f"| {cost / len(rs):.4f} | {cost:.4f} |")
    out.append("\nLatency includes retries. The 95th percentile column averages each run's own 95th percentile. "
               "Costs are estimates from the token counts and the price table in `ai/budget.py`; the provider's bill is "
               "the authority.")
    return "\n".join(out)


def separation(rows, pairs) -> str:
    """For each scenario, which comparisons differ beyond noise on the main metrics.

    A difference counts when the 95% interval of the seed-by-seed difference excludes zero and, for
    continuous metrics, the mean difference is over 1% of the baseline (so 0.01 Wh/km does not count).
    """
    scenarios = sorted({r["scenario"] for r in rows}, key=_scenario_order)
    if not pairs:
        return "No two configurations in this file are compared."
    out = []
    for sc in scenarios:
        lines = []
        for a, b in pairs:
            better, worse = [], []
            for m in ("time_formed_pct", "crashes", "completed", "completion_time_s", "energy_wh_per_km"):
                p = paired(rows, sc, a, b, m)
                if p is None or p["n"] < 2 or p["lo"] is None or not (p["lo"] > 0 or p["hi"] < 0):
                    continue
                if m not in ("crashes", "completed") and abs(p["mean_diff"]) <= 0.01 * abs(p["a_mean"]):
                    continue
                (better if (p["mean_diff"] > 0) == (METRICS[m][1] > 0) else worse).append(
                    f"{METRICS[m][0]} {p['a_mean']:.4g} → {p['b_mean']:.4g}")
            if better or worse:
                parts = ([f"better on {', '.join(better)}"] if better else []) + ([f"worse on {', '.join(worse)}"] if worse else [])
                lines.append(f"  * {label(b)} vs {label(a)}: " + "; ".join(parts))
        out.append(f"* **{sc}**" + (":\n" + "\n".join(lines) if lines else
                                     ": no comparison differs beyond noise on the main metrics."))
    return "\n".join(out)


def charts(rows, summary, out: Path) -> list[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    made = []
    scenarios = sorted({r["scenario"] for r in rows if "error" not in r}, key=_scenario_order)
    configs = _config_order({r["config"] for r in rows if "error" not in r})
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    for metric, fname in (("time_formed_pct", "time_in_formation.png"), ("crashes", "crashes.png"),
                          ("completion_time_s", "completion_time.png"), ("energy_wh_per_km", "energy.png")):
        fig, ax = plt.subplots(figsize=(10, 3.6))
        width = 0.8 / max(1, len(configs))
        for k, cfg in enumerate(configs):
            xs, ys, err_lo, err_hi = [], [], [], []
            for i, sc in enumerate(scenarios):
                cell = summary.get((sc, cfg), {}).get(metric)
                if not cell or cell["mean"] is None:
                    continue
                xs.append(i + (k - (len(configs) - 1) / 2) * width)
                ys.append(cell["mean"])
                err_lo.append(max(0.0, cell["mean"] - (cell["lo"] if cell["lo"] is not None else cell["mean"])))
                err_hi.append(max(0.0, (cell["hi"] if cell["hi"] is not None else cell["mean"]) - cell["mean"]))
            ax.bar(xs, ys, width, label=short_label(cfg), color=COLORS[k % len(COLORS)], yerr=[err_lo, err_hi],
                   capsize=2, error_kw={"elinewidth": 0.8})
        ax.set_xticks(range(len(scenarios)))
        ax.set_xticklabels([s.replace("_", "\n").replace(", ", ",\n") for s in scenarios], rotation=0)
        ax.set_ylabel(METRICS[metric][0])
        ax.legend(frameon=False, ncol=min(5, len(configs)), loc="lower center", bbox_to_anchor=(0.5, 1.0))
        fig.tight_layout()
        fig.savefig(out / fname, dpi=150, bbox_inches="tight")
        plt.close(fig)
        made.append(fname)
    return made


def build(rows_path, out_dir, pairs=None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = split_variants(load_rows(rows_path))
    good = [r for r in rows if "error" not in r]
    summary = summarise(good)
    pairs = pairs or [("original+fixed", "improved+fixed"), ("improved+fixed", "improved+rule")]
    configs = {r["config"] for r in good}
    if "improved+fixed" not in configs and ("original+fixed", "improved+rule") not in pairs:
        pairs.append(("original+fixed", "improved+rule"))   # e.g. noise and scaling sweeps that skip improved+fixed
    for c in sorted(configs):
        if ("nemotron" in c or c.startswith("improved-no-")) and ("improved+rule", c) not in pairs:
            pairs.append(("improved+rule", c))
    pairs = [(a, b) for a, b in pairs if a in configs and b in configs]
    made = charts(good, summary, out)
    seeds = sorted({r["seed"] for r in good})
    errors = [r for r in rows if "error" in r]
    source = (f"Source: `{Path(rows_path).name}`, {len(good)} runs, seeds {seeds[0]}-{seeds[-1]} ({len(seeds)} seeds)"
              if seeds else "No runs")
    md = [f"# Benchmark report\n",
          source + (f", {len(errors)} failed runs (listed at the end).\n" if errors else ".\n"),
          "Crashes count drones destroyed by collisions with each other, obstacles or the ground. "
          "Completion time and energy average only the runs where the value exists.\n"]
    for fname in made:
        md.append(f"![{fname}]({fname})\n")
    md.append("## Results by scenario\n")
    md.append(tables(good, summary))
    md.append("\n## Which scenarios separate the configurations\n")
    md.append("A difference counts when the 95% bootstrap interval of the seed-by-seed difference excludes zero and, "
              "except for crashes and completions, the mean difference is more than 1% of the baseline.\n")
    md.append(separation(good, pairs))
    usage = ai_usage(good)
    if usage:
        md.append("\n## Nemotron usage\n")
        md.append(usage)
    md.append("\n## Paired comparisons (same seeds)\n")
    md.append(comparisons(good, pairs))
    if errors:
        md.append("\n## Failed runs\n")
        md.extend(f"* {r['scenario']}, seed {r['seed']}, {label(r['config'])}: {r['error'][:200]}" for r in errors)
    (out / "REPORT.md").write_text("\n".join(md) + "\n")
    serial = {f"{k[0]}|{k[1]}": v for k, v in summary.items()}
    (out / "summary.json").write_text(json.dumps(serial, indent=1, default=lambda x: None if isinstance(x, float) and math.isnan(x) else x))
    return out / "REPORT.md"
