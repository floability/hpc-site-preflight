"""Deterministic Floability adapter and preflight tests."""

import json
from pathlib import Path

from hpc_site_preflight.backpack.loader import load_backpack
from hpc_site_preflight.cli import main
from hpc_site_preflight.preflight.planner import plan_preflight
from hpc_site_preflight.profiles.models import SiteProfile

ROOT = Path(__file__).parents[1]
BACKPACK = ROOT / "examples" / "backpacks" / "preflight-case-study"


def _profile(site: str) -> SiteProfile:
    path = ROOT / "artifacts" / site / "site-profile.json"
    return SiteProfile.model_validate_json(path.read_text(encoding="utf-8"))


def test_floability_adapter_applies_cli_precedence() -> None:
    requirements = load_backpack(
        BACKPACK,
        f"floability execute --backpack {BACKPACK} --workers 2 --cores-per-worker 64",
    )

    assert requirements.action == "execute"
    assert requirements.resources.minimum_workers == 2
    assert requirements.resources.maximum_workers == 2
    assert requirements.resources.cores_per_worker == 64
    assert requirements.resources.memory_mb_per_worker == 1200000
    assert requirements.resources.gpus_per_worker == 0


def test_case_study_is_ready_on_stampede3() -> None:
    requirements = load_backpack(
        BACKPACK,
        f"floability execute --backpack {BACKPACK}",
    )

    result = plan_preflight(requirements, _profile("stampede3"), {"time": "01:00:00"})

    assert result.result == "ready"
    assert result.execution_plan is not None
    assert result.execution_plan.selected_resource.name == "amd-rtx"
    assert "--batch-type slurm" in result.execution_plan.floability_command_text
    assert "--time=01:00:00" in result.execution_plan.scheduler_arguments
    assert "--nodes=1" in result.execution_plan.scheduler_arguments
    assert "--ntasks-per-node=1" in result.execution_plan.scheduler_arguments
    assert "--partition=amd-rtx" in result.execution_plan.scheduler_arguments
    assert result.execution_plan.settings["manager_ports"] == [30000, 30264]


def test_case_study_is_blocked_on_anvil() -> None:
    requirements = load_backpack(
        BACKPACK,
        f"floability execute --backpack {BACKPACK}",
    )

    result = plan_preflight(
        requirements,
        _profile("anvil"),
        {"account": "paper-allocation"},
    )

    assert result.result == "blocked"
    assert result.execution_plan is None
    assert any(issue.field_path == "/slurm/partitions" for issue in result.issues)
    assert len(result.issues) == 1


def test_unverified_disk_produces_unknown(tmp_path: Path) -> None:
    backpack = tmp_path / "disk-workflow"
    backpack.mkdir()
    (backpack / "compute.yml").write_text(
        "vine_factory_config:\n  cores: 1\n  disk: 4000\n",
        encoding="utf-8",
    )
    requirements = load_backpack(
        backpack,
        f"floability execute --backpack {backpack}",
    )

    result = plan_preflight(requirements, _profile("anvil"), {"account": "allocation"})

    assert result.result == "unknown"
    assert any(issue.field_path == "/resources/disk_mb_per_worker" for issue in result.issues)


def test_gpu_count_is_part_of_resource_matching(tmp_path: Path) -> None:
    backpack = tmp_path / "gpu-workflow"
    backpack.mkdir()
    (backpack / "compute.yml").write_text(
        "vine_factory_config:\n  cores: 1\n  memory: 1024\n  gpus: 5\n",
        encoding="utf-8",
    )
    requirements = load_backpack(
        backpack,
        f"floability execute --backpack {backpack}",
    )

    result = plan_preflight(requirements, _profile("anvil"), {"account": "allocation"})

    assert requirements.resources.gpus_per_worker == 5
    assert result.result == "blocked"
    assert result.issues[0].field_path == "/slurm/partitions"


def test_preflight_cli_writes_structured_result(tmp_path: Path) -> None:
    output = tmp_path / "preflight-result.json"
    exit_code = main(
        [
            "preflight",
            "--backpack",
            str(BACKPACK),
            "--site-profile",
            str(ROOT / "artifacts" / "stampede3" / "site-profile.json"),
            "--floability-command",
            f"floability execute --backpack {BACKPACK}",
            "--scheduler-value",
            "time=01:00:00",
            "--output",
            str(output),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["result"] == "ready"
    assert result["execution_plan"]["selected_resource"]["name"] == "amd-rtx"


def test_optional_recorded_narration_cannot_change_decision(tmp_path: Path) -> None:
    recording = tmp_path / "model.json"
    recording.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "note": "Offline preflight narration fixture.",
                "responses": [
                    {
                        "output_name": "preflight_narration",
                        "data": {"message": "The workflow is ready on Stampede3."},
                        "response_id": "narration-1",
                        "input_tokens": 10,
                        "output_tokens": 8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "narrated.json"

    exit_code = main(
        [
            "preflight",
            "--backpack",
            str(BACKPACK),
            "--site-profile",
            str(ROOT / "artifacts" / "stampede3" / "site-profile.json"),
            "--floability-command",
            f"floability execute --backpack {BACKPACK}",
            "--scheduler-value",
            "time=01:00:00",
            "--explain-with-model",
            "--model-mode",
            "simulate",
            "--model-recording",
            str(recording),
            "--output",
            str(output),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert result["result"] == "ready"
    assert result["narrative"] == "The workflow is ready on Stampede3."
