"""Zene (YouTube) API."""

from __future__ import annotations

import logging
import os

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


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    session = ctx.music_session

    def music_path(filename: str) -> str | None:
        """A zenei mappa beluli, letezo, zenei kiterjesztesu fajl utvonala, kulonben None."""
        name = os.path.basename(str(filename or "").strip())
        if not name.lower().endswith(library.MUSIC_EXTENSIONS):
            return None
        return library.resolve_track(music_download.DOWNLOAD_DIR, name)

    def start_music(service: MusicService):
        """Elinditja a szolgaltatast; egyszerre csak egy zene futhat."""
        if not session.start(service):
            return error(BUSY_MSG, 409)
        return JSONResponse({"ok": True, "started": True}, status_code=202)

    @router.get("/api/music")
    def list_music():
        os.makedirs(music_download.DOWNLOAD_DIR, exist_ok=True)
        items = library.list_tracks(music_download.DOWNLOAD_DIR)
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

    @router.get("/api/music/status")
    def music_status():
        return session.status()

    @router.post("/api/music/stop")
    def stop_music():
        session.stop(ctx.music_player)
        return {"ok": True}

    @router.delete("/api/music/{name}")
    def delete_music(name: str):
        path = music_path(name)
        if path is None:
            return error("A fajl nem talalhato.", 404)
        svc = session.current
        if svc is not None and svc.status == "playing" and path in [os.path.realpath(f) for f in svc.filename]:
            return error("A fajl jelenleg lejatszas alatt all.", 409)
        os.remove(path)
        logger.info("Zene torolve: %s", os.path.basename(path))
        return {"ok": True}

    return router
