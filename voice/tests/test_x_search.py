"""X is the fastest source available and the least trustworthy.

These pin the properties that make it safe to use at all: it degrades to
nothing without a key, its posts are counted per author rather than per post,
and it goes through the same gates as any forum rather than becoming context
the model reads as background.
"""

from datetime import datetime, timezone

from app.tools import x_search
from app.tools.credibility import Anecdote, filter_anecdotes


def test_inactive_without_a_key(monkeypatch):
    monkeypatch.setattr(x_search.settings, "xai_api_key", "")
    assert x_search.available() is False


async def test_search_returns_nothing_without_a_key(monkeypatch):
    """Absent key must be silence, not an exception — it is an optional source."""
    monkeypatch.setattr(x_search.settings, "xai_api_key", "")
    assert await x_search.search("H-1B RFE wave") == []


def test_handle_is_extracted_so_authors_can_be_counted():
    """The corroboration gate counts distinct authors.

    Without a handle every post looks like one person and nothing can ever
    corroborate, which would silently disable the gate rather than trip it.
    """
    assert x_search._handle_from("https://x.com/some_attorney/status/1234567890") == "some_attorney"
    assert x_search._handle_from("https://twitter.com/another_one/status/42") == "another_one"
    assert x_search._handle_from("https://x.com/nothing-here") is None


def test_one_loud_account_is_not_a_pattern():
    """Twenty posts from one handle is one opinion, not twenty data points.

    This is the failure mode X invites: a claim plus nineteen quote-tweets
    looks like overwhelming consensus to anything counting posts.
    """
    now = datetime.now(timezone.utc)
    posts = [
        Anecdote(
            text=(
                "I filed my I-539 in March and got my receipt in two weeks, "
                "then the RFE came in June asking for the same documents."
            ),
            url=f"https://x.com/one_account/status/{i}",
            published=now,
            author="one_account",
        )
        for i in range(20)
    ]
    _, corroborated = filter_anecdotes(posts, "policy", now=now)
    assert corroborated is False, "one account must never corroborate itself"


def test_three_independent_accounts_can_be_a_pattern():
    now = datetime.now(timezone.utc)
    posts = [
        Anecdote(
            text=(
                "I filed my I-539 in March, got the receipt in two weeks, and "
                "the RFE arrived in June asking for the same documents again."
            ),
            url=f"https://x.com/{handle}/status/1",
            published=now,
            author=handle,
        )
        for handle in ("first_person", "second_person", "third_person")
    ]
    kept, corroborated = filter_anecdotes(posts, "policy", now=now)
    assert kept, "firsthand accounts with specifics should survive the filter"
    assert corroborated is True
