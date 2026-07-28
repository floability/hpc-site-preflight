"""Bounded TCP helpers shared by scheduler pilot implementations."""

from __future__ import annotations

import secrets
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

MAX_PORT_COUNT = 32

# Copied into each run directory so compute nodes need only standard-library Python.
COMPUTE_PROGRAM = r'''#!/usr/bin/env python3
"""Run one compute-node role for the bounded network pilot."""

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
        "slurm_node_name": os.environ.get("SLURMD_NODENAME"),
    }


def connect(check, host, timeout):
    attempt = {"port": check["port"], "status": "failed", "error": None}
    try:
        connection = socket.create_connection((host, check["port"]), timeout=timeout)
        with connection:
            connection.settimeout(timeout)
            connection.sendall(check["nonce"].encode("utf-8"))
            reply = connection.recv(256).decode("utf-8", errors="replace")
        if reply == "ACK:{}".format(check["nonce"]):
            attempt["status"] = "passed"
        else:
            attempt["error"] = "acknowledgement mismatch"
    except (OSError, TimeoutError, KeyError) as exc:
        attempt["error"] = "{}: {}".format(type(exc).__name__, exc)
    return attempt


def connect_all(checks, host, timeout):
    with ThreadPoolExecutor(max_workers=max(1, len(checks))) as executor:
        return list(executor.map(lambda check: connect(check, host, timeout), checks))


def reserve_ports(start_port, count):
    checks = []
    listeners = {}
    random_source = secrets.SystemRandom()
    try:
        for window in range(count // 2):
            low = start_port + window * 1000
            reserved = 0
            tried = set()
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
                except OSError:
                    server.close()
                    continue
                nonce = secrets.token_hex(16)
                checks.append({"port": port, "nonce": nonce})
                listeners[port] = (server, nonce)
                reserved += 1
                if reserved == 2:
                    break
            if reserved != 2:
                raise RuntimeError(
                    "could not reserve two ports in {}-{}".format(low, low + 999)
                )
    except Exception:
        for server, _nonce in listeners.values():
            server.close()
        raise
    return checks, listeners


def accept_one(port, server, nonce, stop_event, timeout):
    attempt = {"port": port, "status": "failed", "error": None}
    try:
        server.settimeout(1)
        while not stop_event.is_set():
            try:
                connection, _address = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(timeout)
                received = connection.recv(256).decode("utf-8", errors="replace")
                if received != nonce:
                    attempt["error"] = "nonce mismatch"
                    continue
                connection.sendall("ACK:{}".format(nonce).encode("utf-8"))
                attempt["status"] = "passed"
                attempt["error"] = None
                break
    except (OSError, TimeoutError) as exc:
        attempt["error"] = "{}: {}".format(type(exc).__name__, exc)
    finally:
        server.close()
    return attempt


def run_primary(run_dir, config):
    node = identity()
    write_json(run_dir / "primary-node.json", node)
    write_json(
        run_dir / "login-compute-client.json",
        connect_all(
            config["login_port_checks"],
            config["login_host"],
            config["connect_timeout"],
        ),
    )
    checks, listeners = reserve_ports(config["start_port"], config["port_count"])
    write_json(
        run_dir / "compute-manager.json",
        {"host": node["fqdn"] or node["hostname"], "node": node, "checks": checks},
    )
    stop_event = threading.Event()
    attempts = []
    with ThreadPoolExecutor(max_workers=len(listeners)) as executor:
        futures = [
            executor.submit(
                accept_one,
                port,
                server,
                nonce,
                stop_event,
                config["listener_timeout"],
            )
            for port, (server, nonce) in listeners.items()
        ]
        deadline = time.monotonic() + config["coordination_timeout"]
        while time.monotonic() < deadline and not (run_dir / "peer-finished.txt").exists():
            time.sleep(1)
        stop_event.set()
        attempts = [future.result() for future in as_completed(futures)]
    write_json(run_dir / "compute-manager-attempts.json", attempts)


def run_peer(run_dir, config):
    node = identity()
    write_json(run_dir / "peer-node.json", node)
    manager = read_json(run_dir / "compute-manager.json")
    attempts = connect_all(
        manager["checks"], manager["host"], config["connect_timeout"]
    )
    write_json(run_dir / "compute-compute-client.json", attempts)
    (run_dir / "peer-finished.txt").write_text("completed\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--role", choices=("primary", "peer"), required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    config = read_json(run_dir / "pilot-config.json")
    if args.role == "primary":
        run_primary(run_dir, config)
    else:
        run_peer(run_dir, config)


if __name__ == "__main__":
    main()
'''


