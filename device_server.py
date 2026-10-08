"""Az eszkozszerver: kulon port, kulon app, amiben CSAK a hang-stream route van.

Az admin app (server.py) 127.0.0.1-en marad; ide jonnek a halozati eszkozok (ESP32)
Bearer-tokennel es regisztralt IP-rol.
"""

from __future__ import annotations

import logging
import threading
import time

import uvicorn
from fastapi import FastAPI, WebSocket

from api.mixer import stream_audio
from services.devices import DeviceStore, Presence, check
from services.devices.audit import log_event

logger = logging.getLogger("csengetes")


def create_app(mixer, store: DeviceStore, presence: Presence) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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
        presence.enter(device.id)
        store.touch(device.id)
        log_event(device.name, "connected", ip=client_ip)
        reason = "closed"
        try:
            reason = await stream_audio(ws, mixer, lambda: store.is_current(device.id, device.token_hash))
        finally:
            presence.leave(device.id)
            log_event(device.name, "disconnected", reason=reason, seconds=int(time.time() - started))

    return app


class DeviceServer:
    def __init__(self, mixer, store: DeviceStore, presence: Presence, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._uvicorn = uvicorn.Server(
            uvicorn.Config(create_app(mixer, store, presence), host=host, port=port, log_level="warning")
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
