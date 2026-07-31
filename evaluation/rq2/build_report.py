"""Build the RQ2 model and retrieval comparison report."""

# ruff: noqa: E402, I001

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/hpc-site-preflight-matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/hpc-site-preflight-font-cache")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from mistune import create_markdown
from weasyprint import HTML


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUN_METRICS = ROOT / "evaluation/rq1/tables/run-metrics.csv"
RUN_LOG = ROOT / "evaluation/run-log.csv"
FIGURES = HERE / "figures"
TABLES = HERE / "tables"
MARKDOWN = HERE / "rq2-results.md"
PDF = HERE / "rq2-results.pdf"

MODEL_ORDER = [
    "gpt-5-mini",
    "gpt-5.6-terra",
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
]
MODEL_LABELS = {
    "gpt-5-mini": "GPT-5 Mini",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gemini-3.6-flash": "Gemini Flash",
    "gemini-3.1-pro-preview": "Gemini Pro",
}
MODE_ORDER = ["bm25", "expanded", "full-corpus"]
MODE_LABELS = {
    "bm25": "BM25",
    "expanded": "LLM-expanded BM25",
    "full-corpus": "Full corpus",
}
MODEL_COLORS = {
    "GPT-5 Mini": "#4472C4",
    "GPT-5.6 Terra": "#2A9D8F",
    "Gemini Flash": "#E07A5F",
    "Gemini Pro": "#8064A2",
}
MODEL_SHORT_LABELS = {
    "GPT-5 Mini": "Mini",
    "GPT-5.6 Terra": "Terra",
    "Gemini Flash": "Flash",
    "Gemini Pro": "Pro",
}
MODE_MARKERS = {
    "BM25": "o",
    "LLM-expanded BM25": "s",
    "Full corpus": "^",
}
SITE_LABELS = {
    "anvil": "Anvil",
    "stampede3": "Stampede3",
    "notre-dame-crc": "ND CRC",
}
SITE_COLORS = {
    "Anvil": "#4472C4",
    "Stampede3": "#2A9D8F",
    "ND CRC": "#E07A5F",
}
INK = "#2F3B45"

QUALITY_COLUMNS = [
    "field_accuracy",
    "precision",
    "recall",
    "abstention_accuracy",
]
COST_COLUMNS = [
    "extraction_tokens",
    "latency_sec",
    "model_calls",
    "rejected_count",
    "unresolved_count",
]
SUMMARY_COLUMNS = [*QUALITY_COLUMNS, *COST_COLUMNS]


def format_table(frame: pd.DataFrame) -> str:
    """Return a small GitHub-style Markdown table."""
    headers = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def weighted_summary(group: pd.DataFrame) -> pd.Series:
    """Return configuration-balanced means for quality and cost columns."""
    weights = group["analysis_weight"]
    values: dict[str, Any] = {
        column: float(np.average(group[column], weights=weights))
        for column in SUMMARY_COLUMNS
    }
    values["balanced_configurations"] = float(weights.sum())
    values["raw_runs"] = len(group)
    return pd.Series(values)


