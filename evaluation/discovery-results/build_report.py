"""Build the compact document-discovery evaluation report."""

# ruff: noqa: E402, I001

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("MPLCONFIGDIR", "/tmp/hpc-site-preflight-matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/hpc-site-preflight-font-cache")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from mistune import create_markdown
from weasyprint import HTML


HERE = Path(__file__).resolve().parent
RUN_LOG = HERE / "run-log.csv"
FIGURE = HERE / "discovery-model-comparison"
MARKDOWN = HERE / "discovery-quality-report.md"
PDF = HERE / "discovery-quality-report.pdf"

MODEL_ORDER = [
    "gpt-5-mini",
    "gpt-5.6-terra",
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
]
MODEL_LABELS = {
    "gpt-5-mini": "GPT-5\nMini",
    "gpt-5.6-terra": "GPT-5.6\nTerra",
    "gemini-3.6-flash": "Gemini\nFlash",
    "gemini-3.1-pro-preview": "Gemini\nPro",
}
SITE_LABELS = {"anvil": "Anvil", "stampede3": "Stampede3"}
SITE_COLORS = {"Anvil": "#4472C4", "Stampede3": "#2A9D8F"}
FROZEN_INPUTS = HERE.parent / "site-inputs"
INK = "#2F3B45"


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


