"""Mikrofongépek API (csak az admin porton): felvétel, adás, hangerő és tokenek."""

from __future__ import annotations

from fastapi import APIRouter, Body

from micserver.protocol import DEFAULT_PORT
from services.remote_mic.manager import RemoteMicNotLive, RemoteMicRotationFailed
from services.remote_mic.store import MAX_SOURCES, RemoteMicConflict, RemoteMicError, RemoteMicNotFound

from .context import ApiContext, error

BAD_BODY = "Érvénytelen kéréstartalom."


def _fail(exc: RemoteMicError):
    if isinstance(exc, RemoteMicNotFound):
        return error(str(exc), 404)
    if isinstance(exc, (RemoteMicConflict, RemoteMicNotLive)):
        return error(str(exc), 409)
    if isinstance(exc, RemoteMicRotationFailed):
        return error(str(exc), 502)
    return error(str(exc), 400)


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/remote-mics")
    def list_sources():
        return {"sources": ctx.remote_mics.sources(), "max": MAX_SOURCES}

    @router.post("/api/remote-mics")
    def create_source(body=Body(...)):
        if not isinstance(body, dict):
            return error(BAD_BODY)
        try:
            source, token = ctx.remote_mics.add(body.get("name"), body.get("host"), body.get("port", DEFAULT_PORT))
        except RemoteMicError as exc:
            return _fail(exc)
        return {"source": source, "token": token}

    @router.patch("/api/remote-mics/{source_id}")
    def update_source(source_id: str, body=Body(...)):
        if not isinstance(body, dict):
            return error(BAD_BODY)
        try:
            return {"source": ctx.remote_mics.update(source_id, **body)}
        except RemoteMicError as exc:
            return _fail(exc)

    @router.delete("/api/remote-mics/{source_id}")
    def delete_source(source_id: str):
        try:
            ctx.remote_mics.delete(source_id)
        except RemoteMicError as exc:
            return _fail(exc)
        return {"ok": True}

    @router.post("/api/remote-mics/{source_id}/live")
    def set_live(source_id: str, body=Body(...)):
        live = body.get("live") if isinstance(body, dict) else None
        if not isinstance(live, bool):
            return error("A 'live' mező (true/false) kötelező.")
        try:
            return {"ok": True, "source": ctx.remote_mics.set_live(source_id, live)}
        except RemoteMicError as exc:
            return _fail(exc)

    @router.post("/api/remote-mics/{source_id}/volume")
    def set_volume(source_id: str, body=Body(...)):
        if not isinstance(body, dict):
            return error(BAD_BODY)
        try:
            return {"ok": True, "source": ctx.remote_mics.set_volume(source_id, body.get("volume"))}
        except RemoteMicError as exc:
            return _fail(exc)

    @router.post("/api/remote-mics/{source_id}/rotate")
    def rotate(source_id: str):
        try:
            return {"ok": True, "source": ctx.remote_mics.rotate_now(source_id)}
        except RemoteMicError as exc:
            return _fail(exc)

    @router.post("/api/remote-mics/{source_id}/reissue")
    def reissue(source_id: str):
        try:
            source, token = ctx.remote_mics.reissue(source_id)
        except RemoteMicError as exc:
            return _fail(exc)
        return {"source": source, "token": token}

    return router
