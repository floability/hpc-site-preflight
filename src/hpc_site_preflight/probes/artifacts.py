"""Small artifact helpers for inspectable pilot runs."""

import json
from pathlib import Path
from typing import Any


def write_text(path: Path, value: str) -> None:
    """Write one pilot artifact atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, value: Any) -> None:
    """Write stable, readable JSON."""

    write_text(path, json.dumps(value, indent=2, default=str) + "\n")


def read_json(path: Path) -> Any:
    """Read one JSON artifact."""

    return json.loads(path.read_text(encoding="utf-8"))


def read_json_if_present(path: Path, default: Any) -> Any:
    """Read optional JSON or return the supplied default."""

    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        return default
