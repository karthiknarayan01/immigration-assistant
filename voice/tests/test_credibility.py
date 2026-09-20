from datetime import datetime, timedelta, timezone

from app.tools.credibility import (
    Anecdote,
    CORROBORATION_THRESHOLD,
    credibility_score,
    filter_anecdotes,
    is_fresh,
)
from app.tools.sources import SourceTier, classify

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _post(text, *, days_old=10, author="u1"):
    return Anecdote(
        text=text, url="https://reddit.com/r/immigration/x",
        published=NOW - timedelta(days=days_old), author=author,
    )


# ── source tiering ───────────────────────────────────────────────────────────

def test_government_domains_are_authoritative():
    assert classify("https://www.uscis.gov/policy-manual") is SourceTier.AUTHORITATIVE
    assert classify("https://travel.state.gov/visa-bulletin") is SourceTier.AUTHORITATIVE
    assert classify("https://www.ecfr.gov/title-8") is SourceTier.AUTHORITATIVE


def test_subdomain_does_not_collapse_to_wrong_tier():
    # travel.state.gov must not be reduced to state.gov by naive suffixing.
    assert classify("https://travel.state.gov/content/visa.html") is SourceTier.AUTHORITATIVE


def test_forums_are_anecdotal():
    assert classify("https://www.reddit.com/r/h1b/comments/abc") is SourceTier.ANECDOTAL
    assert classify("https://x.com/someone/status/1") is SourceTier.ANECDOTAL


def test_unknown_domain_is_not_promoted():
    # An unrecognised blog must never be treated as trustworthy by default.
    assert classify("https://random-visa-blog.example/post") is SourceTier.UNKNOWN


# ── recency ──────────────────────────────────────────────────────────────────

def test_stale_processing_time_reports_are_rejected():
    assert not is_fresh(_post("took 4 months", days_old=120), "processing_times", now=NOW)
    assert is_fresh(_post("took 4 months", days_old=30), "processing_times", now=NOW)


def test_undated_timeline_claims_are_still_rejected():
    # A stale number is a false fact, not merely an old story.
    item = Anecdote(text="approved in 2 weeks", url="u", published=None)
    assert not is_fresh(item, "processing_times", now=NOW)


def test_undated_stories_are_allowed_but_only_outside_timelines():
    # Providers almost never date forum posts, and rejecting them all left
    # community search returning nothing. A story stays true when it is old;
    # the agent is required to say the date is unknown.
    item = Anecdote(text="my RFE asked for a detailed job description", url="u", published=None)
    assert is_fresh(item, "enforcement", now=NOW)
    assert is_fresh(item, "policy", now=NOW)
    assert is_fresh(item, "procedure", now=NOW)


def test_procedure_tolerates_older_posts_than_processing_times():
    old = _post("you file I-765 with the I-485", days_old=365)
    assert is_fresh(old, "procedure", now=NOW)
    assert not is_fresh(old, "processing_times", now=NOW)


# ── credibility scoring ──────────────────────────────────────────────────────

def test_firsthand_with_specifics_scores_high():
    s = credibility_score(_post(
        "I filed my I-129 on 03/14/2026 at the Vermont Service Center and got "
        "approved in about 21 days with premium processing."
    ))
    assert s >= 0.8


def test_hearsay_scores_low():
    s = credibility_score(_post(
        "I heard from my friend's cousin that apparently they are denying "
        "everyone at that service center right now, people say it is hopeless."
    ))
    assert s < 0.5


def test_solicitation_spam_is_rejected():
    s = credibility_score(_post(
        "We offer guaranteed approval for your green card application! "
        "100% success rate, DM me or contact us at our website today."
    ))
    assert s < 0.5


# ── corroboration ────────────────────────────────────────────────────────────

def test_single_report_is_not_a_pattern():
    good = ("I filed my I-129 on 03/14/2026 at the Vermont Service Center "
            "and it was approved in 21 days.")
    kept, corroborated = filter_anecdotes([_post(good)], "processing_times", now=NOW)
    assert kept and not corroborated


def test_repeated_posts_by_one_author_are_not_corroboration():
    good = ("I filed my I-129 on 03/14/2026 at the Vermont Service Center "
            "and it was approved in 21 days.")
    items = [_post(good, author="same") for _ in range(4)]
    kept, corroborated = filter_anecdotes(items, "processing_times", now=NOW)
    assert len(kept) == 4
    assert not corroborated, "one prolific poster is one opinion, not a trend"


def test_independent_reports_do_corroborate():
    good = ("I filed my I-129 on 03/14/2026 at the Vermont Service Center "
            "and it was approved in 21 days.")
    items = [_post(good, author=f"u{i}") for i in range(CORROBORATION_THRESHOLD)]
    _, corroborated = filter_anecdotes(items, "processing_times", now=NOW)
    assert corroborated


def test_spam_is_dropped_before_counting_corroboration():
    good = ("I filed my I-129 on 03/14/2026 at the Vermont Service Center "
            "and it was approved in 21 days.")
    spam = ("Guaranteed approval! 100% success rate, DM me now, "
            "contact us at our website for a consultation fee.")
    items = [_post(good, author="u1")] + [_post(spam, author=f"s{i}") for i in range(5)]
    kept, corroborated = filter_anecdotes(items, "processing_times", now=NOW)
    assert len(kept) == 1
    assert not corroborated


def test_scraped_page_furniture_is_rejected():
    # Observed live from Parallel: the scraper returned Reddit's nav chrome
    # instead of the post. Read aloud it is worse than no story.
    s = credibility_score(_post(
        "Skip to main content Open menu Open navigation Go to Reddit Home "
        "Sign Up Sign up for Reddit Log In Log in to Reddit"
    ))
    assert s < 0.5
