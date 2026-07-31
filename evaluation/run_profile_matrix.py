#!/usr/bin/env python3
"""Run one site's frozen-input profile evaluation matrix serially."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "evaluation"
FROZEN_ROOT = EVALUATION / "site-inputs"
RESULTS_ROOT = EVALUATION / "profile-runs"
FAILED_RESULTS_ROOT = EVALUATION / "failed-profile-runs"
RUN_ROOT = EVALUATION / "performance-runs" / "profile-matrix"
RUN_LOG = EVALUATION / "profile-run-log.csv"

MODELS = (
    "gpt-5-mini",
    "gpt-5.6-terra",
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
)
MODE_REPETITIONS = (
    ("bm25", "bm25", 1),
    ("bm25", "bm25", 2),
    ("bm25", "bm25", 3),
    ("expanded", "llm-expanded-bm25", 1),
    ("expanded", "llm-expanded-bm25", 2),
    ("expanded", "llm-expanded-bm25", 3),
    ("full-corpus", "full-corpus", 1),
    ("full-corpus", "full-corpus", 2),
    ("full-corpus", "full-corpus", 3),
)
CSV_COLUMNS = (
    "run_id",
    "site",
    "model",
    "mode",
    "rep",
    "status",
    "extraction_tokens",
    "discovery_tokens",
    "model_calls",
    "rejected_count",
    "unresolved_count",
    "conflicts",
    "latency_sec",
    "corpus_fingerprint",
    "notes",
)
REPORT_PATTERN = re.compile(r"Report:\s+(.+?/performance\.json)\s*$")


@dataclass(frozen=True)
class Case:
    """One model, retrieval mode, and repetition."""

    site: str
    model: str
    mode: str
    cli_mode: str
    rep: int

    @property
    def key(self) -> tuple[str, str, str, int]:
        return self.site, self.model, self.mode, self.rep

    @property
    def output_dir(self) -> Path:
        return RESULTS_ROOT / f"{self.site}-{self.model}-{self.mode}-rep{self.rep}"


def load_json(path: Path) -> dict[str, Any]:
    """Load one JSON object from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


