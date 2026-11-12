"""YouTube hang letoltese MP3-ba (yt-dlp + ffmpeg)."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

import limits

logger = logging.getLogger("csengetes")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "download")

ALLOWED_HOSTS = ("youtube.com", "youtu.be")
_MB = 1024 * 1024


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
    def __init__(
        self,
        url: list[str],
        preferredquality: int = 192,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> None:
        self.url = [validate_url(u) for u in url]
        self.preferredquality = preferredquality
        self.progress_cb = progress_cb
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

    def check_limits(self) -> list[dict]:
        """Letoltes elott ellenorzi a hosszt: elemenkent {title, duration_s, est_bytes}. Hibánál MusicDownloadError."""
        max_s = limits.max_yt_seconds()
        items: list[dict] = []
        try:
            with YoutubeDL({"quiet": True, "noplaylist": False, "skip_download": True}) as ydl:
                for url in self.url:
                    info = ydl.extract_info(url, download=False)
                    entries = (info.get("entries") or []) if info.get("_type") == "playlist" else [info]
                    for entry in entries:
                        if not entry:
                            continue
                        title = str(entry.get("title") or url)
                        if entry.get("is_live"):
                            raise MusicDownloadError(f"A(z) '{title}' elo adas, nem toltheto le.")
                        duration = entry.get("duration")
                        if duration is None:
                            raise MusicDownloadError(f"A(z) '{title}' hossza ismeretlen, nem toltheto le.")
                        duration_s = float(duration)
                        if duration_s > max_s:
                            raise MusicDownloadError(
                                f"A(z) '{title}' tul hosszu ({duration_s / 60:.1f} perc), "
                                f"a megengedett maximum {max_s / 60:g} perc."
                            )
                        items.append(
                            {
                                "title": title,
                                "duration_s": duration_s,
                                "est_bytes": int(duration_s * self.preferredquality * 1000 / 8),
                            }
                        )
        except MusicDownloadError:
            raise
        except Exception as exc:
            logger.error("Zene hosszellenorzesi hiba: %s", exc)
            raise MusicDownloadError(str(exc)) from exc
        return items

    def _on_progress(self, d: dict) -> None:
        """yt-dlp progress hook: a progress_cb-nek {status, percent, title} dictet ad."""
        if self.progress_cb is None:
            return
        status = d.get("status")
        if status not in ("downloading", "finished"):
            return
        if status == "finished":
            percent = 100.0
        else:
            done = d.get("downloaded_bytes")
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            percent = done / total * 100 if done is not None and total else None
        title = (d.get("info_dict") or {}).get("title")
        if not title:
            title = os.path.splitext(os.path.basename(d.get("filename") or ""))[0]
        self.progress_cb({"status": status, "percent": percent, "title": str(title)})

    def __call__(self) -> list[str]:
        """Letolti a linkeket, es a kesz mp3 fajlok abszolut utvonalait adja vissza."""
        if shutil.which("ffmpeg") is None:
            raise MusicDownloadError("Az ffmpeg nincs telepitve vagy nincs a PATH-on.")
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        max_bytes = limits.max_download_bytes()
        self.opts["max_filesize"] = max_bytes
        self.opts["progress_hooks"] = [self._on_progress]
        filenames: list[str] = []
        try:
            with YoutubeDL(self.opts) as ydl:
                for url in self.url:
                    info = ydl.extract_info(url, download=True)
                    entries = info["entries"] if info.get("_type") == "playlist" else [info]
                    for entry in entries:
                        if not entry:
                            continue
                        title = str(entry.get("title") or url)
                        downloads = entry.get("requested_downloads")
                        if not downloads:
                            raise MusicDownloadError(f"A fajl tul nagy vagy nem toltheto le: {title}")
                        path = os.path.abspath(downloads[0]["filepath"])
                        size = os.path.getsize(path)
                        if size > max_bytes:
                            os.remove(path)
                            raise MusicDownloadError(
                                f"A fajl tul nagy ({size / _MB:.0f} MB, a megengedett maximum "
                                f"{max_bytes / _MB:g} MB): {title}"
                            )
                        filenames.append(path)
        except MusicDownloadError:
            raise
        except Exception as exc:
            logger.error("Zene letoltesi hiba: %s", exc)
            raise MusicDownloadError(str(exc)) from exc
        logger.info("Zene letoltve: %d fajl", len(filenames))
        return filenames
