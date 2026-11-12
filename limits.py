"""Tarhely- es meretkorlatok. A kornyezeti valtozokat minden hivaskor ujra olvassa."""
from __future__ import annotations

import math
import os
from typing import BinaryIO

from config_store import config

_MB = 1024 * 1024
_COPY_CHUNK = 64 * 1024


def _env_mb(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def max_storage_bytes() -> int:
    mb = _env_mb("CSENGETES_MAX_STORAGE_MB")
    return int((mb if mb is not None else 5120) * _MB)


def max_upload_bytes() -> int:
    mb = _env_mb("CSENGETES_MAX_UPLOAD_MB")
    if mb is None:
        mb = config.MAX_UPLOAD_MB
    return int(mb * _MB)


def max_download_bytes() -> int:
    mb = _env_mb("CSENGETES_MAX_DOWNLOAD_MB")
    return int((mb if mb is not None else 100) * _MB)


def max_yt_seconds() -> int:
    minutes = _env_mb("CSENGETES_MAX_YT_MINUTES")
    return int((minutes if minutes is not None else 15) * 60)


def dir_size_bytes(*dirs: str) -> int:
    """A megadott mappak kozvetlen fajljainak merete. Symlink es alkonyvtar kimarad, hianyzo mappa 0."""
    total = 0
    for d in dirs:
        if not os.path.isdir(d):
            continue
        with os.scandir(d) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


def storage_info(*dirs: str) -> dict:
    return {"used_bytes": dir_size_bytes(*dirs), "max_bytes": max_storage_bytes()}


def check_space(extra_bytes: int, *dirs: str) -> str | None:
    used = dir_size_bytes(*dirs)
    max_bytes = max_storage_bytes()
    if used + extra_bytes > max_bytes:
        return f"Nincs eleg tarhely ({used / _MB:.0f} / {max_bytes / _MB:.0f} MB hasznalt)."
    return None


def copy_limited(src: BinaryIO, dest_path: str, max_bytes: int) -> bool:
    """Masolja a `src` tartalmat `dest_path`-ra. Ha tobb mint `max_bytes`, torli a reszleges fajlt es False-t ad."""
    ok = False
    try:
        with open(dest_path, "wb") as out:
            total = 0
            while True:
                chunk = src.read(_COPY_CHUNK)
                if not chunk:
                    ok = True
                    break
                total += len(chunk)
                if total > max_bytes:
                    break
                out.write(chunk)
    finally:
        if not ok and os.path.exists(dest_path):
            os.remove(dest_path)
    return ok
