"""Zene (YouTube) API."""

from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from services.music import download as music_download
from services.music import MusicDownloadError, MusicService, validate_url

from .context import ApiContext, error

logger = logging.getLogger("csengetes")

DOWNLOAD_DIR = music_download.DOWNLOAD_DIR


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    lock = threading.Lock()
    state: dict[str, MusicService | None] = {"current": None}

    def music_files() -> list[str]:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        return sorted(f for f in os.listdir(DOWNLOAD_DIR) if f.lower().endswith(".mp3"))

    def music_path(filename: str) -> str | None:
        """A download/ mappan beluli, letezo fajl utvonala, kulonben None."""
        safe = os.path.basename(str(filename or "").strip())
        root = os.path.realpath(DOWNLOAD_DIR)
        path = os.path.realpath(os.path.join(root, safe))
        if not safe or not path.startswith(root + os.sep) or not os.path.isfile(path):
            return None
        return path

    def start_music(service: MusicService):
        """Elinditja a szolgaltatast; egyszerre csak egy zene futhat."""
        with lock:
            current = state["current"]
            if current is not None and current.status in ("downloading", "playing"):
                return error("Mar fut egy zene, allitsd le elobb.", 409)
            state["current"] = service
        threading.Thread(target=service.run, daemon=True).start()
        return JSONResponse({"ok": True, "started": True}, status_code=202)

    @router.get("/api/music")
    def list_music():
        return {"files": music_files()}

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
        svc = state["current"]
        if svc is None:
            return {"status": "idle", "files": [], "current": None, "error": None}
        return {
            "status": svc.status,
            "files": [os.path.basename(f) for f in svc.filename],
            "current": os.path.basename(svc.current) if svc.current else None,
            "error": svc.error,
        }

    @router.post("/api/music/stop")
    def stop_music():
        svc = state["current"]
        if svc is not None:
            svc.stop()
        else:
            ctx.music_player.stop()
        return {"ok": True}

    @router.delete("/api/music/{name}")
    def delete_music(name: str):
        path = music_path(name)
        if path is None:
            return error("A fajl nem talalhato.", 404)
        svc = state["current"]
        if svc is not None and svc.status == "playing" and path in [os.path.realpath(f) for f in svc.filename]:
            return error("A fajl jelenleg lejatszas alatt all.", 409)
        os.remove(path)
        logger.info("Zene torolve: %s", os.path.basename(path))
        return {"ok": True}

    return router
