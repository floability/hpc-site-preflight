#!/usr/bin/env python3
"""Create and run one inspectable Slurm pilot from a login node."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.4"
DEFAULT_START_PORT = 9000
DEFAULT_PORT_COUNT = 8
MAX_PORT_COUNT = 32
ARTIFACT_GRACE_SECONDS = 60
TERMINAL_SLURM_STATES = {
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

# This standard-library-only program is copied into every run directory.
COMPUTE_PROGRAM = r'''#!/usr/bin/env python3
"""Run the compute-node portion of one HPC Site Preflight pilot."""

import argparse
import json
import os
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_port(check, host, timeout):
    port = check["port"]
    attempt = {"port": port, "status": "failed", "error": None}
    try:
        nonce = check["nonce"]
        connection = socket.create_connection(
            (host, port),
            timeout=timeout,
        )
        with connection:
            connection.settimeout(timeout)
            connection.sendall(nonce.encode("utf-8"))
            response = connection.recv(256).decode(
                "utf-8",
                errors="replace",
            )
        if response == "ACK:{}".format(nonce):
            attempt["status"] = "passed"
        else:
            attempt["error"] = "acknowledgement mismatch"
    except (OSError, TimeoutError, KeyError) as exc:
        attempt["error"] = "{}: {}".format(type(exc).__name__, exc)
    return attempt


def check_ports(checks, host, timeout):
    with ThreadPoolExecutor(max_workers=max(1, len(checks))) as executor:
        return list(
            executor.map(
                lambda check: check_port(check, host, timeout),
                checks,
            )
        )


def reserve_ports(start_port, count):
    checks = []
    listeners = {}
    unavailable = []
    windows = count // 2
    random_source = secrets.SystemRandom()
    try:
        for window in range(windows):
            low = start_port + window * 1000
            high = low + 999
            reserved = 0
            tried = set()
            for attempt_index in range(20):
                port = (
                    start_port
                    if window == 0 and attempt_index == 0
                    else random_source.randrange(low, high + 1)
                )
                if port in tried:
                    continue
                tried.add(port)
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    server.bind(("0.0.0.0", port))
                    server.listen(4)
                except OSError as exc:
                    server.close()
                    unavailable.append(
                        {
                            "port": port,
                            "error": "{}: {}".format(type(exc).__name__, exc),
                        }
                    )
                    continue
                nonce = secrets.token_hex(16)
                checks.append({"port": port, "nonce": nonce})
                listeners[port] = (server, nonce)
                reserved += 1
                if reserved == 2:
                    break
            if reserved != 2:
                raise RuntimeError(
                    "could not reserve two ports in {}-{}".format(low, high)
                )
    except Exception:
        for server, _nonce in listeners.values():
            server.close()
        raise
    return checks, listeners, unavailable


def accept_peer(port, server, nonce, stop_event, timeout):
    attempt = {"port": port, "status": "failed", "error": None}
    try:
        server.settimeout(1)
        while not stop_event.is_set():
            try:
                connection, address = server.accept()
            except TimeoutError:
                continue
            try:
                with connection:
                    connection.settimeout(timeout)
                    received = connection.recv(256).decode(
                        "utf-8",
                        errors="replace",
                    )
                    if received != nonce:
                        attempt["error"] = "nonce mismatch"
                        continue
                    connection.sendall("ACK:{}".format(nonce).encode("utf-8"))
                    attempt["status"] = "passed"
                    attempt["error"] = None
                    attempt["peer"] = address[0]
                    break
            except (OSError, TimeoutError) as exc:
                attempt["error"] = "{}: {}".format(type(exc).__name__, exc)
    except (OSError, TimeoutError) as exc:
        attempt["error"] = "{}: {}".format(type(exc).__name__, exc)
    finally:
        server.close()
    if attempt["status"] != "passed" and attempt["error"] is None:
        attempt["error"] = "no authenticated connection before the peer ended"
    return attempt


def identity():
    return {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "slurm_node_name": os.environ.get("SLURMD_NODENAME"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    }


def run_primary(run_dir, config):
    node = identity()
    write_json(run_dir / "compute-ready.json", node)
    login_attempts = check_ports(
        config["port_checks"],
        config["login_host"],
        config["connect_timeout"],
    )
    write_json(
        run_dir / "compute-results.json",
        {
            **node,
            "manager_host": config["login_host"],
            "attempts": login_attempts,
            "successful": any(
                item["status"] == "passed" for item in login_attempts
            ),
        },
    )

    checks, listeners, unavailable = reserve_ports(
        config["start_port"],
        config["port_count"],
    )
    manager = {
        **node,
        "host": node["fqdn"] or node["hostname"],
        "port_checks": checks,
        "reservation_failures": unavailable,
    }
    write_json(run_dir / "compute-manager.json", manager)

    stop_event = threading.Event()
    attempts_by_port = {}
    with ThreadPoolExecutor(max_workers=max(1, len(listeners))) as executor:
        futures = {
            executor.submit(
                accept_peer,
                port,
                server,
                nonce,
                stop_event,
                config["listener_timeout"],
            ): port
            for port, (server, nonce) in listeners.items()
        }
        deadline = time.monotonic() + config["coordination_timeout"]
        peer_finished = run_dir / "peer-finished.txt"
        while time.monotonic() < deadline and not peer_finished.exists():
            time.sleep(1)
        stop_event.set()
        for future in as_completed(futures):
            attempt = future.result()
            attempts_by_port[attempt["port"]] = attempt

    attempts = [attempts_by_port[check["port"]] for check in checks]
    write_json(
        run_dir / "compute-manager-results.json",
        {
            **node,
            "attempts": attempts,
            "successful": any(
                item["status"] == "passed" for item in attempts
            ),
        },
    )
    return 0


def run_peer(run_dir, config):
    node = identity()
    write_json(run_dir / "peer-ready.json", node)
    manager = read_json(run_dir / "compute-manager.json")
    attempts = check_ports(
        manager["port_checks"],
        manager["host"],
        config["connect_timeout"],
    )
    write_json(
        run_dir / "peer-results.json",
        {
            **node,
            "manager_host": manager["host"],
            "attempts": attempts,
            "successful": any(
                item["status"] == "passed" for item in attempts
            ),
        },
    )
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--role", choices=("primary", "peer"), required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    config = read_json(run_dir / "pilot-config.json")
    if args.role == "primary":
        return run_primary(run_dir, config)
    return run_peer(run_dir, config)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def utc_now() -> str:
    """Return a UTC timestamp for run artifacts."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: UP017


