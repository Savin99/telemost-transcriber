import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .speaker_refiner import env_bool
from .transcribe import TranscriberPipeline, TranscribeResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def validate_required_env() -> None:
    """Падаем на старте, если включённые фичи требуют недостающих env."""
    missing: list[str] = []

    llm_enabled = env_bool("SPEAKER_LLM_REFINEMENT_ENABLED", False) or env_bool(
        "TRANSCRIPT_LLM_REFINEMENT_ENABLED", False
    )
    if llm_enabled and not os.getenv("ANTHROPIC_API_KEY", "").strip():
        missing.append(
            "ANTHROPIC_API_KEY is required when SPEAKER_LLM_REFINEMENT_ENABLED "
            "or TRANSCRIPT_LLM_REFINEMENT_ENABLED is true"
        )

    if not os.getenv("HF_TOKEN", "").strip():
        missing.append("HF_TOKEN is required for speaker diarization")

    if missing:
        for error in missing:
            logger.error("Startup env check failed: %s", error)
        raise RuntimeError(
            "Missing required environment variables: " + "; ".join(missing)
        )


pipeline: TranscriberPipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    validate_required_env()
    logger.info("Starting transcriber service...")
    pipeline = TranscriberPipeline()
    pipeline.preload()
    logger.info("Transcriber service ready")
    yield
    pipeline = None


app = FastAPI(title="Transcriber Service", lifespan=lifespan)


class TranscribeRequest(BaseModel):
    audio_path: str
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None


class SegmentResponse(BaseModel):
    speaker: str | None = None
    start: float
    end: float
    text: str


class AiStatusResponse(BaseModel):
    speaker_refinement: str = "disabled"
    transcript_refinement: str = "disabled"


class TranscribeResponse(BaseModel):
    segments: list[SegmentResponse]
    ai_status: AiStatusResponse | None = None
    # metrics: modal_seconds, claude_*_tokens, *_cost_usd, preprocessing — для admin-панели.
    metrics: dict[str, Any] = {}


class SpeakerReviewRequest(BaseModel):
    audio_path: str
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    samples_per_speaker: int = 3
    sample_max_seconds: float = 12.0


class SpeakerSegmentResponse(BaseModel):
    start: float
    end: float


class SpeakerReviewItemResponse(BaseModel):
    speaker_label: str
    current_name: str
    confidence: float
    is_known: bool
    segments: list[SpeakerSegmentResponse]
    sample_count: int


class SpeakerReviewResponse(BaseModel):
    meeting_key: str
    items: list[SpeakerReviewItemResponse]


class SpeakerLabelRequest(BaseModel):
    name: str
    alpha: float = 0.05


class SpeakerMergedLabelResponse(BaseModel):
    speaker_label: str
    previous_name: str
    name: str
    confidence: float


class SpeakerLabelResponse(BaseModel):
    meeting_key: str
    speaker_label: str
    previous_name: str
    name: str
    merged_labels: list[SpeakerMergedLabelResponse] = []


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(request: TranscribeRequest):
    """Транскрибировать аудиофайл с диаризацией."""
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")

    if not os.path.exists(request.audio_path):
        raise HTTPException(
            status_code=400,
            detail=f"Audio file not found: {request.audio_path}",
        )

    try:
        result: TranscribeResult = pipeline.transcribe(
            request.audio_path,
            num_speakers=request.num_speakers,
            min_speakers=request.min_speakers,
            max_speakers=request.max_speakers,
        )
        return TranscribeResponse(
            segments=[
                SegmentResponse(
                    speaker=seg.speaker,
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                )
                for seg in result.segments
            ],
            ai_status=AiStatusResponse(
                speaker_refinement=result.ai_status.speaker_refinement,
                transcript_refinement=result.ai_status.transcript_refinement,
            ),
            metrics=dict(result.metrics or {}),
        )
    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/speaker-review", response_model=SpeakerReviewResponse)
