"""Search provider clients.

Each provider activates only when its key is configured, so the agent
degrades to answering from its own knowledge instead of erroring. Providers
run concurrently and their results are merged and de-duplicated by URL.

Note: uscis.gov returns 403 to datacenter traffic, so official sources are
reached through these providers' crawlers rather than fetched directly.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import httpx
from loguru import logger

from app.config import settings
from app.failures import FailureKind, classify_status
from app.tools.sources import SourceTier, classify

_TIMEOUT = httpx.Timeout(settings.tool_timeout_secs)

#: One shared client, not one per search. Creating a client per call meant
#: every tool call paid fresh TLS handshakes to three providers: measured
#: warm, Tavily answers in ~70ms and Exa in ~180ms, but a cold call cost
#: 2.5-6s. Connection setup, not search, was the bulk of tool latency.
_client: httpx.AsyncClient | None = None

#: The loop the pooled client's connections belong to. A client outliving its
#: loop fails every request with "Event loop is closed" — the connections are
#: bound to the loop that opened them, not to the client object. A long-lived
#: server has one loop and never hits this, but anything that runs turns on
#: separate loops does, and it fails as a search outage rather than as an
#: obvious crash: the agent falls back to memory and sounds fine.
_client_loop: asyncio.AbstractEventLoop | None = None


def get_client() -> httpx.AsyncClient:
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            # Keep connections hot across turns in a long voice session.
            limits=httpx.Limits(max_keepalive_connections=12, keepalive_expiry=300.0),
        )
        _client_loop = loop
    return _client


async def aclose() -> None:
    """Close the shared client; called when a session ends."""
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    _client_loop = None


#: A turn is only worth so much waiting. Retries are bounded by a deadline
#: rather than an attempt count: an attempt count lets a retry start at 4.5s
#: of a 6s budget and make the turn worse than the failure would have. Once
#: this much of the tool budget is gone, we answer with what we have.
RETRY_DEADLINE_FRACTION = 0.5

#: One retry, and only for failures that a retry can actually fix. Auth and
#: billing problems return the same error every time, so retrying them buys
#: nothing and costs the user a second of silence.
MAX_RETRIES = 1

#: How long to wait before the single retry. Long enough to clear a blip,
#: short enough to stay inside the budget above.
RETRY_BACKOFF_SECS = 0.25


def _is_worth_retrying(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        # 5xx and 429 may differ next time; 401/403/402 will not.
        status = error.response.status_code
        return status >= 500 or status == 429
    # Timeouts and dropped connections are the canonical transient case.
    return isinstance(error, (httpx.TimeoutException, httpx.TransportError))


async def _attempt(coro_factory, *, deadline: float, what: str):
    """Run a provider call, retrying once if that is both useful and affordable."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except Exception as error:  # noqa: BLE001 - classified below
            remaining = deadline - time.monotonic()
            if (
                attempt == MAX_RETRIES
                or not _is_worth_retrying(error)
                or remaining <= RETRY_BACKOFF_SECS
            ):
                raise
            logger.info(
                f"{what} failed ({type(error).__name__}); one retry, "
                f"{remaining:.1f}s of budget left"
            )
            await asyncio.sleep(RETRY_BACKOFF_SECS)
    raise RuntimeError("unreachable")


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
        # Fetch more than the tool will forward, so the trim happens against
        # real content rather than against an already-truncated snippet.
        "contents": {"text": {"maxCharacters": 2400}},
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


async def _parallel(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
    """Parallel's search, used for community sources.

    Far better forum coverage than the general providers: the same query
    returns ten relevant Reddit threads here versus two from Tavily, and
    none at all from Exa. It still does not date forum posts, so the
    undated-story handling in credibility.py stays necessary.

    Domain filtering goes in the query text — the API rejects a
    source_policy field with a 422.
    """
    response = await client.post(
        "https://api.parallel.ai/v1/search",
        headers={"x-api-key": settings.parallel_api_key},
        json={
            "objective": f"First-hand personal accounts about: {query}",
            "search_queries": [f"site:reddit.com {query}"],
            # ~700ms. "advanced" is ~3s, too slow to sit inside a voice turn.
            "mode": "fast",
        },
    )
    response.raise_for_status()

    hits: list[SearchHit] = []
    for position, item in enumerate(response.json().get("results", [])[:limit]):
        url = item.get("url", "")
        excerpts = item.get("excerpts") or []
        hits.append(
            SearchHit(
                title=item.get("title") or "",
                url=url,
                text=" ".join(excerpts)[:1500],
                tier=classify(url),
                published=_parse_date(item.get("publish_date")),
                relevance=_rank_relevance(position),
            )
        )
    return hits


async def search_community(query: str, *, limit: int = 8) -> list[SearchHit]:
    """Find forum accounts, preferring the provider that actually indexes them."""
    if settings.parallel_api_key:
        deadline = time.monotonic() + settings.tool_timeout_secs * RETRY_DEADLINE_FRACTION
        try:
            return _rank(
                await _attempt(
                    lambda: _parallel(get_client(), query, limit),
                    deadline=deadline,
                    what="parallel",
                )
            )
        except Exception as error:  # noqa: BLE001 - fall back, don't fail the turn
            _record_failure(error)

    # Without Parallel, a plain unconstrained search still surfaces some
    # threads; pinning it to reddit.com collapses relevance instead.
    return await search(f"{query} reddit", limit=limit)


#: Failure kind from the most recent search, or None if it succeeded. A
#: search returning zero hits because the account is out of credit is a very
#: different thing from one returning zero hits because nothing matched, and
#: the caller has to be able to tell them apart.
_last_failure: FailureKind | None = None


def _record_failure(error: BaseException) -> None:
    global _last_failure
    if isinstance(error, httpx.HTTPStatusError):
        kind = classify_status(error.response.status_code)
        logger.warning(
            f"search provider failed: HTTP {error.response.status_code} ({kind.value})"
        )
    else:
        kind = FailureKind.CONNECTIVITY
        logger.warning(f"search provider failed: {error!r}")
    # Funds and auth problems outrank a transient blip when several
    # providers fail at once.
    if _last_failure is None or kind in (FailureKind.FUNDS, FailureKind.AUTH):
        _last_failure = kind


def take_last_failure() -> FailureKind | None:
    """Return and clear the failure recorded by the most recent search."""
    global _last_failure
    failure, _last_failure = _last_failure, None
    return failure


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
    # Deliberately not cleared here: search_groups runs several searches and
    # a later success must not erase an earlier group's billing failure.
    # take_last_failure() clears on read instead.
    active = available_providers()
    if not active:
        return []

    client = get_client()
    # Shared across both providers: the budget belongs to the turn, not to
    # each call, so a slow Tavily retry cannot also delay Exa's.
    deadline = time.monotonic() + settings.tool_timeout_secs * RETRY_DEADLINE_FRACTION
    tasks = []
    if "tavily" in active:
        tasks.append(
            _attempt(
                lambda: _tavily(client, query, domains, limit), deadline=deadline, what="tavily"
            )
        )
    if "exa" in active:
        tasks.append(
            _attempt(lambda: _exa(client, query, domains, limit), deadline=deadline, what="exa")
        )
    settled = await asyncio.gather(*tasks, return_exceptions=True)

    merged: dict[str, SearchHit] = {}
    for result in settled:
        if isinstance(result, BaseException):
            _record_failure(result)
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
