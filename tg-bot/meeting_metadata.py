import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ADVISOR_BETA_HEADER = "advisor-tool-2026-03-01"

DEFAULT_EXECUTOR_MODEL = "claude-sonnet-4-6"
DEFAULT_ADVISOR_MODEL = "claude-opus-4-6"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class MetadataRule:
    rule_id: str
    folder_path: list[str]
    title_prefix: str
    keywords_any: list[str] = field(default_factory=list)
    keywords_all: list[str] = field(default_factory=list)
    speaker_keywords_any: list[str] = field(default_factory=list)
    priority: int = 0


@dataclass
class RuleMatch:
    rule: MetadataRule
    score: int


@dataclass
class MeetingMetadata:
    title: str
    folder_path: list[str]
    filename: str
    source: str
    rule_id: str | None = None


DEFAULT_RULES = [
    MetadataRule(
        rule_id="hiring",
        folder_path=["Hiring"],
        title_prefix="Собеседование",
        keywords_any=[
            "вакан",
            "кандидат",
            "резюме",
            "собесед",
            "hr",
            "рекрутер",
            "найм",
            "интервью",
        ],
        priority=90,
    ),
    MetadataRule(
        rule_id="harness",
        folder_path=["Projects", "Harness"],
        title_prefix="Harness",
        keywords_any=[
            "харнесс",
            "harness",
            "benchmark",
            "бенчмарк",
            "eval",
            "evaluation",
            "оценка модели",
            "test harness",
        ],
        speaker_keywords_any=["егор", "вадим", "илья"],
        priority=100,
    ),
    MetadataRule(
        rule_id="product",
        folder_path=["Product"],
        title_prefix="Продуктовая встреча",
        keywords_any=[
            "roadmap",
            "роадмап",
            "feature",
            "фича",
            "метрик",
            "релиз",
            "бэклог",
            "продукт",
            "приорит",
            "пользователь",
        ],
        priority=70,
    ),
    MetadataRule(
        rule_id="research",
        folder_path=["Research"],
        title_prefix="Исследование",
        keywords_any=[
            "llm",
            "модель",
            "rag",
            "агент",
            "prompt",
            "трансформер",
            "inference",
            "fine-tuning",
        ],
        priority=60,
    ),
]


def extract_known_speakers(transcript: dict) -> list[str]:
    speakers: list[str] = []
    seen: set[str] = set()
    for segment in transcript.get("segments", []):
        speaker = str(segment.get("speaker") or "").strip()
        if not speaker:
            continue
        if speaker.startswith("Unknown Speaker") or speaker == "?":
            continue
        if speaker in seen:
            continue
        seen.add(speaker)
        speakers.append(speaker)
    return speakers


def transcript_text_for_metadata(transcript: dict, max_chars: int = 18000) -> str:
    parts: list[str] = []
    for segment in transcript.get("segments", []):
        speaker = str(segment.get("speaker") or "Unknown").strip()
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"{speaker}: {text}")
        if sum(len(part) for part in parts) >= max_chars:
            break
    text = "\n".join(parts)
    return text[:max_chars]


def sanitize_drive_component(value: str, fallback: str = "Meeting") -> str:
    candidate = re.sub(r"[\\/:*?\"<>|#]+", " ", str(value or ""))
    candidate = re.sub(r"\s+", " ", candidate).strip(" .")
    if not candidate:
        candidate = fallback
    return candidate[:120]


def slugify_filename_stem(value: str, fallback: str = "meeting") -> str:
    lowered = str(value or "").lower()
    normalized = re.sub(r"[^a-zа-я0-9]+", "-", lowered, flags=re.IGNORECASE)
    normalized = normalized.strip("-")
    return (normalized or fallback)[:120]