async def speaker_review(request: SpeakerReviewRequest):
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")

    if not os.path.exists(request.audio_path):
        raise HTTPException(
            status_code=400,
            detail=f"Audio file not found: {request.audio_path}",
        )

    try:
        bundle = pipeline.prepare_speaker_review(
            request.audio_path,
            num_speakers=request.num_speakers,
            min_speakers=request.min_speakers,
            max_speakers=request.max_speakers,
            samples_per_speaker=request.samples_per_speaker,
            sample_max_seconds=request.sample_max_seconds,
        )
        items = []
        ordered_labels = list(bundle.get("ordered_labels") or [])
        for speaker_label in bundle.get("cluster_profiles", {}):
            if speaker_label not in ordered_labels:
                ordered_labels.append(speaker_label)

        for speaker_label in ordered_labels:
            profile = bundle["cluster_profiles"].get(speaker_label, {})
            result = bundle.get("mapping", {}).get(speaker_label)
            current_name = result.name if result else speaker_label
            confidence = result.confidence if result else 0.0
            is_known = result.is_known if result else False
            sample_segments = bundle.get("sample_segments", {})
            if speaker_label in sample_segments:
                segments = sample_segments[speaker_label]
            else:
                segments = (
                    profile.get("embedding_segments") or profile.get("segments") or []
                )
            items.append(
                SpeakerReviewItemResponse(
                    speaker_label=speaker_label,
                    current_name=current_name,
                    confidence=confidence,
                    is_known=is_known,
                    segments=[
                        SpeakerSegmentResponse(
                            start=float(segment["start"]),
                            end=float(segment["end"]),
                        )
                        for segment in segments[:3]
                    ],
                    sample_count=len(
                        bundle.get("sample_paths", {}).get(speaker_label, [])
                    ),
                )
            )

        return SpeakerReviewResponse(
            meeting_key=str(bundle["meeting_key"]),
            items=items,
        )
    except Exception as e:
        logger.exception("Speaker review preparation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/speaker-review/{meeting_key}/{speaker_label}/samples/{sample_index}")
async def speaker_review_sample(
    meeting_key: str,
    speaker_label: str,
    sample_index: int,
):
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")

    bundle = pipeline.voice_bank.load_meeting_bundle_by_key(meeting_key)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Review bundle not found")

    sample_path = (
        pipeline.voice_bank.meeting_dir_for_key(meeting_key)
        / "samples"
        / f"{speaker_label}_{sample_index}.wav"
    )
    if not sample_path.exists():
        raise HTTPException(status_code=404, detail="Sample clip not found")

    return FileResponse(
        sample_path,
        media_type="audio/wav",
        filename=sample_path.name,
    )


