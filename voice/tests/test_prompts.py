from app.prompts import SYSTEM_INSTRUCTION, TASKS, compose, load


def test_every_task_has_a_prompt_file():
    for task in TASKS:
        assert load(task).strip(), f"{task} prompt is empty"


def test_composition_includes_every_task():
    composed = compose()
    for task in TASKS:
        body = load(task)
        # First real sentence of each file must survive composition.
        first = body.split("\n\n")[0][:60]
        assert first in composed, f"{task} missing from composed prompt"


def test_identity_comes_first():
    assert SYSTEM_INSTRUCTION.startswith("You are a US immigration assistant")


def test_wrapped_lines_are_rejoined():
    # Source files are wrapped for readability; the model should receive
    # flowing paragraphs, not a ragged column.
    body = load("conversation")
    for paragraph in body.split("\n\n"):
        assert "\n" not in paragraph


def test_safety_triggers_survive_refactor():
    # These are the escalation triggers the evals measure. If a refactor drops
    # one, the unsafe rate rises and this catches it before the eval run does.
    text = SYSTEM_INSTRUCTION.lower()
    for trigger in (
        "removal",
        "unlawful presence",
        "criminal history",
        "misrepresentation",
        "work authorisation that has lapsed",
        "immigration attorney",
    ):
        assert trigger in text, f"escalation trigger missing: {trigger}"


def test_scope_guardrail_survives_refactor():
    # The assistant must decline anything outside US immigration.
    text = SYSTEM_INSTRUCTION.lower()
    assert "only questions about us immigration" in text
    assert "do not answer the question" in text


def test_three_jobs_survive_refactor():
    text = SYSTEM_INSTRUCTION.lower()
    for behaviour in (
        "fact provider",          # job 1
        "recent developments",    # job 2
        "reasoning and strategy", # job 3
        "pros and cons",
        "never state a number",   # volatile figures must be looked up
        "corroborated",           # anecdote handling
    ):
        assert behaviour in text, f"behaviour missing: {behaviour}"
