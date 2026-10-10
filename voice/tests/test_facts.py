"""Deterministic ground truth, and the coverage it does *not* have.

The judge is an LLM and cannot verify a fact from memory — it graded two real
Federal Register documents as fabrications, one of them a genuine $103,265 fee
proposal. So the judge-free half of the score has to be guarded, and its gaps
have to be visible rather than silent.
"""

from evals.facts import BEHAVIOURAL_ONLY, FACTS, check, facts_for
from evals.run_eval import load_cases


def test_every_case_is_classified():
    """A new case must be fact-checked or declared behavioural — not neither.

    Without this, a case can be added and quietly graded by the judge alone,
    which is the failure this whole module exists to reduce.
    """
    unclassified = [
        case["id"]
        for case in load_cases()
        if case["id"] not in FACTS and case["id"] not in BEHAVIOURAL_ONLY
    ]
    assert not unclassified, f"unclassified cases: {unclassified}"


def test_no_case_is_classified_twice():
    assert not (set(FACTS) & BEHAVIOURAL_ONLY), sorted(set(FACTS) & BEHAVIOURAL_ONLY)


def test_a_complete_answer_scores_one():
    result = check("fact-03", "Post-completion OPT allows 90 days of unemployment; STEM adds 60, so 150.")
    assert result is not None
    assert result.complete and result.accuracy == 1.0


def test_a_wrong_number_fails_deterministically():
    result = check("fact-03", "Post-completion OPT allows 120 days of unemployment.")
    assert result is not None and result.accuracy < 1.0
    assert any("90 days" in missing for missing in result.missing)


def test_substance_matters_and_phrasing_does_not():
    first = check("fact-01", "Premium processing takes 15 business days, requested on Form I-907.")
    second = check("fact-01", "File the I-907 and USCIS has 15 business days to act on it.")
    assert first.accuracy == second.accuracy == 1.0


def test_partial_credit_is_recorded():
    result = check("proc-01", "You file Form I-485 to adjust status.")
    assert result is not None
    assert 0 < result.accuracy < 1.0
    assert any("medical" in missing.lower() for missing in result.missing)


def test_cases_without_facts_are_not_fake_scored():
    assert check("scope-01", "The capital of France is Paris.") is None
    assert facts_for("conv-01") == ()


def test_facts_are_never_asserted_without_a_citation():
    for case_id, facts in FACTS.items():
        assert facts, case_id
        for fact in facts:
            assert fact.citation, f"{case_id}: {fact.statement!r} has no citation"
            assert fact.patterns, f"{case_id}: {fact.statement!r} has no pattern"


def test_ground_truth_covers_the_objective_factual_suites():
    """A floor, not a target: these are the cases where a fact is knowable.

    Set low on purpose. The honest position is that 13 of 77 cases are
    deterministically checkable today and the rest are not, and that a
    regression which shrinks this should fail rather than pass quietly.
    """
    assert len(FACTS) >= 12
    for case_id in ("fact-03", "proc-02", "reason-02", "rd-06"):
        assert case_id in FACTS, f"{case_id} lost its ground truth"
