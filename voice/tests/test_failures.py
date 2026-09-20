import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.failures import FailureKind, classify_error_frame, classify_status, session_failure_message
from app.tools import providers
from app.tools.registry import search_community_experiences, search_official_guidance


def _frame(error="", category=None):
    return SimpleNamespace(error=error, category=category)


# ── HTTP status classification ───────────────────────────────────────────────

def test_payment_and_rate_limit_are_funds_problems():
    assert classify_status(402) is FailureKind.FUNDS
    assert classify_status(429) is FailureKind.FUNDS


def test_credential_problems_are_auth():
    assert classify_status(401) is FailureKind.AUTH
    assert classify_status(403) is FailureKind.AUTH


def test_upstream_outage_is_connectivity():
    assert classify_status(503) is FailureKind.CONNECTIVITY


# ── error frame classification ───────────────────────────────────────────────

def test_structured_quota_category_wins():
    assert classify_error_frame(_frame(category=SimpleNamespace(name="QUOTA"))) is FailureKind.FUNDS


def test_falls_back_to_message_when_category_missing():
    # Providers word these inconsistently and don't always set a category.
    assert classify_error_frame(_frame("RESOURCE_EXHAUSTED: quota exceeded")) is FailureKind.FUNDS
    assert classify_error_frame(_frame("Billing account not configured")) is FailureKind.FUNDS
    assert classify_error_frame(_frame("401 Unauthorized")) is FailureKind.AUTH


def test_unknown_errors_do_not_claim_a_billing_problem():
    # Telling someone we are out of money when we are not is its own failure.
    assert classify_error_frame(_frame("unexpected end of stream")) is FailureKind.OTHER


def test_every_kind_has_a_user_facing_message():
    for kind in FailureKind:
        message = session_failure_message(kind)
        assert message and not message.endswith("None")


def test_funds_message_does_not_blame_the_user():
    message = session_failure_message(FailureKind.FUNDS).lower()
    assert "your" not in message
    assert "credit" in message


# ── tool criticality ─────────────────────────────────────────────────────────

def _call(handler, **arguments):
    captured = {}

    async def result_callback(value):
        captured["result"] = value

    asyncio.run(handler(SimpleNamespace(arguments=arguments, result_callback=result_callback)))
    return captured["result"]


@pytest.fixture
def billing_failure(monkeypatch):
    monkeypatch.setattr(providers, "available_providers", lambda: ["tavily"])

    async def failing_search(*args, **kwargs):
        providers._record_failure(
            httpx.HTTPStatusError(
                "payment required",
                request=httpx.Request("POST", "https://api.tavily.com/search"),
                response=httpx.Response(402),
            )
        )
        return []

    monkeypatch.setattr(providers, "search", failing_search)
    monkeypatch.setattr(providers, "search_groups", failing_search)
    monkeypatch.setattr(providers, "search_community", failing_search)
    yield
    providers.take_last_failure()


def test_official_search_surfaces_a_billing_failure(billing_failure):
    # Load-bearing tool: the user must learn that current policy is unverified.
    result = _call(search_official_guidance, query="H-1B premium processing")
    assert result["unavailable"] is True
    assert result["reason"] == "funds"
    assert "out of date" in result["message"]


def test_community_search_degrades_quietly_on_billing_failure(billing_failure):
    # Optional tool: losing anecdotes costs colour, not correctness, so it
    # must not interrupt the answer with an account problem.
    result = _call(search_community_experiences, query="RFE stories", topic="policy")
    assert "unavailable" not in result
    assert result["reports"] == []