def validate_port_plan(start_port: int, count: int) -> None:
    """Require two bounded ports in each 1,000-port block."""

    if count < 2 or count > MAX_PORT_COUNT or count % 2:
        raise ValueError(f"port count must be even and between 2 and {MAX_PORT_COUNT}")
    if start_port < 1024 or start_port + (count // 2) * 1000 - 1 > 65535:
        raise ValueError("pilot ports must remain between 1024 and 65535")


def reserve_ports(
    start_port: int,
    count: int,
) -> tuple[list[dict[str, Any]], dict[int, tuple[socket.socket, str]]]:
    """Bind login listeners before submitting the primary job."""

    validate_port_plan(start_port, count)
    checks: list[dict[str, Any]] = []
    listeners: dict[int, tuple[socket.socket, str]] = {}
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
                except OSError:
                    server.close()
                    continue
                nonce = secrets.token_hex(16)
                checks.append({"port": port, "nonce": nonce})
                listeners[port] = (server, nonce)
                reserved += 1
                if reserved == 2:
                    break
            if reserved != 2:
                raise RuntimeError(f"could not reserve two ports in {low}-{low + 999}")
    except Exception:
        close_listeners(listeners)
        raise
    return checks, listeners


def run_listeners(
    listeners: dict[int, tuple[socket.socket, str]],
    stop_event: threading.Event,
    timeout: float,
) -> list[dict[str, Any]]:
    """Authenticate compute connections on all login listeners concurrently."""

    with ThreadPoolExecutor(max_workers=max(1, len(listeners))) as executor:
        futures = [
            executor.submit(_accept_one, port, server, nonce, stop_event, timeout)
            for port, (server, nonce) in listeners.items()
        ]
        return [future.result() for future in as_completed(futures)]


def _accept_one(
    port: int,
    server: socket.socket,
    nonce: str,
    stop_event: threading.Event,
    timeout: float,
) -> dict[str, Any]:
    """Accept one nonce-authenticated connection."""

    result: dict[str, Any] = {"port": port, "status": "failed", "error": None}
    try:
        server.settimeout(1)
        while not stop_event.is_set():
            try:
                connection, _address = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(timeout)
                received = connection.recv(256).decode("utf-8", errors="replace")
                if received != nonce:
                    result["error"] = "nonce mismatch"
                    continue
                connection.sendall(f"ACK:{nonce}".encode())
                result["status"] = "passed"
                result["error"] = None
                break
    except (OSError, TimeoutError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        server.close()
    return result


def reconcile_attempts(
    server_attempts: list[dict[str, Any]],
    client_attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Require both endpoints to report a passed nonce handshake."""

    server = {
        port: item
        for item in server_attempts
        if isinstance((port := item.get("port")), int)
    }
    client = {
        port: item
        for item in client_attempts
        if isinstance((port := item.get("port")), int)
    }
    ports = sorted(set(server) | set(client))
    results: list[dict[str, Any]] = []
    for port in ports:
        passed = (
            server.get(port, {}).get("status") == "passed"
            and client.get(port, {}).get("status") == "passed"
        )
        errors = []
        for item in (server.get(port, {}), client.get(port, {})):
            error = item.get("error")
            if isinstance(error, str) and error:
                errors.append(error)
        results.append(
            {
                "port": port,
                "status": "passed" if passed else "failed",
                "error": "; ".join(errors) or None,
            }
        )
    return results


def close_listeners(
    listeners: dict[int, tuple[socket.socket, str]],
) -> None:
    """Close listeners that were not consumed by a running manager."""

    for server, _nonce in listeners.values():
        try:
            server.close()
        except OSError:
            pass
