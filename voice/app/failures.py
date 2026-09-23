"""Classifying failures so the user hears something useful instead of silence.

Two very different cases:

* The model itself fails (quota exhausted, billing disabled). Nothing can
  generate speech, so the session is over — the user must be told plainly.
* A tool fails. Whether that matters depends on the tool. Losing official
  guidance means the answer may be wrong and the user should know; losing
  community anecdotes just means a less colourful answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pipecat.frames.frames import ErrorFrame


class FailureKind(str, Enum):
    #: Out of credit, over quota, or rate limited — an account problem.
    FUNDS = "funds"
    #: Bad or missing credentials.
    AUTH = "auth"
    #: Network or upstream outage.
    CONNECTIVITY = "connectivity"
    OTHER = "other"


#: Substrings that indicate an account/billing problem when no structured
#: category is available. Providers word these inconsistently.
_FUNDS_HINTS = (
    "quota",
    "insufficient",
    "billing",
    "payment",
    "credit",
    "exceeded",
    "resource_exhausted",
    "rate limit",
    "429",
)

_AUTH_HINTS = ("unauthorized", "unauthenticated", "api key", "permission", "forbidden", "401", "403")


def classify_status(status_code: int) -> FailureKind:
    """Map an HTTP status from a search provider to a failure kind."""
    if status_code in (402, 429):
        return FailureKind.FUNDS
    if status_code in (401, 403):
        return FailureKind.AUTH
    if status_code >= 500:
        return FailureKind.CONNECTIVITY
    return FailureKind.OTHER


@dataclass(frozen=True)
class Failure:
    """What went wrong, and whether trying again could help.

    Kind and retryability are deliberately separate. Kind answers "what do we
    tell the user"; retryable answers "should we try again". Those have
    different answers for the same status: 429 and 402 both read as FUNDS to
    a person — the service is over its limit — but a 429 usually succeeds on
    retry and a 402 never will. Collapsing them means either retrying
    something hopeless or giving up on something transient.
    """

    kind: FailureKind
    retryable: bool
    detail: str = ""


#: Transport failures, matched by class name so this stays independent of
#: which HTTP library a given path uses — streaming model calls run on
#: aiohttp while search runs on httpx, and an earlier httpx-only check let
#: aiohttp's ClientPayloadError through as if it were a model failure.
_TRANSPORT_ERROR_NAMES = frozenset({
    "ClientPayloadError",
    "ClientConnectorError",
    "ClientOSError",
    "ServerDisconnectedError",
    "ServerTimeoutError",
    "RemoteProtocolError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "ReadError",
    "WriteError",
    "PoolTimeout",
    "IncompleteRead",
    "ConnectionResetError",
    "TimeoutError",
})

#: Upstream busy or briefly broken, rather than refusing us outright.
_TRANSIENT_HINTS = ("resource_exhausted", "rate limit", "429", "503", "unavailable", "timeout")


def classify_status_detail(status_code: int) -> Failure:
    """Classify an HTTP status, keeping retryability separate from kind."""
    if status_code == 429:
        # Over the rate limit, not out of money. Backing off is the fix.
        return Failure(FailureKind.FUNDS, retryable=True, detail=f"HTTP {status_code}")
    if status_code == 402:
        return Failure(FailureKind.FUNDS, retryable=False, detail=f"HTTP {status_code}")
    if status_code in (401, 403):
        return Failure(FailureKind.AUTH, retryable=False, detail=f"HTTP {status_code}")
    if status_code >= 500:
        return Failure(FailureKind.CONNECTIVITY, retryable=True, detail=f"HTTP {status_code}")
    return Failure(FailureKind.OTHER, retryable=False, detail=f"HTTP {status_code}")


def classify_exception(error: BaseException) -> Failure:
    """Classify any exception raised by an outbound call.

    Unknown errors are deliberately NOT retryable. Retrying a genuine bug
    turns one stack trace into several, and delays the failure the user is
    waiting on without ever succeeding.
    """
    status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, int):
        return classify_status_detail(status)

    if type(error).__name__ in _TRANSPORT_ERROR_NAMES:
        return Failure(FailureKind.CONNECTIVITY, retryable=True, detail=type(error).__name__)

    message = str(error).lower()
    if any(hint in message for hint in _AUTH_HINTS):
        return Failure(FailureKind.AUTH, retryable=False, detail=type(error).__name__)
    # Checked before the broader funds hints: "quota exceeded" is permanent,
    # "rate limit" is not, and they share vocabulary.
    if any(hint in message for hint in _TRANSIENT_HINTS):
        return Failure(FailureKind.FUNDS, retryable=True, detail=type(error).__name__)
    if any(hint in message for hint in _FUNDS_HINTS):
        return Failure(FailureKind.FUNDS, retryable=False, detail=type(error).__name__)
    return Failure(FailureKind.OTHER, retryable=False, detail=type(error).__name__)


def classify_error_frame(frame: ErrorFrame) -> FailureKind:
    """Classify a pipeline ErrorFrame, preferring its structured category."""
    category = getattr(frame, "category", None)
    name = getattr(category, "name", "") or str(category or "")
    name = name.upper()
    if name in ("QUOTA", "RATE_LIMIT"):
        return FailureKind.FUNDS
    if name in ("AUTHENTICATION", "AUTHORIZATION"):
        return FailureKind.AUTH
    if name == "CONNECTIVITY":
        return FailureKind.CONNECTIVITY

    # Fall back to the message: the category is not always populated.
    message = (getattr(frame, "error", "") or "").lower()
    if any(hint in message for hint in _FUNDS_HINTS):
        return FailureKind.FUNDS
    if any(hint in message for hint in _AUTH_HINTS):
        return FailureKind.AUTH
    return FailureKind.OTHER


#: Shown to the user when the model itself cannot run. Deliberately plain:
#: they cannot fix it, so it should not sound like their fault or their
#: network, and it should not pretend the assistant is still listening.
SESSION_FAILURE_MESSAGES = {
    FailureKind.FUNDS: (
        "Sorry — I can't answer right now because this service has run out of "
        "credit. Please try again later."
    ),
    FailureKind.AUTH: (
        "Sorry — I can't answer right now because of a configuration problem "
        "on our side. Please try again later."
    ),
    FailureKind.CONNECTIVITY: (
        "Sorry — I lost connection to the assistant. Please try again in a moment."
    ),
    FailureKind.OTHER: (
        "Sorry — something went wrong and I can't continue this conversation. "
        "Please try again."
    ),
}


def session_failure_message(kind: FailureKind) -> str:
    return SESSION_FAILURE_MESSAGES.get(kind, SESSION_FAILURE_MESSAGES[FailureKind.OTHER])
