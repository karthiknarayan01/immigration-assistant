"""Covering the silence between a user's question and the agent's answer.

A tool-using turn is ~6s to the first real word, and Gemini Live spends all
of it silent: it emits tool calls without speaking, which was verified three
ways and is not fixable by prompting. Measured, the gap breaks down as ~1.2s
for the model to decide it needs a search, ~0.9s for the search, then ~1.9s
to start answering. Only the middle third is the tool.

So the pipeline speaks instead, in a ladder:

    ~250ms   a short acknowledgement, before anything has been decided
    ~1.2s    a phrase naming what is being looked up, when the tool fires
    ~6s      the real answer

This does not make the answer arrive sooner. It replaces dead air with
evidence that the question was heard, which is a different thing and worth
keeping separate when reporting latency: time-to-first-audio drops below a
second, time-to-first-token does not move.

Audio frames play in queue order, so everything here is short and bounded —
a clip queued now delays the real answer by exactly its own length. That is
also why there is no continuous "working" tone: it would sit in front of the
answer. Ambient sound belongs on the client, driven by status events.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    OutputAudioRawFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from app.fillers import NUM_CHANNELS, SAMPLE_RATE, FillerPicker, clip_duration_ms

#: How long to wait after the user stops before acknowledging. Long enough
#: that a fast turn answers on its own and no filler is needed; short enough
#: that it still reads as an immediate response.
ACK_DELAY_SECS = 0.25

#: Treat bot-speaking events inside this window as our own clip rather than
#: the model's speech. The transport cannot tell us which is which, and
#: mistaking our own acknowledgement for the model answering would suppress
#: the tool filler that should follow it.
_SELF_AUDIO_GRACE_SECS = 0.15


class FillerSpeaker:
    """Pushes pre-rendered clips into the pipeline to cover tool-call silence.

    Bound to the pipeline after construction because the LLM service is built
    before the pipeline exists, and tool handlers are registered on the LLM.
    """

    def __init__(self, ack_delay_secs: float = ACK_DELAY_SECS) -> None:
        self._picker = FillerPicker()
        self._worker = None
        self._ack_delay = ack_delay_secs
        self._pending: asyncio.Task | None = None
        #: Monotonic time until which audio in flight is ours, not the model's.
        self._own_audio_until = 0.0
        self._model_speaking = False

    def bind(self, worker) -> None:
        self._worker = worker

    # -- lifecycle -----------------------------------------------------

    def on_user_turn_started(self) -> None:
        """User started talking: abandon anything queued for the last turn."""
        self._model_speaking = False
        self._cancel_pending()

    def on_user_turn_ended(self) -> None:
        """User stopped talking: start the clock on the acknowledgement."""
        self._cancel_pending()
        self._pending = asyncio.create_task(self._acknowledge_after_delay())

    def on_bot_started_speaking(self) -> None:
        if time.monotonic() < self._own_audio_until:
            # Our own clip echoing back as a transport event.
            return
        self._model_speaking = True
        self._cancel_pending()

    def on_bot_stopped_speaking(self) -> None:
        self._model_speaking = False

    # -- speaking ------------------------------------------------------

    async def speak_for_tool(self, function_name: str) -> None:
        """Name what is being looked up, at the moment the lookup starts."""
        self._cancel_pending()
        if self._model_speaking:
            # The model found something to say on its own. Rare, but if it
            # happens, talking over it is worse than saying nothing.
            return
        await self._play(self._picker.for_tool(function_name), why=function_name)

    async def _acknowledge_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._ack_delay)
        except asyncio.CancelledError:
            return
        if self._model_speaking:
            return
        await self._play(self._picker.acknowledgement(), why="ack")

    async def _play(self, clip: bytes | None, *, why: str) -> None:
        if not clip or self._worker is None:
            return
        duration_ms = clip_duration_ms(clip)
        self._own_audio_until = time.monotonic() + duration_ms / 1000 + _SELF_AUDIO_GRACE_SECS
        try:
            await self._worker.queue_frames(
                [
                    OutputAudioRawFrame(
                        audio=clip,
                        sample_rate=SAMPLE_RATE,
                        num_channels=NUM_CHANNELS,
                    )
                ]
            )
        except Exception as error:  # noqa: BLE001 - filler must never break a turn
            logger.warning(f"filler audio failed ({why}): {error!r}")
            return
        logger.debug(f"filler audio: {why} ({duration_ms:.0f}ms)")

    def _cancel_pending(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None


class BotSpeechObserver(BaseObserver):
    """Tells the speaker when the model is actually talking.

    The WebSocket transport registers only connect/disconnect/timeout events,
    so bot speech is not available as a transport event handler — it arrives
    as a frame from the output transport and has to be observed.

    Our own filler clips go out through that same output and raise the same
    frames, which is what the speaker's self-audio window is for.
    """

    def __init__(self, speaker: FillerSpeaker) -> None:
        super().__init__()
        self._speaker = speaker

    async def on_push_frame(self, data: FramePushed) -> None:
        if isinstance(data.frame, BotStartedSpeakingFrame):
            self._speaker.on_bot_started_speaking()
        elif isinstance(data.frame, BotStoppedSpeakingFrame):
            self._speaker.on_bot_stopped_speaking()
