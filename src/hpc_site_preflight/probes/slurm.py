"""Approved two-job Slurm pilot."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hpc_site_preflight.probes.artifacts import (
    read_json,
    write_json,
    write_text,
)
from hpc_site_preflight.probes.base import (
    PilotInputs,
    PilotResultBundle,
    PilotRunner,
    PortAttempt,
    StoragePilotResult,
)
from hpc_site_preflight.probes.network import (
    COMPUTE_PROGRAM,
    close_listeners,
    reconcile_attempts,
    reserve_ports,
    run_listeners,
)

TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "TIMEOUT",
}


class SlurmPilot(PilotRunner):
    """Run Bash-first storage checks and two bounded network checks."""

    def run(self, inputs: PilotInputs) -> PilotResultBundle:
        """Always return and save the evidence collected before any failure."""

        pilot_id = uuid.uuid4().hex
        run_dir = inputs.runs_dir.expanduser().resolve() / pilot_id
        run_dir.mkdir(parents=True, exist_ok=False)
        run_dir.chmod(0o700)
        result = PilotResultBundle(
            site_id=inputs.site_id,
            scheduler="slurm",
            evidence_source="measured",
            pilot_id=pilot_id,
            collected_at=_utc_now(),
            status="partial",
            source_reference=str(run_dir / "pilot-result.json"),
        )
        jobs: list[str] = []
        listeners: dict[int, tuple[Any, str]] = {}
        login_stop = threading.Event()
        login_executor: ThreadPoolExecutor | None = None
        login_future: Any | None = None
        try:
            if inputs.scheduler != "slurm":
                raise ValueError("SlurmPilot requires scheduler=slurm")
            if shutil.which("sbatch") is None:
                raise RuntimeError("sbatch is unavailable on this host")

            checks, listeners = reserve_ports(inputs.start_port, inputs.port_count)
            config = {
                "login_host": inputs.login_host,
                "login_port_checks": checks,
                "start_port": inputs.start_port,
                "port_count": inputs.port_count,
                "connect_timeout": 10.0,
                "listener_timeout": 30.0,
                "coordination_timeout": inputs.coordination_timeout,
            }
            write_json(run_dir / "pilot-config.json", config)
            compute_program = run_dir / "compute-pilot.py"
            write_text(compute_program, COMPUTE_PROGRAM)
            compute_program.chmod(0o700)

            primary_script = run_dir / "primary-job.sh"
            write_text(
                primary_script,
                _bash_job(
                    run_dir=run_dir,
                    compute_program=compute_program,
                    storage=inputs.storage,
                    partition=inputs.partition,
                    account=inputs.account,
                    role="primary",
                ),
            )
            primary_script.chmod(0o700)
            primary_id = _submit(primary_script)
            jobs.append(primary_id)
            print(f"[submitted] primary Slurm pilot {primary_id}", flush=True)

            login_executor = ThreadPoolExecutor(max_workers=1)
            login_future = login_executor.submit(
                run_listeners,
                listeners,
                login_stop,
                30.0,
            )
            _wait_for(run_dir / "primary-bash-status.txt", primary_id, inputs)
            result.storage_results = _parse_storage(run_dir / "storage-results.tsv")
            print("[completed] compute storage checks", flush=True)

            if not _python_started(run_dir, primary_id, inputs, peer=False):
                result.errors.append("Python was unavailable in the primary job.")
                return _finish(result, inputs.output, run_dir)

            _wait_for(run_dir / "login-compute-client.json", primary_id, inputs)
            login_stop.set()
            login_server = login_future.result(timeout=35)
            login_future = None
            login_executor.shutdown(wait=True)
            login_executor = None
            login_client = read_json(run_dir / "login-compute-client.json")
            result.login_compute_attempts = [
                PortAttempt.model_validate(item)
                for item in reconcile_attempts(login_server, login_client)
            ]
            print("[completed] login/compute network checks", flush=True)

            _wait_for(run_dir / "compute-manager.json", primary_id, inputs)
            manager = read_json(run_dir / "compute-manager.json")
            primary_node = manager["node"]
            result.primary_node = _node_name(primary_node)
            if result.primary_node is None:
                raise RuntimeError("primary job did not report a Slurm node name")

            peer_script = run_dir / "peer-job.sh"
            write_text(
                peer_script,
                _bash_job(
                    run_dir=run_dir,
                    compute_program=compute_program,
                    storage={},
                    partition=inputs.partition,
                    account=inputs.account,
                    role="peer",
                    exclude=result.primary_node,
                ),
            )
            peer_script.chmod(0o700)
            peer_id = _submit(peer_script)
            jobs.append(peer_id)
            print(
                f"[submitted] peer Slurm pilot {peer_id}, excluding {result.primary_node}",
                flush=True,
            )
            _wait_for(run_dir / "peer-bash-status.txt", peer_id, inputs)
            if not _python_started(run_dir, peer_id, inputs, peer=True):
                result.errors.append("Python was unavailable in the peer job.")
                return _finish(result, inputs.output, run_dir)

            _wait_for(run_dir / "compute-compute-client.json", peer_id, inputs)
            _wait_for(run_dir / "compute-manager-attempts.json", primary_id, inputs)
            peer_node = read_json(run_dir / "peer-node.json")
            result.peer_node = _node_name(peer_node)
            result.distinct_nodes = (
                result.primary_node != result.peer_node
                if result.peer_node is not None
                else None
            )
            manager_attempts = read_json(run_dir / "compute-manager-attempts.json")
            peer_attempts = read_json(run_dir / "compute-compute-client.json")
            result.compute_compute_attempts = [
                PortAttempt.model_validate(item)
                for item in reconcile_attempts(manager_attempts, peer_attempts)
            ]
            if result.distinct_nodes is not True:
                result.errors.append("The two Slurm jobs did not verify distinct nodes.")
            result.status = "completed" if not result.errors else "partial"
            print("[completed] compute/compute network checks", flush=True)
        except Exception as exc:
            result.errors.append(f"{type(exc).__name__}: {exc}")
            result.status = "partial" if jobs else "failed"
        finally:
            write_text(run_dir / "peer-finished.txt", "completed\n")
            login_stop.set()
            if login_future is not None:
                try:
                    login_future.result(timeout=35)
                except Exception:
                    pass
            elif login_executor is None:
                close_listeners(listeners)
            if login_executor is not None:
                login_executor.shutdown(wait=True)
            if result.status != "completed":
                _cancel(jobs)
        return _finish(result, inputs.output, run_dir)


def _bash_job(
    *,
    run_dir: Path,
    compute_program: Path,
    storage: dict[str, str],
    partition: str | None,
    account: str | None,
    role: str,
    exclude: str | None = None,
) -> str:
    """Build one fixed Slurm job with Bash checks before Python."""

    peer = role == "peer"
    directives = [
        f"#SBATCH --job-name=hsp-{role}-pilot",
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks=1",
        "#SBATCH --cpus-per-task=1",
        "#SBATCH --mem-per-cpu=256M",
        "#SBATCH --time=00:10:00",
        f"#SBATCH --output={run_dir}/{role}-%j.log",
    ]
    for option, value in (
        ("partition", partition),
        ("account", account),
        ("exclude", exclude),
    ):
        if value:
            directives.append(f"#SBATCH --{option}={_safe_slurm_value(value)}")
    status = "peer-bash-status.txt" if peer else "primary-bash-status.txt"
    python_status = "peer-python-status.txt" if peer else "primary-python-status.txt"
    storage_lines = [
        f"check_storage {shlex.quote(name)} {shlex.quote(path)}"
        for name, path in storage.items()
    ]
    lines = [
        "#!/bin/bash",
        *directives,
        "",
        "set -u",
        f"RUN_DIR={shlex.quote(str(run_dir))}",
        f"PROGRAM={shlex.quote(str(compute_program))}",
        f"PREFERRED_PYTHON={shlex.quote(str(Path(sys.executable).resolve()))}",
        "",
        "check_storage() {",
        '  local name="$1" path="$2"',
        "  local visible=false readable=false writable=false",
        '  [[ -e "$path" ]] && visible=true',
        '  [[ -r "$path" ]] && readable=true',
        '  [[ -w "$path" ]] && writable=true',
        "  printf '%s\\t%s\\t%s\\t%s\\t%s\\n' \\",
        '    "$name" "$path" "$visible" "$readable" "$writable" \\',
        '    >> "$RUN_DIR/storage-results.tsv"',
        "}",
    ]
    if not peer:
        lines.extend([': > "$RUN_DIR/storage-results.tsv"', *storage_lines])
    lines.extend(
        [
            f'printf "completed\\n" > "$RUN_DIR/{status}"',
            'PYTHON=""',
            'if [[ -x "$PREFERRED_PYTHON" ]]; then PYTHON="$PREFERRED_PYTHON";',
            'elif command -v python3 >/dev/null 2>&1; then PYTHON="$(command -v python3)";',
            'elif command -v python >/dev/null 2>&1; then PYTHON="$(command -v python)"; fi',
            'if [[ -z "$PYTHON" ]]; then',
            f'  printf "unavailable\\n" > "$RUN_DIR/{python_status}"',
            "else",
            f'  printf "running\\n" > "$RUN_DIR/{python_status}"',
            f'  "$PYTHON" "$PROGRAM" --run-dir "$RUN_DIR" --role {role}',
            "  code=$?",
            '  if [[ "$code" -eq 0 ]]; then state=completed; else state=failed; fi',
            f'  printf "%s\\t%s\\n" "$state" "$code" > "$RUN_DIR/{python_status}"',
            "fi",
        ]
    )
    if peer:
        lines.append('printf "completed\\n" > "$RUN_DIR/peer-finished.txt"')
    return "\n".join(lines) + "\n"


def _submit(script: Path) -> str:
    """Submit one saved script and return the Slurm job ID."""

    completed = subprocess.run(
        ["sbatch", "--parsable", str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"sbatch failed: {detail}")
    job_id = completed.stdout.strip().split(";", 1)[0]
    if not job_id:
        raise RuntimeError("sbatch returned no job ID")
    return job_id


def _state(job_id: str) -> str:
    """Return the visible queue state, falling back to Slurm accounting."""

    try:
        completed = subprocess.run(
            ["squeue", "--noheader", "--jobs", job_id, "--format=%T"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"
    states = completed.stdout.strip().splitlines()
    if completed.returncode == 0 and states:
        return states[0].upper()
    if shutil.which("sacct") is None:
        return "NOT_VISIBLE"
    try:
        accounted = subprocess.run(
            [
                "sacct",
                "--noheader",
                "--parsable2",
                "--jobs",
                job_id,
                "--format=JobIDRaw,State",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "NOT_VISIBLE"
    for line in accounted.stdout.splitlines():
        record_id, separator, state = line.partition("|")
        if separator and record_id.strip() == job_id:
            return state.strip().split()[0].rstrip("+").upper()
    return "NOT_VISIBLE"


def _wait_for(path: Path, job_id: str, inputs: PilotInputs) -> None:
    """Wait for one shared artifact without hiding terminal failure."""

    started = time.monotonic()
    deadline = started + inputs.coordination_timeout
    last_report = 0.0
    while time.monotonic() < deadline:
        if path.exists():
            return
        state = _state(job_id)
        now = time.monotonic()
        if last_report == 0 or now - last_report >= 10:
            print(f"[waiting] job {job_id}: {state} ({int(now - started)}s)", flush=True)
            last_report = now
        if state in TERMINAL_STATES:
            for _ in range(20):
                if path.exists():
                    return
                time.sleep(0.5)
            raise RuntimeError(f"job {job_id} ended as {state} before {path.name}")
        time.sleep(2)
    raise TimeoutError(f"timed out waiting for {path.name}")


def _python_started(
    run_dir: Path,
    job_id: str,
    inputs: PilotInputs,
    *,
    peer: bool,
) -> bool:
    """Return false when Bash reports that Python could not run."""

    ready = run_dir / ("peer-node.json" if peer else "primary-node.json")
    status = run_dir / (
        "peer-python-status.txt" if peer else "primary-python-status.txt"
    )
    deadline = time.monotonic() + inputs.coordination_timeout
    while time.monotonic() < deadline:
        if ready.exists():
            return True
        value = status.read_text().strip() if status.exists() else ""
        if value.startswith(("unavailable", "failed")):
            return False
        if _state(job_id) in TERMINAL_STATES:
            return ready.exists()
        time.sleep(2)
    raise TimeoutError("timed out waiting for compute-node Python")


def _parse_storage(path: Path) -> list[StoragePilotResult]:
    """Parse the Bash storage table."""

    results: list[StoragePilotResult] = []
    if not path.exists():
        return results
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        results.append(
            StoragePilotResult(
                name=fields[0],
                path=fields[1],
                visible=fields[2] == "true",
                readable=fields[3] == "true",
                writable=fields[4] == "true",
            )
        )
    return results


def _node_name(value: dict[str, Any]) -> str | None:
    """Choose the scheduler node name, then fall back to host identity."""

    for key in ("slurm_node_name", "hostname", "fqdn"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def _safe_slurm_value(value: str) -> str:
    """Reject directive injection and unsupported Slurm value characters."""

    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError("Slurm option contains unsupported characters")
    return value


def _cancel(job_ids: list[str]) -> None:
    """Best-effort cleanup for jobs remaining after a partial pilot."""

    for job_id in job_ids:
        try:
            subprocess.run(
                ["scancel", job_id],
                check=False,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _finish(
    result: PilotResultBundle,
    output: Path,
    run_dir: Path,
) -> PilotResultBundle:
    """Save the same flat result in the run directory and requested output."""

    run_result = run_dir / "pilot-result.json"
    result.source_reference = str(run_result)
    payload = result.model_dump(mode="json", exclude_none=True)
    write_json(run_result, payload)
    write_json(output, payload)
    return result


def _utc_now() -> datetime:
    """Return a timezone-aware run timestamp."""

    return datetime.now(UTC)
