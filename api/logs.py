"""Naplo API."""

from __future__ import annotations

import os

from fastapi import APIRouter

from logger_setup import LOG_PATH

from .context import ApiContext


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/logs")
    def get_logs(limit: int = 200):
        if not os.path.isfile(LOG_PATH):
            return {"lines": []}
        with open(LOG_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        return {"lines": [ln.rstrip("\n") for ln in lines[-limit:]]}

    return router
