"""Compare one constructed site profile with its reviewed ground truth."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

MISSING = object()
PLACEHOLDER = re.compile(r"\{[^{}]+\}|<[^<>]+>")
ALIASES_PATH = (
    Path(__file__).parents[1] / "ground-truth" / "normalization-aliases.json"
)


def parse_args() -> argparse.Namespace:
    """Return the profile, ground-truth, and output paths from the CLI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def normalize(value: Any) -> Any:
    """Normalize strings recursively while preserving numeric and boolean types."""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip().casefold()
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key.casefold(): normalize(item) for key, item in sorted(value.items())}
    return value


def load_aliases() -> dict[str, str]:
    """Return normalized reviewed aliases keyed by every accepted spelling."""
    document = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    for group in document["aliases"]:
        canonical = normalize(group["canonical"])
        for value in group["values"]:
            aliases[normalize(value)] = canonical
    return aliases


TEXT_ALIASES = load_aliases()


def is_absent(value: Any) -> bool:
    """Return whether a profile abstained by omitting or emptying a field."""
    if value is MISSING or value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value or all(is_absent(item) for item in value)
    if isinstance(value, dict):
        return not value
    return False


def named(items: list[dict[str, Any]] | None, key: str) -> dict[str, dict[str, Any]]:
    """Index an array of profile entities by its stable name or identifier."""
    return {
        str(item[key]): item
        for item in items or []
        if isinstance(item, dict) and item.get(key) is not None
    }


def flatten_dict(value: dict[str, Any], prefix: str, output: dict[str, Any]) -> None:
    """Flatten ordinary objects while retaining scalar arrays as field values."""
    for key, item in value.items():
        path = f"{prefix}/{key}"
        if isinstance(item, dict):
            output[path] = item
            flatten_dict(item, path, output)
        elif not isinstance(item, list) or not item or not isinstance(item[0], dict):
            output[path] = item


def add_named_entities(
    output: dict[str, Any],
    prefix: str,
    items: list[dict[str, Any]] | None,
    key: str,
) -> None:
    """Flatten fields from a named entity array into stable evaluation paths."""
    for entity_name, entity in named(items, key).items():
        entity_prefix = f"{prefix}/{entity_name}"
        for field, value in entity.items():
            if field in {key, "name"}:
                continue
            path = f"{entity_prefix}/{field}"
            output[path] = value
            if isinstance(value, dict):
                flatten_dict(value, path, output)


def add_htcondor_summary(output: dict[str, Any], htcondor: dict[str, Any]) -> None:
    """Derive stable flat HTCondor facts from order-independent resource groups."""
    submit_command = htcondor.get("submit_command")
    output["/htcondor/submit_command_available"] = bool(submit_command)

    cpu_groups = htcondor.get("cpu_groups") or []
    gpu_groups = htcondor.get("gpu_groups") or []
    pool_total = (htcondor.get("pool_totals") or {}).get("machine_count")
    classified_total = sum(
        group.get("machine_count", 0)
        for group in cpu_groups
        if isinstance(group.get("machine_count"), int)
    )
    if isinstance(pool_total, int):
        output["/htcondor/unclassified_machine_count"] = pool_total - classified_total

    cpu_values = [
        group["cpu_cores_per_machine"]
        for group in cpu_groups
        if isinstance(group.get("cpu_cores_per_machine"), int)
    ]
    memory_min = [
        group["memory_mib_min"]
        for group in cpu_groups
        if isinstance(group.get("memory_mib_min"), int)
    ]
    memory_max = [
        group["memory_mib_max"]
        for group in cpu_groups
        if isinstance(group.get("memory_mib_max"), int)
    ]
    gpu_values = [
        group["gpu_count_per_machine"]
        for group in gpu_groups
        if isinstance(group.get("gpu_count_per_machine"), int)
    ]
    gpu_machine_count = sum(
        group.get("machine_count", 0)
        for group in gpu_groups
        if isinstance(group.get("machine_count"), int)
    )

    summary = "/htcondor/resource_summary"
    if cpu_values:
        output[f"{summary}/cpu_cores_per_machine_values"] = sorted(set(cpu_values))
        output[f"{summary}/cpu_cores_per_machine_min"] = min(cpu_values)
        output[f"{summary}/cpu_cores_per_machine_max"] = max(cpu_values)
    if memory_min:
        output[f"{summary}/memory_mib_per_machine_min"] = min(memory_min)
    if memory_max:
        output[f"{summary}/memory_mib_per_machine_max"] = max(memory_max)
    if gpu_values:
        output[f"{summary}/gpu_capable_machine_count"] = gpu_machine_count
        output[f"{summary}/gpus_per_machine_values"] = sorted(set(gpu_values))
        output[f"{summary}/gpus_per_machine_min"] = min(gpu_values)
        output[f"{summary}/gpus_per_machine_max"] = max(gpu_values)


