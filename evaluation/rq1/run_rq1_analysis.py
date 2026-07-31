"""Generate RQ1 field comparisons, summary tables, and paper-ready figures."""

# ruff: noqa: E402, I001

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/hpc-site-preflight-matplotlib")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from evaluate_profile import (
    add_htcondor_summary,
    compare,
    is_absent,
    named,
    parse_expected,
    values_match,
)


ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "evaluation"
RESULTS = EVALUATION / "profile-runs"
OUTPUT = EVALUATION / "rq1"
TABLES = OUTPUT / "tables"
FIGURES = OUTPUT / "figures"

GROUND_TRUTH = {
    "anvil": EVALUATION
    / "ground-truth"
    / "completed-anvil-site-profile-ground-truth.csv",
    "stampede3": EVALUATION
    / "ground-truth"
    / "completed-stampede3-site-profile-ground-truth.csv",
    "notre-dame-crc": EVALUATION
    / "ground-truth"
    / "completed-notre-dame-crc-site-profile-ground-truth.csv",
}
SITE_LABELS = {
    "anvil": "Anvil",
    "stampede3": "Stampede3",
    "notre-dame-crc": "ND CRC",
}
MODEL_LABELS = {
    "gpt-5-mini": "GPT-5 Mini",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gemini-3.6-flash": "Gemini Flash",
    "gemini-3.1-pro-preview": "Gemini Pro",
}
OUTCOMES = (
    "correct_value",
    "correct_abstention",
    "incorrect_value",
    "false_abstention",
    "unsupported_value",
)
CORRECT_OUTCOMES = {"correct_value", "correct_abstention"}

OUTCOME_COLORS = {
    "correct_value": "#2A9D8F",
    "correct_abstention": "#4C78A8",
    "incorrect_value": "#D1495B",
    "false_abstention": "#E69F00",
    "unsupported_value": "#8064A2",
}
SITE_COLORS = {
    "Anvil": "#4472C4",
    "Stampede3": "#2A9D8F",
    "ND CRC": "#E07A5F",
}
METRIC_COLORS = {
    "Precision": "#4472C4",
    "Recall": "#2A9D8F",
    "Correct abstention": "#8064A2",
    "Field accuracy": "#E69F00",
}
ABSTENTION_COLORS = {
    "Correct abstention": "#2A9D8F",
    "False abstention": "#E69F00",
    "Unsupported fill": "#8064A2",
}
HEATMAP_COLORS = ["#F2F5F7", "#B7D5DA", "#5E9EAD", "#245B78"]
INK = "#2F3B45"

COMPLETION_ORDER = [
    "measured_correctly",
    "recovered_documentation",
    "recovered_pilot",
    "correct_abstention",
    "error_or_unresolved",
]
def load_truth(path: Path) -> list[dict[str, str]]:
    """Return reviewed truth rows without pandas type inference."""
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def result_directory(row: dict[str, str]) -> Path:
    """Return the artifact directory for one completed matrix row."""
    name = (
        f"{row['site']}-{row['model']}-{row['mode']}-rep{row['rep']}"
    )
    return RESULTS / name


def load_completed_runs() -> list[dict[str, str]]:
    """Load the 108 successful matrix cases and verify their profile artifacts."""
    with (EVALUATION / "profile-run-log.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = [row for row in csv.DictReader(handle) if row["status"] == "completed"]

    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["site"], row["model"], row["mode"], row["rep"])
        profile_path = result_directory(row) / "site-profile.json"
        if profile_path.exists():
            unique[key] = row
    completed = list(unique.values())
    if len(completed) != 108:
        raise RuntimeError(f"Expected 108 completed profiles, found {len(completed)}")

    repeat_counts = Counter(
        (row["site"], row["model"], row["mode"]) for row in completed
    )
    for row in completed:
        row["analysis_weight"] = str(
            1.0 / repeat_counts[(row["site"], row["model"], row["mode"])]
        )
    return completed


def outcome_counts(frame: pd.DataFrame, weight: str = "analysis_weight") -> dict[str, float]:
    """Return weighted counts for all five comparison outcomes."""
    return {
        outcome: float(frame.loc[frame["outcome"] == outcome, weight].sum())
        for outcome in OUTCOMES
    }


def metrics_from_counts(counts: dict[str, float]) -> dict[str, float]:
    """Calculate accuracy, precision, recall, and abstention metrics."""
    correct_value = counts["correct_value"]
    correct_abstention = counts["correct_abstention"]
    incorrect_value = counts["incorrect_value"]
    false_abstention = counts["false_abstention"]
    unsupported_value = counts["unsupported_value"]

    populated = correct_value + incorrect_value + unsupported_value
    known = correct_value + incorrect_value + false_abstention
    absent = correct_abstention + unsupported_value
    total = known + absent
    return {
        "field_accuracy": (correct_value + correct_abstention) / total,
        "precision": correct_value / populated if populated else float("nan"),
        "recall": correct_value / known if known else float("nan"),
        "abstention_accuracy": (
            correct_abstention / absent if absent else float("nan")
        ),
        "false_abstention_rate": (
            false_abstention / known if known else float("nan")
        ),
        "unsupported_fill_rate": (
            unsupported_value / absent if absent else float("nan")
        ),
        "predicted_abstention_rate": (
            (correct_abstention + false_abstention) / total
        ),
        "ground_truth_absent_fraction": absent / total,
    }


