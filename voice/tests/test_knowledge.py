"""Regulation lookup: does a real question reach the section that answers it.

These are written as retrieval assertions rather than text assertions — the
CFR gets reworded and renumbered, so pinning exact sentences would make the
suite fail on amendments rather than on bugs. What must hold is that the
question lands in the right part.

Every case here is one the agent previously got wrong in an eval.
"""

import pytest

from app import knowledge

pytestmark = pytest.mark.skipif(
    not knowledge.available(), reason="regulation pack not built in this environment"
)


def parts_for(query: str, limit: int = 3) -> list[str]:
    return [p.citation for p in knowledge.search(query, limit=limit)]


def assert_finds(query: str, *expected_parts: str, within: int = 2):
    found = parts_for(query)
    assert any(
        any(part in citation for part in expected_parts) for citation in found[:within]
    ), f"{query!r} returned {found[:within]}, expected one of {expected_parts}"


def test_finds_opt_unemployment_rules():
    """fact-03: the agent answered 120 days; the regulation says 150."""
    assert_finds("unemployment days post-completion OPT STEM extension", "214")


def test_finds_grace_period_after_employment_ends():
    """reason-02: the answer missed that the 60 days is capped by the I-94."""
    assert_finds("grace period after H-1B employment ends", "214")


def test_finds_conditional_residence_rules():
    """rd-23: 8 CFR 216 was not in the pack at all, so this was unanswerable."""
    assert_finds("conditional permanent resident remove conditions", "216")


def test_finds_revocation_rules_from_everyday_words():
    """rd-23 again, asked the way a person asks it.

    The CFR never says "green card", so without synonym expansion this
    returned Border Crossing Card rules — "revocation" was the only word that
    matched anything.
    """
    assert_finds("can our green card be revoked", "216", "245", "246", "237")


def test_finds_inadmissibility_rules_for_advance_parole():
    """rd-11: the unlawful presence bar is the whole danger in that question."""
    assert_finds("travel on advance parole unlawful presence bar", "212", "245")


def test_finds_change_of_status_rules():
    """rd-14: the employer files an I-129, and work before approval is barred."""
    assert_finds("change status B-1 to L-1 employer petition", "214", "248")


def test_narrow_population_sections_do_not_crowd_out_general_ones():
    """Plain BM25 answered an advance parole question with a Haiti-specific
    provision: it used exactly the right vocabulary and applied to almost
    nobody."""
    found = parts_for("advance parole travel while adjustment pending")
    assert found, "expected some result"
    headings = [p.heading.lower() for p in knowledge.search("advance parole travel", limit=2)]
    assert not any("haitian" in h for h in headings), headings


def test_a_narrow_section_is_still_findable_when_asked_for():
    """The penalty must not bury a section from someone who wants it."""
    passages = knowledge.search("Haitian Refugee Immigration Fairness Act adjustment", limit=5)
    assert passages, "expected the Haitian provisions to remain reachable"


def test_lookup_is_fast_enough_to_skip_filler_audio():
    """The tool is exempt from filler audio on the grounds that it is instant."""
    import time

    started = time.perf_counter()
    knowledge.search("grace period after employment ends")
    assert (time.perf_counter() - started) * 1000 < 50


def test_empty_query_returns_nothing_rather_than_everything():
    assert knowledge.search("") == []
    assert knowledge.search("the and of") == []
