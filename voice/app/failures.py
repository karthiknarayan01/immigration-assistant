"""Classifying failures so the user hears something useful instead of silence.

Two very different cases:

* The model itself fails (quota exhausted, billing disabled). Nothing can
  generate speech, so the session is over — the user must be told plainly.
* A tool fails. Whether that matters depends on the tool. Losing official
  guidance means the answer may be wrong and the user should know; losing
  community anecdotes just means a less colourful answer.
"""

from __future__ import annotations

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
