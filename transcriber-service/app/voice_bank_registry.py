"""Реестр opaque voice_bank_id <-> display_name для публичного /v1 API.

VoiceBank сам по себе адресуется по имени спикера (``Илья Савин``), но во
внешнем контракте используется opaque-строка (``recruiter-ilya-savin`` и т.п.),
чтобы потребитель мог хранить ID в своей БД и не зависеть от человекочитаемых
имён. Реестр — это тонкая JSON-проекция: id → имя (основная запись) и кэш
reverse-lookup имя → id (чтобы find_id_for_name работал за O(1)).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def slugify(name: str) -> str:
    lowered = name.strip().lower()
    transliterated = "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in lowered)
    slug = _SLUG_RE.sub("-", transliterated).strip("-")
    return slug or "speaker"


def generate_voice_bank_id(display_name: str) -> str:
    return f"{slugify(display_name)}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class RegistryEntry:
    voice_bank_id: str
    display_name: str


class VoiceBankRegistry:
    """Thread-safe JSON-файл с двунаправленным отображением id <-> name."""

    def __init__(self, path: str | os.PathLike):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read voice-bank registry %s: %s", self._path, exc)
            return {}
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Voice-bank registry %s is malformed: %s", self._path, exc)
            return {}
        # Поддерживаем форматы: {"entries": {id: name}} и «плоский» {id: name}.
        if isinstance(payload, dict) and "entries" in payload:
            entries = payload.get("entries") or {}
        else:
            entries = payload
        return {str(k): str(v) for k, v in dict(entries).items()}

    def _persist(self, mapping: dict[str, str]) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".voice_bank_ids-", suffix=".json", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump({"entries": mapping}, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    def list_entries(self) -> list[RegistryEntry]:
        with self._lock:
            mapping = self._load()
        return [RegistryEntry(vb_id, name) for vb_id, name in sorted(mapping.items())]

    def get_name(self, voice_bank_id: str) -> str | None:
        with self._lock:
            return self._load().get(voice_bank_id)

    def find_id_for_name(self, display_name: str) -> str | None:
        """Любой ID, который сейчас ссылается на это имя. Не детерминирован при
        наличии нескольких исторических ID — возвращает первый по алфавиту."""
        with self._lock:
            mapping = self._load()
        for vb_id in sorted(mapping):
            if mapping[vb_id] == display_name:
                return vb_id
        return None

    def assign(
        self,
        display_name: str,
        voice_bank_id: str | None = None,
    ) -> str:
        """Создаёт запись id → name. Если id не задан, генерируется новый.

        Если id уже существует и указывает на другое имя — перезаписываем
        (у внешнего потребителя право решать, кому принадлежит ID).
        """
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("display_name is required")
        new_id = voice_bank_id or generate_voice_bank_id(display_name)
        with self._lock:
            mapping = self._load()
            mapping[new_id] = display_name
            self._persist(mapping)
        return new_id

    def remove(self, voice_bank_id: str) -> None:
        with self._lock:
            mapping = self._load()
            if voice_bank_id in mapping:
                del mapping[voice_bank_id]
                self._persist(mapping)

    def rename(self, voice_bank_id: str, new_display_name: str) -> None:
        new_display_name = new_display_name.strip()
        if not new_display_name:
            raise ValueError("new_display_name is required")
        with self._lock:
            mapping = self._load()
            if voice_bank_id not in mapping:
                raise KeyError(voice_bank_id)
            mapping[voice_bank_id] = new_display_name
            self._persist(mapping)
