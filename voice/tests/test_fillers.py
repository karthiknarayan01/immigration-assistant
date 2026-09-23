import pathlib

from app.fillers import NUM_CHANNELS, SAMPLE_RATE, TOOL_GROUPS, FillerPicker, _CLIPS
from app.tools.registry import _HANDLERS

FILLER_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "audio" / "fillers"


def test_clips_are_shipped_for_every_group():
    # Missing audio makes tool calls silent again, which is the bug this fixes.
    assert _CLIPS, "no filler audio loaded"
    for group in set(TOOL_GROUPS.values()):
        assert _CLIPS.get(group), f"no clips for {group}"


#: Tools fast enough that there is no silence to cover. Filler before one of
#: these would *add* delay rather than mask it — the clip is ~1.7s and the
#: lookup is under a millisecond, so the agent would announce a search that
#: had already finished.
INSTANT_TOOLS = {"lookup_regulation"}


def test_every_slow_tool_has_filler_audio():
    # A new network-bound tool without a clip would silently reintroduce the
    # dead air this whole mechanism exists to remove.
    for name in _HANDLERS:
        if name in INSTANT_TOOLS:
            continue
        assert name in TOOL_GROUPS, f"{name} has no filler group"


def test_instant_tools_are_actually_instant():
    """Guards the exemption above: if one of these gains a network call, the
    exemption becomes the bug it was written to avoid."""
    import time

    from app import knowledge

    if not knowledge.available():
        return  # pack not built in this environment
    started = time.perf_counter()
    knowledge.search("grace period after employment ends", limit=3)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 50, f"lookup took {elapsed_ms:.0f}ms — it now needs filler audio"


def test_picker_returns_audio_for_known_tools():
    picker = FillerPicker()
    for name in TOOL_GROUPS:
        assert picker.for_tool(name)


def test_picker_ignores_unknown_tools():
    assert FillerPicker().for_tool("something_else") is None


def test_picker_avoids_immediate_repeats():
    # Hearing the same sentence twice running is what makes filler feel canned.
    picker = FillerPicker()
    picks = [picker.for_tool("search_official_guidance") for _ in range(12)]
    assert all(a is not b for a, b in zip(picks, picks[1:]))


def test_clips_are_plausible_length():
    # Too short is unintelligible; too long and the answer is ready before the
    # filler finishes. Also catches the silence-padding regression.
    #
    # Acknowledgements are held to a tighter bound at the other end. They play
    # before the model has decided anything, which puts them ahead of the real
    # answer in the output queue, so their length is added to every turn they
    # fire on.
    bounds = {"ack": (0.2, 0.8)}
    for path in FILLER_DIR.rglob("*.pcm"):
        seconds = path.stat().st_size / 2 / SAMPLE_RATE
        low, high = bounds.get(path.parent.name, (0.8, 3.5))
        assert low <= seconds <= high, f"{path.name} is {seconds:.2f}s"


def test_clips_are_whole_samples_at_the_expected_format():
    assert NUM_CHANNELS == 1
    for path in FILLER_DIR.rglob("*.pcm"):
        # 16-bit mono: an odd byte count means a truncated sample and clicks.
        assert path.stat().st_size % 2 == 0, f"{path.name} is not 16-bit aligned"