def atomic_write_text(path: Path, value: str) -> None:
    """Write one text artifact atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def atomic_write_json(path: Path, value: Any) -> None:
    """Write one JSON artifact atomically."""

    atomic_write_text(path, json.dumps(value, indent=2) + "\n")


def read_json(path: Path) -> Any:
    """Read one JSON artifact."""

    return json.loads(path.read_text(encoding="utf-8"))


def select_ports(
    start_port: int,
    count: int = DEFAULT_PORT_COUNT,
    *,
    random_source: Any | None = None,
) -> list[int]:
    """Select two bounded ports from each consecutive 1,000-port window."""

    if count < 2 or count > MAX_PORT_COUNT or count % 2:
        raise ValueError(f"port count must be an even number from 2 to {MAX_PORT_COUNT}")
    window_count = count // 2
    if start_port < 1024 or start_port + window_count * 1000 - 1 > 65535:
        raise ValueError("selected pilot ports must remain between 1024 and 65535")

    random_source = random_source or secrets.SystemRandom()
    ports = [start_port]
    first_high = start_port + 999
    ports.append(random_source.randrange(start_port + 1, first_high + 1))
    for window in range(1, window_count):
        low = start_port + window * 1000
        high = low + 999
        first = random_source.randrange(low, high + 1)
        second = random_source.randrange(low, high + 1)
        while second == first:
            second = random_source.randrange(low, high + 1)
        ports.extend([first, second])
    return ports


def reserve_ports(
    start_port: int,
    count: int = DEFAULT_PORT_COUNT,
    *,
    random_source: Any | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[int, tuple[socket.socket, str]],
    list[dict[str, Any]],
]:
    """Bind two ports per window and retain every successful listener."""

    if count < 2 or count > MAX_PORT_COUNT or count % 2:
        raise ValueError(f"port count must be an even number from 2 to {MAX_PORT_COUNT}")
    window_count = count // 2
    if start_port < 1024 or start_port + window_count * 1000 - 1 > 65535:
        raise ValueError("selected pilot ports must remain between 1024 and 65535")

    random_source = random_source or secrets.SystemRandom()
    checks: list[dict[str, Any]] = []
    listeners: dict[int, tuple[socket.socket, str]] = {}
    unavailable: list[dict[str, Any]] = []
    try:
        for window in range(window_count):
            low = start_port + window * 1000
            high = low + 999
            reserved_in_window = 0
            tried: set[int] = set()
            for attempt_index in range(20):
                port = (
                    start_port
                    if window == 0 and attempt_index == 0
                    else random_source.randrange(low, high + 1)
                )
                if port in tried:
                    continue
                tried.add(port)
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    server.bind(("0.0.0.0", port))
                    server.listen(4)
                except OSError as exc:
                    server.close()
                    unavailable.append(
                        {
                            "port": port,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                nonce = secrets.token_hex(16)
                checks.append({"port": port, "nonce": nonce})
                listeners[port] = (server, nonce)
                reserved_in_window += 1
                if reserved_in_window == 2:
                    break
            if reserved_in_window != 2:
                raise RuntimeError(
                    f"could not reserve two ports in the {low}-{high} window"
                )
    except Exception:
        for server, _nonce in listeners.values():
            server.close()
        raise
    return checks, listeners, unavailable


def parse_storage(values: list[str]) -> dict[str, str]:
    """Parse repeated NAME=/absolute/path storage arguments."""

    storage: dict[str, str] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        name = name.strip().lower()
        path = Path(raw_path.strip()).expanduser()
        if not separator or not name or not raw_path.strip():
            raise ValueError("--storage must use NAME=/absolute/path")
        if not path.is_absolute():
            raise ValueError("--storage paths must be absolute")
        storage[name] = str(path)
    return storage


def validate_slurm_value(name: str, value: str | None) -> str | None:
    """Reject newlines and other unsafe characters in Slurm directive values."""

    if value is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError(f"--{name} contains unsupported characters")
    return value


def bash_job(
    *,
    run_dir: Path,
    compute_program: Path,
    preferred_python: Path,
    storage: dict[str, str],
    partition: str | None,
    account: str | None,
    walltime: str,
    role: str = "primary",
    exclude: str | None = None,
) -> str:
    """Create the complete Bash job, omitting unspecified Slurm directives."""

    if role not in {"primary", "peer"}:
        raise ValueError("job role must be primary or peer")
    is_peer = role == "peer"
    job_name = "hsp-pilot-peer" if is_peer else "hsp-pilot"
    output_name = "slurm-peer-%j.log" if is_peer else "slurm-primary-%j.log"
    bash_results_name = "peer-bash-results.tsv" if is_peer else "bash-results.tsv"
    bash_status_name = "peer-bash-status.txt" if is_peer else "bash-status.txt"
    python_status_name = "peer-python-status.txt" if is_peer else "python-status.txt"
    directives = [
        f"#SBATCH --job-name={job_name}",
        "#SBATCH --nodes=1-1",
        "#SBATCH --ntasks=1",
        "#SBATCH --ntasks-per-node=1",
        "#SBATCH --cpus-per-task=1",
        "#SBATCH --mem-per-cpu=256M",
        f"#SBATCH --time={walltime}",
        f"#SBATCH --output={run_dir}/{output_name}",
    ]
    if partition:
        directives.append(f"#SBATCH --partition={partition}")
    if account:
        directives.append(f"#SBATCH --account={account}")
    if exclude:
        directives.append(f"#SBATCH --exclude={exclude}")

    storage_calls = [
        f"check_storage {shlex.quote(name)} {shlex.quote(path)}"
        for name, path in storage.items()
    ]
    return "\n".join(
        [
            "#!/bin/bash",
            *directives,
            "",
            "set -u",
            f"RUN_DIR={shlex.quote(str(run_dir))}",
            f"COMPUTE_PROGRAM={shlex.quote(str(compute_program))}",
            f"PREFERRED_PYTHON={shlex.quote(str(preferred_python))}",
            f'BASH_RESULTS="$RUN_DIR/{bash_results_name}"',
            f'PYTHON_STATUS="$RUN_DIR/{python_status_name}"',
            "",
            "check_storage() {",
            '  local name="$1" path="$2"',
            "  local exists=false readable=false writable=false executable=false",
            '  [[ -e "$path" ]] && exists=true',
            '  [[ -r "$path" ]] && readable=true',
            '  [[ -w "$path" ]] && writable=true',
            '  [[ -x "$path" ]] && executable=true',
            "  printf 'storage\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \\",
            '    "$name" "$path" "$exists" "$readable" "$writable" "$executable" \\',
            '    >> "$BASH_RESULTS"',
            "}",
            "",
            ': > "$BASH_RESULTS"',
            "printf 'identity\\tslurm_job_id\\t%s\\n' \\",
            '  "${SLURM_JOB_ID:-}" >> "$BASH_RESULTS"',
            "printf 'identity\\tslurm_node_name\\t%s\\n' \\",
            '  "${SLURMD_NODENAME:-}" >> "$BASH_RESULTS"',
            "printf 'identity\\thostname\\t%s\\n' \\",
            '  "${HOSTNAME:-}" >> "$BASH_RESULTS"',
            *storage_calls,
            f'printf "completed\\n" > "$RUN_DIR/{bash_status_name}"',
            "",
            'PYTHON=""',
            'if [[ -x "$PREFERRED_PYTHON" ]]; then',
            '  PYTHON="$PREFERRED_PYTHON"',
            "elif command -v python3 >/dev/null 2>&1; then",
            '  PYTHON="$(command -v python3)"',
            "elif command -v python >/dev/null 2>&1; then",
            '  PYTHON="$(command -v python)"',
            "fi",
            "",
            'if [[ -z "$PYTHON" ]]; then',
            '  printf "unavailable\\n" > "$PYTHON_STATUS"',
            "else",
            '  printf "running\\t%s\\n" "$PYTHON" > "$PYTHON_STATUS"',
            f'  if "$PYTHON" "$COMPUTE_PROGRAM" --run-dir "$RUN_DIR" '
            f"--role {role}; then",
            '    printf "completed\\t%s\\n" "$PYTHON" > "$PYTHON_STATUS"',
            "  else",
            '    code="$?"',
            '    printf "failed\\t%s\\t%s\\n" "$PYTHON" "$code" > "$PYTHON_STATUS"',
            "  fi",
            "fi",
            *(
                ['printf "completed\\n" > "$RUN_DIR/peer-finished.txt"']
                if is_peer
                else []
            ),
            "",
        ]
    )


def submit_job(job_file: Path) -> str:
    """Submit the saved Bash job and return its Slurm job ID."""

    try:
        completed = subprocess.run(
            ["sbatch", "--parsable", str(job_file)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not run sbatch: {exc}") from exc
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"sbatch failed: {message}")
    job_id = completed.stdout.strip().split(";", 1)[0]
    if not job_id:
        raise RuntimeError("sbatch returned no job ID")
    return job_id


def cancel_jobs(job_ids: list[str]) -> None:
    """Best-effort cancellation after orchestration failure."""

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


def slurm_job_state(job_id: str) -> str:
    """Return the visible current or final state of one Slurm job."""

    try:
        queued = subprocess.run(
            ["squeue", "--noheader", "--jobs", job_id, "--format=%T"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"
    if queued.returncode != 0:
        return "UNKNOWN"
    states = queued.stdout.strip().splitlines()
    if states:
        return states[0].strip().upper()

    if shutil.which("sacct"):
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
        if accounted.returncode == 0:
            for line in accounted.stdout.splitlines():
                record_id, separator, raw_state = line.partition("|")
                if separator and record_id.strip() == job_id:
                    return raw_state.strip().split()[0].rstrip("+").upper()
    return "NOT_VISIBLE"


def read_optional_text(path: Path) -> str | None:
    """Return stripped text when an artifact exists."""

    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def log_tail(path: Path, line_count: int = 20) -> str:
    """Return a short Slurm log tail for error reporting."""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-line_count:])


def wait_for_artifact(
    *,
    path: Path,
    job_id: str,
    job_log: Path,
    timeout: float,
) -> None:
    """Wait for an artifact while reporting queue state and terminal failure."""

    started = time.monotonic()
    deadline = started + timeout
    last_report = 0.0
    while time.monotonic() < deadline:
        if path.exists():
            return
        state = slurm_job_state(job_id)
        now = time.monotonic()
        if last_report == 0 or now - last_report >= 10:
            print(f"[waiting] job {job_id}: {state} ({int(now - started)}s)", flush=True)
            last_report = now
        if state in TERMINAL_SLURM_STATES:
            grace_deadline = min(
                deadline,
                time.monotonic() + ARTIFACT_GRACE_SECONDS,
            )
            while time.monotonic() < grace_deadline:
                if path.exists():
                    return
                time.sleep(0.5)
            detail = log_tail(job_log)
            message = f"job {job_id} ended as {state} before producing {path.name}"
            if detail:
                message += f"\n--- job log ---\n{detail}"
            raise RuntimeError(message)
        time.sleep(2)
    raise TimeoutError(f"timed out waiting for {path.name}")


def wait_for_compute_or_runtime(
    *,
    run_dir: Path,
    job_id: str,
    job_log: Path,
    timeout: float,
    ready_name: str = "compute-ready.json",
    status_name: str = "python-status.txt",
    label: str = "compute Python",
) -> bool:
    """Return true when compute Python starts, or false when unavailable."""

    ready = run_dir / ready_name
    status_path = run_dir / status_name
    started = time.monotonic()
    deadline = started + timeout
    last_report = 0.0
    while time.monotonic() < deadline:
        if ready.exists():
            return True
        status = read_optional_text(status_path)
        if status and status.split("\t", 1)[0] in {"unavailable", "failed"}:
            return False
        state = slurm_job_state(job_id)
        now = time.monotonic()
        if last_report == 0 or now - last_report >= 10:
            print(
                f"[waiting] {label} in job {job_id}: {state} "
                f"({int(now - started)}s)",
                flush=True,
            )
            last_report = now
        if state in TERMINAL_SLURM_STATES:
            return ready.exists()
        time.sleep(2)
    raise TimeoutError(f"timed out waiting for {label}")


def accept_compute_connection(
    port: int,
    server: socket.socket,
    nonce: str,
    stop_event: threading.Event,
    listener_timeout: float,
) -> dict[str, Any]:
    """Wait for the authenticated compute connection on one reserved port."""

    attempt: dict[str, Any] = {"port": port, "status": "failed", "error": None}
    try:
        server.settimeout(1)
        while not stop_event.is_set():
            try:
                connection, address = server.accept()
            except TimeoutError:
                continue
            try:
                with connection:
                    connection.settimeout(listener_timeout)
                    received = connection.recv(256).decode(
                        "utf-8",
                        errors="replace",
                    )
                    if received != nonce:
                        attempt["error"] = "nonce mismatch"
                        continue
                    connection.sendall(f"ACK:{nonce}".encode())
                    attempt["status"] = "passed"
                    attempt["error"] = None
                    attempt["peer"] = address[0]
                    break
            except (OSError, TimeoutError) as exc:
                attempt["error"] = f"{type(exc).__name__}: {exc}"
                continue
    except (OSError, TimeoutError) as exc:
        attempt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        server.close()
    if attempt["status"] != "passed" and attempt["error"] is None:
        attempt["error"] = "no authenticated connection before the test ended"
    return attempt


def run_login_manager(
    *,
    listeners: dict[int, tuple[socket.socket, str]],
    stop_event: threading.Event,
    listener_timeout: float,
) -> dict[str, Any]:
    """Authenticate connections on ports reserved before job submission."""

    ports = list(listeners)
    attempts_by_port: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(listeners))) as executor:
        futures = {
            executor.submit(
                accept_compute_connection,
                port,
                server,
                nonce,
                stop_event,
                listener_timeout,
            ): port
            for port, (server, nonce) in listeners.items()
        }
        for future in as_completed(futures):
            attempt = future.result()
            attempts_by_port[attempt["port"]] = attempt
            detail = f" ({attempt['error']})" if attempt["error"] else ""
            print(
                f"[testing] compute-to-login port {attempt['port']}: "
                f"{attempt['status']}{detail}",
                flush=True,
            )

    attempts = [attempts_by_port[port] for port in ports]
    return {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "attempts": attempts,
        "successful": any(item["status"] == "passed" for item in attempts),
    }


def parse_bash_results(path: Path) -> dict[str, Any]:
    """Convert the simple Bash TSV artifact into structured output."""

    identity: dict[str, str] = {}
    storage: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[0] == "identity":
            identity[fields[1]] = fields[2]
        elif len(fields) == 7 and fields[0] == "storage":
            storage.append(
                {
                    "name": fields[1],
                    "path": fields[2],
                    "exists": fields[3] == "true",
                    "readable": fields[4] == "true",
                    "writable": fields[5] == "true",
                    "executable": fields[6] == "true",
                }
            )
    return {"identity": identity, "storage": storage}


def reconciled_network(
    manager: dict[str, Any] | None,
    worker: dict[str, Any] | None,
    *,
    distinct_nodes: bool | None = None,
    require_distinct: bool = False,
) -> dict[str, Any]:
    """Return ports passed by both sides of one nonce handshake."""

    manager_ports = {
        item["port"]
        for item in (manager or {}).get("attempts", [])
        if item["status"] == "passed"
    }
    worker_ports = {
        item["port"]
        for item in (worker or {}).get("attempts", [])
        if item["status"] == "passed"
    }
    verified_ports = sorted(manager_ports & worker_ports)
    result: dict[str, Any] = {
        "manager": manager,
        "worker": worker,
        "verified_ports": verified_ports,
        "successful": bool(verified_ports),
    }
    if require_distinct:
        result["distinct_nodes"] = distinct_nodes
        result["successful"] = bool(verified_ports) and distinct_nodes is True
    return result


def nodes_are_distinct(
    manager: dict[str, Any] | None,
    worker: dict[str, Any] | None,
) -> bool | None:
    """Compare scheduler node names, falling back to operating-system names."""

    if not manager or not worker:
        return None
    for key in ("slurm_node_name", "hostname", "fqdn"):
        left = manager.get(key)
        right = worker.get(key)
        if left and right:
            return left != right
    return None


def flat_pilot_result(
    detailed: dict[str, Any],
    *,
    source_reference: str,
) -> dict[str, Any]:
    """Convert the inspectable run record to the flat pipeline contract."""

    network = detailed.get("network", {})
    login_compute = network.get("login_compute", {})
    compute_compute = network.get("compute_compute", {})
    storage = (detailed.get("bash", {}).get("primary") or {}).get("storage", [])
    storage_results = [
        {
            "name": item["name"],
            "path": item["path"],
            "visible": item.get("exists"),
            "readable": item.get("readable"),
            "writable": item.get("writable"),
        }
        for item in storage
        if isinstance(item, dict) and "name" in item and "path" in item
    ]
    login_attempts = _flat_network_attempts(login_compute)
    compute_attempts = _flat_network_attempts(compute_compute)
    primary = compute_compute.get("manager") or login_compute.get("worker")
    peer = compute_compute.get("worker")
    distinct = compute_compute.get("distinct_nodes")
    errors: list[str] = []
    if login_attempts is None:
        errors.append("The login/compute network test did not complete.")
    if compute_attempts is None:
        errors.append("The compute/compute network test did not complete.")
    if distinct is not True:
        errors.append("The pilot did not verify two distinct compute nodes.")
    status = "completed" if not errors else "partial"
    result: dict[str, Any] = {
        "schema_version": "0.1",
        "site_id": detailed["site_id"],
        "scheduler": "slurm",
        "evidence_source": "measured",
        "pilot_id": detailed["run_id"],
        "collected_at": detailed["collected_at"],
        "status": status,
        "storage_results": storage_results,
        "primary_node": _result_node_name(primary),
        "peer_node": _result_node_name(peer),
        "distinct_nodes": distinct,
        "errors": errors,
        "source_reference": source_reference,
    }
    if login_attempts is not None:
        result["login_compute_attempts"] = login_attempts
    if compute_attempts is not None:
        result["compute_compute_attempts"] = compute_attempts
    return result


def _flat_network_attempts(
    network: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Require both endpoints to report each completed nonce handshake."""

    manager = network.get("manager")
    worker = network.get("worker")
    if not isinstance(manager, dict) or not isinstance(worker, dict):
        return None
    manager_by_port = {
        item.get("port"): item
        for item in manager.get("attempts", [])
        if isinstance(item, dict) and isinstance(item.get("port"), int)
    }
    worker_by_port = {
        item.get("port"): item
        for item in worker.get("attempts", [])
        if isinstance(item, dict) and isinstance(item.get("port"), int)
    }
    ports = sorted(set(manager_by_port) | set(worker_by_port))
    if not ports:
        return None
    attempts: list[dict[str, Any]] = []
    for port in ports:
        manager_attempt = manager_by_port.get(port, {})
        worker_attempt = worker_by_port.get(port, {})
        passed = (
            manager_attempt.get("status") == "passed"
            and worker_attempt.get("status") == "passed"
        )
        errors = [
            error
            for error in (
                manager_attempt.get("error"),
                worker_attempt.get("error"),
            )
            if isinstance(error, str) and error
        ]
        attempts.append(
            {
                "port": port,
                "status": "passed" if passed else "failed",
                "error": "; ".join(errors) or None,
            }
        )
    return attempts


