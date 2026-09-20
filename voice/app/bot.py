from google.genai import types as genai_types
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.services.google.gemini_live.llm import GeminiVADParams
from pipecat.services.google.gemini_live.vertex.llm import (
    GeminiLiveVertexLLMService,
    GeminiLiveVertexLLMSettings,
)
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_turn_completion_mixin import UserTurnCompletionConfig
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.config import settings
from app.prompt import (
    GREETING_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    TURN_COMPLETION_INSTRUCTIONS,
)
from app.tools.registry import build_tools


def _build_llm() -> GeminiLiveVertexLLMService:
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
            handler,
            timeout_secs=settings.tool_timeout_secs,
            # If the user starts talking again, whatever we were looking up is
            # no longer what they asked. Abandon it rather than answering late.
            cancel_on_interruption=True,
        )

    return llm


async def run_bot(webrtc_connection) -> None:
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )

    llm = _build_llm()

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
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

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
