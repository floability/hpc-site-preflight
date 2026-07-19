"""Small consistency checks for the common login-node fact catalog."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "schemas" / "measurement-fields" / "common.json"

REQUIRED_TOP_LEVEL_KEYS = {"schema_version", "catalog_id", "statuses", "categories"}
REQUIRED_CATEGORIES = {
    "collection",
    "identity",
    "platform",
    "user",
    "scheduler_detection",
    "storage",
    "temporary_storage",
    "software",
    "networking",
    "system_limits",
}
REQUIRED_FIELD_KEYS = {
    "path",
    "type",
    "description",
    "method",
    "command_id",
    "command",
    "requires_write",
    "optional",
}
ALLOWED_METHODS = {
    "python_api",
    "environment_variable",
    "fixed_command",
    "executable_lookup",
    "collector_function",
    "derived",
}
JSON_TYPES = {"array", "boolean", "integer", "number", "object", "string"}


def _load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_common_catalog_has_expected_shape() -> None:
    catalog = _load_catalog()
    assert set(catalog) == REQUIRED_TOP_LEVEL_KEYS
    assert set(catalog["categories"]) == REQUIRED_CATEGORIES
    assert catalog["statuses"]


def test_common_catalog_fields_have_required_metadata() -> None:
    catalog = _load_catalog()
    fields = [field for group in catalog["categories"].values() for field in group]
    paths = [field["path"] for field in fields]

    assert 40 <= len(fields) <= 50
    assert len(paths) == len(set(paths))

    for field in fields:
        assert set(field) == REQUIRED_FIELD_KEYS
        assert field["path"].startswith("/")
        assert field["type"] in JSON_TYPES
        assert field["method"] in ALLOWED_METHODS
        assert isinstance(field["description"], str) and field["description"]
        assert isinstance(field["command_id"], str) and field["command_id"]
        assert isinstance(field["requires_write"], bool)
        assert isinstance(field["optional"], bool)

        if field["method"] == "fixed_command":
            assert isinstance(field["command"], list) and field["command"]
            assert all(isinstance(argument, str) for argument in field["command"])
            assert field["command"][0] not in {"bash", "sh", "zsh"}
        else:
            assert field["command"] is None
