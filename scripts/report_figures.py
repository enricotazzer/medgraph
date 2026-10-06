"""Figures for the LaTeX report in docs/reports/, drawn from saved evaluation metrics.

    uv run python scripts/report_figures.py

Reads only aggregate, per-report scores (``runs/extraction/<config>/<split>/metrics.json``) and
writes vector PDFs to ``docs/reports/figures/``, so every number in a figure traces back to a
results file.
"""

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from medgraph.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "reports" / "figures"
BLUE, ORANGE, MUTED, INK_2 = "#2a78d6", "#eb6834", "#898781", "#52514e"
STYLE: dict[str, Any] = {
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": "#e1e0d9",
    "grid.linewidth": 0.6,
    "axes.edgecolor": "#c3c2b7",
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "font.size": 8,
    "legend.frameon": False,
    "pdf.fonttype": 42,
}
FAMILIES = ["colon", "dotted", "sections", "table", "narrative", "two_column"]
HELD_OUT = {"narrative", "two_column"}


def load(runs: Path, config: str, split: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (runs / config / split / "metrics.json").read_text(encoding="utf-8")
    )
    return data


def by_family(metrics: dict[str, Any], numerator: str, denominator: str) -> dict[str, float]:
    """Pooled rate per layout family over the text-format reports (NaN: no such rows)."""
    sums: dict[str, list[int]] = {}
    for s in metrics["scores"]:
        if s["format"] == "text":
            pair = sums.setdefault(s["family"], [0, 0])
            pair[0] += s.get(numerator, 0)
            pair[1] += s.get(denominator, 0)
    return {f: (n / d if d else float("nan")) for f, (n, d) in sums.items()}


def bars(ax: Any, series: dict[str, tuple[dict[str, float], str]], families: list[str]) -> None:
    x = np.arange(len(families))
    width = 0.8 / len(series)
    for i, (label, (rates, color)) in enumerate(series.items()):
        values = [100 * rates.get(f, float("nan")) for f in families]
        drawn = ax.bar(x + (i - (len(series) - 1) / 2) * width, values, width, color=color)
        drawn.set_label(label)
        text = ["n/a" if np.isnan(v) else f"{v:.0f}" for v in values]
        ax.bar_label(drawn, labels=text, fontsize=6, color=INK_2, padding=1.5)
    names = [f.replace("_", "-") + ("\n(held out)" if f in HELD_OUT else "") for f in families]
    ax.set_xticks(x, names, fontsize=7)
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100])


def legend(fig: Any, ax: Any) -> None:
    """One legend above all panels, clear of the bars."""
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncols=len(labels), fontsize=7)
    fig.tight_layout(rect=(0, 0, 1, 0.93))


def test_by_family(runs: Path) -> Path:
    llm, rules = load(runs, "qwen35-9b", "test"), load(runs, "rules", "test")
    panels = [
        ("Row recall", "matched", "truth_rows"),
        ("End-to-end, seen names", "end_to_end_seen_ok", "in_scope_seen"),
        ("End-to-end, held-out names", "end_to_end_held_out_ok", "in_scope_held_out"),
    ]
    fig, axes = plt.subplots(3, 1, figsize=(6.4, 5.6), sharex=True, sharey=True)
    for ax, (title, num, den) in zip(axes, panels, strict=True):
        series = {
            "rules baseline": (by_family(rules, num, den), ORANGE),
            "qwen3.5:9b, prompt v3": (by_family(llm, num, den), BLUE),
        }
        bars(ax, series, FAMILIES)
        ax.set_title(title, loc="left", fontsize=8.5)
        ax.set_ylabel("% of rows")
    legend(fig, axes[0])
    path = OUT / "extraction-test-by-family.pdf"
    fig.savefig(path)
    plt.close(fig)
    return path


def dev_prompts(runs: Path) -> Path:
    v2 = load(runs, "qwen35-9b", "dev-transcribe-v2")
    v3, rules = load(runs, "qwen35-9b", "dev"), load(runs, "rules", "dev")
    families = FAMILIES[:4]
    panels = [
        ("Value exact", "value_ok", "matched"),
        ("End-to-end canonical value", "end_to_end_ok", "in_scope"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.6), sharey=True)
    for ax, (title, num, den) in zip(axes, panels, strict=True):
        series = {
            "rules baseline": (by_family(rules, num, den), ORANGE),
            "qwen3.5:9b, prompt v2": (by_family(v2, num, den), MUTED),
            "qwen3.5:9b, prompt v3": (by_family(v3, num, den), BLUE),
        }
        bars(ax, series, families)
        ax.set_title(title, loc="left", fontsize=8.5)
    axes[0].set_ylabel("% (development split, text)")
    legend(fig, axes[0])
    path = OUT / "extraction-dev-prompts.pdf"
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> int:
    runs = Settings().runs_dir / "extraction"
    OUT.mkdir(parents=True, exist_ok=True)
    plt.switch_backend("Agg")
    plt.style.use(STYLE)
    for path in (test_by_family(runs), dev_prompts(runs)):
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
