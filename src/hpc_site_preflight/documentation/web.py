"""Bounded documentation search, fetch, and finish tools."""

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import ValidationError

from hpc_site_preflight.documentation.identity import classify_source
from hpc_site_preflight.documentation.models import (
    BlockKind,
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


SearchFunction = Callable[[str, int, float], list[SearchResult]]


class LiveWebBackend:
    """Search the web and normalize bounded HTML pages."""

    def __init__(
        self,
        allowed_domains: list[str],
        *,
        search_function: SearchFunction | None = None,
        transport: httpx.BaseTransport | None = None,
        maximum_download_bytes: int = 2_000_000,
    ) -> None:
        self.allowed_domains = allowed_domains
        self.search_function = search_function or _ddgs_search
        self.transport = transport
        self.maximum_download_bytes = maximum_download_bytes

    def search(self, query: str, limit: int, timeout_seconds: float) -> list[SearchResult]:
        return self.search_function(query, limit, timeout_seconds)

    def fetch(self, url: str, timeout_seconds: float) -> RecordedPage:
        final_url, content_type, content = self._download(url, timeout_seconds)
        if "text/html" in content_type or "application/xhtml+xml" in content_type:
            title, sections = _html_sections(final_url, content)
        elif "text/plain" in content_type:
            title = final_url.rsplit("/", 1)[-1] or final_url
            sections = [
                DocumentSection(
                    heading_path=[title],
                    blocks=[DocumentBlock(kind="text", text=content.strip())],
                )
            ]
        else:
            raise DocumentationError(
                f"Unsupported documentation content type: {content_type or 'unknown'}"
            )
        return RecordedPage(
            url=final_url,
            title=title,
            fetched_at=datetime.now(UTC),
            sections=sections,
        )

    def _download(self, url: str, timeout_seconds: float) -> tuple[str, str, str]:
        current_url = url
        headers = {
            "User-Agent": "HPCSitePreflight/0.1 (bounded documentation discovery)"
        }
        with httpx.Client(
            timeout=timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            for _ in range(4):
                self._validate_url(current_url)
                with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise DocumentationError("Documentation redirect has no location.")
                        current_url = urljoin(current_url, location)
                        continue
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise DocumentationError(
                            f"Documentation fetch returned HTTP {response.status_code}."
                        ) from exc
                    content = _read_bounded(response, self.maximum_download_bytes)
                    final_url = str(response.url)
                    self._validate_url(final_url)
                    content_type = response.headers.get("content-type", "").lower()
                    encoding = response.encoding or "utf-8"
                    return final_url, content_type, content.decode(encoding, errors="replace")
        raise DocumentationError("Documentation fetch exceeded the redirect limit.")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().strip(".")
        allowed = any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in self.allowed_domains
        )
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.port not in {None, 443}
            or not allowed
        ):
            raise DocumentationError("Documentation URL is outside the HTTPS domain allowlist.")


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
        if not self._url_allowed(recorded.url):
            raise DocumentationError("Documentation redirect left the HTTPS domain allowlist.")
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
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.port in {None, 443}
            and allowed
        )


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


def _ddgs_search(query: str, limit: int, timeout_seconds: float) -> list[SearchResult]:
    try:
        from ddgs import DDGS

        raw_results = DDGS(timeout=int(timeout_seconds)).text(query, max_results=limit)
        return [
            SearchResult(
                url=str(item.get("href") or item.get("url") or ""),
                title=str(item.get("title") or "")[:500],
                snippet=str(item.get("body") or item.get("snippet") or "")[:1000],
            )
            for item in raw_results or []
            if item.get("href") or item.get("url")
        ]
    except Exception as exc:
        raise DocumentationError(f"Documentation search failed: {exc}") from exc


def _read_bounded(response: httpx.Response, maximum_bytes: int) -> bytes:
    content = bytearray()
    for chunk in response.iter_bytes():
        remaining = maximum_bytes - len(content)
        if remaining <= 0:
            break
        content.extend(chunk[:remaining])
    return bytes(content)


def _html_sections(url: str, html: str) -> tuple[str, list[DocumentSection]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else url
    heading_path = [title]
    blocks: list[DocumentBlock] = []
    sections: list[DocumentSection] = []

    def finish_section() -> None:
        nonlocal blocks
        if blocks:
            sections.append(DocumentSection(heading_path=heading_path, blocks=blocks))
            blocks = []

    for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "table"]):
        if element.find_parent("table") is not None and element.name != "table":
            continue
        if element.name and element.name.startswith("h"):
            text = element.get_text(" ", strip=True)
            if not text:
                continue
            finish_section()
            level = int(element.name[1])
            heading_path = heading_path[:level]
            heading_path.append(text)
            continue
        if element.name == "table":
            rows = [
                " | ".join(cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"]))
                for row in element.find_all("tr")
            ]
            text = "\n".join(row for row in rows if row)
            kind: BlockKind = "table"
        else:
            text = element.get_text(" ", strip=True)
            kind = "text"
        if text:
            blocks.append(DocumentBlock(kind=kind, text=text))

    finish_section()
    if not sections:
        text = soup.get_text("\n", strip=True)
        if not text:
            raise DocumentationError("No readable text was found in the documentation page.")
        sections.append(
            DocumentSection(
                heading_path=[title],
                blocks=[DocumentBlock(kind="text", text=text)],
            )
        )
    return title[:500], sections
