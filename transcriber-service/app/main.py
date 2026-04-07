import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .transcribe import TranscriberPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pipeline: TranscriberPipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    logger.info("Starting transcriber service...")
    pipeline = TranscriberPipeline()
    pipeline.preload()
    logger.info("Transcriber service ready")
    yield
    pipeline = None


app = FastAPI(title="Transcriber Service", lifespan=lifespan)


class TranscribeRequest(BaseModel):
    audio_path: str


class SegmentResponse(BaseModel):
    speaker: str | None = None
    start: float
    end: float
    text: str


class TranscribeResponse(BaseModel):
    segments: list[SegmentResponse]


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(request: TranscribeRequest):
    """Транскрибировать аудиофайл с диаризацией."""
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")

    import os
    if not os.path.exists(request.audio_path):
        raise HTTPException(
            status_code=400,
            detail=f"Audio file not found: {request.audio_path}",
        )

    try:
        segments = pipeline.transcribe(request.audio_path)
        return TranscribeResponse(
            segments=[
                SegmentResponse(
                    speaker=seg.speaker,
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                )
                for seg in segments
            ]
        )
    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "pipeline_ready": pipeline is not None,
    }
