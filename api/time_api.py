"""Ido API."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Body

from config_store import config

from .context import ApiContext, error

logger = logging.getLogger("csengetes")


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    time_src = ctx.time_src

    @router.get("/api/time")
    def get_time():
        return {
            "mode": time_src.get_mode(),
            "ntp_server": time_src.get_ntp_server(),
            "now": time_src.get_now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @router.post("/api/time/mode")
    def set_time_mode(body: dict = Body(...)):
        mode = body.get("mode")
        try:
            time_src.set_mode(mode)
        except ValueError as exc:
            return error(str(exc))
        config.TIME_MODE = mode
        ctx.save_config()
        return {"ok": True}

    @router.post("/api/time/sync")
    def sync_time(body: dict = Body(...)):
        server_host = str(body.get("ntp_server", "")).strip()
        if server_host:
            time_src.set_ntp_server(server_host)
        config.NTP_SERVER = server_host
        ctx.save_config()
        try:
            now = time_src.sync_now()
        except Exception as exc:
            logger.warning("NTP szinkron sikertelen, rendszerora hasznalata: %s", exc)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "now": now.strftime("%Y-%m-%d %H:%M:%S")}

    @router.post("/api/time/manual")
    def set_manual_time(body: dict = Body(...)):
        text = str(body.get("value", "")).strip()
        try:
            value = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return error("Ervenytelen formatum. Varhato: YYYY-MM-DD HH:MM:SS")
        time_src.set_manual(value)
        config.NTP_SERVER = "manual"
        ctx.save_config()
        return {"ok": True}

    return router
