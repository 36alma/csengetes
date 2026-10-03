"""Mixer API: allapot, vezerles es az MP3 stream WebSocketen."""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

from fastapi import APIRouter, Body, WebSocket

from model import MixerStatus
from services.music import download as music_download
from services.mixer.main import BITRATE_KBPS
from services.mixer.mixing import BLOCK_SIZE, SAMPLE_RATE

from .context import ApiContext, error

logger = logging.getLogger("csengetes")

MUSIC_DIR = music_download.DOWNLOAD_DIR

# (kulcs, a Mixer metodusa) - a hangerok kozos kezelese
_VOLUMES = (
    ("master", "change_master_volume"),
    ("music", "change_music_volume"),
    ("mic", "change_volume"),
)

STREAM_INFO = {
    "codec": "mp3",
    "sample_rate": SAMPLE_RATE,
    "channels": 1,
    "bitrate_kbps": BITRATE_KBPS,
    "frame_samples": BLOCK_SIZE,
}


def _music_path(filename: str) -> str | None:
    """A download/ mappan beluli, letezo fajl utvonala, kulonben None."""
    safe = os.path.basename(str(filename or "").strip())
    root = os.path.realpath(MUSIC_DIR)
    path = os.path.realpath(os.path.join(root, safe))
    if not safe or not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return path


def _same_origin(ws: WebSocket) -> bool:
    """Cross-site WebSocket hijacking ellen: a bongeszo Origin-jenek a hosztra kell mutatnia.

    Origin nelkul nem bongeszo a kliens, az pedig nem tamadhat a felhasznalo sutijevel.
    """
    origin = ws.headers.get("origin")
    return origin is None or urlparse(origin).netloc == ws.headers.get("host")


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    def volumes(mixer) -> dict:
        return {"master": mixer.master_volume, "music": mixer.music_volume, "mic": mixer.mic_volume}

    @router.get("/api/mixer")
    def get_state():
        mixer = ctx.get_mixer()
        status = mixer.get_status()
        return {
            "running": mixer.running,
            "status": status.value,
            "flags": [f.name for f in MixerStatus if f and f in status],
            "level": round(mixer.level, 3),
            "volume": volumes(mixer),
            "input": mixer.current_output_device.model_dump(),
            "head": mixer.buffer.head,
            "stream": STREAM_INFO,
        }

    @router.post("/api/mixer/start")
    def start():
        mixer = ctx.get_mixer()
        try:
            mixer.start()
        except Exception:
            logger.exception("A mixer nem indult el")
            return error("A mikrofon nem nyithato meg.", 503)
        return {"ok": True}

    @router.post("/api/mixer/stop")
    def stop():
        ctx.get_mixer().stop()
        return {"ok": True}

    @router.get("/api/mixer/inputs")
    def list_inputs():
        mixer = ctx.get_mixer()
        return {
            "inputs": [d.model_dump() for d in mixer.list_inputs()],
            "current": mixer.current_output_device.model_dump(),
        }

    @router.post("/api/mixer/input")
    def set_input(body: dict = Body(...)):
        mixer = ctx.get_mixer()
        device = next((d for d in mixer.list_inputs() if d.index == body.get("index")), None)
        if device is None:
            return error("Ismeretlen bemeneti eszkoz.", 404)
        try:
            mixer.change_output_device(device)
        except Exception:
            logger.exception("A mikrofon valtasa sikertelen")
            return error("A mikrofon nem nyithato meg.", 503)
        return {"ok": True}

    @router.post("/api/mixer/volume")
    def set_volume(body: dict = Body(...)):
        mixer = ctx.get_mixer()
        changes = []
        for key, method in _VOLUMES:
            if key in body:
                try:
                    changes.append((getattr(mixer, method), float(body[key])))
                except (TypeError, ValueError):
                    return error("Ervenytelen ertek.")
        if not changes:
            return error("Legalabb egy hangero kotelezo (master, music, mic).")
        for apply, value in changes:
            try:
                apply(value)
            except ValueError as exc:
                return error(str(exc))
        return {"ok": True, "volume": volumes(mixer)}

    @router.post("/api/mixer/music")
    def start_music(body: dict = Body(...)):
        path = _music_path(body.get("name"))
        if path is None:
            return error("A zene nem talalhato.", 404)
        try:
            ctx.get_mixer().start_music(path)
        except Exception:
            logger.exception("A zene betoltese sikertelen: %s", path)
            return error("A zenefajl nem olvashato.")
        return {"ok": True}

    @router.delete("/api/mixer/music")
    def stop_music():
        ctx.get_mixer().stop_music()
        return {"ok": True}

    @router.websocket("/api/mixer/stream")
    async def stream(ws: WebSocket):
        if not _same_origin(ws) or not ctx.ws_authorized(ws):
            await ws.close(code=1008)
            return
        mixer = ctx.get_mixer()
        await ws.accept()
        await ws.send_json(STREAM_INFO)

        async def wait_for_disconnect():
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass

        disconnected = asyncio.create_task(wait_for_disconnect())
        position = mixer.buffer.head  # mint egy radio: onnan hallgat, ahol a stream tart
        try:
            while not disconnected.done():
                item = await asyncio.to_thread(mixer.buffer.read, position, 1.0)
                if item is None:
                    continue
                packet, position = item
                await ws.send_bytes(packet.to_bytes())
        except Exception:
            pass  # a kliens eltunt kuldes kozben
        finally:
            disconnected.cancel()

    return router