def _result_node_name(value: Any) -> str | None:
    """Return the scheduler node name or operating-system hostname."""

    if not isinstance(value, dict):
        return None
    for key in ("slurm_node_name", "hostname", "fqdn"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def orchestrate(args: argparse.Namespace) -> int:
    """Generate and run inspectable login, primary, and peer network checks."""

    if not args.dry_run and shutil.which("sbatch") is None:
        raise RuntimeError("sbatch is not available; run this on a Slurm login node")

    storage = parse_storage(args.storage)
    partition = validate_slurm_value("slurm-partition", args.slurm_partition)
    account = validate_slurm_value("slurm-account", args.slurm_account)
    walltime = validate_slurm_value("walltime", args.walltime)
    if walltime is None:
        raise ValueError("--walltime is required")

    run_id = uuid.uuid4().hex
    run_dir = args.runs_dir.expanduser().resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_dir.chmod(0o700)
    listeners: dict[int, tuple[socket.socket, str]] = {}
    if args.dry_run:
        ports = select_ports(args.start_port, args.port_count)
        port_checks = [
            {"port": port, "nonce": secrets.token_hex(16)} for port in ports
        ]
        reservation_failures: list[dict[str, Any]] = []
    else:
        port_checks, listeners, reservation_failures = reserve_ports(
            args.start_port,
            args.port_count,
        )
        ports = [check["port"] for check in port_checks]

    config = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "site_id": args.site_id,
        "scheduler": "slurm",
        "created_at": utc_now(),
        "login_host": args.login_host or socket.getfqdn(),
        "start_port": args.start_port,
        "port_count": args.port_count,
        "ports": ports,
        "port_checks": port_checks,
        "reservation_failures": reservation_failures,
        "connect_timeout": args.connect_timeout,
        "listener_timeout": args.listener_timeout,
        "coordination_timeout": args.coordination_timeout,
        "slurm": {
            "partition": partition,
            "account": account,
            "walltime": walltime,
        },
        "storage": storage,
        "jobs": {},
    }
    config_path = run_dir / "pilot-config.json"
    compute_path = run_dir / "compute-pilot.py"
    primary_job_path = run_dir / "compute-job.sh"
    peer_job_path = run_dir / "peer-job.sh"
    preferred_python = Path(sys.executable).resolve()
    atomic_write_json(config_path, config)
    config_path.chmod(0o600)
    atomic_write_text(compute_path, COMPUTE_PROGRAM)
    compute_path.chmod(0o700)
    primary_job = bash_job(
        run_dir=run_dir,
        compute_program=compute_path,
        preferred_python=preferred_python,
        storage=storage,
        partition=partition,
        account=account,
        walltime=walltime,
        role="primary",
    )
    atomic_write_text(primary_job_path, primary_job)
    primary_job_path.chmod(0o700)

    print(f"Run directory: {run_dir}")
    port_label = "Planned" if args.dry_run else "Reserved"
    print(f"{port_label} login ports: {', '.join(str(port) for port in ports)}")
    for failure in reservation_failures:
        print(
            f"Skipped unavailable port {failure['port']}: {failure['error']}",
            flush=True,
        )
    print(f"Saved compute program: {compute_path}")
    print(f"Saved primary Bash job: {primary_job_path}")
    if args.dry_run:
        peer_job = bash_job(
            run_dir=run_dir,
            compute_program=compute_path,
            preferred_python=preferred_python,
            storage={},
            partition=partition,
            account=account,
            walltime=walltime,
            role="peer",
            exclude="<primary-slurm-node>",
        )
        atomic_write_text(peer_job_path, peer_job)
        peer_job_path.chmod(0o700)
        print(f"Saved peer Bash job: {peer_job_path}")
        print("\nPRIMARY JOB\n" + primary_job, end="")
        print("\nPEER JOB\n" + peer_job, end="")
        return 0

    job_ids: dict[str, str] = {}
    job_logs: dict[str, Path] = {}
    login_stop = threading.Event()
    login_executor: ThreadPoolExecutor | None = None
    login_future: Any | None = None
    login_network = None
    primary_bash = None
    peer_bash = None
    primary_results = None
    manager_description = None
    manager_results = None
    peer_results = None
    try:
        print("[submitting] primary compute pilot", flush=True)
        primary_id = submit_job(primary_job_path)
        job_ids["primary"] = primary_id
        job_logs["primary"] = run_dir / f"slurm-primary-{primary_id}.log"
        config["jobs"]["primary"] = primary_id
        atomic_write_json(config_path, config)
        config_path.chmod(0o600)
        print(f"Submitted primary compute pilot: {primary_id}", flush=True)

        login_executor = ThreadPoolExecutor(max_workers=1)
        login_future = login_executor.submit(
            run_login_manager,
            listeners=listeners,
            stop_event=login_stop,
            listener_timeout=args.listener_timeout,
        )
        print("[listening] reserved login ports are ready", flush=True)

        wait_for_artifact(
            path=run_dir / "bash-status.txt",
            job_id=primary_id,
            job_log=job_logs["primary"],
            timeout=args.coordination_timeout,
        )
        primary_bash = parse_bash_results(run_dir / "bash-results.tsv")
        print("[completed] primary Bash identity and storage checks", flush=True)

        primary_started = wait_for_compute_or_runtime(
            run_dir=run_dir,
            job_id=primary_id,
            job_log=job_logs["primary"],
            timeout=args.coordination_timeout,
        )
        if not primary_started:
            print("[skipped] primary compute Python unavailable", flush=True)
        else:
            wait_for_artifact(
                path=run_dir / "compute-results.json",
                job_id=primary_id,
                job_log=job_logs["primary"],
                timeout=args.coordination_timeout,
            )
            primary_results = read_json(run_dir / "compute-results.json")

            login_stop.set()
            login_network = login_future.result(
                timeout=args.listener_timeout + 5
            )
            atomic_write_json(run_dir / "login-results.json", login_network)
            login_future = None
            login_executor.shutdown(wait=True)
            login_executor = None

            wait_for_artifact(
                path=run_dir / "compute-manager.json",
                job_id=primary_id,
                job_log=job_logs["primary"],
                timeout=args.coordination_timeout,
            )
            manager_description = read_json(run_dir / "compute-manager.json")
            print(
                f"[ready] compute manager {manager_description['host']} "
                f"reserved {len(manager_description['port_checks'])} ports",
                flush=True,
            )
            node_name = validate_slurm_value(
                "exclude-node",
                manager_description.get("slurm_node_name"),
            )
            if node_name is None:
                raise RuntimeError(
                    "primary job did not report SLURMD_NODENAME; "
                    "cannot guarantee a distinct peer node"
                )

            peer_job = bash_job(
                run_dir=run_dir,
                compute_program=compute_path,
                preferred_python=preferred_python,
                storage={},
                partition=partition,
                account=account,
                walltime=walltime,
                role="peer",
                exclude=node_name,
            )
            atomic_write_text(peer_job_path, peer_job)
            peer_job_path.chmod(0o700)
            print(f"Saved peer Bash job: {peer_job_path}", flush=True)
            print(f"[submitting] peer compute pilot excluding {node_name}", flush=True)
            peer_id = submit_job(peer_job_path)
            job_ids["peer"] = peer_id
            job_logs["peer"] = run_dir / f"slurm-peer-{peer_id}.log"
            config["jobs"]["peer"] = peer_id
            atomic_write_json(config_path, config)
            config_path.chmod(0o600)
            print(f"Submitted peer compute pilot: {peer_id}", flush=True)

            wait_for_artifact(
                path=run_dir / "peer-bash-status.txt",
                job_id=peer_id,
                job_log=job_logs["peer"],
                timeout=args.coordination_timeout,
            )
            peer_bash = parse_bash_results(run_dir / "peer-bash-results.tsv")
            print("[completed] peer Bash identity check", flush=True)

            peer_started = wait_for_compute_or_runtime(
                run_dir=run_dir,
                job_id=peer_id,
                job_log=job_logs["peer"],
                timeout=args.coordination_timeout,
                ready_name="peer-ready.json",
                status_name="peer-python-status.txt",
                label="peer compute Python",
            )
            if peer_started:
                wait_for_artifact(
                    path=run_dir / "peer-results.json",
                    job_id=peer_id,
                    job_log=job_logs["peer"],
                    timeout=args.coordination_timeout,
                )
                peer_results = read_json(run_dir / "peer-results.json")
                print("[completed] peer compute connection attempts", flush=True)
            else:
                print("[skipped] peer compute Python unavailable", flush=True)

            wait_for_artifact(
                path=run_dir / "compute-manager-results.json",
                job_id=primary_id,
                job_log=job_logs["primary"],
                timeout=args.coordination_timeout,
            )
            manager_results = read_json(
                run_dir / "compute-manager-results.json"
            )
            verified = reconciled_network(manager_results, peer_results)[
                "verified_ports"
            ]
            print(
                f"[completed] compute/compute handshake verified "
                f"{len(verified)} ports",
                flush=True,
            )
    except Exception:
        cancel_jobs(list(job_ids.values()))
        raise
    finally:
        atomic_write_text(run_dir / "peer-finished.txt", "completed\n")
        login_stop.set()
        if login_future is not None:
            login_network = login_future.result(
                timeout=args.listener_timeout + 5
            )
            atomic_write_json(run_dir / "login-results.json", login_network)
        elif login_executor is None and not login_network:
            for server, _nonce in listeners.values():
                server.close()
        if login_executor is not None:
            login_executor.shutdown(wait=True)

    distinct_nodes = nodes_are_distinct(manager_description, peer_results)
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "site_id": args.site_id,
        "scheduler": "slurm",
        "evidence_source": "pilot",
        "collected_at": utc_now(),
        "jobs": {
            name: {
                "job_id": job_id,
                "log": str(job_logs[name]),
            }
            for name, job_id in job_ids.items()
        },
        "configuration": config,
        "bash": {
            "primary": primary_bash,
            "peer": peer_bash,
        },
        "python": {
            "primary_status": read_optional_text(
                run_dir / "python-status.txt"
            ),
            "peer_status": read_optional_text(
                run_dir / "peer-python-status.txt"
            ),
        },
        "network": {
            "login_compute": reconciled_network(
                login_network,
                primary_results,
            ),
            "compute_compute": reconciled_network(
                manager_results,
                peer_results,
                distinct_nodes=distinct_nodes,
                require_distinct=True,
            ),
        },
        "artifacts": {
            "run_directory": str(run_dir),
            "primary_job": str(primary_job_path),
            "peer_job": str(peer_job_path),
            "compute_program": str(compute_path),
            "login_results": str(run_dir / "login-results.json"),
            "compute_manager": str(run_dir / "compute-manager.json"),
            "compute_manager_results": str(
                run_dir / "compute-manager-results.json"
            ),
            "peer_results": str(run_dir / "peer-results.json"),
        },
    }
    details_path = run_dir / "pilot-run-details.json"
    atomic_write_json(details_path, result)
    result_path = run_dir / "pilot-result.json"
    flat_result = flat_pilot_result(
        result,
        source_reference=str(details_path),
    )
    atomic_write_json(result_path, flat_result)
    if args.output is not None:
        atomic_write_json(args.output.expanduser().resolve(), flat_result)
    print(f"Pilot result: {result_path}")
    return 0


