from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_evaluator() -> ModuleType:
    path = Path(__file__).parents[1] / "evaluation" / "rq1" / "evaluate_profile.py"
    spec = importlib.util.spec_from_file_location("rq1_evaluate_profile", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVALUATOR = load_evaluator()


def test_unlimited_walltime_is_a_real_value() -> None:
    assert not EVALUATOR.is_absent(-1)
    assert not EVALUATOR.is_absent([-1])


def test_syntax_template_matches_concrete_value() -> None:
    assert EVALUATOR.values_match(
        ["request_cpus = 8"],
        ["request_cpus = {cpu_count}"],
        "pattern_any",
        "/htcondor/submit_attributes/request_cpus/syntax",
    )


def test_any_valid_syntax_pattern_is_enough() -> None:
    assert EVALUATOR.values_match(
        ["-A my_account"],
        ["--account={account}", "-A {account}"],
        "pattern_any",
        "/slurm/options/account/syntax",
    )


def test_array_match_accepts_any_shared_value() -> None:
    assert EVALUATOR.values_match(
        ["CLUSTER_PROJECT", "PROJECT", "WORK"],
        ["PROJECT"],
        "required_subset",
        "/storage/project/environment_variables",
    )


def test_array_without_shared_value_does_not_match() -> None:
    assert not EVALUATOR.values_match(
        ["WORK"],
        ["SCRATCH"],
        "required_subset",
        "/storage/scratch/environment_variables",
    )


def test_gpu_model_aliases_match() -> None:
    assert EVALUATOR.values_match(
        ["Ponte Vecchio"],
        ["Intel Data Center GPU Max 1550"],
        "canonical_set",
        "/slurm/partitions/pvc/gpu_models",
    )
    assert EVALUATOR.values_match(
        ["A100"],
        ["NVIDIA A100"],
        "canonical_set",
        "/slurm/partitions/gpu/gpu_models",
    )


def test_gpu_model_set_rejects_an_extra_model() -> None:
    assert not EVALUATOR.values_match(
        ["NVIDIA A100", "NVIDIA H100"],
        ["NVIDIA A100"],
        "canonical_set",
        "/slurm/partitions/gpu/gpu_models",
    )


def test_strict_matching_does_not_apply_patterns_or_aliases() -> None:
    assert not EVALUATOR.values_match(
        ["request_cpus = 8"],
        ["request_cpus = {cpu_count}"],
        "pattern_any",
        strict=True,
    )
    assert not EVALUATOR.values_match(
        ["A100"],
        ["NVIDIA A100"],
        "canonical_set",
        strict=True,
    )
