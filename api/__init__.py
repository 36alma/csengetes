"""REST API: az endpointok teruletenkent kulon modulokban (FastAPI routerek)."""

from __future__ import annotations

from fastapi import FastAPI

from . import logs, media, mixer, music, output, schedules, time_api
from .context import ApiContext

__all__ = ["ApiContext", "register_api"]


def register_api(app: FastAPI, ctx: ApiContext) -> None:
    for module in (schedules, media, music, time_api, output, logs, mixer):
        app.include_router(module.create_router(ctx))
