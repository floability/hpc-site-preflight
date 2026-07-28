"""Heading-aware corpus construction and persistent JSONL storage."""

import hashlib
import json
import re
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

from pydantic import ValidationError

from hpc_site_preflight.documentation.models import (
    CorpusChunk,
    CorpusDocument,
    CorpusManifest,
    FetchedPage,
)
from hpc_site_preflight.exceptions import DocumentationError

_CorpusRecord = TypeVar("_CorpusRecord", CorpusDocument, CorpusChunk)


def build_corpus(
    site_id: str,
    pages: list[FetchedPage],
    *,
    maximum_chunk_chars: int = 1800,
) -> tuple[CorpusManifest, list[CorpusDocument], list[CorpusChunk]]:
    """Build stable document and chunk records from selected pages."""

    documents: list[CorpusDocument] = []
    chunks: list[CorpusChunk] = []
    used_document_ids: set[str] = set()
    for page in sorted(pages, key=lambda item: item.url):
        document_id = _unique_document_id(page.url, used_document_ids)
        used_document_ids.add(document_id)
        documents.append(
            CorpusDocument(
                document_id=document_id,
                source_url=page.url,
                title=page.title,
                scope=page.scope,
                content_hash=page.content_hash,
                fetched_at=page.fetched_at,
            )
        )
        ordinal = 0
        for section in page.sections:
            for block in section.blocks:
                pieces = (
                    [block.text]
                    if block.kind == "table"
                    else _split(block.text, maximum_chunk_chars)
                )
                for piece in pieces:
                    ordinal += 1
                    chunk_id = f"{document_id}:c{ordinal}"
                    chunks.append(
                        CorpusChunk(
                            chunk_id=chunk_id,
                            document_id=document_id,
                            source_url=page.url,
                            title=page.title,
                            scope=page.scope,
                            heading_path=section.heading_path,
                            block_kind=block.kind,
                            text=piece,
                            content_hash=_hash(piece),
                        )
                    )

    fingerprint_source = "\n".join(
        f"{document.source_url}:{document.content_hash}" for document in documents
    )
    manifest = CorpusManifest(
        site_id=site_id,
        fingerprint=_hash(fingerprint_source),
        document_count=len(documents),
        chunk_count=len(chunks),
    )
    return manifest, documents, chunks


def write_corpus(
    directory: Path,
    manifest: CorpusManifest,
    documents: list[CorpusDocument],
    chunks: list[CorpusChunk],
) -> list[Path]:
    """Write deterministic manifest, document, and chunk artifacts."""

    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    documents_path = directory / "documents.jsonl"
    chunks_path = directory / "chunks.jsonl"
    _atomic_write(manifest_path, manifest.model_dump_json(indent=2) + "\n")
    _atomic_write(
        documents_path,
        "".join(document.model_dump_json() + "\n" for document in documents),
    )
    _atomic_write(chunks_path, "".join(chunk.model_dump_json() + "\n" for chunk in chunks))
    return [manifest_path, documents_path, chunks_path]


def load_corpus(
    directory: Path,
    *,
    expected_site_id: str,
) -> tuple[CorpusManifest, list[CorpusDocument], list[CorpusChunk]]:
    """Load and validate one frozen corpus directory for a known site."""

    manifest_path = directory / "manifest.json"
    documents_path = directory / "documents.jsonl"
    chunks_path = directory / "chunks.jsonl"
    try:
        manifest = CorpusManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        documents = _read_jsonl(documents_path, CorpusDocument)
        chunks = _read_jsonl(chunks_path, CorpusChunk)
    except OSError as exc:
        raise DocumentationError(f"Could not read frozen corpus: {exc.filename}") from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DocumentationError(f"Frozen corpus validation failed: {exc}") from exc

    if manifest.site_id != expected_site_id:
        raise DocumentationError(
            f"Frozen corpus site_id '{manifest.site_id}' does not match "
            f"'{expected_site_id}'."
        )
    if manifest.document_count != len(documents):
        raise DocumentationError("Frozen corpus document count does not match its manifest.")
    if manifest.chunk_count != len(chunks):
        raise DocumentationError("Frozen corpus chunk count does not match its manifest.")

    document_ids = [item.document_id for item in documents]
    chunk_ids = [item.chunk_id for item in chunks]
    if len(document_ids) != len(set(document_ids)):
        raise DocumentationError("Frozen corpus contains duplicate document IDs.")
    if len(chunk_ids) != len(set(chunk_ids)):
        raise DocumentationError("Frozen corpus contains duplicate chunk IDs.")

    by_document_id = {item.document_id: item for item in documents}
    for chunk in chunks:
        document = by_document_id.get(chunk.document_id)
        if document is None:
            raise DocumentationError(
                f"Frozen chunk '{chunk.chunk_id}' references an unknown document."
            )
        if (
            chunk.source_url != document.source_url
            or chunk.title != document.title
            or chunk.scope != document.scope
        ):
            raise DocumentationError(
                f"Frozen chunk '{chunk.chunk_id}' disagrees with its document metadata."
            )
        if chunk.content_hash != _hash(chunk.text):
            raise DocumentationError(
                f"Frozen chunk '{chunk.chunk_id}' has an invalid content hash."
            )

    fingerprint_source = "\n".join(
        f"{document.source_url}:{document.content_hash}" for document in documents
    )
    if manifest.fingerprint != _hash(fingerprint_source):
        raise DocumentationError("Frozen corpus fingerprint does not match its documents.")
    return manifest, documents, chunks


def _read_jsonl(
    path: Path,
    model_type: type[_CorpusRecord],
) -> list[_CorpusRecord]:
    """Parse non-empty JSONL lines through the requested corpus model."""

    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(model_type.model_validate_json(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DocumentationError(
                f"Invalid frozen corpus record in {path.name} at line {line_number}: {exc}"
            ) from exc
    return records


def _unique_document_id(url: str, used: set[str]) -> str:
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    slug = "-".join(segments[-2:]) or "root"
    base = "doc-" + re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    if base not in used:
        return base
    return f"{base}-{_hash(url)[:6]}"


def _split(text: str, maximum_chars: int) -> list[str]:
    remaining = text.strip()
    pieces: list[str] = []
    while remaining:
        if len(remaining) <= maximum_chars:
            pieces.append(remaining)
            break
        boundary = remaining.rfind(" ", 0, maximum_chars + 1)
        if boundary < maximum_chars // 2:
            boundary = maximum_chars
        pieces.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    return pieces


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
