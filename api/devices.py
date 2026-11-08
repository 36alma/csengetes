"""Eszkozkezeles API (csak az admin porton): regisztralt halozati eszkozok es tokenjeik."""

from __future__ import annotations

from fastapi import APIRouter, Body

from services.devices import DeviceConflict, DeviceInvalid, DeviceNotFound
from services.devices.presence import Presence

from .context import ApiContext, error

EXPIRING_DAYS = 14


def public_device(device, presence: Presence, now: float, telemetry=None) -> dict:
    """Az eszkoz kifele adhato alakja: a token hash-e soha nem szerepel benne."""
    connected = presence.is_connected(device.id)
    if not device.enabled:
        status = "disabled"
    elif now >= device.token_expires_at:
        status = "expired"
    elif connected:
        status = "connected"
    else:
        status = "offline"
    data = device.model_dump(exclude={"token_hash"})
    data.update(
        connected=connected,
        status=status,
        expiring_soon=device.enabled and device.token_expires_at <= now + EXPIRING_DAYS * 86400,
        buffer=telemetry.get_buffer(device.id) if telemetry is not None else None,
    )
    return data


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    def view(device) -> dict:
        return public_device(device, ctx.presence, ctx.devices.now(), getattr(ctx, "telemetry", None))

    def fail(exc: Exception):
        if isinstance(exc, DeviceNotFound):
            return error(str(exc), 404)
        if isinstance(exc, DeviceConflict):
            return error(str(exc), 409)
        return error(str(exc), 400)

    @router.get("/api/devices")
    def list_devices():
        return {"devices": [view(d) for d in ctx.devices.list()]}

    @router.post("/api/devices")
    def create_device(body=Body(...)):
        if not isinstance(body, dict):
            return error("Ervenytelen kerestartalom.")
        try:
            device, token = ctx.devices.create(
                body.get("name"),
                body.get("ip_address"),
                mac_address=body.get("mac_address"),
                hostname=body.get("hostname"),
                version=body.get("version"),
            )
        except (DeviceInvalid, DeviceConflict) as exc:
            return fail(exc)
        return {"device": view(device), "token": token}

    @router.get("/api/devices/pending")
    def list_pending():
        enrollment = ctx.enrollment
        return {
            "enabled": enrollment is not None and enrollment.enabled,
            "pending": enrollment.list() if enrollment is not None else [],
        }

    @router.post("/api/devices/pending/{uuid}/approve")
    def approve_pending(uuid: str, body=Body(...)):
        if not isinstance(body, dict) or ctx.enrollment is None:
            return error("Ervenytelen kerestartalom.")
        try:
            device, token = ctx.enrollment.approve(uuid, body.get("name"))
        except (DeviceInvalid, DeviceConflict, DeviceNotFound) as exc:
            return fail(exc)
        return {"device": view(device), "token": token}

    @router.delete("/api/devices/pending/{uuid}")
    def dismiss_pending(uuid: str):
        if ctx.enrollment is None:
            return error("Ismeretlen jelentkezo.", 404)
        try:
            ctx.enrollment.dismiss(uuid)
        except DeviceNotFound as exc:
            return fail(exc)
        return {"ok": True}

    @router.patch("/api/devices/{device_id}")
    def update_device(device_id: str, body=Body(...)):
        if not isinstance(body, dict):
            return error("Ervenytelen kerestartalom.")
        try:
            device = ctx.devices.update(device_id, **body)
        except (DeviceInvalid, DeviceConflict, DeviceNotFound) as exc:
            return fail(exc)
        return {"device": view(device)}

    def known(device_id: str):
        if ctx.devices.get(device_id) is None:
            raise DeviceNotFound("Ismeretlen eszkoz.")

    @router.get("/api/devices/{device_id}/buffer")
    def device_buffer(device_id: str):
        try:
            known(device_id)
        except DeviceNotFound as exc:
            return fail(exc)
        telemetry = getattr(ctx, "telemetry", None)
        return {"buffer": telemetry.get_buffer(device_id) if telemetry is not None else None}

    @router.get("/api/devices/{device_id}/log")
    def device_log(device_id: str, type: str | None = None, since: float | None = None, limit: int = 200):
        try:
            known(device_id)
        except DeviceNotFound as exc:
            return fail(exc)
        telemetry = getattr(ctx, "telemetry", None)
        limit = max(1, min(limit, 1000))
        return {"events": telemetry.read(device_id, type, since, limit) if telemetry is not None else []}

    @router.post("/api/devices/{device_id}/rotate")
    def rotate_device(device_id: str):
        try:
            device, token = ctx.devices.rotate(device_id)
        except DeviceNotFound as exc:
            return fail(exc)
        return {"device": view(device), "token": token}

    @router.delete("/api/devices/{device_id}")
    def delete_device(device_id: str):
        try:
            ctx.devices.delete(device_id)
        except DeviceNotFound as exc:
            return fail(exc)
        if getattr(ctx, "telemetry", None) is not None:
            ctx.telemetry.delete(device_id)
        return {"ok": True}

    return router
