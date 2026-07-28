"""Offline tests for the standalone Anvil pilot prototype."""

from __future__ import annotations

import importlib.util
import random
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

from hpc_site_preflight.cli import main as cli_main
from hpc_site_preflight.probes.base import PilotResultBundle

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "anvil_pilot.py"
ANVIL = ROOT / "examples" / "simulate" / "anvil"


def load_pilot() -> ModuleType:
    spec = importlib.util.spec_from_file_location("anvil_pilot", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_ports_cover_four_thousand_port_windows() -> None:
    pilot = load_pilot()

    ports = pilot.select_ports(9000, 8, random_source=random.Random(7))

    assert len(ports) == 8
    assert ports[0] == 9000
    assert len(set(ports)) == 8
    for index in range(4):
        window = ports[index * 2 : index * 2 + 2]
        low = 9000 + index * 1000
        assert all(low <= port <= low + 999 for port in window)


def test_generated_compute_program_compiles() -> None:
    pilot = load_pilot()

    compile(pilot.COMPUTE_PROGRAM, "compute-pilot.py", "exec")


def test_ports_are_bounded_and_even() -> None:
    pilot = load_pilot()

    with pytest.raises(ValueError, match="even number"):
        pilot.select_ports(9000, 7)
    with pytest.raises(ValueError, match="between 1024 and 65535"):
        pilot.select_ports(65000, 8)


def test_job_omits_optional_slurm_directives(tmp_path: Path) -> None:
    pilot = load_pilot()

    job = pilot.bash_job(
        run_dir=tmp_path,
        compute_program=tmp_path / "compute-pilot.py",
        preferred_python=Path("/usr/bin/python3"),
        storage={"home": "/home/example"},
        partition=None,
        account=None,
        walltime="00:10:00",
    )

    assert "#SBATCH --partition=" not in job
    assert "#SBATCH --account=" not in job
    assert "check_storage home /home/example" in job
    assert 'if [[ -z "$PYTHON" ]]' in job


def test_job_includes_provided_slurm_directives(tmp_path: Path) -> None:
    pilot = load_pilot()

    job = pilot.bash_job(
        run_dir=tmp_path,
        compute_program=tmp_path / "compute-pilot.py",
        preferred_python=Path("/usr/bin/python3"),
        storage={},
        partition="shared",
        account="example-account",
        walltime="00:05:00",
    )

    assert "#SBATCH --partition=shared" in job
    assert "#SBATCH --account=example-account" in job


def test_peer_job_excludes_primary_node(tmp_path: Path) -> None:
    pilot = load_pilot()

    job = pilot.bash_job(
        run_dir=tmp_path,
        compute_program=tmp_path / "compute-pilot.py",
        preferred_python=Path("/usr/bin/python3"),
        storage={},
        partition=None,
        account=None,
        walltime="00:10:00",
        role="peer",
        exclude="a123",
    )

    assert "#SBATCH --exclude=a123" in job
    assert "--role peer" in job
    assert 'peer-finished.txt"' in job


def test_dry_run_saves_all_reviewable_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pilot = load_pilot()
    runs_dir = tmp_path / "pilot-runs"

    exit_code = pilot.main(
        [
            "--dry-run",
            "--runs-dir",
            str(runs_dir),
            "--storage",
            "home=/home/example",
        ]
    )

    assert exit_code == 0
    run_directories = list(runs_dir.iterdir())
    assert len(run_directories) == 1
    run_dir = run_directories[0]
    assert (run_dir / "pilot-config.json").exists()
    assert (run_dir / "compute-pilot.py").exists()
    assert (run_dir / "compute-job.sh").exists()
    assert (run_dir / "peer-job.sh").exists()
    output = capsys.readouterr().out
    assert "Planned login ports:" in output
    assert "Saved compute program:" in output
    assert "Saved primary Bash job:" in output
    assert "Saved peer Bash job:" in output


def test_bash_results_are_parsed(tmp_path: Path) -> None:
    pilot = load_pilot()
    result_path = tmp_path / "bash-results.tsv"
    result_path.write_text(
        "identity\tslurm_job_id\t123\n"
        "storage\thome\t/home/example\ttrue\ttrue\ttrue\ttrue\n",
        encoding="utf-8",
    )

    parsed = pilot.parse_bash_results(result_path)

    assert parsed["identity"]["slurm_job_id"] == "123"
    assert parsed["storage"][0]["name"] == "home"
    assert parsed["storage"][0]["writable"] is True


def test_login_manager_checks_preselected_ports_concurrently(
    tmp_path: Path,
) -> None:
    pilot = load_pilot()
    ports: list[int] = []
    listeners: dict[int, tuple[socket.socket, str]] = {}
    for _ in range(2):
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            candidate.bind(("127.0.0.1", 0))
        except PermissionError:
            candidate.close()
            pytest.skip("the execution sandbox prohibits local TCP sockets")
        candidate.listen(1)
        port = candidate.getsockname()[1]
        ports.append(port)
        listeners[port] = (candidate, f"nonce-{port}")

    def connect(port: int) -> str:
        nonce = listeners[port][1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(nonce.encode())
            return connection.recv(256).decode()

    stop_event = pilot.threading.Event()
    with ThreadPoolExecutor(max_workers=3) as executor:
        manager = executor.submit(
            pilot.run_login_manager,
            listeners=listeners,
            stop_event=stop_event,
            listener_timeout=2,
        )
        responses = list(executor.map(connect, ports))
        stop_event.set()

    result = manager.result()
    assert all(response.startswith("ACK:") for response in responses)
    assert all(attempt["status"] == "passed" for attempt in result["attempts"])


def test_compute_network_requires_matching_ports_and_distinct_nodes() -> None:
    pilot = load_pilot()
    manager = {
        "slurm_node_name": "a001",
        "attempts": [
            {"port": 9000, "status": "passed"},
            {"port": 9100, "status": "failed"},
        ],
    }
    worker = {
        "slurm_node_name": "a002",
        "attempts": [
            {"port": 9000, "status": "passed"},
            {"port": 9100, "status": "passed"},
        ],
    }

    result = pilot.reconciled_network(
        manager,
        worker,
        distinct_nodes=pilot.nodes_are_distinct(manager, worker),
        require_distinct=True,
    )

    assert result["verified_ports"] == [9000]
    assert result["distinct_nodes"] is True
    assert result["successful"] is True


def test_flat_result_matches_profile_pipeline_contract() -> None:
    pilot = load_pilot()
    detailed = {
        "schema_version": "0.4",
        "run_id": "pilot-test",
        "site_id": "anvil",
        "collected_at": "2026-07-27T12:00:00Z",
        "bash": {
            "primary": {
                "storage": [
                    {
                        "name": "home",
                        "path": "/home/example",
                        "exists": True,
                        "readable": True,
                        "writable": True,
                    }
                ]
            }
        },
        "network": {
            "login_compute": {
                "manager": {
                    "attempts": [{"port": 9000, "status": "passed", "error": None}]
                },
                "worker": {
                    "hostname": "a001",
                    "attempts": [{"port": 9000, "status": "passed", "error": None}],
                },
            },
            "compute_compute": {
                "manager": {
                    "slurm_node_name": "a001",
                    "attempts": [{"port": 9000, "status": "passed", "error": None}],
                },
                "worker": {
                    "slurm_node_name": "a002",
                    "attempts": [{"port": 9000, "status": "passed", "error": None}],
                },
                "distinct_nodes": True,
            },
        },
    }

    flat = pilot.flat_pilot_result(
        detailed,
        source_reference="pilot-run-details.json",
    )
    validated = PilotResultBundle.model_validate(flat)

    assert validated.status == "completed"
    assert validated.storage_results[0].visible is True
    assert validated.login_compute_attempts[0].status == "passed"
    assert validated.compute_compute_attempts[0].port == 9000
    assert validated.primary_node == "a001"
    assert validated.peer_node == "a002"


def test_flat_standalone_result_runs_through_simulated_profile_build(
    tmp_path: Path,
) -> None:
    pilot = load_pilot()
    flat = pilot.flat_pilot_result(
        {
            "run_id": "pilot-test",
            "site_id": "anvil",
            "collected_at": "2026-07-27T12:00:00Z",
            "bash": {"primary": {"storage": []}},
            "network": {
                "login_compute": {
                    "manager": {
                        "attempts": [
                            {"port": 9000, "status": "passed", "error": None}
                        ]
                    },
                    "worker": {
                        "attempts": [
                            {"port": 9000, "status": "passed", "error": None}
                        ]
                    },
                },
                "compute_compute": {
                    "manager": {
                        "hostname": "a001",
                        "attempts": [
                            {"port": 9100, "status": "passed", "error": None}
                        ],
                    },
                    "worker": {
                        "hostname": "a002",
                        "attempts": [
                            {"port": 9100, "status": "passed", "error": None}
                        ],
                    },
                    "distinct_nodes": True,
                },
            },
        },
        source_reference="pilot-run-details.json",
    )
    pilot_path = tmp_path / "pilot-result.json"
    pilot.atomic_write_json(pilot_path, flat)
    output_dir = tmp_path / "artifacts"

    exit_code = cli_main(
        [
            "profile",
            "build",
            "--measurements",
            str(ANVIL / "login-measurements.json"),
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--run-pilots",
            "--pilot-results",
            str(pilot_path),
            "--output-dir",
            str(output_dir),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "site-profile.json").exists()
