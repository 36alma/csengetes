"""Zenei playlistek API."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body

from .context import ApiContext, error
from .music import music_path

NOT_FOUND_MSG = "A playlist nem talalhato."
FILE_NOT_FOUND_MSG = "A fajl nem talalhato."


def _failed(exc: Exception):
    """A PlaylistStore hibaja HTTP-hibava: KeyError -> 404, ValueError -> 400."""
    if isinstance(exc, KeyError):
        return error(NOT_FOUND_MSG, 404)
    return error(str(exc), 400)


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    store = ctx.playlists

    @router.get("/api/playlists")
    def list_playlists():
        return {"active": store.active, "playlists": store.list()}

    @router.post("/api/playlists")
    def create_playlist(body: Any = Body(None)):
        name = body.get("name") if isinstance(body, dict) else None
        try:
            playlist = store.create(name)
        except (KeyError, ValueError) as exc:
            return _failed(exc)
        return {"ok": True, "playlist": playlist}

    @router.put("/api/playlists/{pid}")
    def rename_playlist(pid: str, body: Any = Body(None)):
        name = body.get("name") if isinstance(body, dict) else None
        try:
            playlist = store.rename(pid, name)
        except (KeyError, ValueError) as exc:
            return _failed(exc)
        return {"ok": True, "playlist": playlist}

    @router.delete("/api/playlists/{pid}")
    def delete_playlist(pid: str):
        try:
            store.delete(pid)
        except KeyError as exc:
            return _failed(exc)
        return {"ok": True}

    @router.post("/api/playlists/active")
    def set_active_playlist(body: Any = Body(None)):
        if not isinstance(body, dict) or "id" not in body:
            return error("Ervenytelen playlist-azonosito.", 400)
        pid = body["id"]
        if pid is not None and not isinstance(pid, str):
            return error("Ervenytelen playlist-azonosito.", 400)
        try:
            store.set_active(pid)
        except KeyError as exc:
            return _failed(exc)
        return {"ok": True}

    @router.post("/api/playlists/{pid}/tracks")
    def add_tracks(pid: str, body: Any = Body(None)):
        names = body.get("names") if isinstance(body, dict) else None
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            return error("Ervenytelen szamlista.", 400)
        # Mindent vagy semmit: egy hianyzo fajl miatt nem kerul be egyik sem.
        resolved = []
        for name in names:
            path = music_path(name)
            if path is None:
                return error(FILE_NOT_FOUND_MSG, 404)
            resolved.append(path)
        basenames = [os.path.basename(path) for path in resolved]
        try:
            playlist = store.add_tracks(pid, basenames)
        except (KeyError, ValueError) as exc:
            return _failed(exc)
        return {"ok": True, "playlist": playlist}

    @router.delete("/api/playlists/{pid}/tracks/{name}")
    def remove_track(pid: str, name: str):
        try:
            playlist = store.remove_track(pid, name)
        except KeyError as exc:
            return _failed(exc)
        return {"ok": True, "playlist": playlist}

    @router.post("/api/playlists/{pid}/move")
    def move_track(pid: str, body: Any = Body(None)):
        data = body if isinstance(body, dict) else {}
        try:
            playlist = store.move_track(pid, data.get("from_index"), data.get("to_index"))
        except (KeyError, ValueError) as exc:
            return _failed(exc)
        return {"ok": True, "playlist": playlist}

    return router
