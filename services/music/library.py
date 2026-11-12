"""Zenei konyvtar segedek: szamok listazasa, hossza es biztonsagos utvonal."""

from __future__ import annotations

import os

import mutagen
from mutagen import MutagenError

MUSIC_EXTENSIONS = (".mp3", ".wav", ".ogg")


def track_duration(path: str) -> float | None:
    """A szam hossza masodpercben, vagy None, ha nem olvashato."""
    try:
        return float(mutagen.File(path).info.length)
    except (MutagenError, OSError, AttributeError):
        return None


def list_tracks(root: str) -> list[dict]:
    """A mappa zenei fajljai nev szerint (casefold) rendezve."""
    if not os.path.isdir(root):
        return []
    names = sorted(
        (f for f in os.listdir(root) if f.lower().endswith(MUSIC_EXTENSIONS)),
        key=str.casefold,
    )
    tracks = []
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        tracks.append(
            {
                "name": name,
                "size_bytes": os.path.getsize(path),
                "duration_s": track_duration(path),
            }
        )
    return tracks


def resolve_track(root: str, name: str) -> str | None:
    """A root mappabeli, letezo fajl abszolut utvonala, kulonben None."""
    safe = os.path.basename(str(name or "").strip())
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, safe))
    if not safe or not path.startswith(base + os.sep) or not os.path.isfile(path):
        return None
    return path