def validate_frozen_inputs(site: str) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Return the three non-empty frozen inputs and their evaluation metadata."""

    directory = FROZEN_ROOT / site
    measurements = directory / "login-measurements.json"
    pilots = directory / "pilot-results.json"
    corpus = directory / "corpus"
    metadata_path = directory / "evaluation-metadata.json"
    required = (
        measurements,
        pilots,
        corpus / "manifest.json",
        corpus / "documents.jsonl",
        corpus / "chunks.jsonl",
        metadata_path,
    )
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise SystemExit("Missing or empty frozen input(s):\n  " + "\n  ".join(missing))

    metadata = load_json(metadata_path)
    manifest = load_json(corpus / "manifest.json")
    if metadata["corpus_fingerprint"] != manifest["fingerprint"]:
        raise SystemExit("Frozen corpus fingerprint does not match evaluation metadata.")
    return measurements, pilots, corpus, metadata


def build_cases(site: str) -> list[Case]:
    """Build the required 36 cases in matrix order."""

    return [
        Case(site, model, mode, cli_mode, rep)
        for mode, cli_mode, rep in MODE_REPETITIONS
        for model in MODELS
    ]


def completed_keys() -> set[tuple[str, str, str, int]]:
    """Load cases already completed in the append-only run log."""

    if not RUN_LOG.exists():
        return set()
    with RUN_LOG.open(newline="", encoding="utf-8") as handle:
        return {
            (row["site"], row["model"], row["mode"], int(row["rep"]))
            for row in csv.DictReader(handle)
            if row["status"] == "completed"
        }


def append_row(row: dict[str, object]) -> None:
    """Append and durably flush one result row immediately after an attempt."""

    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    new_file = not RUN_LOG.exists() or RUN_LOG.stat().st_size == 0
    with RUN_LOG.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def refresh_divergence_notes(site: str) -> None:
    """Compare completed cases with GPT-5 mini after the full site matrix."""

    if not RUN_LOG.exists():
        return
    with RUN_LOG.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    latest: dict[tuple[str, str, int], int] = {}
    for index, row in enumerate(rows):
        if (
            row["site"] == site
            and row["model"] in MODELS
            and row["status"] == "completed"
        ):
            latest[(row["model"], row["mode"], int(row["rep"]))] = index

    for mode, _, rep in MODE_REPETITIONS:
        baseline_key = (MODELS[0], mode, rep)
        if baseline_key not in latest:
            continue
        baseline_case = Case(site, MODELS[0], mode, mode, rep)
        baseline = flatten_profile(load_json(baseline_case.output_dir / "site-profile.json"))
        for model in MODELS:
            key = model, mode, rep
            if key not in latest:
                continue
            case = Case(site, model, mode, mode, rep)
            current = flatten_profile(load_json(case.output_dir / "site-profile.json"))
            paths = set(current) | set(baseline)
            differing = sum(current.get(path) != baseline.get(path) for path in paths)
            row = rows[latest[key]]
            notes = [
                note
                for note in row["notes"].split("; ")
                if note and not note.startswith("model divergence:")
            ]
            comparison = (
                "baseline"
                if model == MODELS[0]
                else f"{differing} profile value(s) differ from {MODELS[0]}"
            )
            notes.append(f"model divergence: {comparison}")
            row["notes"] = "; ".join(notes)

    temporary = RUN_LOG.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(RUN_LOG)


def flatten_profile(value: Any, path: str = "") -> dict[str, str]:
    """Return comparable profile leaves while excluding run-specific metadata."""

    leaves: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"generated_at", "evidence", "evidence_id", "conflicts", "unresolved"}:
                continue
            leaves.update(flatten_profile(child, f"{path}/{key}"))
    elif isinstance(value, list):
        leaves[path] = json.dumps(value, sort_keys=True, separators=(",", ":"))
    else:
        leaves[path] = json.dumps(value, sort_keys=True)
    return leaves


def divergence_note(
    case: Case,
    profile: dict[str, Any],
    baselines: dict[tuple[str, str, int], tuple[str, dict[str, str]]],
) -> str | None:
    """Compare a profile with the first successful model in the same mode and repetition."""

    key = case.site, case.mode, case.rep
    current = flatten_profile(profile)
    if key not in baselines:
        baselines[key] = case.model, current
        return None
    baseline_model, baseline = baselines[key]
    paths = set(current) | set(baseline)
    differing = sum(current.get(path) != baseline.get(path) for path in paths)
    if differing == 0:
        return f"matches {baseline_model}"
    return f"{differing} profile value(s) differ from {baseline_model}"


def summarize_items(items: list[dict[str, Any]], value_key: str) -> str:
    """Render at most four field/action values for a compact CSV note."""

    values = [str(item.get(value_key) or item.get("field") or "unknown") for item in items]
    suffix = f" (+{len(values) - 4} more)" if len(values) > 4 else ""
    return ", ".join(values[:4]) + suffix


def archive_failed_output(case: Case, run_id: str) -> None:
    """Preserve a failed output directory before retrying the canonical path."""

    if not case.output_dir.exists():
        return
    suffix = run_id[:8] if run_id else str(int(time.time()))
    FAILED_RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    destination = FAILED_RESULTS_ROOT / f"{case.output_dir.name}-failed-{suffix}"
    counter = 2
    while destination.exists():
        destination = FAILED_RESULTS_ROOT / (
            f"{case.output_dir.name}-failed-{suffix}-{counter}"
        )
        counter += 1
    case.output_dir.rename(destination)


def run_case(
    case: Case,
    *,
    measurements: Path,
    pilots: Path,
    corpus: Path,
    metadata: dict[str, Any],
    baselines: dict[tuple[str, str, int], tuple[str, dict[str, str]]],
    retry_number: int = 0,
) -> bool:
    """Run one CLI case, append its metrics, and return whether it fully succeeded."""

    if case.output_dir.exists():
        archive_failed_output(case, "previous")
    command = [
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "hpc-site-preflight",
        "hpc-site-preflight",
        "profile",
        "build",
        "--site-mode",
        "simulate",
        "--measurements",
        str(measurements),
        "--model-mode",
        "live",
        "--model",
        case.model,
        "--web-mode",
        "live",
        "--corpus-input",
        str(corpus),
        "--context-mode",
        case.cli_mode,
        "--run-pilots",
        "--pilot-results",
        str(pilots),
        "--output-dir",
        str(case.output_dir),
        "--run-dir",
        str(RUN_ROOT),
    ]
    label = f"{case.site} | {case.model} | {case.mode} | rep {case.rep}"
    if retry_number:
        label += f" | retry {retry_number}"
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}", flush=True)

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        output_lines.append(line.rstrip())
    return_code = process.wait()

    report_path: Path | None = None
    for line in reversed(output_lines):
        match = REPORT_PATTERN.search(line)
        if match:
            report_path = Path(match.group(1))
            if not report_path.is_absolute():
                report_path = ROOT / report_path
            break

    performance: dict[str, Any] = {}
    if report_path is not None and report_path.is_file():
        performance = load_json(report_path)
    run_id = str(performance.get("run_id", ""))
    profile_path = case.output_dir / "site-profile.json"
    documentation_path = case.output_dir / "documentation-evidence.json"
    failed_stages = [
        stage for stage in performance.get("steps", []) if stage.get("status") == "failed"
    ]
    succeeded = (
        return_code == 0
        and performance.get("status") == "completed"
        and not failed_stages
        and profile_path.is_file()
        and documentation_path.is_file()
    )

    profile = load_json(profile_path) if profile_path.is_file() else {}
    documentation = load_json(documentation_path) if documentation_path.is_file() else {}
    usage = performance.get("model_usage", {})
    usage_available = usage.get("usage_available", False)
    conflicts = profile.get("conflicts", [])
    unresolved = profile.get("unresolved", [])
    rejected = documentation.get("rejected", [])

    notes: list[str] = []
    if not succeeded:
        reason = performance.get("error_message")
        if not reason and failed_stages:
            reason = failed_stages[0].get("error_message")
        if not reason:
            reason = f"exit code {return_code}"
        notes.append(f"failure: {reason}")
    else:
        if conflicts:
            notes.append(f"conflicts: {summarize_items(conflicts, 'field')}")
        if unresolved:
            notes.append(
                f"abstentions: {summarize_items(unresolved, 'next_action')}"
            )
        divergence = divergence_note(case, profile, baselines)
        if divergence:
            notes.append(f"model divergence: {divergence}")
    if retry_number:
        notes.append(f"retry={retry_number}")

    append_row(
        {
            "run_id": run_id,
            "site": case.site,
            "model": case.model,
            "mode": case.mode,
            "rep": case.rep,
            "status": "completed" if succeeded else "failed",
            "extraction_tokens": usage.get("total_tokens", "") if usage_available else "",
            "discovery_tokens": metadata["discovery_tokens"],
            "model_calls": usage.get("requests", ""),
            "rejected_count": len(rejected),
            "unresolved_count": len(unresolved),
            "conflicts": len(conflicts),
            "latency_sec": (
                f"{performance['duration_seconds']:.3f}"
                if "duration_seconds" in performance
                else ""
            ),
            "corpus_fingerprint": metadata["corpus_fingerprint"],
            "notes": "; ".join(notes),
        }
    )
    if not succeeded:
        archive_failed_output(case, run_id)
    return succeeded


def main() -> int:
    """Validate frozen inputs, run unfinished cases, then retry failures twice."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--model", choices=MODELS)
    parser.add_argument(
        "--mode",
        choices=sorted({mode for mode, _, _ in MODE_REPETITIONS}),
    )
    parser.add_argument("--rep", type=int, choices=(1, 2, 3))
    args = parser.parse_args()

    measurements, pilots, corpus, metadata = validate_frozen_inputs(args.site)
    cases = build_cases(args.site)
    if args.model is not None:
        cases = [case for case in cases if case.model == args.model]
    if args.mode is not None:
        cases = [case for case in cases if case.mode == args.mode]
    if args.rep is not None:
        cases = [case for case in cases if case.rep == args.rep]
    done = completed_keys()
    pending = [case for case in cases if case.key not in done]
    print(
        f"Frozen corpus: {metadata['corpus_fingerprint']}\n"
        f"Discovery tokens: {metadata['discovery_tokens']}\n"
        f"Cases: {len(pending)} pending, {len(cases) - len(pending)} already completed",
        flush=True,
    )

    baselines: dict[tuple[str, str, int], tuple[str, dict[str, str]]] = {}
    failures: list[Case] = []
    for case in pending:
        if not run_case(
            case,
            measurements=measurements,
            pilots=pilots,
            corpus=corpus,
            metadata=metadata,
            baselines=baselines,
        ):
            failures.append(case)

    for retry_number, delay in ((1, 30), (2, 60)):
        if not failures:
            break
        print(f"\nRetry pass {retry_number}: waiting {delay}s for {len(failures)} case(s).")
        time.sleep(delay)
        retry_failures: list[Case] = []
        for case in failures:
            if not run_case(
                case,
                measurements=measurements,
                pilots=pilots,
                corpus=corpus,
                metadata=metadata,
                baselines=baselines,
                retry_number=retry_number,
            ):
                retry_failures.append(case)
        failures = retry_failures

    refresh_divergence_notes(args.site)
    if failures:
        print(f"\n{len(failures)} case(s) still failed after retry passes.")
        return 1
    print(f"\nAll {len(pending)} pending case(s) completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