def linked_evidence_sources(directory: Path) -> dict[str, set[str]]:
    """Return accepted evidence source types linked to each profile field."""
    reports = list(directory.glob("evidence-report-*.json"))
    if len(reports) != 1:
        raise RuntimeError(
            f"Expected one evidence report in {directory}, found {len(reports)}"
        )
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    source_by_id = {
        item["evidence_id"]: item["source_type"]
        for item in report.get("evidence", [])
        if item.get("disposition") == "accepted"
    }
    return {
        link["profile_field"]: {
            source_by_id[evidence_id]
            for evidence_id in link.get("evidence_ids", [])
            if evidence_id in source_by_id
        }
        for link in report.get("links", [])
    }


def build_field_comparisons(
    runs: list[dict[str, str]],
    *,
    strict_matching: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare every completed profile and return field and run-level tables."""
    all_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []

    measurement_values = {
        site: flatten_login_measurement(
            json.loads(
                (
                    EVALUATION
                    / "site-inputs"
                    / site
                    / "login-measurements.json"
                ).read_text(encoding="utf-8")
            )
        )
        for site in GROUND_TRUTH
    }

    for run in runs:
        directory = result_directory(run)
        profile_path = directory / "site-profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        evidence_sources = linked_evidence_sources(directory)
        comparisons = compare(
            profile,
            load_truth(GROUND_TRUTH[run["site"]]),
            strict_matching=strict_matching,
        )
        weight = float(run["analysis_weight"])
        for item in comparisons:
            measured = measurement_values[run["site"]].get(item["field_path"], MISSING)
            item.update(
                {
                    "run_id": run["run_id"],
                    "site": run["site"],
                    "model": run["model"],
                    "mode": run["mode"],
                    "rep": int(run["rep"]),
                    "analysis_weight": weight,
                    "profile_path": str(profile_path.relative_to(ROOT)),
                    "measurement_state": (
                        "missing"
                        if measured is MISSING
                        else "empty"
                        if is_absent(measured)
                        else "has_value"
                    ),
                    "measurement_value_json": render_value(measured),
                    "evidence_sources": "|".join(
                        sorted(evidence_sources.get(item["field_path"], set()))
                    ),
                }
            )
            all_rows.append(item)

        frame = pd.DataFrame(comparisons)
        raw_counts = {
            outcome: int((frame["outcome"] == outcome).sum())
            for outcome in OUTCOMES
        }
        metrics = metrics_from_counts({key: float(value) for key, value in raw_counts.items()})
        run_rows.append(
            {
                "run_id": run["run_id"],
                "site": run["site"],
                "model": run["model"],
                "mode": run["mode"],
                "rep": int(run["rep"]),
                "analysis_weight": weight,
                "evaluated_fields": len(comparisons),
                **raw_counts,
                **metrics,
            }
        )
    return pd.DataFrame(all_rows), pd.DataFrame(run_rows)


def matching_sensitivity(
    reviewed_fields: pd.DataFrame,
    strict_fields: pd.DataFrame,
) -> pd.DataFrame:
    """Compare reviewed normalization with deliberately strict matching."""
    frames: list[pd.DataFrame] = []
    for matcher, fields in (
        ("reviewed", reviewed_fields),
        ("strict_exact", strict_fields),
    ):
        metrics = summarize_group(fields, ["site"])
        overall_counts = outcome_counts(fields)
        overall = {
            "site": "overall",
            "weighted_fields": sum(overall_counts.values()),
            **overall_counts,
            **metrics_from_counts(overall_counts),
        }
        metrics = pd.concat([metrics, pd.DataFrame([overall])], ignore_index=True)
        metrics.insert(0, "matcher", matcher)
        frames.append(metrics)
    return pd.concat(frames, ignore_index=True)


MISSING = object()


def render_value(value: Any) -> str:
    """Serialize a measurement value for the comparison audit trail."""
    if value is MISSING:
        return "<missing>"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def flatten_login_measurement(measurement: dict[str, Any]) -> dict[str, Any]:
    """Map observable login facts onto comparable profile field paths."""
    output: dict[str, Any] = {}
    schedulers = measurement.get("detected_schedulers") or []
    if schedulers:
        output["/scheduler_type"] = schedulers[0]

    slurm = measurement.get("slurm") or {}
    if slurm:
        output["/slurm/version"] = slurm.get("version")
        output["/slurm/submit_command"] = (
            "sbatch" if slurm.get("submit_command_available") else None
        )
        output["/slurm/default_partition"] = slurm.get("default_partition")
        for partition_name, partition in named(slurm.get("partitions"), "name").items():
            for field in (
                "maximum_walltime_seconds",
                "node_count",
                "cpus_per_node",
                "memory_mib_per_node",
                "gpu_count_per_node",
                "gpu_models",
            ):
                output[f"/slurm/partitions/{partition_name}/{field}"] = partition.get(
                    field
                )

    htcondor = measurement.get("htcondor") or {}
    if htcondor:
        for field in (
            "version",
            "collector_host",
            "file_transfer_supported",
            "available_commands",
            "submit_command_available",
            "unclassified_machine_count",
        ):
            output[f"/htcondor/{field}"] = htcondor.get(field)
        output["/htcondor/submit_command"] = (
            "condor_submit" if htcondor.get("submit_command_available") else None
        )
        for field, value in (htcondor.get("pool_totals") or {}).items():
            output[f"/htcondor/pool_totals/{field}"] = value
        add_htcondor_summary(output, htcondor)

    locations = (measurement.get("storage") or {}).get("locations") or []
    for storage_id, storage in named(locations, "id").items():
        prefix = f"/storage/{storage_id}"
        output[f"{prefix}/environment_variables"] = storage.get(
            "environment_variables"
        )
        output[f"{prefix}/path_pattern"] = storage.get("path_pattern")
        output[f"{prefix}/filesystem_type"] = storage.get("filesystem_type")
        output[f"{prefix}/login_readable"] = storage.get("readable")
        output[f"{prefix}/login_writable"] = storage.get("writable")
    return output


def summarize_group(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Aggregate weighted field outcomes over the requested grouping columns."""
    rows: list[dict[str, Any]] = []
    group_key: str | list[str] = columns[0] if len(columns) == 1 else columns
    for keys, group in frame.groupby(group_key, sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        counts = outcome_counts(group)
        rows.append(
            {
                **dict(zip(columns, keys, strict=True)),
                "weighted_fields": sum(counts.values()),
                **counts,
                **metrics_from_counts(counts),
            }
        )
    return pd.DataFrame(rows)


def normalize_text(value: str) -> str:
    """Normalize citation text for deterministic containment checks."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def build_citation_tables(
    runs: list[dict[str, str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate accepted citations against each site's frozen corpus."""
    corpus_by_site: dict[str, dict[str, dict[str, Any]]] = {}
    for site in GROUND_TRUTH:
        chunks_path = EVALUATION / "site-inputs" / site / "corpus" / "chunks.jsonl"
        corpus_by_site[site] = {
            chunk["chunk_id"]: chunk
            for chunk in (
                json.loads(line)
                for line in chunks_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }

    citation_rows: list[dict[str, Any]] = []
    finding_rows: list[dict[str, Any]] = []
    for run in runs:
        evidence_path = result_directory(run) / "documentation-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        chunks = corpus_by_site[run["site"]]
        for finding_index, finding in enumerate(evidence.get("findings", [])):
            citations = finding.get("citations") or []
            finding_rows.append(
                {
                    "run_id": run["run_id"],
                    "site": run["site"],
                    "model": run["model"],
                    "mode": run["mode"],
                    "rep": int(run["rep"]),
                    "finding_index": finding_index,
                    "has_citation": bool(citations),
                    "citation_count": len(citations),
                }
            )
            for citation_index, citation in enumerate(citations):
                chunk = chunks.get(citation.get("chunk_id"))
                chunk_exists = chunk is not None
                target_scope = bool(chunk and chunk.get("scope") == "target_site")
                url_matches = bool(
                    chunk
                    and normalize_text(citation.get("url", ""))
                    == normalize_text(chunk.get("source_url", ""))
                )
                quote_matches = bool(
                    chunk
                    and normalize_text(citation.get("quote", ""))
                    in normalize_text(chunk.get("text", ""))
                )
                citation_rows.append(
                    {
                        "run_id": run["run_id"],
                        "site": run["site"],
                        "model": run["model"],
                        "mode": run["mode"],
                        "rep": int(run["rep"]),
                        "finding_index": finding_index,
                        "citation_index": citation_index,
                        "chunk_id": citation.get("chunk_id"),
                        "url": citation.get("url"),
                        "quote": citation.get("quote"),
                        "chunk_exists": chunk_exists,
                        "target_site_scope": target_scope,
                        "url_matches": url_matches,
                        "quote_matches": quote_matches,
                        "mechanically_valid": (
                            chunk_exists and target_scope and url_matches and quote_matches
                        ),
                    }
                )

    citations = pd.DataFrame(citation_rows)
    findings = pd.DataFrame(finding_rows)
    summaries: list[dict[str, Any]] = []
    for site in GROUND_TRUTH:
        site_citations = citations[citations["site"] == site]
        site_findings = findings[findings["site"] == site]
        summaries.append(
            {
                "site": site,
                "accepted_findings": len(site_findings),
                "findings_with_citations": int(site_findings["has_citation"].sum()),
                "citations": len(site_citations),
                "mechanically_valid_citations": int(
                    site_citations["mechanically_valid"].sum()
                ),
                "mechanical_citation_validity": float(
                    site_citations["mechanically_valid"].mean()
                ),
            }
        )
    return citations, pd.DataFrame(summaries)


def build_conflict_tables(
    runs: list[dict[str, str]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Collect explicit reconciliation conflicts and summarize their paths."""
    rows: list[dict[str, Any]] = []
    for run in runs:
        profile = json.loads(
            (result_directory(run) / "site-profile.json").read_text(encoding="utf-8")
        )
        for conflict in profile.get("conflicts") or []:
            rows.append(
                {
                    "run_id": run["run_id"],
                    "site": run["site"],
                    "model": run["model"],
                    "mode": run["mode"],
                    "rep": int(run["rep"]),
                    "analysis_weight": float(run["analysis_weight"]),
                    "field_path": conflict.get("field"),
                    "selected_value_json": json.dumps(
                        conflict.get("selected_value"), ensure_ascii=False
                    ),
                    "selection_rule": conflict.get("selection_rule"),
                    "evidence_values_json": json.dumps(
                        conflict.get("evidence_values"), ensure_ascii=False
                    ),
                }
            )
    details = pd.DataFrame(rows)
    summary = (
        details.groupby(["site", "field_path"], as_index=False)
        .agg(
            weighted_run_occurrences=("analysis_weight", "sum"),
            raw_run_occurrences=("run_id", "count"),
        )
        .sort_values(["site", "field_path"])
    )
    details["conflict_category"] = np.where(
        details["field_path"].str.endswith("/maximum_walltime_seconds"),
        "walltime_policy",
        "other",
    )
    category = (
        details.groupby(["site", "conflict_category"], as_index=False)
        .agg(
            weighted_run_occurrences=("analysis_weight", "sum"),
            raw_run_occurrences=("run_id", "count"),
        )
        .assign(
            conflicts_per_configuration=lambda frame: (
                frame["weighted_run_occurrences"] / 12
            )
        )
    )
    return details, summary, category


def measurement_gap_recoveries(fields: pd.DataFrame) -> pd.DataFrame:
    """Return correct values recovered when login measurement had no value."""
    rows = fields[fields["outcome"] == "correct_value"].copy()
    return rows[rows["measurement_state"].isin(["missing", "empty"])].copy()


def completion_segment(row: pd.Series) -> str:
    """Assign one evaluated field to its first successful evidence path."""
    if row["outcome"] == "correct_abstention":
        return "correct_abstention"
    if row["outcome"] != "correct_value":
        return "error_or_unresolved"

    measurement_correct = False
    if (
        row["measurement_state"] == "has_value"
        and row["expected_state"] == "has_value"
    ):
        measured = json.loads(row["measurement_value_json"])
        expected = parse_expected(row["expected_value_json"])
        measurement_correct = values_match(
            measured,
            expected,
            row["match_rule"],
            row["field_path"],
        )
    if measurement_correct:
        return "measured_correctly"

    sources = set(filter(None, str(row["evidence_sources"]).split("|")))
    if "documentation" in sources:
        return "recovered_documentation"
    if "pilot" in sources:
        return "recovered_pilot"
    if "measurement" in sources:
        return "measured_correctly"

    # Virtual evaluation fields inherit the source class of their parent data.
    if row["evidence_class"] in {"identity", "configuration"}:
        return "measured_correctly"
    if row["evidence_class"] == "capability":
        return "recovered_pilot"
    return "recovered_documentation"


def profile_completion_summary(fields: pd.DataFrame) -> pd.DataFrame:
    """Summarize mutually exclusive profile completion paths by site."""
    frame = fields.copy()
    frame["completion_segment"] = frame.apply(completion_segment, axis=1)
    weighted = (
        frame.groupby(["site", "completion_segment"], as_index=False)
        .agg(weighted_fields=("analysis_weight", "sum"))
    )
    totals = (
        frame.groupby("site", as_index=False)
        .agg(
            weighted_total=("analysis_weight", "sum"),
            ground_truth_fields=("field_path", "nunique"),
        )
    )
    summary = weighted.merge(totals, on="site", how="left")
    summary["fraction"] = summary["weighted_fields"] / summary["weighted_total"]
    summary["fields_per_profile"] = (
        summary["fraction"] * summary["ground_truth_fields"]
    )
    summary["completion_segment"] = pd.Categorical(
        summary["completion_segment"],
        COMPLETION_ORDER,
        ordered=True,
    )
    return summary.sort_values(["site", "completion_segment"])


def measurement_gap_summary(fields: pd.DataFrame) -> pd.DataFrame:
    """Summarize how often later evidence fills facts unavailable at login."""
    gaps = fields[
        (fields["expected_state"] == "has_value")
        & fields["measurement_state"].isin(["missing", "empty"])
    ].copy()
    gaps["recovered"] = gaps["outcome"] == "correct_value"
    return (
        gaps.groupby(["site", "evidence_class"], as_index=False)
        .agg(
            weighted_recoveries=(
                "analysis_weight",
                lambda values: float(values[gaps.loc[values.index, "recovered"]].sum()),
            ),
            weighted_opportunities=("analysis_weight", "sum"),
            raw_recoveries=("recovered", "sum"),
            raw_opportunities=("run_id", "count"),
        )
        .assign(
            recovery_rate=lambda frame: (
                frame["weighted_recoveries"] / frame["weighted_opportunities"]
            )
        )
    )


def gpu_recovery_summary(fields: pd.DataFrame) -> pd.DataFrame:
    """Summarize GPU facts that documentation recovered beyond measurement."""
    gpu = fields[
        (fields["expected_state"] == "has_value")
        & fields["measurement_state"].isin(["missing", "empty"])
        & fields["field_path"].str.contains(
            r"/gpu_(?:count_per_node|models)$", regex=True
        )
    ].copy()
    gpu["recovered"] = gpu["outcome"] == "correct_value"
    return (
        gpu.groupby(["site", "field_path"], as_index=False)
        .agg(
            weighted_recoveries=(
                "analysis_weight",
                lambda values: float(values[gpu.loc[values.index, "recovered"]].sum()),
            ),
            weighted_opportunities=("analysis_weight", "sum"),
            raw_recoveries=("recovered", "sum"),
            raw_opportunities=("run_id", "count"),
        )
        .assign(
            recovery_rate=lambda frame: (
                frame["weighted_recoveries"] / frame["weighted_opportunities"]
            )
        )
    )


def save_csv(frame: pd.DataFrame, name: str) -> None:
    """Write one intermediate table with stable UTF-8 formatting."""
    frame.to_csv(TABLES / name, index=False)


def save_latex(frame: pd.DataFrame, name: str, columns: list[str]) -> None:
    """Write a compact booktabs table for direct paper review."""
    content = frame[columns].to_latex(index=False, float_format="%.3f")
    (TABLES / name).write_text(content, encoding="utf-8")


def set_plot_style() -> None:
    """Apply one restrained visual style to every RQ1 figure."""
    sns.set_theme(
        style="whitegrid",
        context="paper",
        font_scale=1.1,
        palette=list(SITE_COLORS.values()),
    )
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.bbox": "tight",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.edgecolor": "#C7CDD1",
            "grid.color": "#E0E3E5",
            "text.color": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
        }
    )


def save_figure(name: str) -> None:
    """Save the current plot as both PDF and PNG, then close it."""
    plt.savefig(FIGURES / f"{name}.pdf")
    plt.savefig(FIGURES / f"{name}.png", dpi=220)
    plt.close()


def plot_site_metrics(site_metrics: pd.DataFrame) -> None:
    """Plot the four primary RQ1 metrics for each site."""
    columns = ["precision", "recall", "abstention_accuracy", "field_accuracy"]
    long = site_metrics.melt(
        id_vars="site", value_vars=columns, var_name="metric", value_name="value"
    )
    long["site"] = long["site"].map(SITE_LABELS)
    long["metric"] = long["metric"].map(
        {
            "precision": "Precision",
            "recall": "Recall",
            "abstention_accuracy": "Correct abstention",
            "field_accuracy": "Field accuracy",
        }
    )
    plt.figure(figsize=(7.2, 3.5))
    ax = sns.barplot(
        data=long,
        x="site",
        y="value",
        hue="metric",
        palette=METRIC_COLORS,
    )
    ax.set(xlabel="", ylabel="Score", ylim=(0, 1.05))
    ax.legend(title="", ncol=2, loc="lower right")
    save_figure("site-metrics")


def draw_profile_completion_waterfall(
    axis: plt.Axes,
    summary: pd.DataFrame,
    *,
    compact: bool = False,
) -> None:
    """Draw cumulative evidence contributions and the remaining error gap."""
    fractions = summary.pivot(
        index="site",
        columns="completion_segment",
        values="fraction",
    ).reindex(index=list(GROUND_TRUTH), columns=COMPLETION_ORDER, fill_value=0)
    contribution_order = COMPLETION_ORDER[:-1]
    stage_labels = [
        "Login\nmeasurement",
        "Documentation",
        "Pilot jobs",
        "Correct\nabstention",
        "Completed",
    ]
    x = np.arange(len(stage_labels), dtype=float)
    width = 0.22
    offsets = np.linspace(-width, width, len(fractions))
    label_size = 7

    for site_index, (site, values) in enumerate(fractions.iterrows()):
        increments = values[contribution_order].fillna(0).to_numpy(dtype=float)
        cumulative_before = np.concatenate(([0.0], np.cumsum(increments)[:-1]))
        cumulative_after = np.cumsum(increments)
        completed = cumulative_after[-1]
        gap = float(values["error_or_unresolved"])
        label = SITE_LABELS[site]
        color = SITE_COLORS[label]

        for stage_index, (height, bottom) in enumerate(
            zip(increments, cumulative_before, strict=True)
        ):
            axis.bar(
                x[stage_index] + offsets[site_index],
                height,
                width=width,
                bottom=bottom,
                color=color,
                label=label if stage_index == 0 else None,
            )
            if not compact and height >= 0.035:
                axis.text(
                    x[stage_index] + offsets[site_index],
                    bottom + height / 2,
                    f"+{height:.0%}",
                    ha="center",
                    va="center",
                    fontsize=label_size,
                )

        axis.bar(
            x[-1] + offsets[site_index],
            completed,
            width=width,
            color=color,
        )
        if not compact:
            axis.text(
                x[-1] + offsets[site_index],
                completed / 2,
                f"{completed:.0%}",
                ha="center",
                va="center",
                fontsize=label_size,
                fontweight="bold",
            )
            if gap >= 0.035:
                axis.text(
                    x[-1] + offsets[site_index],
                    completed + gap / 2,
                    f"{gap:.0%}\ngap",
                    ha="center",
                    va="center",
                    fontsize=label_size,
                )
        axis.plot(
            x + offsets[site_index],
            [*cumulative_after, completed],
            marker="o",
            linewidth=1.1,
            markersize=2.5,
            color=color,
        )

    axis.axhline(1, color=INK, linewidth=0.8, linestyle="--")
    axis.set_ylim(0, 1.04)
    axis.set_xticks(x, stage_labels)
    axis.set_ylabel("Cumulative fraction of fields")
    axis.set_yticks([0, 0.5, 1], ["0%", "50%", "100%"])
    axis.legend(
        title="Site" if not compact else None,
        ncol=3,
        loc="upper left",
        fontsize=6 if compact else 8,
    )


def plot_profile_completion_waterfall(summary: pd.DataFrame) -> None:
    """Plot how each evidence path cumulatively completes profile fields."""
    _, axis = plt.subplots(figsize=(9.5, 4.4))
    draw_profile_completion_waterfall(axis, summary)
    save_figure("profile-completion-waterfall")


def combined_outcome_counts(site_outcomes: pd.DataFrame) -> pd.DataFrame:
    """Combine the five evaluator outcomes into four paper-facing counts."""
    frame = site_outcomes.copy()
    frame["correct_absence"] = frame["correct_abstention"]
    frame["wrong_populated_value"] = (
        frame["incorrect_value"] + frame["unsupported_value"]
    )
    frame["missed_value"] = frame["false_abstention"]
    columns = [
        "correct_value",
        "correct_absence",
        "wrong_populated_value",
        "missed_value",
    ]
    overall = {"site": "overall"}
    overall.update({column: frame[column].sum() for column in columns})
    frame = pd.concat([frame[["site", *columns]], pd.DataFrame([overall])])
    frame["total"] = frame[columns].sum(axis=1)
    return frame


def plot_combined_outcome_counts(counts: pd.DataFrame) -> None:
    """Plot weighted correct, wrong, and abstention outcomes in one figure."""
    labels = {
        "correct_value": "Correct value",
        "correct_absence": "Correctly detected absence",
        "wrong_populated_value": "Wrong populated value",
        "missed_value": "Value incorrectly absent",
    }
    colors = [
        OUTCOME_COLORS["correct_value"],
        OUTCOME_COLORS["correct_abstention"],
        OUTCOME_COLORS["incorrect_value"],
        OUTCOME_COLORS["false_abstention"],
    ]
    columns = list(labels)
    site_labels = [
        "Overall" if site == "overall" else SITE_LABELS[site]
        for site in counts["site"]
    ]
    fig, axes = plt.subplots(1, 2, figsize=(8.5, 3.8))
    groups = [
        (axes[0], columns[:2], colors[:2], "Correct outcomes"),
        (axes[1], columns[2:], colors[2:], "Errors"),
    ]
    for axis, group_columns, group_colors, title in groups:
        bottom = np.zeros(len(counts))
        for column, color in zip(group_columns, group_colors, strict=True):
            values = counts[column].to_numpy()
            bars = axis.bar(
                site_labels,
                values,
                bottom=bottom,
                label=labels[column],
                color=color,
            )
            for bar, value, base in zip(bars, values, bottom, strict=True):
                if value >= 1:
                    axis.text(
                        bar.get_x() + bar.get_width() / 2,
                        base + value / 2,
                        f"{value:.0f}",
                        ha="center",
                        va="center",
                        fontsize=8,
                    )
            bottom += values
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=20)
        axis.legend(title="", fontsize=8)
    axes[0].set_ylabel("Weighted field outcomes")
    axes[1].set_ylabel("")
    save_figure("combined-outcome-counts")


def plot_compact_rq1_summary(
    completion: pd.DataFrame,
    site_metrics: pd.DataFrame,
    citations: pd.DataFrame,
    conflicts: pd.DataFrame,
) -> None:
    """Combine the four principal RQ1 result views in one paper figure."""
    figure, axes = plt.subplots(
        2,
        2,
        figsize=(7.2, 5.7),
        constrained_layout=True,
    )

    # (a) Cumulative evidence-path contribution to the completed profile.
    axis = axes[0, 0]
    draw_profile_completion_waterfall(axis, completion, compact=True)
    axis.tick_params(axis="x", labelsize=6)

    # (b) Correct and incorrect abstention behavior.
    axis = axes[0, 1]
    abstention_columns = [
        "abstention_accuracy",
        "false_abstention_rate",
        "unsupported_fill_rate",
    ]
    abstention_labels = [
        "Correct abstention",
        "False abstention",
        "Unsupported fill",
    ]
    x = np.arange(len(site_metrics))
    width = 0.24
    for offset, column, label in zip(
        (-width, 0, width),
        abstention_columns,
        abstention_labels,
        strict=True,
    ):
        axis.bar(
            x + offset,
            site_metrics[column],
            width,
            label=label,
            color=ABSTENTION_COLORS[label],
        )
    axis.set_xticks(
        x,
        [SITE_LABELS[site] for site in site_metrics["site"]],
        rotation=18,
    )
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("Rate")
    axis.legend(fontsize=6.5, loc="upper right")

    # (c) Accepted citation volume with validity on a second scale.
    axis = axes[1, 0]
    citation_frame = citations.copy()
    citation_labels = [SITE_LABELS[site] for site in citation_frame["site"]]
    citation_colors = [SITE_COLORS[label] for label in citation_labels]
    axis.bar(citation_labels, citation_frame["citations"], color=citation_colors)
    axis.set_ylabel("Accepted citations")
    axis.tick_params(axis="x", labelrotation=18)
    validity_axis = axis.twinx()
    validity_axis.plot(
        citation_labels,
        citation_frame["mechanical_citation_validity"],
        color=INK,
        marker="D",
        linewidth=1.4,
        markersize=4,
        label="Mechanical validity",
    )
    validity_axis.set_ylim(0, 1.08)
    validity_axis.set_yticks([0, 0.5, 1], ["0%", "50%", "100%"])
    validity_axis.grid(False)
    validity_axis.legend(fontsize=6.5, loc="upper right")

    # (d) Explicit measurement-documentation disagreements.
    axis = axes[1, 1]
    conflict_frame = conflicts.pivot(
        index="site",
        columns="conflict_category",
        values="conflicts_per_configuration",
    ).fillna(0)
    conflict_frame = conflict_frame.reindex(list(GROUND_TRUTH), fill_value=0)
    for column in ("walltime_policy", "other"):
        if column not in conflict_frame:
            conflict_frame[column] = 0
    conflict_labels = [SITE_LABELS[site] for site in conflict_frame.index]
    axis.bar(
        conflict_labels,
        conflict_frame["walltime_policy"],
        label="Walltime policy",
        color=OUTCOME_COLORS["false_abstention"],
    )
    axis.bar(
        conflict_labels,
        conflict_frame["other"],
        bottom=conflict_frame["walltime_policy"],
        label="Other",
        color=OUTCOME_COLORS["incorrect_value"],
    )
    axis.set_ylabel("Conflicts per configuration")
    axis.tick_params(axis="x", labelrotation=18)
    axis.legend(fontsize=6.5, loc="upper right")

    for label, panel in zip(("a", "b", "c", "d"), axes.flat, strict=True):
        panel.text(
            -0.16,
            1.08,
            f"({label})",
            transform=panel.transAxes,
            fontsize=10,
            fontweight="bold",
            va="top",
        )

    figure.align_ylabels(axes)
    save_figure("rq1-compact-summary")


def plot_evidence_accuracy(evidence: pd.DataFrame) -> None:
    """Plot field accuracy across evidence classes and sites."""
    frame = evidence.copy()
    frame["site"] = frame["site"].map(SITE_LABELS)
    plt.figure(figsize=(7.2, 3.5))
    ax = sns.barplot(
        data=frame,
        x="evidence_class",
        y="field_accuracy",
        hue="site",
        palette=SITE_COLORS,
        order=["identity", "configuration", "policy", "capability"],
    )
    ax.set(xlabel="Evidence class", ylabel="Field accuracy", ylim=(0, 1.05))
    ax.legend(title="")
    save_figure("accuracy-by-evidence-class")


def plot_section_heatmap(section: pd.DataFrame) -> None:
    """Plot a compact site-by-section field-accuracy heatmap."""
    pivot = section.pivot(index="site", columns="section", values="field_accuracy")
    pivot = pivot.rename(index=SITE_LABELS)
    plt.figure(figsize=(8.2, 2.8))
    sns.heatmap(
        pivot,
        annot=True,
        fmt=".2f",
        vmin=0,
        vmax=1,
        cmap=sns.blend_palette(HEATMAP_COLORS, as_cmap=True),
        cbar_kws={"label": "Field accuracy"},
    )
    plt.xlabel("")
    plt.ylabel("")
    save_figure("accuracy-by-section")


def plot_run_distribution(run_metrics: pd.DataFrame) -> None:
    """Plot final field accuracy for all completed configurations."""
    frame = run_metrics.copy()
    frame["site"] = frame["site"].map(SITE_LABELS)
    plt.figure(figsize=(6.5, 3.4))
    sns.boxplot(data=frame, x="site", y="field_accuracy", color="#A9BBC8")
    sns.stripplot(
        data=frame,
        x="site",
        y="field_accuracy",
        color=INK,
        alpha=0.65,
        size=3,
    )
    plt.xlabel("")
    plt.ylabel("Field accuracy")
    plt.ylim(0, 1.02)
    save_figure("run-accuracy-distribution")


def plot_top_errors(field_errors: pd.DataFrame) -> None:
    """Plot the fields with the highest balanced error frequency."""
    frame = field_errors.sort_values(
        ["error_rate", "weighted_errors"], ascending=False
    ).head(18)
    frame = frame.sort_values("error_rate")
    labels = [
        f"{SITE_LABELS[site]}: {path}"
        for site, path in zip(frame["site"], frame["field_path"], strict=True)
    ]
    plt.figure(figsize=(8.5, 6.0))
    plt.barh(labels, frame["error_rate"], color=OUTCOME_COLORS["incorrect_value"])
    plt.xlabel("Fraction of balanced configurations with an error")
    plt.xlim(0, 1)
    save_figure("most-frequent-field-errors")


def plot_abstention(site_metrics: pd.DataFrame) -> None:
    """Contrast correct abstention with its two principal error modes."""
    columns = [
        "abstention_accuracy",
        "false_abstention_rate",
        "unsupported_fill_rate",
    ]
    long = site_metrics.melt(
        id_vars="site", value_vars=columns, var_name="metric", value_name="value"
    )
    long["site"] = long["site"].map(SITE_LABELS)
    long["metric"] = long["metric"].map(
        {
            "abstention_accuracy": "Correct abstention",
            "false_abstention_rate": "False abstention",
            "unsupported_fill_rate": "Unsupported fill",
        }
    )
    plt.figure(figsize=(6.8, 3.4))
    ax = sns.barplot(
        data=long,
        x="site",
        y="value",
        hue="metric",
        palette=ABSTENTION_COLORS,
    )
    ax.set(xlabel="", ylabel="Rate", ylim=(0, 1.05))
    ax.legend(title="")
    save_figure("abstention-behavior")


def plot_citations(citations: pd.DataFrame) -> None:
    """Plot structural citation validity and citation volume by site."""
    frame = citations.copy()
    frame["site"] = frame["site"].map(SITE_LABELS)
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
    sns.barplot(
        data=frame,
        x="site",
        y="mechanical_citation_validity",
        hue="site",
        palette=SITE_COLORS,
        legend=False,
        ax=axes[0],
    )
    axes[0].set(xlabel="", ylabel="Mechanical validity", ylim=(0, 1.05))
    sns.barplot(
        data=frame,
        x="site",
        y="citations",
        hue="site",
        palette=SITE_COLORS,
        legend=False,
        ax=axes[1],
    )
    axes[1].set(xlabel="", ylabel="Accepted citations")
    save_figure("citation-integrity")


def plot_conflicts(conflicts: pd.DataFrame) -> None:
    """Plot balanced explicit conflicts, separating policy from other fields."""
    pivot = conflicts.pivot(
        index="site",
        columns="conflict_category",
        values="conflicts_per_configuration",
    ).fillna(0)
    pivot = pivot.reindex(list(GROUND_TRUTH), fill_value=0)
    for column in ("walltime_policy", "other"):
        if column not in pivot:
            pivot[column] = 0
    labels = [SITE_LABELS[site] for site in pivot.index]
    plt.figure(figsize=(5.8, 3.2))
    plt.bar(
        labels,
        pivot["walltime_policy"],
        label="Walltime policy",
        color=OUTCOME_COLORS["false_abstention"],
    )
    plt.bar(
        labels,
        pivot["other"],
        bottom=pivot["walltime_policy"],
        label="Other",
        color=OUTCOME_COLORS["incorrect_value"],
    )
    plt.ylabel("Mean explicit conflicts per configuration")
    plt.legend(title="")
    save_figure("source-conflicts")


def plot_measurement_gap(recoveries: pd.DataFrame) -> None:
    """Plot correct fields recovered when measurement supplied no value."""
    weighted = (
        recoveries.groupby("site", as_index=False)["analysis_weight"].sum()
        .rename(columns={"analysis_weight": "weighted_recoveries"})
    )
    weighted["recoveries_per_configuration"] = weighted["weighted_recoveries"] / 12
    all_sites = pd.DataFrame({"site": list(GROUND_TRUTH)})
    weighted = all_sites.merge(weighted, on="site", how="left").fillna(0)
    weighted["site"] = weighted["site"].map(SITE_LABELS)
    plt.figure(figsize=(5.8, 3.2))
    ax = sns.barplot(
        data=weighted,
        x="site",
        y="recoveries_per_configuration",
        hue="site",
        palette=SITE_COLORS,
        legend=False,
    )
    ax.set(xlabel="", ylabel="Correct measurement-gap recoveries per configuration")
    save_figure("measurement-gap-recovery")


def plot_gpu_recovery(gpu: pd.DataFrame) -> None:
    """Plot recovery rates for GPU facts invisible to login measurement."""
    frame = gpu.copy()
    frame["label"] = frame.apply(
        lambda row: (
            f"{SITE_LABELS[row['site']]}: "
            f"{row['field_path'].split('/')[3]} "
            f"{row['field_path'].split('/')[-1].replace('_', ' ')}"
        ),
        axis=1,
    )
    frame = frame.sort_values("recovery_rate")
    plt.figure(figsize=(7.2, 3.8))
    plt.barh(frame["label"], frame["recovery_rate"], color=METRIC_COLORS["Precision"])
    plt.xlabel("Fraction of balanced configurations recovering the correct value")
    plt.xlim(0, 1)
    save_figure("gpu-measurement-gap-recovery")


def main() -> None:
    """Generate every RQ1 intermediate table and figure."""
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    set_plot_style()

    runs = load_completed_runs()
    fields, run_metrics = build_field_comparisons(runs)
    strict_fields, _ = build_field_comparisons(runs, strict_matching=True)
    site_metrics = summarize_group(fields, ["site"])
    site_outcomes = site_metrics[["site", *OUTCOMES]].copy()
    evidence_metrics = summarize_group(fields, ["site", "evidence_class"])
    section_metrics = summarize_group(fields, ["site", "section"])
    sensitivity = matching_sensitivity(fields, strict_fields)

    fields["is_error"] = ~fields["outcome"].isin(CORRECT_OUTCOMES)
    field_errors = (
        fields.groupby(["site", "field_path"], as_index=False)
        .agg(
            weighted_errors=(
                "analysis_weight",
                lambda values: float(
                    values[fields.loc[values.index, "is_error"]].sum()
                ),
            ),
            weighted_evaluations=("analysis_weight", "sum"),
            raw_errors=("is_error", "sum"),
            raw_evaluations=("run_id", "count"),
        )
    )
    field_errors["error_rate"] = (
        field_errors["weighted_errors"] / field_errors["weighted_evaluations"]
    )

    citations, citation_summary = build_citation_tables(runs)
    conflict_details, conflict_summary, conflict_categories = build_conflict_tables(runs)
    recoveries = measurement_gap_recoveries(fields)
    gap_summary = measurement_gap_summary(fields)
    gpu_summary = gpu_recovery_summary(fields)
    completion = profile_completion_summary(fields)

    save_csv(fields, "all-field-comparisons.csv")
    save_csv(run_metrics, "run-metrics.csv")
    save_csv(site_metrics, "site-metrics.csv")
    save_csv(site_outcomes, "site-outcomes.csv")
    outcome_counts = combined_outcome_counts(site_outcomes)
    save_csv(outcome_counts, "combined-outcome-counts.csv")
    save_csv(evidence_metrics, "evidence-class-metrics.csv")
    save_csv(section_metrics, "section-metrics.csv")
    save_csv(sensitivity, "matching-sensitivity.csv")
    save_csv(field_errors, "field-error-frequency.csv")
    save_csv(citations, "citation-checks.csv")
    save_csv(citation_summary, "citation-summary.csv")
    save_csv(conflict_details, "conflict-details.csv")
    save_csv(conflict_summary, "conflict-summary.csv")
    save_csv(conflict_categories, "conflict-category-summary.csv")
    save_csv(recoveries, "measurement-gap-recoveries.csv")
    save_csv(gap_summary, "measurement-gap-summary.csv")
    save_csv(gpu_summary, "gpu-recovery-summary.csv")
    save_csv(completion, "profile-completion-waterfall.csv")

    save_latex(
        site_metrics,
        "site-metrics.tex",
        ["site", "precision", "recall", "abstention_accuracy", "field_accuracy"],
    )
    save_latex(
        citation_summary,
        "citation-summary.tex",
        [
            "site",
            "accepted_findings",
            "citations",
            "mechanically_valid_citations",
            "mechanical_citation_validity",
        ],
    )
    save_latex(
        evidence_metrics,
        "evidence-class-metrics.tex",
        ["site", "evidence_class", "field_accuracy", "precision", "recall"],
    )
    save_latex(
        sensitivity,
        "matching-sensitivity.tex",
        ["matcher", "site", "precision", "recall", "field_accuracy"],
    )

    plot_site_metrics(site_metrics)
    plot_profile_completion_waterfall(completion)
    plot_combined_outcome_counts(outcome_counts)
    plot_compact_rq1_summary(
        completion,
        site_metrics,
        citation_summary,
        conflict_categories,
    )
    plot_evidence_accuracy(evidence_metrics)
    plot_section_heatmap(section_metrics)
    plot_run_distribution(run_metrics)
    plot_top_errors(field_errors)
    plot_abstention(site_metrics)
    plot_citations(citation_summary)
    plot_conflicts(conflict_categories)
    plot_measurement_gap(recoveries)
    plot_gpu_recovery(gpu_summary)

    print(f"Compared {len(fields)} run-field observations from {len(runs)} runs.")
    print(f"Wrote {len(list(TABLES.iterdir()))} tables to {TABLES.relative_to(ROOT)}")
    print(f"Wrote {len(list(FIGURES.iterdir()))} figures to {FIGURES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
