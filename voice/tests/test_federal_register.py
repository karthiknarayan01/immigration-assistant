"""Federal Register parsing.

The in-force-vs-proposed distinction is the reason this tool exists, so it is
computed from the document type rather than left to the model to infer from
prose. These tests pin that mapping.
"""

from app.tools.federal_register import FrDocument, status_of


def test_document_types_map_to_a_plain_status():
    assert "in force" in status_of("Rule")
    assert "NOT yet in force" in status_of("Proposed Rule")
    assert "notice" in status_of("Notice").lower()
    assert "presidential" in status_of("Presidential Document").lower()


def test_proposed_is_checked_before_rule():
    """'Proposed Rule' contains 'rule', so the order of checks decides it."""
    assert "NOT yet" in status_of("Proposed Rule")
    assert "NOT yet" not in status_of("Rule")


def test_unknown_type_is_passed_through_not_guessed():
    # A type we do not recognise must not be silently labelled in force.
    assert status_of("Some New Thing") == "Some New Thing"


def test_document_renders_the_fields_the_model_needs():
    document = FrDocument(
        title="Fee for Certain H-1B Petitions",
        doc_type="Proposed Rule",
        type_meaning=status_of("Proposed Rule"),
        abstract="A proposed fee.",
        publication_date="2026-09-10",
        effective_on=None,
        comments_close_on="2026-10-10",
        citation="91 FR 12345",
        url="https://www.federalregister.gov/d/2026-12345",
        agencies=["Homeland Security Department"],
    )
    payload = document.as_dict()
    assert payload["status"].startswith("proposed rule")
    assert payload["effective_on"] == ""
    assert payload["citation"] == "91 FR 12345"
    assert payload["published"] == "2026-09-10"
