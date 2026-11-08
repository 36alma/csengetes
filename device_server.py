"""Az eszkozszerver: kulon port, kulon app, amiben CSAK a hang-stream route van.

Az admin app (server.py) 127.0.0.1-en marad; ide jonnek a halozati eszkozok (ESP32)
Bearer-tokennel es regisztralt IP-rol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

from api.mixer import stream_audio
from services.devices import (
    DeviceStore,
    DeviceTelemetry,
    Presence,
    TelemetryInvalid,
    check,
    parse_buffer,
    parse_events,
)
from services.devices.telemetry import MAX_BODY_BYTES
from services.devices.audit import log_event

logger = logging.getLogger("csengetes")


def create_app(mixer, store: DeviceStore, presence: Presence, telemetry: DeviceTelemetry | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if telemetry is None:
        telemetry = DeviceTelemetry(None)  # csak memoria (pl. tesztek)

    def authenticate(request: Request):
        """(device, None) vagy (None, hibavalasz): Bearer-token + regisztralt IP, mint a stream-nel."""
        client_ip = request.client.host if request.client else None
        decision = check(store, request.headers.get("authorization"), client_ip)
        if not decision.allowed:
            name = decision.device.name if decision.device else None
            log_event(name, "denied", logging.WARNING, reason=decision.reason, ip=client_ip)
            status = 401 if decision.reason == "bad_token" else 403
            return None, JSONResponse({"error": "Nincs jogosultsag."}, status_code=status)
        if not telemetry.allow(decision.device.id):
            return None, JSONResponse({"error": "Tul sok kerest."}, status_code=429)
        return decision.device, None

    async def read_json(request: Request):
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            declared = 0
        if declared > MAX_BODY_BYTES:
            return None, JSONResponse({"error": "Tul nagy kerestartalom."}, status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return None, JSONResponse({"error": "Tul nagy kerestartalom."}, status_code=413)
        try:
            return json.loads(body), None
        except ValueError:
            return None, JSONResponse({"error": "Ervenytelen kerestartalom."}, status_code=400)

    @app.post("/api/device/buffer")
    async def post_buffer(request: Request):
        device, failure = authenticate(request)
        if failure:
            return failure
        body, failure = await read_json(request)
        if failure:
            return failure
        try:
            data = parse_buffer(body)
        except TelemetryInvalid as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        telemetry.set_buffer(device.id, data)
        return {"ok": True}

    @app.post("/api/device/log")
    async def post_log(request: Request):
        device, failure = authenticate(request)
        if failure:
            return failure
        body, failure = await read_json(request)
        if failure:
            return failure
        try:
            events = parse_events(body)
        except TelemetryInvalid as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        accepted = await asyncio.to_thread(telemetry.add, device.id, "esp", events)
        return {"ok": True, "accepted": accepted}

    @app.websocket("/api/mixer/stream")
    async def stream(ws: WebSocket):
        client_ip = ws.client.host if ws.client else None
        decision = check(store, ws.headers.get("authorization"), client_ip)
        if not decision.allowed:
            name = decision.device.name if decision.device else None
            log_event(name, "denied", logging.WARNING, reason=decision.reason, ip=client_ip)
            await ws.close(code=1008)
            return
        device = decision.device
        started = time.time()
        reason = "closed"
        presence.enter(device.id)
        try:
            try:
                await asyncio.to_thread(store.touch, device.id)  # fajliras: ne blokkolja az esemenyhurkot
            except Exception:
                logger.warning("A last_seen frissitese nem sikerult (%s).", device.name)
            log_event(device.name, "connected", ip=client_ip)
            await asyncio.to_thread(telemetry.server_event, device.id, "connected", ip=client_ip)
            reason = await stream_audio(ws, mixer, lambda: store.is_current(device.id, device.token_hash))
        finally:
            presence.leave(device.id)
            seconds = int(time.time() - started)
            log_event(device.name, "disconnected", reason=reason, seconds=seconds)
            telemetry.server_event(device.id, "disconnected", reason=reason, seconds=seconds)

    return app


class DeviceServer:
    def __init__(self, mixer, store: DeviceStore, presence: Presence, host: str, port: int,
                 telemetry: DeviceTelemetry | None = None) -> None:
        self._host = host
        self._port = port
        self._uvicorn = uvicorn.Server(
            # proxy_headers=False: az X-Forwarded-For-t nem vesszuk figyelembe, a kliens IP-je a socket cime
            uvicorn.Config(create_app(mixer, store, presence, telemetry), host=host, port=port, log_level="warning",
                           proxy_headers=False)
        )
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        try:
            self._uvicorn.run()
        except SystemExit:
            pass  # a uvicorn sikertelen inditaskor (pl. foglalt port) sys.exit(1)-et hiv; a start() naplozza

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._uvicorn.started or not self._thread.is_alive():
                break
            time.sleep(0.05)
        if not self._uvicorn.started:
            logger.error("Az eszkozszerver nem indult el a %s:%s cimen (foglalt port?).", self._host, self._port)
            return False
        logger.info("Eszkozszerver: %s:%s", self._host, self._port)
        return True

    def stop(self) -> None:
        self._uvicorn.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)
