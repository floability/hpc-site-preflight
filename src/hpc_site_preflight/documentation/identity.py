"""Deterministic site identity, query planning, and source scope."""

import re
from collections.abc import Iterable
from urllib.parse import urlparse

from hpc_site_preflight.documentation.models import (
    DocumentationScope,
    QueryPlan,
    SearchQuery,
    SiteIdentity,
)
from hpc_site_preflight.measurements.base import MeasurementBundle

_SCOPE_MARKERS = {"clusters", "hpc", "systems", "userguides"}
_GENERIC_PATH_TOKENS = {"docs", "documentation", "guide", "guides", "policies"}


def build_site_identity(
    measurements: MeasurementBundle,
    *,
    discovery_site_name: str | None = None,
    discovery_note: str | None = None,
    discovery_keywords: Iterable[str] = (),
) -> SiteIdentity:
    """Build documentation identity from measurements and optional discovery hints."""

    facts = measurements.site_facts
    hosts = [
        value.lower().strip(".")
        for value in (
            facts.hostname,
            facts.fqdn,
        )
        if value
    ]

    preferred_name = _optional_text(discovery_site_name)
    aliases = _dedupe([preferred_name or facts.site_name, facts.site_name, *facts.aliases])
    domains = _dedupe(domain.lower().strip(".") for domain in facts.documentation_domains)
    tokens = _dedupe(token.lower() for token in facts.preferred_path_tokens)
    return SiteIdentity(
        site_id=facts.site_id,
        site_name=facts.site_name,
        aliases=aliases,
        scheduler=measurements.scheduler_type,
        hostname_patterns=[item.lower() for item in facts.hostname_patterns],
        observed_hosts=_dedupe(hosts),
        allowed_domains=domains,
        preferred_path_tokens=tokens,
        discovery_site_name=preferred_name,
        discovery_note=_optional_text(discovery_note),
        discovery_keywords=_dedupe(discovery_keywords),
    )


def build_query_plan(identity: SiteIdentity) -> QueryPlan:
    """Build fixed canonical and policy searches for the target site."""

    alias = identity.discovery_site_name or min(
        identity.aliases,
        key=lambda value: (len(value.split()), len(value)),
    )
    site_filter = " OR ".join(f"site:{domain}" for domain in identity.allowed_domains)
    suffix = f" {site_filter}" if site_filter else ""
    scheduler = identity.scheduler if identity.scheduler != "unknown" else "batch"
    queries = [
        SearchQuery(
            topic="canonical",
            query=f"{alias} official user guide{suffix}",
        ),
        SearchQuery(
            topic="canonical",
            query=f"{alias} documentation user guide{suffix}",
        ),
        SearchQuery(
            topic="submission",
            query=f"{alias} {scheduler} submit job required options account allocation{suffix}",
        ),
        SearchQuery(
            topic="submission",
            query=f"{alias} batch job script submit command project account{suffix}",
        ),
        SearchQuery(
            topic="resources",
            query=_resource_query(alias, identity.scheduler, suffix, detailed=False),
        ),
        SearchQuery(
            topic="resources",
            query=_resource_query(alias, identity.scheduler, suffix, detailed=True),
        ),
        SearchQuery(
            topic="storage",
            query=f"{alias} policies FAQ charging accounting service units{suffix}",
        ),
        SearchQuery(
            topic="storage",
            query=f"{alias} scratch purge retention storage policy{suffix}",
        ),
        SearchQuery(
            topic="networking",
            query=f"{alias} compute login node network firewall TCP ports{suffix}",
        ),
        SearchQuery(
            topic="networking",
            query=f"{alias} worker networking outbound compute nodes{suffix}",
        ),
    ]
    queries.extend(
        SearchQuery(topic="user", query=f"{alias} {keyword}{suffix}")
        for keyword in identity.discovery_keywords
    )
    return QueryPlan(site_id=identity.site_id, queries=queries)


def _resource_query(alias: str, scheduler: str, suffix: str, *, detailed: bool) -> str:
    if scheduler == "htcondor":
        terms = (
            "execute machines slots CPU memory GPU resource limits"
            if detailed
            else "HTCondor pool resources machines slots limits"
        )
    else:
        terms = (
            "partition maximum nodes walltime CPU memory GPU"
            if detailed
            else "queue partition walltime node job limits"
        )
    return f"{alias} {terms}{suffix}"


def classify_source(identity: SiteIdentity, url: str, title: str, text: str) -> DocumentationScope:
    """Classify source scope without model judgment."""

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().strip(".")
    if parsed.scheme != "https" or not _allowed_host(hostname, identity.allowed_domains):
        return "out_of_scope"

    path_segments = [segment.lower() for segment in parsed.path.split("/") if segment]
    preferred = {_normalize(token) for token in identity.preferred_path_tokens}
    normalized_segments = {
        _normalize(segment.split(".", 1)[0]) for segment in path_segments
    }
    if preferred & normalized_segments:
        return "target_site"

    prominent = f"{title}\n{text[:2000]}".lower()
    if any(alias.lower() in prominent for alias in identity.aliases):
        return "target_site"

    scoped_token = _scoped_path_token(path_segments)
    if scoped_token and _normalize(scoped_token) not in preferred:
        return "sibling_site"
    return "organization_general"


def _allowed_host(hostname: str, domains: list[str]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _scoped_path_token(segments: list[str]) -> str | None:
    for index, segment in enumerate(segments[:-1]):
        candidate = segments[index + 1]
        if segment in _SCOPE_MARKERS and candidate not in _GENERIC_PATH_TOKENS:
            return candidate
    return None


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
