"""Search provider clients.

Each provider activates only when its key is configured, so the agent
degrades to answering from its own knowledge instead of erroring. Providers
run concurrently and their results are merged and de-duplicated by URL.

Note: uscis.gov returns 403 to datacenter traffic, so official sources are
reached through these providers' crawlers rather than fetched directly.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import httpx
from loguru import logger

from app.config import settings
from app.tools.sources import SourceTier, classify

_TIMEOUT = httpx.Timeout(settings.tool_timeout_secs)


#: Providers score relevance 0-1. Measured against real queries, off-topic
#: results (a CBP hiring video for "H-1B premium processing") land below 0.1
#: while correct ones land above 0.75, so this threshold separates them
#: cleanly. Without it the model receives authoritative-looking nonsense.
MIN_RELEVANCE = 0.4


def _rank_relevance(position: int) -> float:
    """Approximate a relevance score for providers that don't return one.

    Exa returns results in relevance order but no score, so without this a
    SearchHit would fall back to its default of 1.0 and every Exa result
    would outrank every scored Tavily result regardless of quality. Rank
    position is the only signal available; the scale is chosen to overlap
    Tavily's observed range (~0.5-0.93) rather than dominate it.
    """
    return max(MIN_RELEVANCE, 0.90 - position * 0.08)


@dataclass
class SearchHit:
    title: str
    url: str
    text: str
    tier: SourceTier
    published: datetime | None = None
    author: str | None = None
    relevance: float = 1.0

    @property
    def is_archived(self) -> bool:
        """USCIS keeps superseded announcements under /archive/ indefinitely.

        They rank well for current-policy questions but may be years stale, so
        they are demoted rather than trusted.
        """
        return "/archive/" in self.url.lower()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _canonical(url: str) -> str:
    """Normalise a URL so the same page from two providers dedupes to one."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host = (p.hostname or "").lower().removeprefix("www.")
    path = p.path.rstrip("/") or "/"
    # Providers return the same page both with and without an .html suffix,
    # which otherwise slips past dedup and shows the model one source twice.
    for suffix in (".html", ".htm"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunparse((p.scheme or "https", host, path or "/", "", "", ""))


async def _tavily(client: httpx.AsyncClient, query: str, domains: list[str] | None, limit: int):
    payload: dict = {"query": query, "max_results": limit, "search_depth": "basic"}
    if domains:
        payload["include_domains"] = domains
    r = await client.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
        json=payload,
    )
    r.raise_for_status()
    return [
        SearchHit(
            title=item.get("title", ""),
            url=item.get("url", ""),
            text=item.get("content", ""),
            tier=classify(item.get("url", "")),
            published=_parse_date(item.get("published_date")),
            relevance=float(item.get("score") or 0.0),
        )
        for item in r.json().get("results", [])
    ]


async def _exa(client: httpx.AsyncClient, query: str, domains: list[str] | None, limit: int):
    payload: dict = {
        "query": query,
        "numResults": limit,
        "contents": {"text": {"maxCharacters": 1200}},
    }
    if domains:
        payload["includeDomains"] = domains
    r = await client.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": settings.exa_api_key},
        json=payload,
    )
    r.raise_for_status()
    return [
        SearchHit(
            title=item.get("title") or "",
            url=item.get("url", ""),
            text=item.get("text") or "",
            tier=classify(item.get("url", "")),
            published=_parse_date(item.get("publishedDate")),
            author=item.get("author"),
            relevance=_rank_relevance(position),
        )
        for position, item in enumerate(r.json().get("results", []))
    ]


def available_providers() -> list[str]:
    names = []
    if settings.tavily_api_key:
        names.append("tavily")
    if settings.exa_api_key:
        names.append("exa")
    return names


_TIER_RANK = {
    SourceTier.AUTHORITATIVE: 0,
    SourceTier.PROFESSIONAL: 1,
    SourceTier.ANECDOTAL: 2,
    SourceTier.UNKNOWN: 3,
}


async def search(query: str, *, domains: list[str] | None = None, limit: int = 5) -> list[SearchHit]:
    """Run one query against every configured provider and merge the results.

    A provider failing must not fail the turn — a partial answer beats dead
    air — so exceptions are logged and that provider is skipped.
    """
    active = available_providers()
    if not active:
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = []
        if "tavily" in active:
            tasks.append(_tavily(client, query, domains, limit))
        if "exa" in active:
            tasks.append(_exa(client, query, domains, limit))
        settled = await asyncio.gather(*tasks, return_exceptions=True)

    merged: dict[str, SearchHit] = {}
    for result in settled:
        if isinstance(result, BaseException):
            logger.warning(f"search provider failed: {result!r}")
            continue
        for hit in result:
            if not hit.url or hit.relevance < MIN_RELEVANCE:
                continue
            key = _canonical(hit.url)
            existing = merged.get(key)
            # Keep whichever copy carries more usable text.
            if existing is None or len(hit.text) > len(existing.text):
                merged[key] = hit

    return _rank(merged.values())


async def search_groups(
    query: str, groups: tuple[tuple[str, ...], ...], *, limit: int = 4
) -> list[SearchHit]:
    """Search several narrow domain groups in parallel and merge.

    Deliberately not one query across all domains: providers rank far worse
    when constrained to a large domain list (measured: 0.09 relevance across
    24 domains versus 0.90 against uscis.gov alone for the same query).
    """
    results = await asyncio.gather(
        *(search(query, domains=list(group), limit=limit) for group in groups),
        return_exceptions=True,
    )

    merged: dict[str, SearchHit] = {}
    for group_hits in results:
        if isinstance(group_hits, BaseException):
            logger.warning(f"search group failed: {group_hits!r}")
            continue
        for hit in group_hits:
            merged.setdefault(_canonical(hit.url), hit)

    return _rank(merged.values())


def _rank(hits) -> list[SearchHit]:
    """Trust first, then relevance — but an archived page loses a tier.

    Without the demotion, superseded USCIS announcements from 2017 outrank a
    current, accurate law-firm page purely because they sit on a .gov domain.
    For "what is the processing time right now", that is the wrong answer
    dressed up as the authoritative one.
    """
    return sorted(
        hits,
        key=lambda h: (_TIER_RANK[h.tier] + (1 if h.is_archived else 0), -h.relevance),
    )
