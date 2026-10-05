"""HTTP API for the text agent.

One endpoint, /chat, streams an answer as server-sent events. The browser
converts speech to text on its own and posts plain text here, so there is no
audio pipeline, no WebSocket, and no pipecat — the model is an OpenAI-compatible
endpoint named in configuration.
"""

import json
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.config import settings
from app.failures import classify_exception, session_failure_message
from app.observability import log_agent_response, log_user_query, new_request
from app.text_agent import AgentUnavailable, StatusEvent, stream_answer

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    newMessage: str
    # The browser holds the only copy of the transcript; nothing is stored
    # here, so prior turns have to come back up with each request.
    recentMessages: list[ChatMessage] = []
    summary: str = ""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/chat")
async def chat(request: ChatRequest):
    """Stream one answer, with status events interleaved with tokens.

    Server-sent events rather than plain text: the tokens and the "what am I
    doing right now" updates share one ordered stream, so the client cannot
    show a status after the answer it belongs to.
    """
    new_request(session_id="chat")
    log_user_query(request.newMessage)

    history = [m.model_dump() for m in request.recentMessages]
    if request.summary:
        history.insert(
            0,
            {
                "role": "user",
                "content": f"Earlier in this conversation: {request.summary}",
            },
        )

    async def events():
        answer: list[str] = []
        pending_status: str | None = None

        async def on_status(event: StatusEvent | None):
            nonlocal pending_status
            pending_status = (
                _sse("status", {"state": "working", "label": event.label, "round": event.round})
                if event
                else _sse("status", {"state": "idle"})
            )

        try:
            stream = stream_answer(history, request.newMessage, on_status=on_status)
            async for chunk in stream:
                if pending_status:
                    yield pending_status
                    pending_status = None
                answer.append(chunk)
                yield _sse("token", {"text": chunk})
            if pending_status:
                yield pending_status
        except AgentUnavailable as error:
            failure = error.failure
            logger.warning(f"chat turn failed: {failure.kind.value} ({failure.detail})")
            yield _sse(
                "error",
                {
                    "kind": failure.kind.value,
                    "message": session_failure_message(failure.kind),
                    "retryable": failure.retryable,
                },
            )
        except Exception as error:  # noqa: BLE001 - the client needs to hear why
            failure = classify_exception(error)
            logger.exception("chat turn failed")
            yield _sse(
                "error",
                {
                    "kind": failure.kind.value,
                    "message": session_failure_message(failure.kind),
                    "retryable": failure.retryable,
                },
            )
        else:
            log_agent_response("".join(answer))
        finally:
            yield _sse("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Proxies buffer streamed responses by default, which turns a live
        # answer into one delivered all at once at the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
