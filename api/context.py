"""Az API modulok altal kozosen hasznalt fuggosegek."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from fastapi import WebSocket
from fastapi.responses import JSONResponse

import scheduler as scheduler_mod
import time_source

if TYPE_CHECKING:
    from services.devices import DeviceStore, DeviceTelemetry, Enrollment, Presence
    from services.mixer import Mixer, MixerPlayer
    from services.music import MusicSession
    from services.remote_mic.manager import RemoteMicManager


def _new_music_session() -> "MusicSession":
    from services.music import MusicSession

    return MusicSession()


@dataclass
class ApiContext:
    player: "MixerPlayer"  # csengo (ez az alap lejatszo: media, kimenet)
    music_player: "MixerPlayer"
    mixer: "Mixer"
    time_src: time_source.TimeSource
    scheduler: scheduler_mod.Scheduler
    media_dir: str
    save_config: Callable[[], None]
    ws_authorized: Callable[[WebSocket], bool]  # a HTTP middleware a WebSocketre nem fut
    devices: "DeviceStore"
    presence: "Presence"
    enrollment: "Enrollment | None" = None
    telemetry: "DeviceTelemetry | None" = None
    remote_mics: "RemoteMicManager | None" = None
    music_session: "MusicSession" = field(default_factory=_new_music_session)  # zene es mixer kozos allapota


def error(message: str, status: int = 400) -> JSONResponse:
    """Egyseges hibavalasz: {"error": "..."} a megadott statuszkoddal."""
    return JSONResponse({"error": message}, status_code=status)