def load_metadata_rules() -> list[MetadataRule]:
    rules = list(DEFAULT_RULES)
    inline_json = os.getenv("MEETING_METADATA_RULES_JSON")
    file_path = os.getenv("MEETING_METADATA_RULES_PATH")

    payload = None
    if inline_json:
        payload = inline_json
    elif file_path and Path(file_path).exists():
        payload = Path(file_path).read_text(encoding="utf-8")

    if not payload:
        return rules

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid MEETING_METADATA_RULES payload: %s", exc)
        return rules

    if not isinstance(data, list):
        logger.warning("MEETING_METADATA_RULES payload must be a list")
        return rules

    for item in data:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or item.get("id") or "").strip()
        folder_path = item.get("folder_path")
        title_prefix = str(item.get("title_prefix") or item.get("title") or "").strip()
        if not rule_id or not isinstance(folder_path, list) or not title_prefix:
            continue
        rules.append(
            MetadataRule(
                rule_id=rule_id,
                folder_path=[sanitize_drive_component(part) for part in folder_path if str(part).strip()],
                title_prefix=title_prefix,
                keywords_any=[str(value).lower() for value in item.get("keywords_any", [])],
                keywords_all=[str(value).lower() for value in item.get("keywords_all", [])],
                speaker_keywords_any=[str(value).lower() for value in item.get("speaker_keywords_any", [])],
                priority=int(item.get("priority", 50)),
            )
        )
    return rules


def classify_meeting_by_rules(transcript: dict, rules: list[MetadataRule] | None = None) -> RuleMatch | None:
    rules = rules or load_metadata_rules()
    text = transcript_text_for_metadata(transcript).lower()
    speakers = " ".join(extract_known_speakers(transcript)).lower()
    best_match: RuleMatch | None = None

    for rule in rules:
        if rule.keywords_all and not all(keyword in text for keyword in rule.keywords_all):
            continue

        keyword_hits = sum(1 for keyword in rule.keywords_any if keyword in text)
        speaker_hits = sum(1 for keyword in rule.speaker_keywords_any if keyword in speakers)

        if keyword_hits == 0 and speaker_hits == 0:
            continue

        score = (rule.priority * 10) + (keyword_hits * 3) + (speaker_hits * 2)
        match = RuleMatch(rule=rule, score=score)
        if best_match is None or match.score > best_match.score:
            best_match = match

    return best_match


def build_rule_based_metadata(
    transcript: dict,
    source_filename: str | None = None,
    now: datetime | None = None,
) -> MeetingMetadata:
    now = now or datetime.now()
    speakers = extract_known_speakers(transcript)
    rule_match = classify_meeting_by_rules(transcript)

    if rule_match is not None:
        folder_path = rule_match.rule.folder_path
        prefix = rule_match.rule.title_prefix
    else:
        folder_path = ["General"]
        prefix = "Встреча"

    title_parts = [prefix]
    if speakers:
        title_parts.append(", ".join(speakers[:3]))
    elif source_filename:
        title_parts.append(Path(source_filename).stem[:60])
    title = " — ".join(part for part in title_parts if part).strip()
    title = sanitize_drive_component(title, fallback="Встреча")
    date_suffix = now.strftime("%Y-%m-%d")
    filename = f"{slugify_filename_stem(title)}_{date_suffix}.md"
    return MeetingMetadata(
        title=title,
        folder_path=folder_path,
        filename=filename,
        source="rule",
        rule_id=rule_match.rule.rule_id if rule_match else None,
    )


