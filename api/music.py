"""Zene (YouTube) API."""

from __future__ import annotations

import logging
import math
import os
from typing import Any

from fastapi import APIRouter, Body, File, UploadFile
from fastapi.responses import JSONResponse
from werkzeug.utils import secure_filename

import limits
from services.music import download as music_download
from services.music import MusicDownload, MusicDownloadError, MusicService, library, validate_url

from .context import ApiContext, error, storage_dirs
from .media import too_large_error

logger = logging.getLogger("csengetes")

BUSY_MSG = "Mar fut egy zene, allitsd le elobb."
MAX_SEEK_S = 1e9


def _seek_seconds(value: Any) -> float | None:
    """A tekeres ideje masodpercben; None, ha nem szam, bool, veges nelkuli vagy abszurd nagysagu."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)  # hatalmas int: OverflowError
    except (OverflowError, ValueError, TypeError):
        return None
    if not math.isfinite(seconds) or abs(seconds) > MAX_SEEK_S:
        return None
    return seconds


def music_path(filename: str) -> str | None:
    """A zenei mappa beluli, letezo, zenei kiterjesztesu fajl utvonala, kulonben None."""
    name = os.path.basename(str(filename or "").strip())
    if not name.lower().endswith(library.MUSIC_EXTENSIONS):
        return None
    return library.resolve_track(music_download.DOWNLOAD_DIR, name)


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    session = ctx.music_session

    def start_music(service: MusicService):
        """Elinditja a szolgaltatast; egyszerre csak egy zene futhat."""
        if not session.start(service):
            return error(BUSY_MSG, 409)
        return JSONResponse({"ok": True, "started": True}, status_code=202)

    @router.get("/api/music")
    def list_music():
        os.makedirs(music_download.DOWNLOAD_DIR, exist_ok=True)
        items = library.list_tracks(music_download.DOWNLOAD_DIR)
        for item in items:
            item["playlists"] = ctx.playlists.playlists_containing(item["name"])
        return {"files": [t["name"] for t in items], "items": items}

    @router.post("/api/music/upload")
    def upload_music(file: UploadFile | None = File(None)):
        if file is None:
            return error("Nincs csatolt fajl.")
        filename = secure_filename(file.filename or "")
        if not filename:
            return error("Ervenytelen fajlnev.")
        if not filename.lower().endswith(library.MUSIC_EXTENSIONS):
            return error("Csak MP3/WAV/OGG fajl tolthato fel.")
        root = music_download.DOWNLOAD_DIR
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, filename)
        # Utkozes ellenorzese a masolas elott: copy_limited felulirna, sikertelenul torolne is.
        if os.path.exists(path):
            return error("Mar letezik ilyen nevu fajl.", 409)
        if file.size is not None and file.size > limits.max_upload_bytes():
            return too_large_error()
        msg = limits.check_space(file.size or 0, *storage_dirs(ctx))
        if msg:
            logger.warning("Zene feltoltes elutasitva: nincs tarhely (%s)", filename)
            return error(msg, 507)
        if not limits.copy_limited(file.file, path, limits.max_upload_bytes()):
            return too_large_error()
        logger.info("Zene feltoltve: %s", filename)
        return {"ok": True, "filename": filename}

    @router.post("/api/music/play")
    def play_music(body: dict = Body(...)):
        urls = body.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list) or not urls:
            return error("Az 'urls' mezo kotelezo.")
        try:
            urls = [validate_url(u) for u in urls]
        except MusicDownloadError as exc:
            return error(str(exc))
        busy = session.current
        if busy is not None and busy.status in ("downloading", "playing"):
            return error(BUSY_MSG, 409)
        try:
            items = MusicDownload(urls).check_limits()
        except MusicDownloadError as exc:
            return error(str(exc), 400)
        msg = limits.check_space(sum(item["est_bytes"] for item in items), *storage_dirs(ctx))
        if msg:
            logger.warning("Zene letoltes elutasitva: nincs tarhely")
            return error(msg, 507)
        return start_music(
            MusicService(
                urls,
                ctx.music_player,
                ctx.time_src,
                force=bool(body.get("force", False)),
                now_play=bool(body.get("now_play", False)),
            )
        )

    @router.post("/api/music/play-file")
    def play_music_file(body: dict = Body(...)):
        names = body.get("filenames")
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not names:
            return error("A 'filenames' mezo kotelezo.")
        paths = [music_path(n) for n in names]
        if None in paths:
            return error("A fajl nem talalhato.", 404)
        return start_music(
            MusicService(
                None,
                ctx.music_player,
                ctx.time_src,
                force=bool(body.get("force", False)),
                now_play=bool(body.get("now_play", False)),
                files=paths,
            )
        )

    @router.post("/api/music/play-playlist")
    def play_playlist(body: Any = Body(None)):
        body = body if isinstance(body, dict) else {}
        pid = body.get("id")
        if pid is None:
            pid = ctx.playlists.active
            if pid is None:
                return error("Nincs aktiv playlist.", 400)
        try:
            playlist = ctx.playlists.get(pid)
        except KeyError:
            return error("A playlist nem talalhato.", 404)
        paths = [p for p in (music_path(name) for name in playlist["tracks"]) if p is not None]
        if not paths:
            return error("A playlist ures vagy a fajlok hianyoznak.", 400)
        return start_music(
            MusicService(
                None,
                ctx.music_player,
                ctx.time_src,
                force=bool(body.get("force", False)),
                now_play=bool(body.get("now_play", False)),
                files=paths,
            )
        )

    @router.get("/api/music/status")
    def music_status():
        return session.status()

    @router.post("/api/music/stop")
    def stop_music():
        session.stop(ctx.music_player)
        return {"ok": True}

    @router.post("/api/music/seek")
    def seek_music(body: Any = Body(None)):
        if not isinstance(body, dict) or ("position_s" in body) == ("delta_s" in body):
            return error("Pontosan egy mezo kell: position_s vagy delta_s.")
        key = "position_s" if "position_s" in body else "delta_s"
        seconds = _seek_seconds(body[key])
        if seconds is None:
            return error("Ervenytelen tekeres ertek.")
        try:
            pos = session.seek(**{key: seconds})
        except (ValueError, OverflowError):
            return error("Ervenytelen tekeres ertek.")
        if pos is None:
            return error("Nem szol zene.", 409)
        return {"ok": True, "position_s": float(pos)}

    @router.post("/api/music/loop")
    def loop_music(body: Any = Body(None)):
        mode = body.get("mode") if isinstance(body, dict) else None
        if not isinstance(mode, str):
            return error("Ervenytelen ismetlesi mod.")
        try:
            session.set_loop(mode)
        except ValueError:
            return error("Ervenytelen ismetlesi mod.")
        return {"ok": True, "mode": mode}

    @router.delete("/api/music/{name}")
    def delete_music(name: str):
        path = music_path(name)
        if path is None:
            return error("A fajl nem talalhato.", 404)
        svc = session.current
        if svc is not None and svc.status == "playing" and path in [os.path.realpath(f) for f in svc.filename]:
            return error("A fajl jelenleg lejatszas alatt all.", 409)
        os.remove(path)
        name = os.path.basename(path)
        ctx.playlists.remove_track_everywhere(name)
        logger.info("Zene torolve: %s", name)
        return {"ok": True}

    return router
