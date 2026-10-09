"""The dimensions eval cases are stratified across.

Coverage is not "one question per page of policy". That produces thousands of
near-duplicate cases measuring the same handful of behaviours, costs real money
to run, and reads as rigour while the failure modes that actually break the
product go untested.

What a useful eval set samples is a grid: task x domain x difficulty x failure
mode. Every cell that matters should have cases, and generation should be
stratified across the grid rather than drawn from wherever the source material
happened to be dense.

This module is that grid. It is used to:
  * check that the written cases cover the cells they claim to,
  * stratify generated questions so one long CFR part does not dominate,
  * report coverage, so a gap is visible rather than found in production.
"""

from __future__ import annotations

import re
from collections import Counter

#: Visa/immigration domains the product answers about.
DOMAINS = (
    "h1b",
    "f1_opt",
    "b1_b2",
    "l1",
    "h4",
    "j1",
    "eb1",
    "eb2",
    "eb3",
    "eb5",
    "perm",
    "niw",
    "family",
    "naturalization",
    "asylum",
    "general",
)

#: What kind of question it is — the three jobs, plus the cross-cutting ones.
TASKS = (
    "factual",
    "procedural",
    "recent",
    "reasoning",
    "safety",
    "scope",
    "honesty",
    "clarification",
)

DIFFICULTY = ("single_fact", "multi_condition", "conflicting_source", "ambiguous")

#: The specific ways an answer can be wrong. Cases are written to catch these,
#: so their distribution matters more than the question count.
FAILURE_MODES = (
    "hallucinated_number",   # states an unlooked-up fee, deadline or day count
    "wrong_tool",            # never looked it up at all
    "no_escalation",         # high-stakes question with no attorney referral
    "over_refusal",          # refuses something it should answer
    "ungrounded_claim",      # asserts without a cited source
    "stale_source",          # relies on superseded guidance
    "premise_error",         # accepts a false or outdated premise
    "out_of_scope_answer",   # answers something outside US immigration
    "injection_followed",    # obeys instructions embedded in retrieved content
)

#: Keyword map from a question to the domain it is about. Ordered longest-match
#: first, because "EB-5" contains no "EB-2" but "H-1B" and "B-1" overlap, and a
#: question mentioning both H-1B and OPT should be filed under the one that
#: leads. Unmatched questions are `general`, which is honest: not every
#: immigration question is about a named category.
_DOMAIN_PATTERNS = (
    ("eb5", r"eb-?5|regional center"),
    ("eb1", r"eb-?1"),
    ("eb2", r"eb-?2|priority date"),
    ("eb3", r"eb-?3"),
    ("perm", r"\bperm\b|labor certification"),
    ("niw", r"\bniw\b|national interest waiver"),
    ("h1b", r"h-?1b|specialty occupation|i-129|cap-?gap|premium processing|60-day grace"),
    ("f1_opt", r"\bopt\b|f-?1\b|stem|student|practical training"),
    ("b1_b2", r"b-?1|b-?2|visitor|tourist|i-94"),
    ("l1", r"\bl-?1\b"),
    ("h4", r"h-?4"),
    ("j1", r"\bj-?1\b|exchange visitor"),
    ("family", r"i-130|spouse|marriage|family|fianc|stepchild"),
    ("naturalization", r"naturaliz|n-?400|citizenship|civics"),
    ("asylum", r"asylum|refugee|\btps\b"),
)


def domain_of(text: str) -> str:
    """Which visa domain a question is about, or `general`."""
    lowered = (text or "").lower()
    for domain, pattern in _DOMAIN_PATTERNS:
        if re.search(pattern, lowered):
            return domain
    return "general"


#: Cells that matter enough that an empty one is a real gap. Deliberately
#: narrow: demanding every combination would push toward padding, which is the
#: failure this whole approach exists to avoid.
REQUIRED_FAILURE_MODES = (
    "hallucinated_number",
    "wrong_tool",
    "no_escalation",
    "premise_error",
    "out_of_scope_answer",
    "injection_followed",
    "stale_source",
    "ungrounded_claim",
)


def _field(case: dict, name: str, default: str = "unset") -> str:
    return str(case.get(name) or default)


def coverage(cases: list[dict]) -> dict:
    """Count cases per cell, so a gap is visible rather than assumed."""
    return {
        "total": len(cases),
        "by_task": dict(Counter(_field(c, "category") for c in cases)),
        "by_domain": dict(Counter(_field(c, "domain") for c in cases)),
        "by_difficulty": dict(Counter(_field(c, "difficulty") for c in cases)),
        "by_failure_mode": dict(Counter(_field(c, "failure_mode") for c in cases)),
        "with_trajectory_assertion": sum(1 for c in cases if c.get("expects_tools")),
        "multi_turn": sum(1 for c in cases if c.get("history")),
        "untagged": sum(
            1 for c in cases
            if _field(c, "domain") == "unset" or _field(c, "failure_mode") == "unset"
        ),
    }


def gaps(cases: list[dict]) -> list[str]:
    """Name the required cells with no cases in them."""
    seen_modes = {_field(c, "failure_mode") for c in cases}
    seen_domains = {_field(c, "domain") for c in cases}
    missing = [f"failure_mode:{m}" for m in REQUIRED_FAILURE_MODES if m not in seen_modes]
    # `h1b` and `general` are covered by the existing sets; the rest are the
    # ones the app claims to answer about, so each should appear somewhere.
    for domain in ("f1_opt", "b1_b2", "l1", "eb1", "eb2", "eb5"):
        if domain not in seen_domains:
            missing.append(f"domain:{domain}")
    return missing
