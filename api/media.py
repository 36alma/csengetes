"""Media (csengohang) API."""

from __future__ import annotations

import logging
import os
import shutil

from fastapi import APIRouter, Body, File, UploadFile
from werkzeug.utils import secure_filename

import audio_player
from config_store import config

from .context import ApiContext, error

logger = logging.getLogger("csengetes")

ALLOWED_EXTENSIONS = (".mp3", ".wav", ".ogg")


def too_large_error():
    limit = config.MAX_UPLOAD_MB
    logger.warning("Feltoltes elutasitva: tul nagy fajl (limit: %s MB)", limit)
    return error(f"A fajl tul nagy. A megengedett maximum: {limit} MB.", 413)


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    def media_files() -> list[str]:
        os.makedirs(ctx.media_dir, exist_ok=True)
        return sorted(f for f in os.listdir(ctx.media_dir) if f.lower().endswith(ALLOWED_EXTENSIONS))

    @router.get("/api/media")
    def list_media():
        return {"files": media_files(), "default": config.DEFAULT_MEDIA_FILE}

    @router.post("/api/media/upload")
    def upload_media(file: UploadFile | None = File(None)):
        if file is None:
            return error("Nincs csatolt fajl.")
        filename = secure_filename(file.filename or "")
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            return error("Csak MP3/WAV/OGG fajl tolthato fel.")
        if file.size is not None and file.size > config.MAX_UPLOAD_MB * 1024 * 1024:
            return too_large_error()
        os.makedirs(ctx.media_dir, exist_ok=True)
        with open(os.path.join(ctx.media_dir, filename), "wb") as out:
            shutil.copyfileobj(file.file, out)
        logger.info("Media feltoltve: %s", filename)
        return {"ok": True, "filename": filename}

    @router.get("/api/media/upload-limit")
    def get_upload_limit():
        return {"max_upload_mb": config.MAX_UPLOAD_MB}

    @router.post("/api/media/upload-limit")
    def set_upload_limit(body: dict = Body(...)):
        try:
            mb = float(body.get("max_upload_mb"))
        except (TypeError, ValueError):
            return error("Ervenytelen ertek.")
        if mb <= 0 or mb > 1024:
            return error("A limit 0 es 1024 MB kozott lehet.")
        config.MAX_UPLOAD_MB = mb
        ctx.save_config()
        logger.info("Feltoltesi meretlimit beallitva: %.1f MB", mb)
        return {"ok": True, "max_upload_mb": mb}

    @router.post("/api/media/default")
    def set_default_media(body: dict = Body(...)):
        filename = str(body.get("filename", "")).strip()
        if not filename:
            return error("Fajlnev kotelezo.")
        config.DEFAULT_MEDIA_FILE = filename
        ctx.save_config()
        logger.info("Alapertelmezett csengohang beallitva: %s", filename)
        return {"ok": True}

    @router.post("/api/media/play")
    def play_media(body: dict = Body(...)):
        filename = str(body.get("filename", "")).strip()
        force = bool(body.get("force", False))
        if not filename:
            return error("Fajlnev kotelezo.")
        safe_name = secure_filename(filename)
        media_dir = os.path.realpath(ctx.media_dir)
        filepath = os.path.realpath(os.path.join(media_dir, safe_name))
        is_contained = filepath == media_dir or filepath.startswith(media_dir + os.sep)
        if not safe_name or not is_contained or not os.path.isfile(filepath):
            return error(f"A fajl nem talalhato: {filename}", 404)
        logger.info("Lejatszas inditva (%s), force=%s", filename, force)
        try:
            ctx.player.play(filepath, blocking=True, force=force)
        except audio_player.AudioPlayerError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    return router
