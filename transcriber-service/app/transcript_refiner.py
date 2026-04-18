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
DEFAULT_EXECUTOR_MODEL = "claude-sonnet-4-6"
DEFAULT_ADVISOR_MODEL = "claude-opus-4-7"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class AnthropicAdvisorTranscriptRefiner:
    """Post-process transcript text with Claude while preserving structure."""

    def __init__(
        self,
        api_key: str | None = None,
        executor_model: str = DEFAULT_EXECUTOR_MODEL,
        advisor_model: str = DEFAULT_ADVISOR_MODEL,
        advisor_enabled: bool = True,
        advisor_max_uses: int = 2,
        max_tokens: int = 4096,
        timeout_seconds: float = 120.0,
        max_changes: int = 120,
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
                "TRANSCRIPT_LLM_EXECUTOR_MODEL",
                DEFAULT_EXECUTOR_MODEL,
            ),
            advisor_model=os.getenv(
                "TRANSCRIPT_LLM_ADVISOR_MODEL",
                DEFAULT_ADVISOR_MODEL,
            ),
            advisor_enabled=env_bool("TRANSCRIPT_LLM_ADVISOR_ENABLED", True),
            advisor_max_uses=int(os.getenv("TRANSCRIPT_LLM_ADVISOR_MAX_USES", "2")),
            max_tokens=int(os.getenv("TRANSCRIPT_LLM_MAX_TOKENS", "4096")),
            timeout_seconds=float(os.getenv("TRANSCRIPT_LLM_TIMEOUT_SEC", "120")),
            max_changes=int(os.getenv("TRANSCRIPT_LLM_MAX_CHANGES", "120")),
        )

    def refine(self, segments: list[Any]) -> list[Any]:
        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY is not set; transcript LLM refinement skipped")
            return segments

        if not segments:
            return segments

        request_payload = self._build_request_payload(segments)
        try:
            response_payload = self._call_messages_api(request_payload)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            logger.warning("Transcript LLM refinement request failed: %s", exc)
            return segments

        self._log_cache_usage(response_payload)
        text = self._extract_response_text(response_payload)
        try:
            change_payload = self._parse_json_object(text)
        except ValueError as exc:
            logger.warning("Transcript LLM refinement returned invalid JSON: %s", exc)
            return segments

        refined_segments = self.apply_changes(segments=segments, payload=change_payload)
        changed = sum(
            1 for before, after in zip(segments, refined_segments)
            if before.text != after.text
        )
        logger.info("Transcript LLM refinement applied %d text changes", changed)
        return refined_segments

    def apply_changes(
        self,
        segments: list[Any],
        payload: dict[str, Any],
    ) -> list[Any]:
        changes = payload.get("changes", [])
        if not isinstance(changes, list):
            raise ValueError("Expected 'changes' to be a list")
        if len(changes) > self.max_changes:
            logger.warning(
                "Transcript LLM refinement returned %d changes; capping at %d",
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
            text = change.get("text")
            if not isinstance(text, str):
                continue
            text = text.strip()
            if not text:
                continue
            if index < 0 or index >= len(refined_segments):
                continue
            if refined_segments[index].text == text:
                continue

            logger.info(
                "Transcript LLM refinement: segment %d %.2f-%.2f text updated",
                index,
                float(refined_segments[index].start),
                float(refined_segments[index].end),
            )
            refined_segments[index] = replace(refined_segments[index], text=text)
        return refined_segments

    def _build_request_payload(
        self,
        segments: list[Any],
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
            "system": [
                {
                    "type": "text",
                    "text": self._system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "segments": [
                                {
                                    "index": index,
                                    "speaker": segment.speaker,
                                    "start": round(float(segment.start), 3),
                                    "end": round(float(segment.end), 3),
                                    "text": str(segment.text),
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

    def _log_cache_usage(self, response_payload: dict[str, Any]) -> None:
        usage = response_payload.get("usage") or {}
        logger.info(
            "Transcript LLM usage: input=%s output=%s cache_write=%s cache_read=%s",
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("cache_creation_input_tokens"),
            usage.get("cache_read_input_tokens"),
        )

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
            "You are a post-processor for Russian meeting transcripts. "
            "Your goal is to improve readability while strictly preserving meaning.\n\n"
            "DO:\n"
            "- Fix obvious ASR errors: misheard words, homophones, character confusions.\n"
            "- Correct punctuation, capitalization, quotation marks per Russian norms.\n"
            "- Fix grammar (case, agreement, word order) when the intended meaning is clear.\n"
            "- Expand spoken contractions to literary form: 'щас' -> 'сейчас', "
            "'чё'/'че' -> 'что', 'тыща' -> 'тысяча', 'норм' -> 'нормально', "
            "'пасиб' -> 'спасибо', 'тока' -> 'только'.\n"
            "- Remove ASR-induced duplications (repeated words, stutters, partial retries).\n"
            "- Remove pure filler sounds: 'э-э', 'ммм', 'эээ', 'а-а'.\n"
            "- Drop 'ну'/'вот'/'как бы'/'типа'/'короче' only when they are clearly "
            "meaningless filler; keep them when they carry intonation or meaning.\n"
            "- Apply correct spelling of person/product names when strongly supported "
            "by context (e.g., repeated mentions with clearer pronunciation elsewhere).\n\n"
            "DO NOT:\n"
            "- Change timestamps, segment count, or move text between segments.\n"
            "- Invent facts, numbers, names, or information not in the source.\n"
            "- Change meaning, tone, register, or politeness level.\n"
            "- Translate, paraphrase, or change the language.\n"
            "- Change speaker labels (handled elsewhere).\n"
            "- Edit segments whose wording is already correct.\n\n"
            "Prefer useful improvement over excessive caution. When truly ambiguous "
            "between fixing and preserving, preserve. If a global consistency decision "
            "is needed (one name/term spelled across the transcript), consult the "
            "advisor.\n\n"
            "Return ONLY JSON with this schema: "
            "{\"changes\":[{\"index\":0,\"text\":\"updated text\","
            "\"confidence\":0.0,\"reason\":\"short reason\"}]}. "
            "Omit unchanged segments."
        )
