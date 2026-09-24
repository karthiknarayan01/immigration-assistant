"""Search X (Twitter) through xAI's Live Search.

X is where immigration signal appears first. A consulate starting to issue
221(g) notices in volume, an RFE wave on a particular petition type, a policy
memo being read a new way — practitioners post about it there days or weeks
before it reaches a law firm's blog and months before it reaches USCIS's site.
Nothing else the agent can reach has that latency.

It is also, for the same reason, the least trustworthy thing the agent can
reach. Immigration X carries a heavy population of consultancies advertising
services, accounts farming anxiety for engagement, and confident
misstatements of law. So results here do NOT become context the model reads
as background. They enter as anecdotes and go through the same gates as
Reddit: recency, firsthand-versus-hearsay scoring, solicitation detection,
and a corroboration threshold of three independent authors before anything
may be described as a pattern.

That last gate is the one that matters and the one worth not relaxing. Twenty
posts saying the same thing is not twenty pieces of evidence — on X it is
usually one claim and nineteen quote-tweets of it.

Needs XAI_API_KEY. Without it this is simply inactive and community search
falls back to forums alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from app.config import settings
from app.tools.credibility import Anecdote

API_URL = "https://api.x.ai/v1/chat/completions"

#: Grok's smallest model. This call retrieves and quotes; it is not asked to
#: reason about immigration, which is the agent's job with what comes back.
MODEL = "grok-4-fast"

#: Posts to pull. Wider than the handful kept, because the credibility filter
#: discards most of what X returns and the corroboration gate needs enough
#: distinct authors left over to be meaningful.
MAX_POSTS = 20

#: Immigration chatter goes stale fast — a consulate's behaviour this month
#: says little about last year. Tighter than the forum default for the same
#: reason X is valuable: it is a real-time signal or it is nothing.
DEFAULT_WINDOW_DAYS = 120

_INSTRUCTION = (
    "Report what people on X are saying about this US immigration question. "
    "For each relevant post give the author handle, the date, and what they "
    "actually said, as a quote where possible. Do not summarise across posts, "
    "do not give advice, and do not add your own view. If people disagree, "
    "report the disagreement. Return only posts that speak to the question."
)


def available() -> bool:
    return bool(settings.xai_api_key)


async def search(
    query: str, *, window_days: int = DEFAULT_WINDOW_DAYS, timeout_secs: float = 12.0
) -> list[Anecdote]:
    """Return X posts as anecdotes, for the credibility filter to judge.

    Deliberately returns Anecdote rather than anything richer: everything
    downstream then treats X exactly as it treats a forum post, which is the
    only safe default for a source this noisy.
    """
    if not available():
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat()
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": _INSTRUCTION},
            {"role": "user", "content": query},
        ],
        "search_parameters": {
            "mode": "on",
            "sources": [{"type": "x"}],
            "max_search_results": MAX_POSTS,
            "from_date": since,
            "return_citations": True,
        },
        "temperature": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {settings.xai_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as error:  # noqa: BLE001 - optional source, never fatal
        logger.warning(f"x search failed: {type(error).__name__}: {str(error)[:120]}")
        return []

    choices = data.get("choices") or []
    text = ""
    if choices:
        text = (choices[0].get("message") or {}).get("content", "") or ""

    citations = data.get("citations") or []
    if not text.strip():
        return []

    # Grok returns prose plus a citation list rather than structured posts, so
    # each cited post becomes one anecdote carrying the reported text. The
    # credibility filter scores that text for firsthand detail and
    # solicitation, exactly as it does a Reddit body.
    anecdotes = [
        Anecdote(text=text[:1500], url=url, published=None, author=_handle_from(url))
        for url in citations[:MAX_POSTS]
        if isinstance(url, str) and "x.com" in url or "twitter.com" in str(url)
    ]
    logger.info(f"x search '{query[:50]}' -> {len(anecdotes)} cited posts")
    return anecdotes


def _handle_from(url: str) -> str | None:
    """Pull the author handle out of a post URL.

    The corroboration gate counts distinct authors, so without this every post
    looks like it came from the same person and nothing ever corroborates.
    """
    try:
        parts = [p for p in url.split("/") if p]
        # https://x.com/<handle>/status/<id>
        if "status" in parts:
            return parts[parts.index("status") - 1]
    except (ValueError, IndexError):
        pass
    return None