def build_tables(
    runs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build summaries by model, retrieval mode, interaction, and variance."""
    model = (
        runs.groupby("model", sort=False)
        .apply(weighted_summary, include_groups=False)
        .reset_index()
    )
    retrieval = (
        runs.groupby("mode", sort=False)
        .apply(weighted_summary, include_groups=False)
        .reset_index()
    )
    interaction = (
        runs.groupby(["model", "mode"], sort=False)
        .apply(weighted_summary, include_groups=False)
        .reset_index()
    )
    expanded = runs[runs["mode"] == "expanded"]
    variance = (
        expanded.groupby(["site", "model"], as_index=False)
        .agg(
            accuracy_mean=("field_accuracy", "mean"),
            accuracy_sd=("field_accuracy", "std"),
            tokens_mean=("extraction_tokens", "mean"),
            tokens_sd=("extraction_tokens", "std"),
            latency_mean=("latency_sec", "mean"),
            latency_sd=("latency_sec", "std"),
        )
    )
    return model, retrieval, interaction, variance


def save_tables(
    model: pd.DataFrame,
    retrieval: pd.DataFrame,
    interaction: pd.DataFrame,
    variance: pd.DataFrame,
) -> None:
    """Save machine-readable RQ2 intermediate results."""
    TABLES.mkdir(parents=True, exist_ok=True)
    model.to_csv(TABLES / "model-summary.csv", index=False)
    retrieval.to_csv(TABLES / "retrieval-summary.csv", index=False)
    interaction.to_csv(TABLES / "model-retrieval-summary.csv", index=False)
    variance.to_csv(TABLES / "expanded-variance.csv", index=False)


def pareto_frontier(frame: pd.DataFrame, cost_column: str) -> pd.DataFrame:
    """Return points not dominated on lower cost and higher accuracy."""
    ordered = frame.sort_values(
        [cost_column, "field_accuracy"],
        ascending=[True, False],
    )
    best_accuracy = -np.inf
    rows: list[pd.Series] = []
    for _, row in ordered.iterrows():
        if row["field_accuracy"] > best_accuracy:
            rows.append(row)
            best_accuracy = row["field_accuracy"]
    return pd.DataFrame(rows)


def plot_summary(interaction: pd.DataFrame) -> None:
    """Plot model-retrieval quality against tokens and latency."""
    FIGURES.mkdir(parents=True, exist_ok=True)
    frame = interaction.copy()
    frame["model"] = frame["model"].map(MODEL_LABELS)
    frame["mode"] = frame["mode"].map(MODE_LABELS)

    sns.set_theme(style="whitegrid", context="paper", font_scale=1.05)
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.bbox": "tight",
            "pdf.fonttype": 42,
            "axes.edgecolor": "#BEC7CE",
            "grid.color": "#DDE2E6",
            "text.color": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
        }
    )
    handles = [
        *[
            Line2D(
                [0],
                [0],
                marker="o",
                linestyle="none",
                markerfacecolor=color,
                markeredgecolor="white",
                label=model,
                markersize=6,
            )
            for model, color in MODEL_COLORS.items()
        ],
        *[
            Line2D(
                [0],
                [0],
                marker=marker,
                linestyle="none",
                markerfacecolor="#8A949C",
                markeredgecolor="white",
                label=mode,
                markersize=6,
            )
            for mode, marker in MODE_MARKERS.items()
        ],
        Line2D(
            [0],
            [0],
            color=INK,
            linestyle="--",
            linewidth=1.7,
            label="Pareto frontier",
        ),
    ]

    def render(ylim: tuple[float, float], filename: str) -> None:
        """Render one focused or full-scale version of the Pareto figure."""
        figure, axes = plt.subplots(
            1,
            2,
            figsize=(7.2, 3.35),
            constrained_layout=True,
        )
        panels = [
            ("a", "extraction_tokens", "Extraction tokens", axes[0]),
            ("b", "latency_sec", "Latency (seconds)", axes[1]),
        ]
        for label, cost_column, xlabel, axis in panels:
            for _, row in frame.iterrows():
                axis.scatter(
                    row[cost_column],
                    row["field_accuracy"],
                    s=75,
                    color=MODEL_COLORS[row["model"]],
                    marker=MODE_MARKERS[row["mode"]],
                    edgecolor="white",
                    linewidth=0.8,
                    alpha=0.92,
                    zorder=3,
                )
                offset = -10 if row["mode"] == "LLM-expanded BM25" else 5
                axis.annotate(
                    MODEL_SHORT_LABELS[row["model"]],
                    (row[cost_column], row["field_accuracy"]),
                    xytext=(4, offset),
                    textcoords="offset points",
                    fontsize=6,
                )

            frontier = pareto_frontier(frame, cost_column)
            axis.plot(
                frontier[cost_column],
                frontier["field_accuracy"],
                color=INK,
                linewidth=1.7,
                linestyle="--",
                zorder=2,
            )
            axis.scatter(
                frontier[cost_column],
                frontier["field_accuracy"],
                s=120,
                facecolors="none",
                edgecolors=INK,
                linewidths=1.2,
                zorder=4,
            )
            axis.set_xlabel(xlabel)
            axis.set_ylim(*ylim)
            axis.text(
                -0.13,
                1.06,
                f"({label})",
                transform=axis.transAxes,
                fontsize=10,
                fontweight="bold",
                va="top",
            )
        axes[0].xaxis.set_major_formatter(
            FuncFormatter(lambda value, _: f"{value / 1000:.0f}k")
        )
        figure.supylabel("Field accuracy", x=0.01)
        figure.legend(
            handles=handles,
            ncol=4,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.02),
            fontsize=6.5,
        )
        plt.savefig(FIGURES / f"{filename}.png", dpi=300)
        plt.savefig(FIGURES / f"{filename}.pdf")
        plt.close()

    render((0.78, 0.90), "rq2-quality-cost-pareto")
    render((0, 1), "rq2-quality-cost-pareto-full-scale")


def display_model_table(model: pd.DataFrame) -> pd.DataFrame:
    """Format the model summary for the report."""
    frame = model.copy()
    frame["Model"] = frame["model"].map(MODEL_LABELS)
    columns = {
        "field_accuracy": "Field accuracy",
        "precision": "Precision",
        "recall": "Recall",
        "extraction_tokens": "Tokens",
        "latency_sec": "Latency (s)",
        "rejected_count": "Rejected",
        "unresolved_count": "Unresolved",
    }
    for source, target in columns.items():
        if source in QUALITY_COLUMNS:
            frame[target] = frame[source].map(lambda value: f"{value:.3f}")
        elif source in {"extraction_tokens"}:
            frame[target] = frame[source].map(lambda value: f"{value:,.0f}")
        else:
            frame[target] = frame[source].map(lambda value: f"{value:.1f}")
    return frame[["Model", *columns.values()]]


def display_retrieval_table(retrieval: pd.DataFrame) -> pd.DataFrame:
    """Format the retrieval summary for the report."""
    frame = retrieval.copy()
    frame["Retrieval"] = frame["mode"].map(MODE_LABELS)
    columns = {
        "field_accuracy": "Field accuracy",
        "precision": "Precision",
        "recall": "Recall",
        "extraction_tokens": "Tokens",
        "latency_sec": "Latency (s)",
        "model_calls": "Model calls",
        "rejected_count": "Rejected",
        "unresolved_count": "Unresolved",
    }
    for source, target in columns.items():
        if source in QUALITY_COLUMNS:
            frame[target] = frame[source].map(lambda value: f"{value:.3f}")
        elif source == "extraction_tokens":
            frame[target] = frame[source].map(lambda value: f"{value:,.0f}")
        else:
            frame[target] = frame[source].map(lambda value: f"{value:.1f}")
    return frame[["Retrieval", *columns.values()]]


def write_report(
    model: pd.DataFrame,
    retrieval: pd.DataFrame,
    variance: pd.DataFrame,
) -> None:
    """Write the RQ2 Markdown report and render it as a PDF."""
    model_table = display_model_table(model)
    retrieval_table = display_retrieval_table(retrieval)
    model_index = model.set_index("model")
    retrieval_index = retrieval.set_index("mode")

    model_quality_range = (
        model["field_accuracy"].max() - model["field_accuracy"].min()
    )
    retrieval_quality_range = (
        retrieval["field_accuracy"].max() - retrieval["field_accuracy"].min()
    )
    full_token_ratio = (
        retrieval_index.loc["full-corpus", "extraction_tokens"]
        / retrieval_index.loc["bm25", "extraction_tokens"]
    )
    full_latency_ratio = (
        retrieval_index.loc["full-corpus", "latency_sec"]
        / retrieval_index.loc["bm25", "latency_sec"]
    )
    expanded_token_ratio = (
        retrieval_index.loc["expanded", "extraction_tokens"]
        / retrieval_index.loc["bm25", "extraction_tokens"]
    )
    expanded_latency_ratio = (
        retrieval_index.loc["expanded", "latency_sec"]
        / retrieval_index.loc["bm25", "latency_sec"]
    )
    terra_accuracy_gap = (
        model["field_accuracy"].max()
        - model_index.loc["gpt-5.6-terra", "field_accuracy"]
    )
    terra_token_reduction = 1 - (
        model_index.loc["gpt-5.6-terra", "extraction_tokens"]
        / model_index.loc["gemini-3.6-flash", "extraction_tokens"]
    )
    terra_latency_reduction = 1 - (
        model_index.loc["gpt-5.6-terra", "latency_sec"]
        / model_index.loc["gemini-3.6-flash", "latency_sec"]
    )
    mean_expanded_sd = variance["accuracy_sd"].mean()
    maximum_variance_row = variance.loc[variance["accuracy_sd"].idxmax()]

    text = f"""# RQ2: Model and retrieval effects

## Research question

**RQ2. Does the choice of model or retrieval strategy affect what ends up in
the profile?**

The analysis compares final profile quality after deterministic validation,
alongside extraction-token cost, latency, rejected findings, unresolved fields,
and run-to-run variance.

## Run configuration

- **60 completed profile builds:** 3 sites × 4 models × 3 retrieval modes,
  with additional repetitions for the stochastic expanded mode.
- **Sites:** Purdue Anvil, TACC Stampede3, and Notre Dame CRC.
- **Models:** GPT-5 Mini, GPT-5.6 Terra, Gemini Flash, and Gemini Pro.
- **Retrieval:** BM25, LLM-expanded BM25, and full corpus.
- **Repetitions:** one BM25 and one full-corpus run per site-model pair; three
  LLM-expanded BM25 runs per site-model pair.
- **Frozen inputs:** every run for a site uses the same login measurement,
  pilot result, and documentation corpus. No run performs new discovery,
  measurement, or pilot submission.
- **Balancing:** BM25 and full corpus have weight one; each expanded repetition
  has weight one third. Thus every site-model-retrieval configuration has equal
  aggregate weight.
- **Cost boundary:** extraction tokens and profile-build latency are compared
  here. One-time corpus-discovery tokens are excluded because the corpus is
  frozen and shared by every run at a site.

## Results by model

{format_table(model_table)}

The model-level field-accuracy spread is {model_quality_range:.3f}
({model_quality_range:.1%}). Gemini Flash has the highest balanced field
accuracy. GPT-5.6 Terra is within {terra_accuracy_gap:.3f}
({terra_accuracy_gap:.1%}) of that result while using
{terra_token_reduction:.1%} fewer extraction tokens and
{terra_latency_reduction:.1%} less time than Gemini Flash.

## Results by retrieval strategy

{format_table(retrieval_table)}

The retrieval-level field-accuracy spread is only
{retrieval_quality_range:.3f} ({retrieval_quality_range:.1%}). Relative to
BM25, full corpus uses {full_token_ratio:.2f}× as many extraction tokens and
takes {full_latency_ratio:.2f}× as long without improving aggregate field
accuracy. LLM-expanded BM25 uses {expanded_token_ratio:.2f}× the tokens and
{expanded_latency_ratio:.2f}× the time of BM25, also without an aggregate
accuracy improvement.

## Quality-cost Pareto comparison

![RQ2 quality-cost Pareto plot](figures/rq2-quality-cost-pareto.png)

[Vector PDF figure](figures/rq2-quality-cost-pareto.pdf)

Panel (a) compares field accuracy against extraction tokens; panel (b) compares
accuracy against latency. Color identifies the model, point shape identifies
the retrieval strategy, and all markers have the same size. The dashed line and
outlined points mark the Pareto frontier: configurations for which no other
point is both cheaper and at least as accurate. **The field-accuracy axis is
truncated to expose differences among configurations.**

### Full-scale Pareto reference

![Full-scale Pareto plot](figures/rq2-quality-cost-pareto-full-scale.png)

[Vector PDF figure](figures/rq2-quality-cost-pareto-full-scale.pdf)

This version uses the full 0–1 field-accuracy scale so the absolute magnitude
of the differences remains visible.

## Run-to-run variance

Across expanded-mode site-model groups, the mean field-accuracy standard
deviation is {mean_expanded_sd:.3f}. The largest is
{maximum_variance_row['accuracy_sd']:.3f} for
{MODEL_LABELS[maximum_variance_row['model']]} on
{SITE_LABELS[maximum_variance_row['site']]}. This variance is concentrated in
particular site-model combinations rather than uniformly distributed.

## Answer to RQ2

Retrieval strategy has little effect on aggregate profile quality in this
experiment, but a large effect on cost: full corpus roughly doubles both tokens
and latency. Model choice has a larger quality effect than retrieval choice,
and an even larger latency effect. GPT-5.6 Terra provides the strongest
quality-cost balance in this matrix: its accuracy is close to the best observed
model while its token use and latency are substantially lower.

These are descriptive results over three sites. The deterministic validator
constrains accepted outputs, but it does not make all models identical:
individual hard fields and some site-model combinations still diverge.
"""
    MARKDOWN.write_text(text, encoding="utf-8")

    markdown = create_markdown(plugins=["table"])
    body = markdown(text)
    css = """
    @page {
      size: letter; margin: 0.65in 0.7in 0.7in; background: white;
      @bottom-center { content: counter(page); color: #5b6570; font-size: 8pt; }
    }
    html, body { background: white; }
    body { color: #26333d; font-family: Arial, Helvetica, sans-serif;
           font-size: 9.5pt; line-height: 1.38; }
    h1 { color: #173f5f; font-size: 20pt; margin: 0 0 12pt; }
    h2 { color: #245b78; font-size: 14pt; border-bottom: 1px solid #b8c5ce;
         padding-bottom: 3pt; margin-top: 18pt; }
    p { margin: 5pt 0 8pt; }
    a { color: #245b78; text-decoration: none; }
    code { font-family: Menlo, monospace; font-size: 8.5pt;
           background: #eef2f4; padding: 1pt 2pt; }
    ul { margin: 5pt 0 10pt; padding-left: 18pt; }
    li { margin: 2pt 0; }
    table { border-collapse: collapse; width: 100%; margin: 9pt 0 12pt;
            font-size: 7.7pt; }
    th { background: #dce7ed; font-weight: 700; }
    th, td { border: 0.6px solid #9eabb4; padding: 3.5pt; text-align: left; }
    tr:nth-child(even) td { background: #f5f7f8; }
    img { display: block; max-width: 100%; max-height: 7.1in;
          margin: 8pt auto 5pt; }
    h2, table, img { break-inside: avoid; }
    """
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )
    HTML(string=document, base_url=str(HERE)).write_pdf(PDF)


def main() -> None:
    """Build RQ2 tables, figure, Markdown, and PDF."""
    quality = pd.read_csv(RUN_METRICS)
    performance = pd.read_csv(RUN_LOG)
    performance = performance[performance["status"] == "completed"]
    performance_columns = ["run_id", *COST_COLUMNS]
    runs = quality.merge(
        performance[performance_columns],
        on="run_id",
        how="inner",
        validate="one_to_one",
    )
    if len(runs) != 60:
        raise RuntimeError(f"Expected 60 completed runs, found {len(runs)}")

    model, retrieval, interaction, variance = build_tables(runs)
    save_tables(model, retrieval, interaction, variance)
    plot_summary(interaction)
    write_report(model, retrieval, variance)
    print(f"Summarized {len(runs)} completed profile builds.")
    print(f"Report: {PDF}")


if __name__ == "__main__":
    main()