def flatten_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return profile leaves keyed by the stable paths used in ground truth."""
    output: dict[str, Any] = {}
    for key, value in profile.items():
        if key in {"slurm", "htcondor", "storage"}:
            output[f"/{key}"] = value
            continue
        if isinstance(value, dict):
            flatten_dict(value, f"/{key}", output)
        elif not isinstance(value, list) or not value or not isinstance(value[0], dict):
            output[f"/{key}"] = value

    slurm = profile.get("slurm") or {}
    flatten_dict(
        {key: value for key, value in slurm.items() if key not in {"options", "partitions"}},
        "/slurm",
        output,
    )
    add_named_entities(output, "/slurm/options", slurm.get("options"), "name")
    add_named_entities(output, "/slurm/partitions", slurm.get("partitions"), "name")

    htcondor = profile.get("htcondor") or {}
    flatten_dict(
        {
            key: value
            for key, value in htcondor.items()
            if key not in {"submit_attributes", "cpu_groups", "gpu_groups"}
        },
        "/htcondor",
        output,
    )
    add_named_entities(
        output,
        "/htcondor/submit_attributes",
        htcondor.get("submit_attributes"),
        "name",
    )
    if htcondor:
        add_htcondor_summary(output, htcondor)

    add_named_entities(output, "/storage", profile.get("storage"), "id")
    return output


def parse_expected(raw: str) -> Any:
    """Parse the ground-truth JSON convention, including uppercase booleans."""
    value = raw.strip()
    if value == "TRUE":
        return True
    if value == "FALSE":
        return False
    return json.loads(value)


def normalize_syntax(value: str) -> str:
    """Normalize spacing and placeholder names in scheduler syntax."""
    value = normalize(value)
    value = re.sub(r"\s*=\s*", "=", value)
    return PLACEHOLDER.sub("{value}", value)


def syntax_matches(actual: Any, expected: Any) -> bool:
    """Match one concrete or templated scheduler syntax against another."""
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    actual = normalize_syntax(actual)
    expected = normalize_syntax(expected)
    if actual == expected:
        return True

    # Documentation sometimes writes an executable path as prose-like syntax.
    if "=" in actual and "=" in expected:
        actual_name, actual_value = actual.split("=", 1)
        expected_name, expected_value = expected.split("=", 1)
        if actual_name != expected_name or not actual_value:
            return False
        if (
            expected_value == "{value}"
            or "path_to_" in expected_value
            or "your_" in expected_value
        ):
            return True

    if "{value}" not in expected:
        return False
    expression = re.escape(expected).replace(r"\{value\}", r"\S+")
    return re.fullmatch(expression, actual) is not None


def canonical_text(value: Any) -> Any:
    """Apply reviewed aliases without embedding site values in evaluator code."""
    if not isinstance(value, str):
        return value
    value = normalize(value)
    return TEXT_ALIASES.get(value, value)


def scalar_matches(actual: Any, expected: Any, *, syntax: bool) -> bool:
    """Compare one scalar, including syntax templates and known aliases."""
    if syntax and syntax_matches(actual, expected):
        return True
    return canonical_text(actual) == canonical_text(expected)


def encoded_set(values: list[Any], *, canonical: bool) -> set[str]:
    """Return an order-independent representation of array values."""
    return {
        json.dumps(
            canonical_text(value) if canonical else value,
            ensure_ascii=False,
            sort_keys=True,
        )
        for value in values
    }


def values_match(
    actual: Any,
    expected: Any,
    rule: str,
    field_path: str = "",
    *,
    strict: bool = False,
) -> bool:
    """Apply the reviewed comparison rule to one populated field."""
    actual = normalize(actual)
    expected = normalize(expected)
    array_rules = {"pattern_any", "required_subset", "canonical_set", "set"}
    if strict and rule in array_rules:
        if not isinstance(actual, list) or not isinstance(expected, list):
            return False
        return encoded_set(actual, canonical=False) == encoded_set(
            expected, canonical=False
        )
    if rule == "pattern_any":
        if not isinstance(actual, list) or not isinstance(expected, list):
            return False
        return any(
            syntax_matches(actual_item, expected_item)
            for actual_item in actual
            for expected_item in expected
        )
    if rule == "required_subset":
        if not isinstance(actual, list) or not isinstance(expected, list):
            return False
        return all(
            any(
                scalar_matches(actual_item, expected_item, syntax=False)
                for actual_item in actual
            )
            for expected_item in expected
        )
    if rule in {"canonical_set", "set"}:
        if not isinstance(actual, list) or not isinstance(expected, list):
            return False
        return encoded_set(actual, canonical=True) == encoded_set(
            expected, canonical=True
        )
    if rule == "numeric_exact":
        if isinstance(actual, bool) or isinstance(expected, bool):
            return False
        return actual == expected
    return scalar_matches(actual, expected, syntax=False)


def render(value: Any) -> str:
    """Serialize a compared value for the intermediate CSV."""
    if value is MISSING:
        return "<missing>"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def compare(
    profile: dict[str, Any],
    truth_rows: list[dict[str, str]],
    *,
    strict_matching: bool = False,
) -> list[dict[str, str]]:
    """Return one comparison outcome for every reviewed ground-truth row."""
    values = flatten_profile(profile)
    comparisons: list[dict[str, str]] = []
    for truth in truth_rows:
        path = truth["field_path"]
        actual = values.get(path, MISSING)
        expected_state = truth["expected_state"]
        expected = (
            parse_expected(truth["expected_value_json"])
            if expected_state == "has_value"
            else MISSING
        )
        actual_absent = is_absent(actual)

        if expected_state == "absent":
            outcome = "correct_abstention" if actual_absent else "unsupported_value"
        elif actual_absent:
            outcome = "false_abstention"
        elif values_match(
            actual,
            expected,
            truth["match_rule"],
            path,
            strict=strict_matching,
        ):
            outcome = "correct_value"
        else:
            outcome = "incorrect_value"

        comparisons.append(
            {
                "site_id": truth["site_id"],
                "field_id": truth["field_id"],
                "field_path": path,
                "section": truth["section"],
                "value_type": truth["value_type"],
                "evidence_class": truth["evidence_class"],
                "expected_state": expected_state,
                "expected_value_json": truth["expected_value_json"],
                "example_value_json": truth["example_value_json"],
                "observed_state": (
                    "missing"
                    if actual is MISSING
                    else "empty"
                    if actual_absent
                    else "has_value"
                ),
                "observed_value_json": render(actual),
                "match_rule": truth["match_rule"],
                "outcome": outcome,
            }
        )
    return comparisons


def main() -> None:
    """Load one profile and truth table, compare them, and write field outcomes."""
    args = parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    with args.ground_truth.open(encoding="utf-8", newline="") as handle:
        truth_rows = list(csv.DictReader(handle))
    comparisons = compare(profile, truth_rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)

    counts: dict[str, int] = {}
    for row in comparisons:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    print(json.dumps({"rows": len(comparisons), "outcomes": counts}, indent=2))


if __name__ == "__main__":
    main()
