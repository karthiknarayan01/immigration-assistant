"""The text path is the only path now.

These cover transcript handling and model routing — the parts specific to the
text transport. Answer quality is the eval suite's job, not these tests'.
"""

from app.config import settings
from app.llm import build_tools
from app.prompts import SYSTEM_INSTRUCTION
from app.text_agent import build_messages, models_to_try


def test_history_becomes_alternating_messages():
    messages = build_messages(
        [
            {"role": "user", "content": "I am on an H-1B."},
            {"role": "assistant", "content": "Understood."},
        ],
        "What happens if I am laid off?",
    )
    # system, then user/assistant/user
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "What happens if I am laid off?"
    assert messages[0]["content"] == SYSTEM_INSTRUCTION


def test_empty_history_entries_are_dropped():
    """A placeholder assistant message exists in the UI before text arrives."""
    messages = build_messages(
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}],
        "next question",
    )
    assert [m["role"] for m in messages] == ["system", "user", "user"]


def test_roles_pass_through():
    messages = build_messages([{"role": "assistant", "content": "prior answer"}], "q")
    assert messages[1]["role"] == "assistant"


def test_models_to_try_prefers_primary_then_fallback():
    original = (settings.llm_model, settings.llm_fallback_model)
    try:
        settings.llm_model = "primary/model"
        settings.llm_fallback_model = "fallback/model"
        assert models_to_try() == ["primary/model", "fallback/model"]

        settings.llm_fallback_model = ""
        assert models_to_try() == ["primary/model"]

        settings.llm_fallback_model = "primary/model"  # duplicate
        assert models_to_try() == ["primary/model"]
    finally:
        settings.llm_model, settings.llm_fallback_model = original


def test_tool_declarations_mirror_the_production_schemas():
    from app.tools.registry import _SCHEMAS

    declared = {t["function"]["name"] for t in build_tools(_SCHEMAS)}
    assert declared == {schema.name for schema in _SCHEMAS}


def _with_models(cheap: str, reasoner: str, fallback: str = ""):
    original = (settings.llm_model, settings.reasoner_model, settings.llm_fallback_model)
    settings.llm_model, settings.reasoner_model, settings.llm_fallback_model = cheap, reasoner, fallback
    return original


def _restore(original):
    settings.llm_model, settings.reasoner_model, settings.llm_fallback_model = original


def test_judgement_questions_route_to_the_reasoner():
    original = _with_models("cheap/model", "reasoning/model")
    try:
        for question in (
            "Should I switch from EB-2 to EB-1?",
            "What are my chances of approval?",
            "Compare EB-5 regional center vs direct investment.",
            "What is the best path to a green card for me?",
        ):
            assert models_to_try(question)[0] == "reasoning/model", question
    finally:
        _restore(original)


def test_factual_questions_do_not_route_to_the_reasoner():
    """A factual question that happens to start with "can I" is not judgement.

    Sending it to a reasoning model would be waste, not rigour.
    """
    original = _with_models("cheap/model", "reasoning/model")
    try:
        for question in (
            "What is the H-1B premium processing time?",
            "How many days of unemployment am I allowed on OPT?",
            "Can I travel while my adjustment of status is pending?",
            "What happens after I get an RFE?",
        ):
            assert models_to_try(question)[0] == "cheap/model", question
    finally:
        _restore(original)


def test_reasoner_is_never_used_when_it_is_unset():
    original = _with_models("cheap/model", "")
    try:
        assert models_to_try("Should I switch to EB-1?") == ["cheap/model"]
    finally:
        _restore(original)


async def test_a_successful_round_does_not_also_run_the_fallback(monkeypatch):
    """The fallback exists for failures, not for every turn.

    A bug let control fall through to the next model after a round had already
    succeeded, so each turn ran twice: the reader saw the answer repeated and
    every call was paid for twice. It was found by a smoke test that noticed
    two models in the usage report, not by the suite — hence this test.
    """
    from app import text_agent
    from app.llm import ChatDelta

    original = _with_models("primary/model", "", "fallback/model")
    called: list[str] = []

    async def fake_stream_chat(client, *, model, messages, tools, temperature=0):
        called.append(model)
        yield ChatDelta(content="An answer.")

    try:
        monkeypatch.setattr(text_agent, "stream_chat", fake_stream_chat)
        chunks = []
        async for chunk in text_agent.stream_answer([], "What is the H-1B filing fee?"):
            chunks.append(chunk)
    finally:
        _restore(original)

    assert "".join(chunks).strip() == "An answer."
    assert called == ["primary/model"], f"the fallback ran despite success: {called}"
