"""Tarhely- es meretkorlatok API."""

from __future__ import annotations

from fastapi import APIRouter

import limits

from .context import ApiContext, storage_dirs


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/storage")
    def get_storage():
        info = limits.storage_info(*storage_dirs(ctx))
        return {
            "used_bytes": info["used_bytes"],
            "max_bytes": info["max_bytes"],
            "max_upload_bytes": limits.max_upload_bytes(),
            "max_download_bytes": limits.max_download_bytes(),
            "max_yt_minutes": limits.max_yt_seconds() // 60,
        }

    return router
