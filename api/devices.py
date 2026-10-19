"""Eszkozkezeles API (csak az admin porton): regisztralt halozati eszkozok es tokenjeik."""

from __future__ import annotations

from fastapi import APIRouter, Body

from services.devices import DeviceConflict, DeviceInvalid, DeviceNotFound
from services.devices.presence import Presence

from .context import ApiContext, error

EXPIRING_DAYS = 14


def public_device(device, presence: Presence, now: float) -> dict:
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
    )
    return data


def create_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    def view(device) -> dict:
        return public_device(device, ctx.presence, ctx.devices.now())

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
        return {"ok": True}

    return router
