"""Csak helyi, engedelyezett mappaban levo, engedelyezett tipusu hangfajl jatszhato le."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from .errors import AudioPlayerError

logger = logging.getLogger("csengetes")

MAX_FILE_BYTES = 200 * 1024 * 1024

# a libsndfile altal dekodolhato formatumok
ALLOWED_EXTENSIONS = frozenset({".mp3", ".wav", ".ogg", ".oga", ".flac", ".opus"})

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ALLOWED_ROOTS = (
    os.path.join(_BASE_DIR, "media"),
    os.path.join(_BASE_DIR, "download"),
)


def normalize_roots(roots: Iterable[str] | None) -> list[str]:
    roots = DEFAULT_ALLOWED_ROOTS if roots is None else roots
    return [os.path.normcase(os.path.realpath(r)) for r in roots]


def validate_audio_path(filepath, roots: list[str], max_bytes: int = MAX_FILE_BYTES) -> str:
    """Visszaadja a feloldott, engedelyezett fajl utvonalat, kulonben AudioPlayerError."""
    try:
        raw = os.fspath(filepath)
    except TypeError:
        raise AudioPlayerError("Ervenytelen fajlutvonal.") from None
    if isinstance(raw, bytes) or not raw or "\x00" in raw:
        raise AudioPlayerError("Ervenytelen fajlutvonal.")
    if "://" in raw or raw.startswith(("\\\\", "//")):
        logger.warning("Nem helyi utvonal elutasitva: %r", raw)
        raise AudioPlayerError("Csak helyi fajl jatszhato le.")

    real = os.path.realpath(raw)
    key = os.path.normcase(real)
    if not any(key == r or key.startswith(r + os.sep) for r in roots):
        logger.warning("Engedelyezett mappan kivuli fajl elutasitva: %s", real)
        raise AudioPlayerError("A fajl nem engedelyezett helyen van.")
    if Path(real).suffix.lower() not in ALLOWED_EXTENSIONS:
        logger.warning("Nem engedelyezett fajltipus elutasitva: %s", real)
        raise AudioPlayerError("Nem tamogatott fajltipus.")
    if not os.path.isfile(real):
        raise AudioPlayerError("A fajl nem talalhato.")
    try:
        size = os.path.getsize(real)
    except OSError:
        raise AudioPlayerError("A fajl nem olvashato.") from None
    if size <= 0 or size > max_bytes:
        logger.warning("Ervenytelen fajlmeret (%s bajt): %s", size, real)
        raise AudioPlayerError("A fajl merete nem megfelelo.")
    return real
