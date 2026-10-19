"""Csengetesi rendek API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body

import schedule_store
from config_store import config

from .context import ApiContext, error

logger = logging.getLogger("csengetes")


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/schedules")
    def list_schedules():
        return {
            "schedules": schedule_store.list_schedules(),
            "active": config.ACTIVE_SCHEDULE,
        }

    @router.get("/api/schedules/{name}")
    def get_schedule(name: str):
        try:
            return schedule_store.load(name)
        except (schedule_store.ScheduleError, OSError) as exc:
            return error(str(exc))

    @router.post("/api/schedules/{name}")
    def save_schedule(name: str, data: dict = Body(...)):
        try:
            schedule_store.save(name, data)
        except schedule_store.ScheduleError as exc:
            return error(str(exc))
        ctx.scheduler.set_active_schedule(data)
        return {"ok": True}

    @router.post("/api/schedules")
    def new_schedule(body: dict = Body(...)):
        name = str(body.get("name", "")).strip()
        if not name:
            return error("Nev kotelezo.")
        if not name.endswith(".json"):
            name += ".json"
        data = {"name": name.replace(".json", ""), "events": []}
        try:
            schedule_store.save(name, data)
        except schedule_store.ScheduleError as exc:
            return error(str(exc))
        return {"ok": True, "filename": name}

    @router.post("/api/schedules/{name}/activate")
    def activate_schedule(name: str):
        try:
            data = schedule_store.load(name)
        except (schedule_store.ScheduleError, OSError) as exc:
            return error(str(exc))
        ctx.scheduler.set_active_schedule(data)
        config.ACTIVE_SCHEDULE = name
        ctx.save_config()
        logger.info("Aktiv csengetesi rend: %s", name)
        return {"ok": True}

    return router
