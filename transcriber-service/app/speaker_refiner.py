import json
import logging
import os
import socket
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

logger = logging.getLogger(__name__)


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ADVISOR_BETA_HEADER = "advisor-tool-2026-03-01"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class AnthropicAdvisorSpeakerRefiner:
    """Post-process speaker labels with Claude while preserving text/timing."""

    def __init__(
        self,
        api_key: str | None = None,
        executor_model: str = "claude-sonnet-4-6",
        advisor_model: str = "claude-opus-4-6",
        advisor_enabled: bool = True,
        advisor_max_uses: int = 2,
        max_tokens: int = 4096,
        timeout_seconds: float = 120.0,
        max_changes: int = 80,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.executor_model = executor_model
        self.advisor_model = advisor_model
        self.advisor_enabled = advisor_enabled
        self.advisor_max_uses = advisor_max_uses
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.max_changes = max_changes

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            executor_model=os.getenv(
                "SPEAKER_LLM_EXECUTOR_MODEL",
                "claude-sonnet-4-6",
            ),
            advisor_model=os.getenv(
                "SPEAKER_LLM_ADVISOR_MODEL",
                "claude-opus-4-6",
            ),
            advisor_enabled=env_bool("SPEAKER_LLM_ADVISOR_ENABLED", True),
            advisor_max_uses=int(os.getenv("SPEAKER_LLM_ADVISOR_MAX_USES", "2")),
            max_tokens=int(os.getenv("SPEAKER_LLM_MAX_TOKENS", "4096")),
            timeout_seconds=float(os.getenv("SPEAKER_LLM_TIMEOUT_SEC", "120")),
            max_changes=int(os.getenv("SPEAKER_LLM_MAX_CHANGES", "80")),
        )

    def refine(self, segments: list[Any]) -> list[Any]:
        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY is not set; speaker LLM refinement skipped")
            return segments

        allowed_speakers = sorted(
            {str(segment.speaker) for segment in segments if segment.speaker}
        )
        if len(allowed_speakers) < 2:
            return segments

        request_payload = self._build_request_payload(segments, allowed_speakers)
        try:
            response_payload = self._call_messages_api(request_payload)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            logger.warning("Speaker LLM refinement request failed: %s", exc)
            return segments

        text = self._extract_response_text(response_payload)
        try:
            change_payload = self._parse_json_object(text)
        except ValueError as exc:
            logger.warning("Speaker LLM refinement returned invalid JSON: %s", exc)
            return segments

        refined_segments = self.apply_changes(
            segments=segments,
            payload=change_payload,
            allowed_speakers=set(allowed_speakers),
        )
        changed = sum(
            1 for before, after in zip(segments, refined_segments)
            if before.speaker != after.speaker
        )
        logger.info("Speaker LLM refinement applied %d changes", changed)
        return refined_segments

    def apply_changes(
        self,
        segments: list[Any],
        payload: dict[str, Any],
        allowed_speakers: set[str],
    ) -> list[Any]:
        changes = payload.get("changes", [])
        if not isinstance(changes, list):
            raise ValueError("Expected 'changes' to be a list")
        if len(changes) > self.max_changes:
            logger.warning(
                "Speaker LLM refinement returned %d changes; capping at %d",
                len(changes),
                self.max_changes,
            )

        refined_segments = list(segments)
        for change in changes[: self.max_changes]:
            if not isinstance(change, dict):
                continue
            try:
                index = int(change["index"])
            except (KeyError, TypeError, ValueError):
                continue
            speaker = change.get("speaker")
            if not isinstance(speaker, str) or speaker not in allowed_speakers:
                continue
            if index < 0 or index >= len(refined_segments):
                continue
            if refined_segments[index].speaker == speaker:
                continue

            logger.info(
                "Speaker LLM refinement: segment %d %.2f-%.2f %s -> %s",
                index,
                float(refined_segments[index].start),
                float(refined_segments[index].end),
                refined_segments[index].speaker,
                speaker,
            )
            refined_segments[index] = replace(refined_segments[index], speaker=speaker)
        return refined_segments

    def _build_request_payload(
        self,
        segments: list[Any],
        allowed_speakers: list[str],
    ) -> dict[str, Any]:
        tools = []
        if self.advisor_enabled:
            tools.append(
                {
                    "type": "advisor_20260301",
                    "name": "advisor",
                    "model": self.advisor_model,
                    "max_uses": self.advisor_max_uses,
                }
            )

        payload: dict[str, Any] = {
            "model": self.executor_model,
            "max_tokens": self.max_tokens,
            "system": self._system_prompt(),
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "allowed_speakers": allowed_speakers,
                            "segments": [
                                {
                                    "index": index,
                                    "speaker": segment.speaker,
                                    "start": round(float(segment.start), 3),
                                    "end": round(float(segment.end), 3),
                                    "text": str(segment.text)[:700],
                                }
                                for index, segment in enumerate(segments)
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _call_messages_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self.advisor_enabled:
            headers["anthropic-beta"] = ADVISOR_BETA_HEADER

        request = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _extract_response_text(self, response_payload: dict[str, Any]) -> str:
        parts: list[str] = []
        for block in response_payload.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts).strip()

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        if not text:
            raise ValueError("empty response")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("JSON object not found") from None
            payload = json.loads(text[start:end + 1])
        if not isinstance(payload, dict):
            raise ValueError("top-level JSON is not an object")
        return payload

    def _system_prompt(self) -> str:
        return (
            "You are a conservative post-processor for Russian meeting transcripts. "
            "Your only job is to correct obvious speaker-label mistakes. "
            "Never rewrite transcript text. Never change timestamps. "
            "Never invent new speaker names or identities. Use only allowed_speakers. "
            "Prefer no change when uncertain. Use dialogue structure, question-answer "
            "adjacency, short confirmations, and speaker continuity. "
            "If a hard global consistency decision is needed, consult the advisor. "
            "Return only JSON with this schema: "
            "{\"changes\":[{\"index\":0,\"speaker\":\"allowed speaker\","
            "\"confidence\":0.0,\"reason\":\"short reason\"}]}. "
            "Omit unchanged segments."
        )
