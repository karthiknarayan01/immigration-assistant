"""The filler ladder is all timing, so the edges are what need pinning down.

The failure that matters is the agent talking over itself: an acknowledgement
still playing when the model starts answering. The speaker cannot ask the
transport who is speaking — our own clips raise the same frames the model's
speech does — so it tracks that itself, and these tests cover the cases where
that distinction decides whether a clip plays.
"""

import asyncio

import pytest

from app.fillers import ACK_GROUP, MAX_ACK_MS, _CLIPS, clip_duration_ms
from app.filler_speaker import FillerSpeaker


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)


def speaker(delay=0.02):
    worker = FakeWorker()
    spoken = FillerSpeaker(ack_delay_secs=delay)
    spoken.bind(worker)
    return spoken, worker


@pytest.mark.asyncio
async def test_acknowledges_a_silent_gap():
    spoken, worker = speaker()
    spoken.on_user_turn_started()
    spoken.on_user_turn_ended()
    await asyncio.sleep(0.1)
    assert len(worker.frames) == 1


@pytest.mark.asyncio
async def test_stays_quiet_when_the_model_answers_first():
    """A fast turn needs no filler, and would be interrupted by one."""
    spoken, worker = speaker()
    spoken.on_user_turn_started()
    spoken.on_user_turn_ended()
    spoken.on_bot_started_speaking()
    await asyncio.sleep(0.1)
    assert worker.frames == []


@pytest.mark.asyncio
async def test_own_acknowledgement_does_not_suppress_the_tool_clip():
    """Our clip echoes back as a bot-speaking frame; that is not the model.

    Without the self-audio window the ladder collapses after its first rung:
    the acknowledgement would look like the model answering, and the phrase
    naming the lookup would never play.
    """
    spoken, worker = speaker(delay=0.01)
    spoken.on_user_turn_started()
    spoken.on_user_turn_ended()
    await asyncio.sleep(0.05)
    spoken.on_bot_started_speaking()
    await spoken.speak_for_tool("search_official_guidance")
    assert len(worker.frames) == 2


@pytest.mark.asyncio
async def test_a_speaking_model_does_suppress_the_tool_clip():
    spoken, worker = speaker()
    spoken.on_user_turn_started()
    spoken.on_bot_started_speaking()
    await spoken.speak_for_tool("search_official_guidance")
    assert worker.frames == []


@pytest.mark.asyncio
async def test_barge_in_cancels_a_pending_acknowledgement():
    """The user started talking again: the last turn's filler is now wrong."""
    spoken, worker = speaker(delay=0.08)
    spoken.on_user_turn_ended()
    spoken.on_user_turn_started()
    await asyncio.sleep(0.15)
    assert worker.frames == []


@pytest.mark.asyncio
async def test_frame_matches_what_the_transport_expects():
    spoken, worker = speaker()
    await spoken.speak_for_tool("search_community_experiences")
    frame = worker.frames[0]
    assert frame.sample_rate == 24_000
    assert frame.num_channels == 1
    assert len(frame.audio) > 0


@pytest.mark.asyncio
async def test_unbound_speaker_is_a_no_op():
    """Tool handlers also run in the evals, where there is no pipeline."""
    spoken = FillerSpeaker()
    await spoken.speak_for_tool("search_official_guidance")  # must not raise


def test_acknowledgements_stay_short():
    """Long acks defeat the purpose: they delay the answer they are covering.

    Renders vary enough in pace that this is enforced at load rather than
    trusted — two of the six generated clips came back over the cap.
    """
    for clip in _CLIPS.get(ACK_GROUP, []):
        assert clip_duration_ms(clip) <= MAX_ACK_MS
