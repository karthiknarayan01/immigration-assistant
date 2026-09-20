"""Filtering for anecdotal (Tier C) sources.

Immigration forums contain a lot of confidently wrong advice, and acting on it
can cost someone their status. Nothing here tries to decide whether a claim is
true — that is not knowable from a forum post. It decides whether a claim is
worth repeating *as an anecdote*, and drops the rest.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re

#: A pattern is only mentioned if this many independent people report it.
#: One person's bad month is not a trend, and repeating it as one is harmful.
CORROBORATION_THRESHOLD = 3

#: How stale an anecdote may be, by topic. Immigration practice shifts fast:
#: a processing-time report from last year actively misleads.
MAX_AGE_DAYS = {
    "processing_times": 90,
    "policy": 180,
    "enforcement": 180,
    "procedure": 730,
}
DEFAULT_MAX_AGE_DAYS = 180

#: Below this, an anecdote is dropped rather than repeated.
MIN_CREDIBILITY = 0.5

_FIRSTHAND = re.compile(
    r"\b(i|my|we|our)\b.{0,40}\b(filed|applied|received|got|interview|approved|denied|rfe)\b",
    re.IGNORECASE | re.DOTALL,
)
_HEARSAY = re.compile(
    r"\b(i heard|someone said|my friend'?s? (cousin|friend)|apparently|rumor|people say)\b",
    re.IGNORECASE,
)
_SPECIFICS = re.compile(
    r"\b(\d{1,2}/\d{1,2}/\d{2,4}"          # a date
    r"|[A-Z]{3}\d{10}"                      # a receipt number
    r"|(vermont|nebraska|texas|california|potomac)\s+service\s+center"
    r"|I-\d{3}[A-Z]?"                       # a form number
    r"|\d+\s*(days?|weeks?|months?))\b",
    re.IGNORECASE,
)
_SOLICITATION = re.compile(
    r"\b(dm me|whatsapp|consultation fee|guaranteed approval|100% success"
    r"|contact us at|visit our website|click here|telegram)\b",
    re.IGNORECASE,
)


@dataclass
class Anecdote:
    text: str
    url: str
    published: datetime | None = None
    author: str | None = None
    score: float = field(default=0.0, init=False)


def _topic_max_age(topic: str) -> int:
    return MAX_AGE_DAYS.get(topic, DEFAULT_MAX_AGE_DAYS)


#: Topics where an undated anecdote is still usable. General web search
#: providers rarely return dates for forum posts, so rejecting every undated
#: item silently disables community search entirely. Procedural mechanics
#: ("which form goes with which") change slowly enough that an undated post
#: is acceptable; timelines and enforcement patterns are not — a stale report
#: there is worse than no report. A Reddit-native provider with real date
#: filtering (e.g. Parallel) would remove this compromise.
UNDATED_OK_TOPICS = frozenset({"procedure"})


def is_fresh(item: Anecdote, topic: str, *, now: datetime | None = None) -> bool:
    """Reject anecdotes old enough to be actively misleading."""
    if item.published is None:
        return topic in UNDATED_OK_TOPICS
    now = now or datetime.now(timezone.utc)
    published = item.published
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return (now - published) <= timedelta(days=_topic_max_age(topic))


def credibility_score(item: Anecdote) -> float:
    """Heuristic 0-1 score for whether an anecdote is worth repeating.

    Deliberately cheap and deterministic: it runs inside a voice turn, where
    an extra model call would cost more latency than it is worth. An LLM judge
    belongs in the offline eval pipeline, not here.
    """
    score = 0.5

    if _FIRSTHAND.search(item.text):
        score += 0.25
    if _HEARSAY.search(item.text):
        score -= 0.35
    if _SPECIFICS.search(item.text):
        score += 0.2
    if _SOLICITATION.search(item.text):
        # Visa-mill spam is the most dangerous content on these forums.
        score -= 0.6
    if len(item.text.strip()) < 80:
        # Too short to contain a checkable situation.
        score -= 0.15

    return max(0.0, min(1.0, score))


def filter_anecdotes(
    items: list[Anecdote], topic: str, *, now: datetime | None = None
) -> tuple[list[Anecdote], bool]:
    """Apply recency, credibility, and corroboration gates.

    Returns the surviving anecdotes and whether they corroborate each other
    well enough to describe as a pattern. Callers must not present anecdotes
    as a trend when the flag is False.
    """
    fresh = [i for i in items if is_fresh(i, topic, now=now)]

    kept: list[Anecdote] = []
    for item in fresh:
        item.score = credibility_score(item)
        if item.score >= MIN_CREDIBILITY:
            kept.append(item)

    # Several posts by one prolific commenter are one opinion, not a pattern.
    distinct_authors = {i.author for i in kept if i.author}
    unattributed = sum(1 for i in kept if not i.author)
    independent = len(distinct_authors) + unattributed

    kept.sort(key=lambda i: i.score, reverse=True)
    return kept, independent >= CORROBORATION_THRESHOLD
