"""Stable evidence identifiers."""

import hashlib


def build_evidence_id(source_type: str, site_id: str, field_path: str, source_key: str) -> str:
    """Return a deterministic short ID without embedding evidence contents."""

    payload = "\x1f".join((source_type, site_id, field_path, source_key)).encode()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"{source_type}:{digest}"
