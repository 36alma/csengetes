"""YouTube hang letoltese MP3-ba (yt-dlp + ffmpeg)."""

from __future__ import annotations

import logging
import os
import shutil
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

logger = logging.getLogger("csengetes")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "download")

ALLOWED_HOSTS = ("youtube.com", "youtu.be")


class MusicDownloadError(Exception):
    pass


def validate_url(url: str) -> str:
    """Csak http(s) YouTube linket enged at, kulonben MusicDownloadError."""
    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not any(
        host == h or host.endswith("." + h) for h in ALLOWED_HOSTS
    ):
        raise MusicDownloadError(f"Nem engedelyezett URL: {url!r}")
    return parsed.geturl()


class MusicDownload:
    def __init__(self, url: list[str], preferredquality: int = 192) -> None:
        self.url = [validate_url(u) for u in url]
        self.preferredquality = preferredquality
        self.opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
            "restrictfilenames": True,
            "noplaylist": False,
            "quiet": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(self.preferredquality),
                }
            ],
        }

    def __call__(self) -> list[str]:
        """Letolti a linkeket, es a kesz mp3 fajlok abszolut utvonalait adja vissza."""
        if shutil.which("ffmpeg") is None:
            raise MusicDownloadError("Az ffmpeg nincs telepitve vagy nincs a PATH-on.")
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        filenames: list[str] = []
        try:
            with YoutubeDL(self.opts) as ydl:
                for url in self.url:
                    info = ydl.extract_info(url, download=True)
                    entries = info["entries"] if info.get("_type") == "playlist" else [info]
                    for entry in entries:
                        if not entry:
                            continue
                        path = os.path.abspath(entry["requested_downloads"][0]["filepath"])
                        filenames.append(path)
        except Exception as exc:
            logger.error("Zene letoltesi hiba: %s", exc)
            raise MusicDownloadError(str(exc)) from exc
        logger.info("Zene letoltve: %d fajl", len(filenames))
        return filenames