@app.post(
    "/speaker-review/{meeting_key}/{speaker_label}/label",
    response_model=SpeakerLabelResponse,
)
async def speaker_review_label(
    meeting_key: str,
    speaker_label: str,
    request: SpeakerLabelRequest,
):
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")

    try:
        result = pipeline.label_speaker_from_review(
            meeting_key=meeting_key,
            speaker_label=speaker_label,
            name=request.name,
            alpha=request.alpha,
        )
        return SpeakerLabelResponse(
            meeting_key=result["meeting_key"],
            speaker_label=result["speaker_label"],
            previous_name=result["previous_name"],
            name=result["name"],
            merged_labels=[
                SpeakerMergedLabelResponse(
                    speaker_label=item["speaker_label"],
                    previous_name=item["previous_name"],
                    name=item["name"],
                    confidence=float(item["confidence"]),
                )
                for item in result.get("merged_labels", [])
            ],
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Speaker label update failed")
        raise HTTPException(status_code=500, detail=str(e))


class VoiceBankSpeaker(BaseModel):
    """Один спикер из voice-bank с агрегатами для admin-панели."""

    name: str
    n_samples: int
    is_known: bool = True
    confidence: float | None = None
    last_seen: str | None = None
    enrolled_at: str | None = None
    updated_at: str | None = None


class VoiceBankRenameRequest(BaseModel):
    new_name: str


class VoiceBankMergeRequest(BaseModel):
    source: str
    target: str


class ReviewQueueCandidate(BaseModel):
    name: str
    score: float


class ReviewQueueSample(BaseModel):
    index: int
    path: str


class ReviewQueueItem(BaseModel):
    meeting_id: str
    cluster_label: str
    confidence: float
    samples: list[ReviewQueueSample]
    candidates: list[ReviewQueueCandidate]


def _require_voice_bank():
    if pipeline is None or getattr(pipeline, "voice_bank", None) is None:
        raise HTTPException(status_code=503, detail="Voice bank not ready")
    return pipeline.voice_bank


@app.get("/voice-bank/speakers", response_model=list[VoiceBankSpeaker])
async def voice_bank_speakers() -> list[VoiceBankSpeaker]:
    """Список всех спикеров с агрегатами из index.json."""
    voice_bank = _require_voice_bank()
    result: list[VoiceBankSpeaker] = []
    for speaker in voice_bank.list_speakers():
        name = speaker.get("name", "")
        n_samples = int(speaker.get("n_samples", speaker.get("num_embeddings", 0)) or 0)
        result.append(
            VoiceBankSpeaker(
                name=name,
                n_samples=n_samples,
                is_known=True,
                confidence=speaker.get("confidence"),
                last_seen=speaker.get("last_seen") or speaker.get("updated_at"),
                enrolled_at=speaker.get("enrolled_at"),
                updated_at=speaker.get("updated_at"),
            )
        )
    return result


@app.delete("/voice-bank/{name}")
async def voice_bank_delete(name: str) -> dict[str, str]:
    """Полностью удалить спикера (index + embeddings.npz)."""
    voice_bank = _require_voice_bank()
    speakers = {s["name"] for s in voice_bank.list_speakers()}
    if name not in speakers:
        raise HTTPException(status_code=404, detail=f"Speaker not found: {name}")
    voice_bank.remove(name)
    return {"status": "ok", "name": name}


@app.post("/voice-bank/{name}/rename")
async def voice_bank_rename(
    name: str, request: VoiceBankRenameRequest
) -> dict[str, str]:
    """Переименовать спикера. 409 если new_name занят, 404 если name не найден."""
    voice_bank = _require_voice_bank()
    try:
        ok = voice_bank.rename(name, request.new_name)
    except ValueError as exc:
        message = str(exc)
        if "существует" in message:
            raise HTTPException(status_code=409, detail=message)
        raise HTTPException(status_code=400, detail=message)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Speaker not found: {name}")
    return {"status": "ok", "name": request.new_name}


@app.post("/voice-bank/merge")
async def voice_bank_merge(request: VoiceBankMergeRequest) -> dict[str, Any]:
    """Объединить source → target. Возвращает итоговый n_samples у target."""
    voice_bank = _require_voice_bank()
    try:
        n_samples = voice_bank.merge(request.source, request.target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "status": "ok",
        "source": request.source,
        "target": request.target,
        "n_samples": n_samples,
    }


@app.get("/voice-bank/similarity-matrix")
async def voice_bank_similarity_matrix() -> dict[str, dict[str, float]]:
    """Попарная cosine-similarity между centroid'ами всех known speakers."""
    voice_bank = _require_voice_bank()
    return voice_bank.similarity_matrix()


@app.get("/review-queue", response_model=list[ReviewQueueItem])
async def review_queue() -> list[ReviewQueueItem]:
    """Агрегированная очередь review-кластеров из meetings/*/bundle.json."""
    voice_bank = _require_voice_bank()
    items = voice_bank.list_review_queue()
    return [
        ReviewQueueItem(
            meeting_id=item["meeting_id"],
            cluster_label=item["cluster_label"],
            confidence=float(item.get("confidence") or 0.0),
            samples=[
                ReviewQueueSample(index=int(s["index"]), path=str(s["path"]))
                for s in item.get("samples", [])
            ],
            candidates=[
                ReviewQueueCandidate(name=str(c["name"]), score=float(c["score"]))
                for c in item.get("candidates", [])
            ],
        )
        for item in items
    ]


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "pipeline_ready": pipeline is not None,
    }