def build_summaries(runs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate discovery quality and cost by model and by site-model pair."""
    metrics = {
        "runs": ("run_id", "count"),
        "sites": ("site", "nunique"),
        "topic_coverage_mean": ("coverage_fraction", "mean"),
        "topic_coverage_min": ("coverage_fraction", "min"),
        "selected_url_jaccard_mean": ("selected_url_jaccard_to_rep1", "mean"),
        "selected_pages_mean": ("selected_pages", "mean"),
        "total_tokens_mean": ("total_tokens", "mean"),
        "latency_sec_mean": ("latency_sec", "mean"),
    }
    model = runs.groupby("model", as_index=False).agg(**metrics)
    site_model = runs.groupby(["site", "model"], as_index=False).agg(**metrics)
    model["model"] = pd.Categorical(model["model"], MODEL_ORDER, ordered=True)
    site_model["model"] = pd.Categorical(
        site_model["model"], MODEL_ORDER, ordered=True
    )
    return model.sort_values("model"), site_model.sort_values(["site", "model"])


def build_discovery_table(runs: pd.DataFrame) -> pd.DataFrame:
    """Summarize discovery breadth, official scope, stability, and cost by site."""
    page_rows: list[dict[str, object]] = []
    for site in SITE_LABELS:
        measurement_path = FROZEN_INPUTS / site / "login-measurements.json"
        measurement = json.loads(measurement_path.read_text(encoding="utf-8"))
        domains = measurement["site_facts"]["documentation_domains"]
        for path in sorted(HERE.glob(f"{site}-*.json")):
            result = json.loads(path.read_text(encoding="utf-8"))
            scopes = {
                page["url"]: page["scope"] for page in result["fetched_pages"]
            }
            selected = result["selected_urls"]
            official = 0
            violations = 0
            for url in selected:
                hostname = (urlparse(url).hostname or "").lower()
                domain_ok = any(
                    hostname == domain or hostname.endswith(f".{domain}")
                    for domain in domains
                )
                scope_ok = scopes.get(url) == "target_site"
                official += int(domain_ok and scope_ok)
                violations += int(not (domain_ok and scope_ok))
            page_rows.append(
                {
                    "run_id": result["run_id"],
                    "official_pages": official,
                    "selected_pages_checked": len(selected),
                    "scope_violations": violations,
                }
            )

    frame = runs.merge(pd.DataFrame(page_rows), on="run_id", how="left")
    rows: list[dict[str, object]] = []
    for site, group in frame.groupby("site", sort=False):
        rows.append(
            {
                "site": site,
                "runs": len(group),
                "full_topic_coverage_runs": int(
                    group["coverage_fraction"].eq(1).sum()
                ),
                "official_pages": int(group["official_pages"].sum()),
                "selected_pages_checked": int(
                    group["selected_pages_checked"].sum()
                ),
                "scope_violations": int(group["scope_violations"].sum()),
                "fetch_successes": int(group["fetch_successes"].sum()),
                "fetch_attempts": int(group["fetch_attempts"].sum()),
                "fetch_yield": (
                    group["fetch_successes"].sum()
                    / group["fetch_attempts"].sum()
                ),
                "url_stability": group[
                    "selected_url_jaccard_to_rep1"
                ].mean(),
                "tokens_mean": group["total_tokens"].mean(),
                "tokens_sd": group["total_tokens"].std(),
                "latency_sec_mean": group["latency_sec"].mean(),
                "latency_sec_sd": group["latency_sec"].std(),
            }
        )
    return pd.DataFrame(rows)


def plot_comparison(runs: pd.DataFrame) -> None:
    """Plot quality, stability, token cost, and latency by model and site."""
    frame = runs.copy()
    frame["model"] = frame["model"].map(MODEL_LABELS)
    frame["site"] = frame["site"].map(SITE_LABELS)
    order = [MODEL_LABELS[model] for model in MODEL_ORDER]
    panels = [
        ("coverage_fraction", "Topic coverage", (0, 1.05)),
        ("selected_url_jaccard_to_rep1", "Selected-URL stability", (0, 1.05)),
        ("total_tokens", "Total discovery tokens", None),
        ("latency_sec", "Latency (seconds)", None),
    ]

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
    _, axes = plt.subplots(2, 2, figsize=(7.2, 5.4), constrained_layout=True)
    for label, axis, (column, ylabel, limits) in zip(
        ("a", "b", "c", "d"),
        axes.flat,
        panels,
        strict=True,
    ):
        sns.barplot(
            data=frame,
            x="model",
            y=column,
            hue="site",
            order=order,
            palette=SITE_COLORS,
            errorbar="sd",
            capsize=0.08,
            ax=axis,
        )
        axis.set_xlabel("")
        axis.set_ylabel(ylabel)
        axis.tick_params(axis="x", labelsize=7)
        if limits:
            axis.set_ylim(*limits)
        axis.text(
            -0.13,
            1.06,
            f"({label})",
            transform=axis.transAxes,
            fontsize=10,
            fontweight="bold",
            va="top",
        )
        if label == "a":
            axis.legend(title="", fontsize=7, loc="lower left")
        else:
            axis.get_legend().remove()
    plt.savefig(FIGURE.with_suffix(".png"), dpi=300)
    plt.savefig(FIGURE.with_suffix(".pdf"))
    plt.close()


def write_report(
    discovery: pd.DataFrame,
    model: pd.DataFrame,
    site_model: pd.DataFrame,
) -> None:
    """Write the concise Markdown report and render it as a PDF."""
    discovery_table = discovery.copy()
    discovery_table["Site"] = discovery_table["site"].map(SITE_LABELS)
    discovery_table["Full topic coverage"] = discovery_table.apply(
        lambda row: (
            f"{row['full_topic_coverage_runs']}/{row['runs']} "
            f"({row['full_topic_coverage_runs'] / row['runs']:.1%})"
        ),
        axis=1,
    )
    discovery_table["Official in-scope pages"] = discovery_table.apply(
        lambda row: (
            f"{row['official_pages']}/{row['selected_pages_checked']} "
            f"({row['official_pages'] / row['selected_pages_checked']:.1%})"
        ),
        axis=1,
    )
    discovery_table["Scope violations"] = discovery_table[
        "scope_violations"
    ].astype(int)
    discovery_table["Fetch yield"] = discovery_table.apply(
        lambda row: (
            f"{row['fetch_successes']}/{row['fetch_attempts']} "
            f"({row['fetch_yield']:.1%})"
        ),
        axis=1,
    )
    discovery_table["URL stability"] = discovery_table["url_stability"].map(
        lambda value: f"{value:.3f}"
    )
    discovery_table["Tokens/run"] = discovery_table.apply(
        lambda row: f"{row['tokens_mean']:.0f} ± {row['tokens_sd']:.0f}",
        axis=1,
    )
    discovery_table["Latency/run (s)"] = discovery_table.apply(
        lambda row: (
            f"{row['latency_sec_mean']:.1f} ± {row['latency_sec_sd']:.1f}"
        ),
        axis=1,
    )
    discovery_table = discovery_table[
        [
            "Site",
            "Full topic coverage",
            "Official in-scope pages",
            "Scope violations",
            "Fetch yield",
            "URL stability",
            "Tokens/run",
            "Latency/run (s)",
        ]
    ]

    model_table = model.copy()
    model_table["model"] = model_table["model"].astype(str)
    model_table = model_table.rename(
        columns={
            "model": "Model",
            "runs": "Runs",
            "sites": "Sites",
            "topic_coverage_mean": "Mean coverage",
            "topic_coverage_min": "Minimum coverage",
            "selected_url_jaccard_mean": "URL stability",
            "selected_pages_mean": "Selected pages",
            "total_tokens_mean": "Mean tokens",
            "latency_sec_mean": "Mean latency (s)",
        }
    )
    for column in ["Mean coverage", "Minimum coverage", "URL stability"]:
        model_table[column] = model_table[column].map(lambda value: f"{value:.3f}")
    for column in ["Selected pages", "Mean tokens", "Mean latency (s)"]:
        model_table[column] = model_table[column].map(lambda value: f"{value:.1f}")

    site_table = site_model.copy()
    site_table["site"] = site_table["site"].map(SITE_LABELS)
    site_table["model"] = site_table["model"].astype(str)
    site_table = site_table[
        [
            "site",
            "model",
            "runs",
            "topic_coverage_mean",
            "selected_url_jaccard_mean",
            "total_tokens_mean",
            "latency_sec_mean",
        ]
    ].rename(
        columns={
            "site": "Site",
            "model": "Model",
            "runs": "Runs",
            "topic_coverage_mean": "Coverage",
            "selected_url_jaccard_mean": "URL stability",
            "total_tokens_mean": "Tokens",
            "latency_sec_mean": "Latency (s)",
        }
    )
    for column in ["Coverage", "URL stability"]:
        site_table[column] = site_table[column].map(lambda value: f"{value:.3f}")
    for column in ["Tokens", "Latency (s)"]:
        site_table[column] = site_table[column].map(lambda value: f"{value:.1f}")

    text = f"""# Document discovery quality and cost

This report summarizes **21 completed discovery runs** over Anvil and
Stampede3. Each available model-site pair has three repetitions. Gemini Pro
currently has Stampede3 results only, so its aggregate is not a cross-site
comparison.

## Table 2: Document discovery breadth and compliance

{format_table(discovery_table)}

Full topic coverage means the selected corpus covers submission, resources,
storage, and networking. Official in-scope pages are selected pages whose host
matches the site's allowlisted documentation domain and whose deterministic
scope classifier labels them `target_site`. URL stability is mean selected-URL
Jaccard similarity to repetition one within each model-site group. Costs are
reported as mean ± one standard deviation per discovery run.

## Per-model summary

{format_table(model_table)}

## Quality and cost

![Discovery quality and cost](discovery-model-comparison.png)

[Vector PDF figure](discovery-model-comparison.pdf)

Panel (a) reports coverage of the four tracked discovery topics: submission,
resources, storage, and networking. Panel (b) reports selected-page stability
as URL Jaccard similarity to repetition one. Panels (c) and (d) report total
discovery tokens and wall-clock latency. Error bars show one standard
deviation over the available repetitions.

## Site-level values

{format_table(site_table)}

## Interpretation boundary

Topic coverage and URL stability are useful automated proxies, but they do not
establish that a selected page is authoritative or that it contains every
needed policy field. Those claims require a manual page-relevance audit or
downstream field extraction accuracy. Latency also includes web search and
fetch time, so it is not purely model inference time.
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
    table { border-collapse: collapse; width: 100%; margin: 9pt 0 12pt;
            font-size: 8pt; }
    th { background: #dce7ed; font-weight: 700; }
    th, td { border: 0.6px solid #9eabb4; padding: 4pt; text-align: left; }
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
    """Build the discovery summary tables, figure, Markdown, and PDF."""
    runs = pd.read_csv(RUN_LOG)
    runs = runs[runs["status"] == "completed"].copy()
    discovery = build_discovery_table(runs)
    model, site_model = build_summaries(runs)
    discovery.to_csv(HERE / "document-discovery-table.csv", index=False)
    model.to_csv(HERE / "discovery-model-summary.csv", index=False)
    site_model.to_csv(HERE / "discovery-site-model-summary.csv", index=False)
    plot_comparison(runs)
    write_report(discovery, model, site_model)
    print(f"Summarized {len(runs)} discovery runs.")
    print(f"Report: {PDF}")


if __name__ == "__main__":
    main()
