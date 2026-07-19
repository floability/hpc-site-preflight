"""Bounded documentation search, fetch, and finish tools."""

import hashlib
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from pydantic import ValidationError

from hpc_site_preflight.documentation.identity import classify_source
from hpc_site_preflight.documentation.models import (
    DocumentBlock,
    DocumentSection,
    FetchedPage,
    RecordedPage,
    SearchResult,
    SiteIdentity,
    WebRecording,
)
from hpc_site_preflight.exceptions import DocumentationError


class WebBackend(Protocol):
    """Search and fetch normalized pages from one replaceable backend."""

    def search(self, query: str, limit: int, timeout_seconds: float) -> list[SearchResult]: ...

    def fetch(self, url: str, timeout_seconds: float) -> RecordedPage: ...


class RecordedWebBackend:
    """Replay reviewed search results and normalized pages from JSON."""

    def __init__(self, recording: WebRecording) -> None:
        self.recording = recording
        self._pages = {page.url: page for page in recording.pages}

    @classmethod
    def from_path(cls, path: Path) -> "RecordedWebBackend":
        try:
            recording = WebRecording.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise DocumentationError(f"Could not read web recording: {path}") from exc
        except ValidationError as exc:
            raise DocumentationError(
                f"Web recording failed {exc.error_count()} contract validation(s)."
            ) from exc
        return cls(recording)

    def search(self, query: str, limit: int, timeout_seconds: float) -> list[SearchResult]:
        del timeout_seconds
        query_terms = _tokens(query) - {"site", "official", "policy"}

        def score(result: SearchResult) -> tuple[int, str]:
            text_terms = _tokens(f"{result.title} {result.snippet}")
            return len(query_terms & text_terms), result.url

        ranked = sorted(self.recording.search_results, key=score, reverse=True)
        matching = [result for result in ranked if score(result)[0] > 0]
        return (matching or ranked)[:limit]

    def fetch(self, url: str, timeout_seconds: float) -> RecordedPage:
        del timeout_seconds
        page = self._pages.get(url)
        if page is None:
            raise DocumentationError(f"Recorded page is unavailable: {url}")
        return page


class DocumentationTools:
    """Enforce scope and budgets around a web backend."""

    def __init__(
        self,
        identity: SiteIdentity,
        backend: WebBackend,
        *,
        search_budget: int = 6,
        page_budget: int = 6,
        search_result_limit: int = 8,
        maximum_page_chars: int = 20_000,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.identity = identity
        self.backend = backend
        self.search_budget = search_budget
        self.page_budget = page_budget
        self.search_result_limit = search_result_limit
        self.maximum_page_chars = maximum_page_chars
        self.timeout_seconds = timeout_seconds
        self.searches_used = 0
        self.pages_used = 0
        self.fetched_pages: dict[str, FetchedPage] = {}

    def search_web(self, query: str) -> list[SearchResult]:
        if self.searches_used >= self.search_budget:
            raise DocumentationError("Documentation search budget is exhausted.")
        repaired = self._repair_query(query)
        self.searches_used += 1
        results = self.backend.search(
            repaired,
            self.search_result_limit,
            self.timeout_seconds,
        )
        return [result for result in results if self._url_allowed(result.url)]

    def fetch_page(self, url: str) -> FetchedPage:
        if self.pages_used >= self.page_budget:
            raise DocumentationError("Documentation page budget is exhausted.")
        if not self._url_allowed(url):
            raise DocumentationError("Documentation URL is outside the HTTPS domain allowlist.")
        self.pages_used += 1
        recorded = self.backend.fetch(url, self.timeout_seconds)
        bounded_sections, truncated = _limit_sections(
            recorded.sections,
            self.maximum_page_chars,
        )
        text = "\n".join(block.text for section in bounded_sections for block in section.blocks)
        scope = classify_source(self.identity, url, recorded.title, text)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        page = FetchedPage(
            **recorded.model_dump(exclude={"sections"}),
            sections=bounded_sections,
            scope=scope,
            content_hash=content_hash,
            text_truncated=truncated,
        )
        self.fetched_pages[url] = page
        return page

    def finish_discovery(
        self,
        source_urls: list[str],
        summary: str,
        unanswered_topics: list[str],
    ) -> tuple[list[FetchedPage], str, list[str]]:
        if len(source_urls) > 10:
            raise DocumentationError("Discovery may select at most ten pages.")
        selected: list[FetchedPage] = []
        for url in dict.fromkeys(source_urls):
            page = self.fetched_pages.get(url)
            if page is None:
                raise DocumentationError("Discovery selected a page that was not fetched.")
            if page.scope != "target_site":
                raise DocumentationError("Only target-site pages may become policy evidence.")
            selected.append(page)
        return selected, summary, unanswered_topics

    def partial_selection(self) -> list[FetchedPage]:
        return [
            page
            for _, page in sorted(self.fetched_pages.items())
            if page.scope == "target_site"
        ][:10]

    def _repair_query(self, query: str) -> str:
        if any(alias.lower() in query.lower() for alias in self.identity.aliases):
            return query
        alias = min(self.identity.aliases, key=lambda item: (len(item.split()), len(item)))
        return f"{alias} {query}".strip()

    def _url_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().strip(".")
        allowed = any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in self.identity.allowed_domains
        )
        return parsed.scheme == "https" and parsed.username is None and allowed


def page_trace_details(page: FetchedPage) -> dict[str, str]:
    """Return safe page metadata for the normal trace."""

    return {"url": page.url, "content_hash": page.content_hash, "scope": page.scope}


def _limit_sections(
    sections: list[DocumentSection], maximum_chars: int
) -> tuple[list[DocumentSection], bool]:
    remaining = maximum_chars
    result: list[DocumentSection] = []
    truncated = False
    for section in sections:
        blocks: list[DocumentBlock] = []
        for block in section.blocks:
            if remaining <= 0:
                truncated = True
                break
            text = block.text[:remaining]
            truncated = truncated or len(text) < len(block.text)
            blocks.append(DocumentBlock(kind=block.kind, text=text))
            remaining -= len(text)
        if blocks:
            result.append(DocumentSection(heading_path=section.heading_path, blocks=blocks))
        if remaining <= 0:
            break
    return result, truncated


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))
