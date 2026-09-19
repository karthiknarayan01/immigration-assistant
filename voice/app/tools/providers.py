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


@dataclass
class SearchHit:
    title: str
    url: str
    text: str
    tier: SourceTier
    published: datetime | None = None
    author: str | None = None


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
    return urlunparse((p.scheme or "https", host, path, "", "", ""))


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
        )
        for item in r.json().get("results", [])
    ]


def available_providers() -> list[str]:
    names = []
    if settings.tavily_api_key:
        names.append("tavily")
    if settings.exa_api_key:
        names.append("exa")
    return names


async def search(query: str, *, domains: list[str] | None = None, limit: int = 5) -> list[SearchHit]:
    """Query every configured provider concurrently and merge the results.

    One provider failing must not fail the turn — a partial answer beats dead
    air, so exceptions are logged and that provider is simply skipped.
    """
    providers = available_providers()
    if not providers:
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = []
        if "tavily" in providers:
            tasks.append(_tavily(client, query, domains, limit))
        if "exa" in providers:
            tasks.append(_exa(client, query, domains, limit))
        settled = await asyncio.gather(*tasks, return_exceptions=True)

    merged: dict[str, SearchHit] = {}
    for result in settled:
        if isinstance(result, BaseException):
            logger.warning(f"search provider failed: {result!r}")
            continue
        for hit in result:
            if not hit.url:
                continue
            key = _canonical(hit.url)
            existing = merged.get(key)
            # Keep whichever copy carries more usable text.
            if existing is None or len(hit.text) > len(existing.text):
                merged[key] = hit

    # Authoritative sources first, so the model sees the law before the noise.
    tier_rank = {
        SourceTier.AUTHORITATIVE: 0,
        SourceTier.PROFESSIONAL: 1,
        SourceTier.ANECDOTAL: 2,
        SourceTier.UNKNOWN: 3,
    }
    return sorted(merged.values(), key=lambda h: tier_rank[h.tier])
