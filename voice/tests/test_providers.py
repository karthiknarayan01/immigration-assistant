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
