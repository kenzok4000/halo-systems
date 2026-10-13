"""Benchmark report: markdown tables and charts from runner rows.

``swarmlab report runs.jsonl --out results/`` writes ``results/REPORT.md``,
``results/summary.json`` and PNG charts (charts need the optional
``matplotlib`` extra; tables do not). Every number comes from the rows
file; nothing is typed in by hand.
"""

from __future__ import annotations

import json
import math
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
                 "completion_time_s", "energy_wh_per_km", "drones_retired")
COLORS = ["#8c8c8c", "#4c78a8", "#f58518", "#54a24b", "#b279a2", "#e45756", "#72b7b2", "#eeca3b"]


def label(config: str) -> str:
    if config in CONFIG_LABELS:
        return CONFIG_LABELS[config]
    ctrl, _, sup = config.partition("+")
    if sup.startswith("nemotron"):
        model = sup.partition(":")[2]
        return "Improved + Nemotron supervisor" + (f" ({model.split('/')[-1]})" if model else "")
    return config


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
    digits = 0 if metric in ("time_formed_pct", "completed", "completion_time_s") else 2
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
    return _ORDER.index(name) if name in _ORDER else len(_ORDER)


def _config_order(configs):
    pref = ["original+fixed", "improved+fixed", "original+rule", "improved+rule"]
    return sorted(configs, key=lambda c: (pref.index(c) if c in pref else len(pref), c))


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
            ax.bar(xs, ys, width, label=label(cfg), color=COLORS[k % len(COLORS)], yerr=[err_lo, err_hi],
                   capsize=2, error_kw={"elinewidth": 0.8})
        ax.set_xticks(range(len(scenarios)))
        ax.set_xticklabels([s.replace("_", " ") for s in scenarios], rotation=0)
        ax.set_ylabel(METRICS[metric][0])
        ax.legend(frameon=False, ncol=min(4, len(configs)), loc="upper center", bbox_to_anchor=(0.5, 1.18))
        fig.tight_layout()
        fig.savefig(out / fname, dpi=150)
        plt.close(fig)
        made.append(fname)
    return made


def build(rows_path, out_dir, pairs=None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = load_rows(rows_path)
    good = [r for r in rows if "error" not in r]
    summary = summarise(good)
    pairs = pairs or [("original+fixed", "improved+fixed"), ("improved+fixed", "improved+rule")]
    configs = {r["config"] for r in good}
    for c in configs:
        if "nemotron" in c and ("improved+rule", c) not in pairs:
            pairs.append(("improved+rule", c))
    made = charts(good, summary, out)
    seeds = sorted({r["seed"] for r in good})
    errors = [r for r in rows if "error" in r]
    md = [f"# Benchmark report\n",
          f"Source: `{Path(rows_path).name}`, {len(good)} runs, seeds {seeds[0]}-{seeds[-1]} ({len(seeds)} seeds)"
          if seeds else "No runs.",
          f", {len(errors)} failed runs.\n" if errors else ".\n",
          "Crashes count drones destroyed by collisions with each other, obstacles or the ground. "
          "Completion time and energy average only the runs where the value exists.\n"]
    for fname in made:
        md.append(f"![{fname}]({fname})\n")
    md.append("## Results by scenario\n")
    md.append(tables(good, summary))
    md.append("\n## Paired comparisons (same seeds)\n")
    md.append(comparisons(good, pairs))
    (out / "REPORT.md").write_text("\n".join(md) + "\n")
    serial = {f"{k[0]}|{k[1]}": v for k, v in summary.items()}
    (out / "summary.json").write_text(json.dumps(serial, indent=1, default=lambda x: None if isinstance(x, float) and math.isnan(x) else x))
    return out / "REPORT.md"
