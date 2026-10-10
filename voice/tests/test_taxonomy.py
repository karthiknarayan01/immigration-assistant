"""The eval taxonomy.

Coverage reporting is what stops "N cases" being mistaken for "the domain is
covered", so the counters and the gap list are pinned here.
"""

from evals.taxonomy import coverage, gaps


def test_coverage_counts_each_dimension():
    cases = [
        {
            "category": "factual",
            "domain": "h1b",
            "difficulty": "single_fact",
            "failure_mode": "hallucinated_number",
        },
        {
            "category": "recent",
            "domain": "eb5",
            "difficulty": "multi_condition",
            "failure_mode": "stale_source",
            "expects_tools": ["search_federal_register"],
        },
    ]
    report = coverage(cases)
    assert report["total"] == 2
    assert report["by_domain"]["h1b"] == 1
    assert report["by_task"]["recent"] == 1
    assert report["with_trajectory_assertion"] == 1
    assert report["multi_turn"] == 0


def test_multi_turn_is_counted():
    report = coverage([{"category": "reasoning", "history": [{"role": "user", "content": "x"}]}])
    assert report["multi_turn"] == 1


def test_untagged_cases_are_visible():
    report = coverage([{"category": "factual"}])
    assert report["untagged"] == 1


def test_gaps_name_the_missing_required_cells():
    # A set with one case should report its holes, not look complete.
    missing = gaps([
        {
            "category": "factual",
            "domain": "h1b",
            "difficulty": "single_fact",
            "failure_mode": "hallucinated_number",
        }
    ])
    assert "failure_mode:no_escalation" in missing
    assert "failure_mode:injection_followed" in missing
    assert "domain:eb5" in missing


def test_a_covered_set_reports_no_gaps():
    one_of_each = [
        {"domain": d, "failure_mode": m}
        for d in ("h1b", "f1_opt", "b1_b2", "l1", "eb1", "eb2", "eb5")
        for m in ("hallucinated_number", "wrong_tool", "no_escalation", "premise_error",
                  "out_of_scope_answer", "injection_followed", "stale_source", "ungrounded_claim")
    ]
    assert gaps(one_of_each) == []
