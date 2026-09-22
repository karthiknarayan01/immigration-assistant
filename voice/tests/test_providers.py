from app.tools.providers import (
    MIN_RELEVANCE,
    SearchHit,
    _canonical,
    _rank,
    _rank_relevance,
)
from app.tools.sources import SourceTier


def _hit(url, tier, relevance=0.9):
    return SearchHit(title="t", url=url, text="x", tier=tier, relevance=relevance)


# ── canonicalisation ─────────────────────────────────────────────────────────

def test_html_suffix_does_not_defeat_dedup():
    # Observed live: the same Federal Register page came back both ways and
    # was shown to the model twice.
    a = "https://www.federalregister.gov/documents/2023/12/21/2023-28160/pilot.html"
    b = "https://federalregister.gov/documents/2023/12/21/2023-28160/pilot"
    assert _canonical(a) == _canonical(b)


def test_trailing_slash_and_www_normalise():
    assert _canonical("https://www.uscis.gov/forms/") == _canonical("https://uscis.gov/forms")


def test_distinct_pages_stay_distinct():
    assert _canonical("https://uscis.gov/a") != _canonical("https://uscis.gov/b")


# ── ranking ──────────────────────────────────────────────────────────────────

def test_current_official_page_outranks_everything():
    hits = _rank([
        _hit("https://boundless.com/x", SourceTier.PROFESSIONAL, 0.93),
        _hit("https://uscis.gov/policy-manual/v7", SourceTier.AUTHORITATIVE, 0.80),
    ])
    assert "uscis.gov" in hits[0].url


def test_archived_official_page_loses_to_current_professional_page():
    # The bug this guards: a superseded 2017 USCIS announcement outranking a
    # current law-firm page on "what is the processing time right now".
    hits = _rank([
        _hit("https://uscis.gov/archive/premium-processing-resumed", SourceTier.AUTHORITATIVE, 0.90),
        _hit("https://boundless.com/h-1b-premium-processing-time", SourceTier.PROFESSIONAL, 0.93),
    ])
    assert "boundless.com" in hits[0].url
    assert hits[1].is_archived


def test_archived_still_beats_anecdotal():
    hits = _rank([
        _hit("https://reddit.com/r/h1b/x", SourceTier.ANECDOTAL, 0.99),
        _hit("https://uscis.gov/archive/y", SourceTier.AUTHORITATIVE, 0.60),
    ])
    assert "uscis.gov" in hits[0].url


def test_relevance_breaks_ties_within_a_tier():
    hits = _rank([
        _hit("https://uscis.gov/low", SourceTier.AUTHORITATIVE, 0.50),
        _hit("https://uscis.gov/high", SourceTier.AUTHORITATIVE, 0.95),
    ])
    assert hits[0].url.endswith("/high")


def test_archive_detection():
    assert _hit("https://uscis.gov/archive/thing", SourceTier.AUTHORITATIVE).is_archived
    assert not _hit("https://uscis.gov/policy-manual", SourceTier.AUTHORITATIVE).is_archived


def test_relevance_threshold_is_between_observed_noise_and_signal():
    # Measured live: off-topic results scored <=0.094, correct ones >=0.73.
    assert 0.1 < MIN_RELEVANCE < 0.7


# ── scoreless providers ──────────────────────────────────────────────────────

def test_scoreless_provider_does_not_automatically_outrank_scored_one():
    # Exa returns no score. Without a rank-derived proxy every Exa hit would
    # default to 1.0 and beat every Tavily hit regardless of quality.
    assert _rank_relevance(0) < 1.0


def test_rank_relevance_decreases_and_stays_usable():
    scores = [_rank_relevance(i) for i in range(6)]
    assert scores == sorted(scores, reverse=True)
    # Top results must clear the threshold, or good hits get silently dropped.
    assert scores[0] >= MIN_RELEVANCE
    assert all(s >= MIN_RELEVANCE for s in scores)


def test_rank_relevance_overlaps_observed_tavily_range():
    # Tavily scored real results roughly 0.5-0.93; the proxy should sit in
    # that band so cross-provider merging stays meaningful.
    assert 0.5 <= _rank_relevance(0) <= 0.95


# -- retry policy ------------------------------------------------------
#
# Retries exist to survive a blip, not to turn a failed turn into a slow one.
# The cases that matter are the ones where retrying is the wrong call.

import time as _time

import httpx as _httpx
import pytest as _pytest

from app.tools.providers import (
    MAX_RETRIES,
    RETRY_BACKOFF_SECS,
    _attempt,
    _is_worth_retrying,
)


def _status_error(code: int) -> _httpx.HTTPStatusError:
    request = _httpx.Request("POST", "https://example.test/search")
    response = _httpx.Response(code, request=request)
    return _httpx.HTTPStatusError("boom", request=request, response=response)


def test_only_transient_failures_are_retried():
    assert _is_worth_retrying(_status_error(500))
    assert _is_worth_retrying(_status_error(429))
    assert _is_worth_retrying(_httpx.ConnectTimeout("slow"))
    assert _is_worth_retrying(_httpx.ConnectError("refused"))
    # These return the same answer every time; retrying only costs silence.
    assert not _is_worth_retrying(_status_error(401))
    assert not _is_worth_retrying(_status_error(403))
    assert not _is_worth_retrying(_status_error(402))


async def test_retries_once_then_succeeds():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _httpx.ConnectError("refused")
        return ["hit"]

    result = await _attempt(flaky, deadline=_time.monotonic() + 10, what="test")
    assert result == ["hit"]
    assert calls["n"] == MAX_RETRIES + 1


async def test_does_not_retry_when_the_budget_is_spent():
    """The failure is transient, but there is no time left to try again.

    This is the case an attempt-count retry gets wrong: it would start a
    second call the turn cannot afford to wait for.
    """
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        raise _httpx.ConnectError("refused")

    with _pytest.raises(_httpx.ConnectError):
        await _attempt(
            flaky, deadline=_time.monotonic() + RETRY_BACKOFF_SECS / 2, what="test"
        )
    assert calls["n"] == 1


async def test_auth_failure_is_not_retried_even_with_budget():
    calls = {"n": 0}

    async def denied():
        calls["n"] += 1
        raise _status_error(403)

    with _pytest.raises(_httpx.HTTPStatusError):
        await _attempt(denied, deadline=_time.monotonic() + 30, what="test")
    assert calls["n"] == 1
