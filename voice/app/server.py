import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from app.bot import run_bot
from app.config import settings

webrtc_handler = SmallWebRTCRequestHandler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await webrtc_handler.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    async def webrtc_connection_callback(connection):
        background_tasks.add_task(run_bot, connection)

    return await webrtc_handler.handle_web_request(
        request=request,
        webrtc_connection_callback=webrtc_connection_callback,
    )


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest):
    await webrtc_handler.handle_patch_request(request)
    return {"status": "success"}


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
