#!/usr/bin/env python3
"""Create and run an inspectable two-job HTCondor pilot."""

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

SCHEMA_VERSION = "0.1"
DEFAULT_START_PORT = 9000
DEFAULT_PORT_COUNT = 8
MAX_PORT_COUNT = 32
ARTIFACT_GRACE_SECONDS = 60
TERMINAL_CONDOR_STATES = {"REMOVED", "COMPLETED", "HELD"}
CONDOR_STATES = {
    "1": "IDLE",
    "2": "RUNNING",
    "3": "REMOVED",
    "4": "COMPLETED",
    "5": "HELD",
    "6": "TRANSFERRING_OUTPUT",
    "7": "SUSPENDED",
}

# This standard-library-only program is copied into every run directory.
COMPUTE_PROGRAM = r'''#!/usr/bin/env python3
"""Run one compute-node role for the HTCondor pilot."""

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


def identity():
    return {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "condor_job_id": os.environ.get("CONDOR_JOB_ID"),
        "condor_scratch_dir": os.environ.get("_CONDOR_SCRATCH_DIR"),
    }


def check_port(check, host, timeout):
    port = check["port"]
    attempt = {"port": port, "status": "failed", "error": None}
    try:
        nonce = check["nonce"]
        connection = socket.create_connection((host, port), timeout=timeout)
        with connection:
            connection.settimeout(timeout)
            connection.sendall(nonce.encode("utf-8"))
            response = connection.recv(256).decode("utf-8", errors="replace")
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
            executor.map(lambda check: check_port(check, host, timeout), checks)
        )


def reserve_ports(start_port, count):
    checks = []
    listeners = {}
    unavailable = []
    random_source = secrets.SystemRandom()
    try:
        for window in range(count // 2):
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
            with connection:
                connection.settimeout(timeout)
                received = connection.recv(256).decode(
                    "utf-8", errors="replace"
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
    finally:
        server.close()
    if attempt["status"] != "passed" and attempt["error"] is None:
        attempt["error"] = "no authenticated connection before the peer ended"
    return attempt


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
        config["start_port"], config["port_count"]
    )
    write_json(
        run_dir / "compute-manager.json",
        {
            **node,
            "host": node["fqdn"] or node["hostname"],
            "port_checks": checks,
            "reservation_failures": unavailable,
        },
    )

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
    """Return one UTC timestamp for artifacts."""

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


def read_optional_text(path: Path) -> str | None:
    """Read an optional short text artifact."""

    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


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


def select_ports(
    start_port: int,
    count: int,
    *,
    random_source: Any | None = None,
) -> list[int]:
    """Select two ports from each consecutive 1,000-port window."""

    validate_port_range(start_port, count)
    random_source = random_source or secrets.SystemRandom()
    ports = [start_port, random_source.randrange(start_port + 1, start_port + 1000)]
    for window in range(1, count // 2):
        low = start_port + window * 1000
        first = random_source.randrange(low, low + 1000)
        second = random_source.randrange(low, low + 1000)
        while second == first:
            second = random_source.randrange(low, low + 1000)
        ports.extend([first, second])
    return ports


def validate_port_range(start_port: int, count: int) -> None:
    """Reject an unsafe count or a range outside normal TCP ports."""

    if count < 2 or count > MAX_PORT_COUNT or count % 2:
        raise ValueError(f"port count must be even and between 2 and {MAX_PORT_COUNT}")
    if start_port < 1024 or start_port + (count // 2) * 1000 - 1 > 65535:
        raise ValueError("pilot ports must remain between 1024 and 65535")


def reserve_ports(
    start_port: int,
    count: int,
) -> tuple[
    list[dict[str, Any]],
    dict[int, tuple[socket.socket, str]],
    list[dict[str, Any]],
]:
    """Bind bounded login ports before either job is submitted."""

    validate_port_range(start_port, count)
    checks: list[dict[str, Any]] = []
    listeners: dict[int, tuple[socket.socket, str]] = {}
    unavailable: list[dict[str, Any]] = []
    random_source = secrets.SystemRandom()
    try:
        for window in range(count // 2):
            low = start_port + window * 1000
            reserved = 0
            tried: set[int] = set()
            for attempt_index in range(20):
                port = (
                    start_port
                    if window == 0 and attempt_index == 0
                    else random_source.randrange(low, low + 1000)
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
                        {"port": port, "error": f"{type(exc).__name__}: {exc}"}
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
                    f"could not reserve two ports in {low}-{low + 999}"
                )
    except Exception:
        for server, _nonce in listeners.values():
            server.close()
        raise
    return checks, listeners, unavailable


def validate_machine(value: str) -> str:
    """Validate one hostname before placing it in a ClassAd expression."""

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError("primary machine contains unsupported characters")
    return value


def bash_job(
    *,
    run_dir: Path,
    compute_program: Path,
    preferred_python: Path,
    storage: dict[str, str],
    role: str,
) -> str:
    """Create the Bash-first executable for one HTCondor job."""

    if role not in {"primary", "peer"}:
        raise ValueError("job role must be primary or peer")
    peer = role == "peer"
    results_name = "peer-bash-results.tsv" if peer else "bash-results.tsv"
    status_name = "peer-bash-status.txt" if peer else "bash-status.txt"
    python_status = "peer-python-status.txt" if peer else "python-status.txt"
    storage_calls = [
        f"check_storage {shlex.quote(name)} {shlex.quote(path)}"
        for name, path in storage.items()
    ]
    lines = [
        "#!/bin/bash",
        "set -u",
        f"RUN_DIR={shlex.quote(str(run_dir))}",
        f"COMPUTE_PROGRAM={shlex.quote(str(compute_program))}",
        f"PREFERRED_PYTHON={shlex.quote(str(preferred_python))}",
        f'BASH_RESULTS="$RUN_DIR/{results_name}"',
        f'PYTHON_STATUS="$RUN_DIR/{python_status}"',
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
        "printf 'identity\\tcondor_job_id\\t%s\\n' \\",
        '  "${CONDOR_JOB_ID:-}" >> "$BASH_RESULTS"',
        "printf 'identity\\thostname\\t%s\\n' \\",
        '  "${HOSTNAME:-}" >> "$BASH_RESULTS"',
        *storage_calls,
        f'printf "completed\\n" > "$RUN_DIR/{status_name}"',
        "",
        'PYTHON=""',
        'if [[ -x "$PREFERRED_PYTHON" ]]; then',
        '  PYTHON="$PREFERRED_PYTHON"',
        "elif command -v python3 >/dev/null 2>&1; then",
        '  PYTHON="$(command -v python3)"',
        "elif command -v python >/dev/null 2>&1; then",
        '  PYTHON="$(command -v python)"',
        "fi",
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
    ]
    if peer:
        lines.append('printf "completed\\n" > "$RUN_DIR/peer-finished.txt"')
    return "\n".join(lines) + "\n"


def submit_description(
    *,
    run_dir: Path,
    executable: Path,
    role: str,
    request_cpus: int,
    request_memory: str,
    request_disk: str,
    exclude_machine: str | None = None,
) -> str:
    """Create one reviewed HTCondor submit description."""

    if role not in {"primary", "peer"}:
        raise ValueError("job role must be primary or peer")
    lines = [
        "universe = vanilla",
        f"executable = {executable}",
        f"initialdir = {run_dir}",
        f"output = {role}.out",
        f"error = {role}.err",
        f"log = {role}.condor.log",
        "should_transfer_files = NO",
        f"request_cpus = {request_cpus}",
        f"request_memory = {request_memory}",
        f"request_disk = {request_disk}",
    ]
    if exclude_machine:
        machine = validate_machine(exclude_machine)
        lines.append(f'requirements = (Machine =!= "{machine}")')
    lines.extend(
        [
            'environment = "CONDOR_JOB_ID=$(Cluster).$(Process)"',
            "queue 1",
        ]
    )
    return "\n".join(lines) + "\n"


def submit_job(submit_file: Path) -> str:
    """Submit one saved description and return its cluster.process ID."""

    try:
        completed = subprocess.run(
            ["condor_submit", "-terse", str(submit_file)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not run condor_submit: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"condor_submit failed: {detail}")
    match = re.search(r"\b(\d+\.\d+)\b", completed.stdout)
    if not match:
        raise RuntimeError("condor_submit returned no cluster.process ID")
    return match.group(1)


def cancel_jobs(job_ids: list[str]) -> None:
    """Best-effort removal after orchestration failure."""

    for job_id in job_ids:
        try:
            subprocess.run(
                ["condor_rm", job_id],
                check=False,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def condor_job_state(job_id: str) -> str:
    """Return the visible queue or history state for one job."""

    commands = [
        ["condor_q", job_id, "-af", "JobStatus"],
        ["condor_history", job_id, "-limit", "1", "-af", "JobStatus"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        values = completed.stdout.strip().splitlines()
        if completed.returncode == 0 and values:
            return CONDOR_STATES.get(values[0].strip(), "UNKNOWN")
    return "NOT_VISIBLE"


def log_tail(path: Path, line_count: int = 20) -> str:
    """Return a short job error tail."""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-line_count:])


def wait_for_artifact(
    *,
    path: Path,
    job_id: str,
    error_log: Path,
    timeout: float,
) -> None:
    """Wait for a shared artifact while reporting HTCondor state."""

    started = time.monotonic()
    deadline = started + timeout
    last_report = 0.0
    while time.monotonic() < deadline:
        if path.exists():
            return
        state = condor_job_state(job_id)
        now = time.monotonic()
        if last_report == 0 or now - last_report >= 10:
            print(f"[waiting] job {job_id}: {state} ({int(now - started)}s)", flush=True)
            last_report = now
        if state in TERMINAL_CONDOR_STATES:
            grace_deadline = min(deadline, time.monotonic() + ARTIFACT_GRACE_SECONDS)
            while time.monotonic() < grace_deadline:
                if path.exists():
                    return
                time.sleep(0.5)
            detail = log_tail(error_log)
            message = f"job {job_id} ended as {state} before producing {path.name}"
            if detail:
                message += f"\n--- job error ---\n{detail}"
            raise RuntimeError(message)
        time.sleep(2)
    raise TimeoutError(f"timed out waiting for {path.name}")


def accept_connection(
    port: int,
    server: socket.socket,
    nonce: str,
    stop_event: threading.Event,
    timeout: float,
) -> dict[str, Any]:
    """Authenticate one compute connection to a reserved login port."""

    attempt: dict[str, Any] = {"port": port, "status": "failed", "error": None}
    try:
        server.settimeout(1)
        while not stop_event.is_set():
            try:
                connection, address = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(timeout)
                received = connection.recv(256).decode("utf-8", errors="replace")
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
    finally:
        server.close()
    if attempt["status"] != "passed" and attempt["error"] is None:
        attempt["error"] = "no authenticated connection before the test ended"
    return attempt


def run_login_manager(
    *,
    listeners: dict[int, tuple[socket.socket, str]],
    stop_event: threading.Event,
    timeout: float,
) -> dict[str, Any]:
    """Accept compute connections on all reserved ports concurrently."""

    ordered_ports = list(listeners)
    attempts_by_port: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(listeners))) as executor:
        futures = {
            executor.submit(
                accept_connection, port, server, nonce, stop_event, timeout
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
    attempts = [attempts_by_port[port] for port in ordered_ports]
    return {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "attempts": attempts,
        "successful": any(item["status"] == "passed" for item in attempts),
    }


def parse_bash_results(path: Path) -> dict[str, Any]:
    """Convert one Bash TSV artifact to structured evidence."""

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
    require_distinct: bool = False,
) -> dict[str, Any]:
    """Keep only authenticated ports reported as passed by both endpoints."""

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
    verified = sorted(manager_ports & worker_ports)
    result: dict[str, Any] = {
        "manager": manager,
        "worker": worker,
        "verified_ports": verified,
        "successful": bool(verified),
    }
    if require_distinct:
        distinct = nodes_are_distinct(manager, worker)
        result["distinct_nodes"] = distinct
        result["successful"] = bool(verified) and distinct is True
    return result


def nodes_are_distinct(
    manager: dict[str, Any] | None,
    worker: dict[str, Any] | None,
) -> bool | None:
    """Compare FQDNs first and short hostnames second."""

    if not manager or not worker:
        return None
    for key in ("fqdn", "hostname"):
        left = manager.get(key)
        right = worker.get(key)
        if left and right:
            return left != right
    return None


def wait_for_python_start(
    run_dir: Path,
    job_id: str,
    error_log: Path,
    timeout: float,
    *,
    peer: bool = False,
) -> bool:
    """Return whether Python started, without discarding Bash-only evidence."""

    ready = run_dir / ("peer-ready.json" if peer else "compute-ready.json")
    status = run_dir / (
        "peer-python-status.txt" if peer else "python-status.txt"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready.exists():
            return True
        value = read_optional_text(status)
        if value and value.split("\t", 1)[0] in {"unavailable", "failed"}:
            return False
        if condor_job_state(job_id) in TERMINAL_CONDOR_STATES:
            return ready.exists()
        time.sleep(2)
    raise TimeoutError("timed out waiting for compute Python")


def orchestrate(args: argparse.Namespace) -> int:
    """Generate, submit, observe, and reconcile the two HTCondor jobs."""

    if not args.dry_run and shutil.which("condor_submit") is None:
        raise RuntimeError(
            "condor_submit is unavailable; run this on an HTCondor submit node"
        )
    storage = parse_storage(args.storage)
    run_id = uuid.uuid4().hex
    run_dir = args.runs_dir.expanduser().resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_dir.chmod(0o700)

    listeners: dict[int, tuple[socket.socket, str]] = {}
    if args.dry_run:
        ports = select_ports(args.start_port, args.port_count)
        checks = [
            {"port": port, "nonce": secrets.token_hex(16)} for port in ports
        ]
        unavailable: list[dict[str, Any]] = []
    else:
        checks, listeners, unavailable = reserve_ports(
            args.start_port, args.port_count
        )
        ports = [item["port"] for item in checks]

    config = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "site_id": args.site_id,
        "scheduler": "htcondor",
        "created_at": utc_now(),
        "login_host": args.login_host or socket.getfqdn(),
        "start_port": args.start_port,
        "port_count": args.port_count,
        "port_checks": checks,
        "reservation_failures": unavailable,
        "connect_timeout": args.connect_timeout,
        "listener_timeout": args.listener_timeout,
        "coordination_timeout": args.coordination_timeout,
        "storage": storage,
        "htcondor": {
            "request_cpus": args.request_cpus,
            "request_memory": args.request_memory,
            "request_disk": args.request_disk,
            "shared_filesystem_required": True,
        },
        "jobs": {},
    }
    config_path = run_dir / "pilot-config.json"
    compute_path = run_dir / "compute-pilot.py"
    primary_job_path = run_dir / "primary-job.sh"
    peer_job_path = run_dir / "peer-job.sh"
    primary_submit_path = run_dir / "primary.submit"
    peer_submit_path = run_dir / "peer.submit"
    preferred_python = Path(sys.executable).resolve()
    atomic_write_json(config_path, config)
    atomic_write_text(compute_path, COMPUTE_PROGRAM)
    compute_path.chmod(0o700)
    primary_job = bash_job(
        run_dir=run_dir,
        compute_program=compute_path,
        preferred_python=preferred_python,
        storage=storage,
        role="primary",
    )
    atomic_write_text(primary_job_path, primary_job)
    primary_job_path.chmod(0o700)
    primary_submit = submit_description(
        run_dir=run_dir,
        executable=primary_job_path,
        role="primary",
        request_cpus=args.request_cpus,
        request_memory=args.request_memory,
        request_disk=args.request_disk,
    )
    atomic_write_text(primary_submit_path, primary_submit)

    print(f"Run directory: {run_dir}")
    label = "Planned" if args.dry_run else "Reserved"
    print(f"{label} login ports: {', '.join(str(port) for port in ports)}")
    print(f"Saved compute program: {compute_path}")
    print(f"Saved primary Bash job: {primary_job_path}")
    print(f"Saved primary submit file: {primary_submit_path}")

    if args.dry_run:
        peer_job = bash_job(
            run_dir=run_dir,
            compute_program=compute_path,
            preferred_python=preferred_python,
            storage={},
            role="peer",
        )
        atomic_write_text(peer_job_path, peer_job)
        peer_job_path.chmod(0o700)
        peer_submit = submit_description(
            run_dir=run_dir,
            executable=peer_job_path,
            role="peer",
            request_cpus=args.request_cpus,
            request_memory=args.request_memory,
            request_disk=args.request_disk,
            exclude_machine="primary.example.edu",
        )
        atomic_write_text(peer_submit_path, peer_submit)
        print(f"Saved peer Bash job: {peer_job_path}")
        print(f"Saved peer submit file: {peer_submit_path}")
        return 0

    job_ids: dict[str, str] = {}
    login_stop = threading.Event()
    login_executor: ThreadPoolExecutor | None = None
    login_future: Any | None = None
    login_result = primary_result = manager_result = peer_result = None
    primary_bash = peer_bash = None
    try:
        print("[submitting] primary HTCondor pilot", flush=True)
        primary_id = submit_job(primary_submit_path)
        job_ids["primary"] = primary_id
        config["jobs"]["primary"] = primary_id
        atomic_write_json(config_path, config)
        print(f"Submitted primary pilot: {primary_id}", flush=True)

        login_executor = ThreadPoolExecutor(max_workers=1)
        login_future = login_executor.submit(
            run_login_manager,
            listeners=listeners,
            stop_event=login_stop,
            timeout=args.listener_timeout,
        )
        print("[listening] reserved login ports are ready", flush=True)

        wait_for_artifact(
            path=run_dir / "bash-status.txt",
            job_id=primary_id,
            error_log=run_dir / "primary.err",
            timeout=args.coordination_timeout,
        )
        primary_bash = parse_bash_results(run_dir / "bash-results.tsv")
        print("[completed] primary Bash identity and storage checks", flush=True)

        if not wait_for_python_start(
            run_dir,
            primary_id,
            run_dir / "primary.err",
            args.coordination_timeout,
        ):
            print("[skipped] primary compute Python unavailable", flush=True)
        else:
            wait_for_artifact(
                path=run_dir / "compute-results.json",
                job_id=primary_id,
                error_log=run_dir / "primary.err",
                timeout=args.coordination_timeout,
            )
            primary_result = read_json(run_dir / "compute-results.json")
            login_stop.set()
            login_result = login_future.result(timeout=args.listener_timeout + 5)
            login_future = None
            login_executor.shutdown(wait=True)
            login_executor = None
            atomic_write_json(run_dir / "login-results.json", login_result)

            wait_for_artifact(
                path=run_dir / "compute-manager.json",
                job_id=primary_id,
                error_log=run_dir / "primary.err",
                timeout=args.coordination_timeout,
            )
            manager = read_json(run_dir / "compute-manager.json")
            primary_machine = validate_machine(
                manager.get("fqdn") or manager["hostname"]
            )
            print(
                f"[ready] compute manager {primary_machine} reserved "
                f"{len(manager['port_checks'])} ports",
                flush=True,
            )

            peer_job = bash_job(
                run_dir=run_dir,
                compute_program=compute_path,
                preferred_python=preferred_python,
                storage={},
                role="peer",
            )
            atomic_write_text(peer_job_path, peer_job)
            peer_job_path.chmod(0o700)
            peer_submit = submit_description(
                run_dir=run_dir,
                executable=peer_job_path,
                role="peer",
                request_cpus=args.request_cpus,
                request_memory=args.request_memory,
                request_disk=args.request_disk,
                exclude_machine=primary_machine,
            )
            atomic_write_text(peer_submit_path, peer_submit)
            print(f"Saved peer submit file: {peer_submit_path}", flush=True)
            print(
                f"[submitting] peer excluding machine {primary_machine}",
                flush=True,
            )
            peer_id = submit_job(peer_submit_path)
            job_ids["peer"] = peer_id
            config["jobs"]["peer"] = peer_id
            atomic_write_json(config_path, config)
            print(f"Submitted peer pilot: {peer_id}", flush=True)

            wait_for_artifact(
                path=run_dir / "peer-bash-status.txt",
                job_id=peer_id,
                error_log=run_dir / "peer.err",
                timeout=args.coordination_timeout,
            )
            peer_bash = parse_bash_results(run_dir / "peer-bash-results.tsv")
            print("[completed] peer Bash identity check", flush=True)
            if wait_for_python_start(
                run_dir,
                peer_id,
                run_dir / "peer.err",
                args.coordination_timeout,
                peer=True,
            ):
                wait_for_artifact(
                    path=run_dir / "peer-results.json",
                    job_id=peer_id,
                    error_log=run_dir / "peer.err",
                    timeout=args.coordination_timeout,
                )
                peer_result = read_json(run_dir / "peer-results.json")
                print("[completed] peer compute connection attempts", flush=True)
            else:
                print("[skipped] peer compute Python unavailable", flush=True)

            wait_for_artifact(
                path=run_dir / "compute-manager-results.json",
                job_id=primary_id,
                error_log=run_dir / "primary.err",
                timeout=args.coordination_timeout,
            )
            manager_result = read_json(run_dir / "compute-manager-results.json")
    except Exception:
        cancel_jobs(list(job_ids.values()))
        raise
    finally:
        atomic_write_text(run_dir / "peer-finished.txt", "completed\n")
        login_stop.set()
        if login_future is not None:
            login_result = login_future.result(timeout=args.listener_timeout + 5)
            atomic_write_json(run_dir / "login-results.json", login_result)
        elif login_executor is None and not login_result:
            for server, _nonce in listeners.values():
                server.close()
        if login_executor is not None:
            login_executor.shutdown(wait=True)

    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "site_id": args.site_id,
        "scheduler": "htcondor",
        "evidence_source": "pilot",
        "collected_at": utc_now(),
        "jobs": job_ids,
        "configuration": config,
        "bash": {"primary": primary_bash, "peer": peer_bash},
        "python": {
            "primary_status": read_optional_text(run_dir / "python-status.txt"),
            "peer_status": read_optional_text(
                run_dir / "peer-python-status.txt"
            ),
        },
        "network": {
            "login_compute": reconciled_network(login_result, primary_result),
            "compute_compute": reconciled_network(
                manager_result, peer_result, require_distinct=True
            ),
        },
        "artifacts": {
            "run_directory": str(run_dir),
            "primary_submit": str(primary_submit_path),
            "peer_submit": str(peer_submit_path),
            "primary_job": str(primary_job_path),
            "peer_job": str(peer_job_path),
            "compute_program": str(compute_path),
        },
    }
    result_path = run_dir / "pilot-result.json"
    atomic_write_json(result_path, result)
    print(f"Pilot result: {result_path}")
    return 0


def even_port_count(value: str) -> int:
    """Parse a bounded even port count."""

    count = int(value)
    try:
        validate_port_range(DEFAULT_START_PORT, count)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return count


def positive_float(value: str) -> float:
    """Parse one positive timeout."""

    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def positive_integer(value: str) -> int:
    """Parse one positive integer request."""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def condor_quantity(value: str) -> str:
    """Accept a simple HTCondor memory or disk quantity."""

    if not re.fullmatch(r"[1-9][0-9]*(?:KB|MB|GB|TB)", value, re.IGNORECASE):
        raise argparse.ArgumentTypeError("use a value such as 256MB or 1GB")
    return value.upper()


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the small set of site and resource inputs needed by the pilot."""

    parser = argparse.ArgumentParser(
        description="Generate and run a Notre Dame CRC HTCondor pilot."
    )
    parser.add_argument("--site-id", default="notre-dame-crc")
    parser.add_argument(
        "--storage",
        action="append",
        default=[],
        metavar="NAME=/PATH",
        help="Observed shared storage path; may be repeated.",
    )
    parser.add_argument(
        "--login-host",
        help="Submit hostname reachable from execute nodes; defaults to hostname -f.",
    )
    parser.add_argument("--request-cpus", type=positive_integer, default=1)
    parser.add_argument("--request-memory", type=condor_quantity, default="256MB")
    parser.add_argument("--request-disk", type=condor_quantity, default="10MB")
    parser.add_argument("--start-port", type=int, default=DEFAULT_START_PORT)
    parser.add_argument(
        "--port-count", type=even_port_count, default=DEFAULT_PORT_COUNT
    )
    parser.add_argument("--connect-timeout", type=positive_float, default=10.0)
    parser.add_argument("--listener-timeout", type=positive_float, default=30.0)
    parser.add_argument(
        "--coordination-timeout", type=positive_float, default=3600.0
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("pilot-runs"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Run the login-side HTCondor pilot orchestrator."""

    try:
        return orchestrate(parse_args(arguments))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
