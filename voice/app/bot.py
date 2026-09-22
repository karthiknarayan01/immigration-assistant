import time
import uuid

from google.genai import types as genai_types
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.services.llm_service import FunctionCallParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor
from pipecat.services.google.gemini_live.llm import GeminiVADParams
from pipecat.services.google.gemini_live.vertex.llm import (
    GeminiLiveVertexLLMService,
    GeminiLiveVertexLLMSettings,
)
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_turn_completion_mixin import UserTurnCompletionConfig
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.config import settings
from app.filler_speaker import BotSpeechObserver, FillerSpeaker
from app.status import AgentStatus
from app.observability import (
    STAGE_USER_TURN,
    Timing,
    bound,
    current_request_id,
    log_agent_response,
    log_user_query,
    new_request,
    record,
)
from app.session_health import SessionFailureObserver
from app.prompts import (
    GREETING_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    TURN_COMPLETION_INSTRUCTIONS,
)
from app.tools.registry import build_tools


def _announced(name, handler, speaker: FillerSpeaker, status: AgentStatus):
    """Wrap a tool handler so the user hears and sees that it is running.

    Done here rather than inside the tools: covering silence is a property of
    the transport, not of searching, and the same handlers run in the evals
    where there is no audio pipeline at all.
    """

    async def wrapped(params: FunctionCallParams):
        await speaker.speak_for_tool(name)
        await status.working(name)
        try:
            return await handler(params)
        finally:
            await status.done()

    return wrapped


def _build_llm(speaker: FillerSpeaker, status: AgentStatus) -> GeminiLiveVertexLLMService:
    if not settings.google_cloud_project_id:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT_ID is not set. Vertex AI needs a project id; "
            "see voice/.env.example."
        )

    tools_schema, handlers = build_tools()

    llm = GeminiLiveVertexLLMService(
        # All credential paths are optional: on Cloud Run both are empty and
        # the service falls back to Application Default Credentials.
        credentials=settings.google_vertex_credentials or None,
        credentials_path=settings.google_vertex_credentials_path or None,
        project_id=settings.google_cloud_project_id,
        location=settings.google_cloud_location,
        system_instruction=SYSTEM_INSTRUCTION,
        tools=tools_schema,
        settings=GeminiLiveVertexLLMSettings(
            model=settings.gemini_model,
            voice=settings.gemini_voice,
            vad=GeminiVADParams(
                # Low end-sensitivity means Gemini waits longer before deciding
                # the user has finished. Combined with the longer silence
                # window, this is the first line of defence against talking
                # over someone who pauses mid-sentence.
                end_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                # High start-sensitivity keeps barge-in responsive: the moment
                # the user speaks over the agent, the agent yields.
                start_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_HIGH,
                silence_duration_ms=settings.vad_silence_duration_ms,
                prefix_padding_ms=settings.vad_prefix_padding_ms,
            ),
            # Long sessions would otherwise grow the audio context unbounded,
            # and audio tokens are the dominant cost.
            context_window_compression={"enabled": True},
        ),
    )

    for name, handler in handlers.items():
        llm.register_function(
            name,
            _announced(name, handler, speaker, status),
            timeout_secs=settings.tool_timeout_secs,
            # If the user starts talking again, whatever we were looking up is
            # no longer what they asked. Abandon it rather than answering late.
            cancel_on_interruption=True,
        )

    return llm


def _message_text(message) -> str:
    """Pull plain text out of a context message of whatever shape."""
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = getattr(message, "content", "") or ""
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        ).strip()
    return str(content).strip()


async def run_bot(websocket) -> None:
    session_id = uuid.uuid4().hex[:8]
    new_request(session_id=session_id)
    bound().info("event=session_started")

    # WebSocket rather than WebRTC because Cloud Run accepts only HTTP/1.1,
    # HTTP/2 and WebSockets — no UDP — so a WebRTC media path can never
    # establish there. Signalling succeeded and ICE stalled at "checking",
    # which looks like a hung client rather than an unsupported protocol.
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    # Both are bound to the pipeline further down: tool handlers are
    # registered on the LLM service, which has to exist before the pipeline
    # that will carry their audio and their status events.
    speaker = FillerSpeaker()
    status = AgentStatus()
    llm = _build_llm(speaker, status)

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        realtime_service_mode=True,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # Acoustic VAD alone cannot tell a thinking pause from a finished
            # question. This adds a semantic check on top of it.
            user_turn_strategies=FilterIncompleteUserTurnStrategies(
                config=UserTurnCompletionConfig(instructions=TURN_COMPLETION_INSTRUCTIONS)
            ),
        ),
    )

    # The browser uses the PipecatClient SDK, which speaks RTVI: it sends
    # "client-ready" over the data channel and waits for "bot-ready" before
    # it reports a connection. Without this processor in the pipeline nothing
    # answers, so the client sits on "connecting" and retries every ~20s.
    rtvi = RTVIProcessor(transport=transport)
    status.bind(rtvi)

    pipeline = Pipeline(
        [
            transport.input(),
            rtvi,
            user_aggregator,
            llm,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        rtvi_processor=rtvi,
        # The processor handles the protocol; the observer is what actually
        # emits RTVI events onto the wire. Pipecat rejects one without the
        # other, and the session then never becomes ready.
        observers=[
            RTVIObserver(rtvi),
            SessionFailureObserver(rtvi),
            BotSpeechObserver(speaker),
        ],
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    speaker.bind(worker)
    turn_started: dict[str, float] = {}

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(_aggregator, _strategy):
        # One request id per user turn. Filtering logs on it reconstructs the
        # whole turn: question, tool calls, and the answer finally spoken.
        request_id = new_request(session_id=session_id)
        turn_started[request_id] = time.perf_counter()
        speaker.on_user_turn_started()

    @user_aggregator.event_handler("on_user_turn_message_added")
    async def on_user_turn_message_added(_aggregator, message):
        log_user_query(_message_text(message))
        # The turn is complete here, so this is where the silence starts.
        speaker.on_user_turn_ended()


    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_aggregator, message):
        text = _message_text(message)
        log_agent_response(text)
        request_id = current_request_id()
        started = turn_started.pop(request_id, None)
        if started is not None:
            # End to end for the turn: what the user actually waited through.
            record(
                Timing(
                    stage=STAGE_USER_TURN,
                    name="turn",
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    request_id=request_id,
                    session_id=session_id,
                    started_at=started,
                    attributes={"response_chars": len(text)},
                )
            )

    @rtvi.event_handler("on_client_ready")
    async def on_client_ready(processor):
        # Greet on RTVI readiness rather than on transport connect: the peer
        # connection exists before the client can actually receive audio.
        logger.info("client ready")
        await processor.set_bot_ready()
        context.add_message({"role": "developer", "content": GREETING_INSTRUCTION})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        # Session state lives only in this worker; dropping it here is what
        # makes "we never store your conversation" literally true.
        logger.info("client disconnected")
        await runner.cancel()

    await runner.run()
