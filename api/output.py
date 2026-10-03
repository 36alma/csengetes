"""Hangkimenet API."""

from __future__ import annotations

from fastapi import APIRouter, Body

import audio_player
from config_store import config

from .context import ApiContext, error


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    player = ctx.player

    @router.get("/api/output/devices")
    def list_devices():
        devices = player.list_output_devices()
        return {
            "devices": [{"index": idx, "name": name} for idx, name in devices],
            "current": player.get_device(),
            "volume": player.get_volume(),
            "max_volume": audio_player.MAX_VOLUME_PERCENT,
        }

    @router.post("/api/output/device")
    def set_device(body: dict = Body(...)):
        index = body.get("index")
        player.set_device(index)
        config.DEVICE_INDEX = index
        config.DEVICE_NAME = player.get_device_name()
        ctx.save_config()
        return {"ok": True}

    @router.post("/api/output/volume")
    def set_volume(body: dict = Body(...)):
        try:
            percent = float(body.get("percent", 100.0))
        except (TypeError, ValueError):
            return error("Ervenytelen ertek.")
        player.set_volume(percent)
        config.VOLUME_PERCENT = player.get_volume()
        ctx.save_config()
        return {"ok": True, "percent": player.get_volume()}

    return router
