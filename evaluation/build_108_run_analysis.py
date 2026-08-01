"""Build normalized RQ1/RQ2 tables for the completed 108-run matrix."""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "evaluation"
RQ1_TABLES = EVALUATION / "rq1" / "tables"
OUTPUT = EVALUATION / "analysis-108"
TABLES = OUTPUT / "tables"
PERFORMANCE = EVALUATION / "performance-runs" / "profile-matrix"

sys.path.insert(0, str(EVALUATION / "rq1"))
from run_rq1_analysis import completion_segment  # noqa: E402


SITE_LABELS = {
    "anvil": "Anvil",
    "stampede3": "Stampede3",
    "notre-dame-crc": "Notre Dame CRC",
}
MODEL_LABELS = {
    "gpt-5-mini": "GPT-5 mini",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
}
RETRIEVAL_LABELS = {
    "bm25": "BM25",
    "expanded": "LLM-expanded BM25",
    "full-corpus": "Full corpus",
}
COMPLETION_ORDER = [
    "measured_correctly",
    "recovered_documentation",
    "recovered_pilot",
    "correct_abstention",
    "error_or_unresolved",
]
ANALYSIS_STAGES = {
    "documentation_context_selection",
    "structured_model_call",
    "documentation_evidence_validation",
}


def load_completed_log() -> pd.DataFrame:
    """Load and validate the balanced 108-run experiment matrix."""
    frame = pd.read_csv(EVALUATION / "profile-run-log.csv")
    frame = frame[frame["status"] == "completed"].copy()
    frame = frame.drop_duplicates(["site", "model", "mode", "rep"], keep="last")
    expected = pd.MultiIndex.from_product(
        [SITE_LABELS, MODEL_LABELS, RETRIEVAL_LABELS, [1, 2, 3]],
        names=["site", "model", "mode", "rep"],
    )
    observed = pd.MultiIndex.from_frame(frame[["site", "model", "mode", "rep"]])
    missing = expected.difference(observed)
    extra = observed.difference(expected)
    if len(frame) != 108 or len(missing) or len(extra):
        raise RuntimeError(
            f"Invalid run matrix: rows={len(frame)}, missing={len(missing)}, "
            f"extra={len(extra)}"
        )
    return frame


def performance_rows(run_log: pd.DataFrame) -> pd.DataFrame:
    """Extract provider usage and documentation-analysis latency per run."""
    rows: list[dict[str, object]] = []
    for run_id in run_log["run_id"]:
        path = PERFORMANCE / str(run_id) / "performance.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        usage = report["model_usage"]
        steps = report.get("steps", [])
        analysis_latency = sum(
            float(step["duration_seconds"])
            for step in steps
            if step.get("name") in ANALYSIS_STAGES
        )
        rows.append(
            {
                "run_id": run_id,
                "input_tokens": int(usage["input_tokens"]),
                "output_tokens": int(usage["output_tokens"]),
                "performance_total_tokens": int(usage["total_tokens"]),
                "performance_model_calls": int(usage["requests"]),
                "extraction_latency_sec": analysis_latency,
                "profile_latency_sec": float(report["duration_seconds"]),
                "performance_status": report["status"],
            }
        )
    return pd.DataFrame(rows)


def pooled_repeat_sd(group: pd.DataFrame) -> float:
    """Return pooled SD across repetitions within site/model/retrieval cells."""
    cell_sd = group.groupby(["site", "model", "mode"])["field_accuracy"].std(ddof=1)
    return float(math.sqrt((cell_sd.dropna() ** 2).mean()))


