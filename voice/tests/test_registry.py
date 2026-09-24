import asyncio
from types import SimpleNamespace

from app.tools import providers
from app.tools.providers import SearchHit, _canonical
from app.tools.registry import search_community_experiences, search_official_guidance
from app.tools.sources import SourceTier


def _params(**arguments):
    captured = {}

    async def result_callback(value):
        captured["result"] = value

    return SimpleNamespace(arguments=arguments, result_callback=result_callback), captured


def test_official_search_uses_the_web_when_providers_are_unconfigured(monkeypatch):
    """No provider keys is no longer a dead end.

    Google grounding bills to Vertex and needs no third-party subscription, so
    an exhausted or missing search plan now degrades to a wider web search
    rather than to an apology.
    """
    from app.tools import google_search

    monkeypatch.setattr(providers, "available_providers", lambda: [])

    async def fake_search(query, **kwargs):
        return google_search.GroundedAnswer(text="Premium processing is 15 days.", sources=[])

    monkeypatch.setattr(google_search, "search", fake_search)
    params, captured = _params(query="H-1B premium processing time")
    asyncio.run(search_official_guidance(params))

    assert "summary" in captured["result"]
    # The model must be told to attribute this rather than assert it.
    assert "could not confirm" in captured["result"]["guidance"]


def test_official_search_admits_it_could_not_check_when_everything_fails(monkeypatch):
    """The guarantee the previous test protected, one layer further down.

    With nothing reachable at all, the agent must say so rather than quietly
    answering current policy from memory.
    """
    from app.tools import google_search

    monkeypatch.setattr(providers, "available_providers", lambda: [])

    async def nothing(query, **kwargs):
        return google_search.GroundedAnswer(text="", sources=[])

    monkeypatch.setattr(google_search, "search", nothing)
    params, captured = _params(query="H-1B premium processing time")
    asyncio.run(search_official_guidance(params))

    result = captured["result"]
    assert result["count"] == 0
    assert "rather than answering from memory" in result["guidance"]


def test_community_search_refuses_to_guess_when_unconfigured(monkeypatch):
    monkeypatch.setattr(providers, "available_providers", lambda: [])
    params, captured = _params(query="RFE rates lately")
    asyncio.run(search_community_experiences(params))
    assert captured["result"]["unavailable"] is True


def test_empty_query_is_rejected():
    params, captured = _params(query="   ")
    asyncio.run(search_official_guidance(params))
    assert "error" in captured["result"]


def test_official_search_drops_forum_results(monkeypatch):
    # Even when a forum post ranks well, it must not surface as official guidance.
    monkeypatch.setattr(providers, "available_providers", lambda: ["tavily"])

    async def fake_search(query, **kwargs):
        return [
            SearchHit("USCIS page", "https://uscis.gov/a", "official text",
                      SourceTier.AUTHORITATIVE),
            SearchHit("Reddit thread", "https://reddit.com/r/h1b/b", "someone's opinion",
                      SourceTier.ANECDOTAL),
            SearchHit("Random blog", "https://visa-blog.example/c", "unverified",
                      SourceTier.UNKNOWN),
        ]

    monkeypatch.setattr(providers, "search", fake_search)
    params, captured = _params(query="H-1B cap")
    asyncio.run(search_official_guidance(params))

    urls = [r["url"] for r in captured["result"]["results"]]
    assert urls == ["https://uscis.gov/a"]


def test_uncorroborated_reports_are_labelled_as_such(monkeypatch):
    monkeypatch.setattr(providers, "available_providers", lambda: ["tavily"])
    from datetime import datetime, timezone

    async def fake_search(query, **kwargs):
        return [
            SearchHit(
                "thread", "https://reddit.com/r/h1b/x",
                "I filed my I-129 on 03/14/2026 at the Vermont Service Center and "
                "it was approved in about 21 days.",
                SourceTier.ANECDOTAL,
                published=datetime.now(timezone.utc), author="only_one",
            )
        ]

    monkeypatch.setattr(providers, "search_community", fake_search)
    params, captured = _params(query="how fast is premium processing", topic="processing_times")
    asyncio.run(search_community_experiences(params))

    result = captured["result"]
    assert result["corroborated"] is False
    assert "too few independent reports" in result["guidance"].lower()


# ── URL canonicalisation (dedup across providers) ────────────────────────────

def test_same_page_from_two_providers_dedupes():
    assert _canonical("https://www.uscis.gov/forms/") == _canonical("https://uscis.gov/forms")


def test_different_pages_do_not_dedupe():
    assert _canonical("https://uscis.gov/a") != _canonical("https://uscis.gov/b")
