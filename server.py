"""Csengetesi rend - FastAPI backend + pywebview asztali ablak.

A UI teljes egeszeben HTML/CSS/JS (static/), a Python csak a hattermotort
(utemezo, hanglejatszas, NTP, fajlkezeles) es egy helyi REST API-t ad.
Az API endpointok az api/ csomagban vannak.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import audio_player
from api import ApiContext, register_api
from api.context import error
from api.media import too_large_error
from config_store import config
import schedule_store
import scheduler as scheduler_mod
import time_source
from logger_setup import setup_logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
STATIC_DIR = os.path.join(BASE_DIR, "static")

logger = logging.getLogger("csengetes")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

HOST = "127.0.0.1"
PORT = 8765
SESSION_COOKIE = "csengetes_session"

AUTH_TOKEN = secrets.token_urlsafe(32)
_sessions: set[str] = set()

DEFAULT_MAX_UPLOAD_MB = 20

player = audio_player.AudioPlayer()
time_src = time_source.TimeSource(ntp_server=config.NTP_SERVER)
scheduler = scheduler_mod.Scheduler(time_src, player)

# Az eszkozt nev alapjan keressuk meg (az index eszkozvaltozasnal elcsuszhat)
if config.DEVICE_NAME:
    _device_index = player.find_device_index(config.DEVICE_NAME)
    if _device_index is None:
        logger.warning("A mentett kimeneti eszkoz nem talalhato (%s), alapertelmezett hasznalata.", config.DEVICE_NAME)
    player.set_device(_device_index)
    config.DEVICE_INDEX = _device_index
player.set_volume(config.VOLUME_PERCENT)
time_src.set_mode(config.TIME_MODE)

_active_name = config.ACTIVE_SCHEDULE
if _active_name and _active_name in schedule_store.list_schedules():
    try:
        scheduler.set_active_schedule(schedule_store.load(_active_name))
    except (schedule_store.ScheduleError, OSError):
        pass


def _save_config() -> None:
    config.save_config()


@app.middleware("http")
async def _auth_and_limits(request: Request, call_next):
    new_session = None
    if request.url.path == "/" and request.query_params.get("token") == AUTH_TOKEN:
        new_session = secrets.token_urlsafe(32)
        _sessions.add(new_session)
    elif request.cookies.get(SESSION_COOKIE) not in _sessions:
        return error("Forbidden", 403)

    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > config.MAX_UPLOAD_MB * 1024 * 1024:
        return too_large_error()

    response = await call_next(request)
    if new_session:
        response.set_cookie(SESSION_COOKIE, new_session, httponly=True, samesite="strict")
    return response


@app.exception_handler(StarletteHTTPException)
async def _http_error(_request: Request, exc: StarletteHTTPException):
    return error(str(exc.detail), exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, _exc: RequestValidationError):
    return error("Ervenytelen kerestartalom.")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


register_api(
    app,
    ApiContext(
        player=player,
        time_src=time_src,
        scheduler=scheduler,
        media_dir=MEDIA_DIR,
        save_config=_save_config,
    ),
)


# A statikus fajlok az API utvonalak utan kerulnek felcsatolasra
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")

_uvicorn = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))


def _run_server():
    _uvicorn.run()


def main():
    setup_logging()
    os.makedirs(MEDIA_DIR, exist_ok=True)
    scheduler.start()

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()
    for _ in range(100):
        if _uvicorn.started or not server_thread.is_alive():
            break
        time.sleep(0.05)

    try:
        import webview

        webview.create_window(
            "Csengetesi rend",
            f"http://{HOST}:{PORT}/?token={AUTH_TOKEN}",
            width=1040,
            height=760,
            min_size=(820, 600),
        )
        icon_path = os.path.join(STATIC_DIR, "favicon.ico")
        webview.start(icon=icon_path if os.path.isfile(icon_path) else None)
    except ImportError:
        logger.warning("pywebview nincs telepitve, csak a szerver fut a http://%s:%s cimen.", HOST, PORT)
        server_thread.join()

    _uvicorn.should_exit = True
    scheduler.stop()


if __name__ == "__main__":
    main()
