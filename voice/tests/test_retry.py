"""Retry policy: retry only what a retry can fix, and only within budget.

The cases worth pinning down are the ones where retrying is wrong — because
those are the ones that turn a fast failure into a slow one, or a real bug
into three stack traces.
"""

import time

import httpx
import pytest

from app.failures import FailureKind, classify_exception, classify_status_detail
from app.retry import with_retry


def status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/x")
    return httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(code, request=request)
    )


class ClientPayloadError(Exception):
    """Stands in for aiohttp's error, which the streaming path raises."""


def test_rate_limit_and_no_credit_are_told_apart():
    """Both read as FUNDS to a user; only one is worth retrying.

    Collapsing them is how you end up either retrying something hopeless or
    giving up on something that would have worked.
    """
    rate_limited = classify_status_detail(429)
    no_credit = classify_status_detail(402)

    assert rate_limited.kind is FailureKind.FUNDS
    assert no_credit.kind is FailureKind.FUNDS
    assert rate_limited.retryable is True
    assert no_credit.retryable is False


def test_auth_failures_are_never_retried():
    for code in (401, 403):
        failure = classify_status_detail(code)
        assert failure.kind is FailureKind.AUTH
        assert failure.retryable is False


def test_server_errors_are_retryable():
    assert classify_status_detail(503).retryable is True
    assert classify_status_detail(500).kind is FailureKind.CONNECTIVITY


def test_transport_errors_are_retryable_whichever_library_raised_them():
    """Search runs on httpx, streaming runs on aiohttp.

    An httpx-only check let aiohttp's ClientPayloadError through as if it
    were a model failure, and a dropped stream was scored as an unsafe answer.
    """
    assert classify_exception(httpx.RemoteProtocolError("dropped")).retryable is True
    assert classify_exception(ClientPayloadError("stream died")).retryable is True
    assert classify_exception(ClientPayloadError("x")).kind is FailureKind.CONNECTIVITY


def test_unknown_errors_are_not_retried():
    """Retrying a real bug just produces the same stack trace more slowly."""
    failure = classify_exception(ValueError("index out of range"))
    assert failure.kind is FailureKind.OTHER
    assert failure.retryable is False


async def test_transient_failure_recovers():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return "ok"

    assert await with_retry(flaky, what="test") == "ok"
    assert calls["n"] == 2


async def test_auth_failure_fails_immediately():
    calls = {"n": 0}

    async def denied():
        calls["n"] += 1
        raise status_error(403)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(denied, what="test")
    assert calls["n"] == 1, "an auth failure must not be retried"


async def test_no_retry_once_the_budget_is_gone():
    """Transient, but there is no time left for a retry to help."""
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        await with_retry(flaky, what="test", deadline=time.monotonic() + 0.01)
    assert calls["n"] == 1


async def test_retries_are_bounded():
    calls = {"n": 0}

    async def always_fails():
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        await with_retry(always_fails, what="test", max_retries=2, backoff_secs=0.01)
    assert calls["n"] == 3  # the first attempt plus two retries
