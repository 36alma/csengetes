"""Az API modulok altal kozosen hasznalt fuggosegek."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from fastapi import WebSocket
from fastapi.responses import JSONResponse

import audio_player
import scheduler as scheduler_mod
import time_source

if TYPE_CHECKING:
    from services.mixer import Mixer


@dataclass
class ApiContext:
    player: audio_player.AudioPlayer
    time_src: time_source.TimeSource
    scheduler: scheduler_mod.Scheduler
    media_dir: str
    save_config: Callable[[], None]
    get_mixer: Callable[[], "Mixer"]  # lusta: a Mic() inicializalasa eszkozt kerdez le
    ws_authorized: Callable[[WebSocket], bool]  # a HTTP middleware a WebSocketre nem fut


def error(message: str, status: int = 400) -> JSONResponse:
    """Egyseges hibavalasz: {"error": "..."} a megadott statuszkoddal."""
    return JSONResponse({"error": message}, status_code=status)