class AnthropicMeetingMetadataGenerator:
    def __init__(
        self,
        api_key: str | None = None,
        executor_model: str = DEFAULT_EXECUTOR_MODEL,
        advisor_model: str = DEFAULT_ADVISOR_MODEL,
        advisor_enabled: bool = True,
        advisor_max_uses: int = 2,
        timeout_seconds: float = 120.0,
        max_tokens: int = 1024,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.executor_model = executor_model
        self.advisor_model = advisor_model
        self.advisor_enabled = advisor_enabled
        self.advisor_max_uses = advisor_max_uses
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            executor_model=os.getenv(
                "MEETING_METADATA_EXECUTOR_MODEL",
                DEFAULT_EXECUTOR_MODEL,
            ),
            advisor_model=os.getenv(
                "MEETING_METADATA_ADVISOR_MODEL",
                DEFAULT_ADVISOR_MODEL,
            ),
            advisor_enabled=env_bool("MEETING_METADATA_ADVISOR_ENABLED", True),
            advisor_max_uses=int(os.getenv("MEETING_METADATA_ADVISOR_MAX_USES", "2")),
            timeout_seconds=float(os.getenv("MEETING_METADATA_TIMEOUT_SEC", "120")),
            max_tokens=int(os.getenv("MEETING_METADATA_MAX_TOKENS", "1024")),
        )

    def refine(
        self,
        transcript: dict,
        base_metadata: MeetingMetadata,
        source_filename: str | None = None,
    ) -> MeetingMetadata:
        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY is not set; metadata LLM refinement skipped")
            return base_metadata

        payload = self._build_request_payload(
            transcript=transcript,
            base_metadata=base_metadata,
            source_filename=source_filename,
        )
        try:
            response_payload = self._call_messages_api(payload)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            logger.warning("Meeting metadata LLM request failed: %s", exc)
            return base_metadata

        text = self._extract_response_text(response_payload)
        try:
            parsed = self._parse_json_object(text)
        except ValueError as exc:
            logger.warning("Meeting metadata LLM returned invalid JSON: %s", exc)
            return base_metadata

        title = sanitize_drive_component(
            str(parsed.get("title") or base_metadata.title),
            fallback=base_metadata.title,
        )

        folder_path = base_metadata.folder_path
        if base_metadata.rule_id is None:
            raw_folder_path = parsed.get("folder_path")
            if isinstance(raw_folder_path, list):
                folder_path = [
                    sanitize_drive_component(str(part), fallback="General")
                    for part in raw_folder_path[:4]
                    if str(part).strip()
                ] or folder_path

        filename = parsed.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            filename = base_metadata.filename
        else:
            filename = sanitize_drive_component(filename, fallback=base_metadata.filename)
            if not filename.endswith(".md"):
                filename = f"{filename}.md"

        return MeetingMetadata(
            title=title,
            folder_path=folder_path,
            filename=filename,
            source="llm",
            rule_id=base_metadata.rule_id,
        )

    def _build_request_payload(
        self,
        transcript: dict,
        base_metadata: MeetingMetadata,
        source_filename: str | None,
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
                            "base_metadata": {
                                "title": base_metadata.title,
                                "folder_path": base_metadata.folder_path,
                                "filename": base_metadata.filename,
                                "rule_id": base_metadata.rule_id,
                                "folder_path_locked": base_metadata.rule_id is not None,
                            },
                            "source_filename": source_filename,
                            "known_speakers": extract_known_speakers(transcript),
                            "transcript_excerpt": transcript_text_for_metadata(transcript, max_chars=12000),
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
            "You generate semantic Google Drive metadata for Russian meeting transcripts. "
            "Return only JSON with keys title, folder_path, filename, reason. "
            "Keep titles short and human-readable. "
            "folder_path must be a list of 1 to 4 folder names. "
            "If folder_path_locked is true, do not change folder_path. "
            "Prefer categories like Product, Hiring, Projects, Research, Ops, General. "
            "Use participants and transcript meaning. Do not invent people or facts."
        )


def resolve_meeting_metadata(
    transcript: dict,
    source_filename: str | None = None,
) -> MeetingMetadata:
    metadata = build_rule_based_metadata(transcript, source_filename=source_filename)
    if not env_bool("MEETING_METADATA_LLM_ENABLED", False):
        return metadata
    refiner = AnthropicMeetingMetadataGenerator.from_env()
    return refiner.refine(
        transcript=transcript,
        base_metadata=metadata,
        source_filename=source_filename,
    )
