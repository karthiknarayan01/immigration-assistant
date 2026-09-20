import pathlib

from app.fillers import NUM_CHANNELS, SAMPLE_RATE, TOOL_GROUPS, FillerPicker, _CLIPS
from app.tools.registry import _HANDLERS

FILLER_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "audio" / "fillers"


def test_clips_are_shipped_for_every_group():
    # Missing audio makes tool calls silent again, which is the bug this fixes.
    assert _CLIPS, "no filler audio loaded"
    for group in set(TOOL_GROUPS.values()):
        assert _CLIPS.get(group), f"no clips for {group}"


def test_every_registered_tool_has_filler_audio():
    # A new tool without a clip would silently reintroduce the dead air.
    for name in _HANDLERS:
        assert name in TOOL_GROUPS, f"{name} has no filler group"


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
    for path in FILLER_DIR.rglob("*.pcm"):
        seconds = path.stat().st_size / 2 / SAMPLE_RATE
        assert 0.8 <= seconds <= 3.5, f"{path.name} is {seconds:.2f}s"


def test_clips_are_whole_samples_at_the_expected_format():
    assert NUM_CHANNELS == 1
    for path in FILLER_DIR.rglob("*.pcm"):
        # 16-bit mono: an odd byte count means a truncated sample and clicks.
        assert path.stat().st_size % 2 == 0, f"{path.name} is not 16-bit aligned"