def summarize_runs(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Macro-average run quality and cost over a balanced grouping."""
    rows: list[dict[str, object]] = []
    key: str | list[str] = columns[0] if len(columns) == 1 else columns
    for values, group in frame.groupby(key, sort=False):
        if not isinstance(values, tuple):
            values = (values,)
        rows.append(
            {
                **dict(zip(columns, values, strict=True)),
                "field_accuracy": group["field_accuracy"].mean(),
                "precision": group["precision"].mean(),
                "recall": group["recall"].mean(),
                "abstention_accuracy": group["abstention_accuracy"].mean(),
                "tokens": group["extraction_tokens"].mean(),
                "latency_sec": group["extraction_latency_sec"].mean(),
                "profile_latency_sec": group["profile_latency_sec"].mean(),
                "run_to_run_sd": pooled_repeat_sd(group),
                "input_tokens": group["input_tokens"].mean(),
                "output_tokens": group["output_tokens"].mean(),
                "model_calls": group["model_calls"].mean(),
                "rejected_count": group["rejected_count"].mean(),
                "unresolved_count": group["unresolved_count"].mean(),
                "conflicts": group["conflicts"].mean(),
                "runs": len(group),
            }
        )
    return pd.DataFrame(rows)


def site_table() -> pd.DataFrame:
    """Join field-level RQ1 metrics with aggregate citation integrity."""
    metrics = pd.read_csv(RQ1_TABLES / "site-metrics.csv")
    citations = pd.read_csv(RQ1_TABLES / "citation-summary.csv")
    table = metrics.merge(citations, on="site", validate="one_to_one")
    table["Site"] = table["site"].map(SITE_LABELS)
    return table[
        [
            "Site",
            "precision",
            "recall",
            "abstention_accuracy",
            "field_accuracy",
            "mechanical_citation_validity",
        ]
    ].rename(
        columns={
            "precision": "Precision",
            "recall": "Recall",
            "abstention_accuracy": "Abstention Accuracy",
            "field_accuracy": "Field Accuracy",
            "mechanical_citation_validity": "Citation Integrity",
        }
    )


def completion_tables(fields: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build aggregate and per-run profile-completion contributions."""
    fields = fields.copy()
    fields["completion_segment"] = fields.apply(completion_segment, axis=1)
    per_run = (
        fields.groupby(["run_id", "site", "completion_segment"], as_index=False)
        .size()
        .rename(columns={"size": "field_count"})
    )
    totals = per_run.groupby("run_id")["field_count"].transform("sum")
    per_run["fraction"] = per_run["field_count"] / totals
    aggregate = (
        per_run.groupby(["site", "completion_segment"], as_index=False)
        .agg(
            mean_fraction=("fraction", "mean"),
            sd_fraction=("fraction", "std"),
            mean_fields=("field_count", "mean"),
            runs=("run_id", "nunique"),
        )
    )
    return per_run, aggregate


def save_latex_table(frame: pd.DataFrame, name: str) -> None:
    """Write a compact booktabs tabular for direct paper editing."""
    content = frame.to_latex(index=False, float_format="%.3f")
    (TABLES / name).write_text(content, encoding="utf-8")


def label_summaries(
    model: pd.DataFrame,
    retrieval: pd.DataFrame,
    combination: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Add paper-facing labels while retaining stable machine identifiers."""
    model.insert(1, "Model", model["model"].map(MODEL_LABELS))
    retrieval.insert(1, "Retrieval", retrieval["mode"].map(RETRIEVAL_LABELS))
    combination.insert(1, "Model", combination["model"].map(MODEL_LABELS))
    combination.insert(3, "Retrieval", combination["mode"].map(RETRIEVAL_LABELS))
    model["model"] = pd.Categorical(
        model["model"], categories=list(MODEL_LABELS), ordered=True
    )
    retrieval["mode"] = pd.Categorical(
        retrieval["mode"], categories=list(RETRIEVAL_LABELS), ordered=True
    )
    combination["model"] = pd.Categorical(
        combination["model"], categories=list(MODEL_LABELS), ordered=True
    )
    combination["mode"] = pd.Categorical(
        combination["mode"], categories=list(RETRIEVAL_LABELS), ordered=True
    )
    model = model.sort_values("model").reset_index(drop=True)
    retrieval = retrieval.sort_values("mode").reset_index(drop=True)
    combination = combination.sort_values(["model", "mode"]).reset_index(drop=True)
    return model, retrieval, combination


def main() -> None:
    """Write normalized runs and all requested paper-table inputs."""
    TABLES.mkdir(parents=True, exist_ok=True)
    run_log = load_completed_log()
    run_metrics = pd.read_csv(RQ1_TABLES / "run-metrics.csv")
    if set(run_metrics["run_id"]) != set(run_log["run_id"]):
        raise RuntimeError("RQ1 run metrics do not match the completed 108-run log")

    runs = run_metrics.merge(
        run_log.drop(columns=["status"]),
        on=["run_id", "site", "model", "mode", "rep"],
        validate="one_to_one",
        suffixes=("", "_log"),
    ).merge(performance_rows(run_log), on="run_id", validate="one_to_one")
    if not (
        runs["extraction_tokens"].astype(int)
        == runs["performance_total_tokens"].astype(int)
    ).all():
        raise RuntimeError("Run-log and tracker token totals disagree")
    if not (runs["performance_status"] == "completed").all():
        raise RuntimeError("A performance record is not completed")

    model = summarize_runs(runs, ["model"])
    retrieval = summarize_runs(runs, ["mode"])
    combination = summarize_runs(runs, ["model", "mode"])
    model, retrieval, combination = label_summaries(model, retrieval, combination)

    fields = pd.read_csv(RQ1_TABLES / "all-field-comparisons.csv")
    per_run_completion, aggregate_completion = completion_tables(fields)

    table_1 = site_table()
    table_3 = model[
        [
            "Model",
            "field_accuracy",
            "precision",
            "recall",
            "tokens",
            "latency_sec",
            "run_to_run_sd",
        ]
    ].rename(
        columns={
            "field_accuracy": "Field Accuracy",
            "precision": "Precision",
            "recall": "Recall",
            "tokens": "Tokens",
            "latency_sec": "Latency",
            "run_to_run_sd": "Run-to-Run SD",
        }
    )
    table_4 = retrieval[
        [
            "Retrieval",
            "field_accuracy",
            "precision",
            "recall",
            "tokens",
            "latency_sec",
            "run_to_run_sd",
        ]
    ].rename(
        columns={
            "field_accuracy": "Field Accuracy",
            "precision": "Precision",
            "recall": "Recall",
            "tokens": "Tokens",
            "latency_sec": "Latency",
            "run_to_run_sd": "Run-to-Run SD",
        }
    )
    table_5 = combination[
        [
            "Model",
            "Retrieval",
            "field_accuracy",
            "tokens",
            "latency_sec",
            "run_to_run_sd",
        ]
    ].rename(
        columns={
            "field_accuracy": "Field Accuracy",
            "tokens": "Tokens",
            "latency_sec": "Latency",
            "run_to_run_sd": "Run-to-Run SD",
        }
    )

    table_1.to_csv(TABLES / "table-1-results-by-site.csv", index=False)
    table_3.to_csv(TABLES / "table-3-results-by-model.csv", index=False)
    table_4.to_csv(TABLES / "table-4-results-by-retrieval.csv", index=False)
    table_5.to_csv(TABLES / "table-5-model-retrieval-combinations.csv", index=False)
    save_latex_table(table_1, "table-1-results-by-site.tex")
    save_latex_table(table_3, "table-3-results-by-model.tex")
    save_latex_table(table_4, "table-4-results-by-retrieval.tex")
    save_latex_table(table_5, "table-5-model-retrieval-combinations.tex")
    model.to_csv(TABLES / "model-summary-detailed.csv", index=False)
    retrieval.to_csv(TABLES / "retrieval-summary-detailed.csv", index=False)
    combination.to_csv(TABLES / "model-retrieval-summary-detailed.csv", index=False)
    runs.to_csv(TABLES / "normalized-runs.csv", index=False)
    per_run_completion.to_csv(TABLES / "completion-by-run.csv", index=False)
    aggregate_completion.to_csv(TABLES / "completion-summary.csv", index=False)

    diagnostics = combination[
        [
            "Model",
            "Retrieval",
            "model_calls",
            "rejected_count",
            "unresolved_count",
            "conflicts",
            "input_tokens",
            "output_tokens",
            "profile_latency_sec",
        ]
    ]
    diagnostics.to_csv(TABLES / "model-retrieval-diagnostics.csv", index=False)

    print(f"Validated {len(runs)} completed runs.")
    print(f"Wrote analysis tables to {TABLES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
