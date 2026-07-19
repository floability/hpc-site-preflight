"""Helpers for writing JSON artifacts and registering them with a tracker."""

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> Path:
    """Write a JSON-serializable value with stable formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, default=str, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
