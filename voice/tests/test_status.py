"""Status labels are user-facing text built from the model's own tool calls.

Two ways this goes wrong: it leaks something internal (a tool name, a search
operator, a provider), or it stops changing and the UI reads as frozen while
the agent is still working. Both are covered here.
"""

from app.status import (
    DEFAULT_VERB,
    MAX_SUBJECT_CHARS,
    build_label,
    summarise_subject,
)


def test_label_names_the_subject_not_the_tool():
    label = build_label(
        "search_official_guidance",
        {"query": "H-1B grace period after layoff"},
        round_number=1,
    )
    assert "H-1B grace period after layoff" in label
    assert "search_official_guidance" not in label


def test_label_falls_back_without_a_query():
    assert build_label("search_official_guidance", {}, round_number=1) == (
        "Checking official guidance"
    )


def test_unknown_tool_never_leaks_its_name():
    """A tool added later must not surface its function name in the UI."""
    label = build_label("some_internal_tool", {"query": "whatever"}, round_number=1)
    assert "some_internal_tool" not in label
    assert label.startswith(DEFAULT_VERB)


def test_search_operators_are_stripped():
    subject = summarise_subject('site:reddit.com "H4 EAD" AND pending')
    assert "site:" not in subject
    assert "reddit.com" not in subject
    assert '"' not in subject
    assert "H4 EAD" in subject


def test_long_subjects_are_clipped_on_a_word_boundary():
    subject = summarise_subject(
        "what happens if my green card is approved while I am outside "
        "the united states holding advance parole"
    )
    assert len(subject) <= MAX_SUBJECT_CHARS + 1  # +1 for the ellipsis
    assert "…" in subject
    # Clipped mid-word looks like a rendering bug rather than a summary.
    assert not subject.rstrip("…").endswith(" ")


def test_a_second_round_says_something_different():
    """Round two looking identical to round one reads as a frozen UI."""
    first = build_label("search_official_guidance", {"query": "opt cap gap"}, round_number=1)
    second = build_label("search_official_guidance", {"query": "opt cap gap"}, round_number=2)
    assert first != second
    assert "Still" in second