def even_port_count(value: str) -> int:
    """Parse the bounded even port-count CLI value."""

    count = int(value)
    if count < 2 or count > MAX_PORT_COUNT or count % 2:
        raise argparse.ArgumentTypeError(
            f"must be an even number from 2 to {MAX_PORT_COUNT}"
        )
    return count


def positive_float(value: str) -> float:
    """Parse one positive timeout."""

    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse only the login-measurement values relevant to this pilot."""

    parser = argparse.ArgumentParser(
        description="Generate and run one inspectable Anvil Slurm pilot."
    )
    parser.add_argument("--site-id", default="anvil")
    parser.add_argument("--slurm-partition")
    parser.add_argument("--slurm-account")
    parser.add_argument(
        "--storage",
        action="append",
        default=[],
        metavar="NAME=/PATH",
        help="Observed storage path from login measurements; may be repeated.",
    )
    parser.add_argument(
        "--login-host",
        help="Login hostname reachable from compute; defaults to hostname -f.",
    )
    parser.add_argument("--walltime", default="00:10:00")
    parser.add_argument("--start-port", type=int, default=DEFAULT_START_PORT)
    parser.add_argument(
        "--port-count",
        type=even_port_count,
        default=DEFAULT_PORT_COUNT,
    )
    parser.add_argument("--connect-timeout", type=positive_float, default=10.0)
    parser.add_argument("--listener-timeout", type=positive_float, default=30.0)
    parser.add_argument(
        "--coordination-timeout",
        type=positive_float,
        default=3600.0,
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("pilot-runs"))
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional copy of the flat result for profile-build replay.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Run the login-side pilot orchestrator."""

    try:
        return orchestrate(parse_args(arguments))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
