"""Zene lejatszasa a csengetesi rend szuneteiben."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import config_store
from services.mixer import MixerPlayer
from schedule_store import load_obj
from time_source import TimeSource

from .download import MusicDownload
from .library import track_duration

logger = logging.getLogger("csengetes")

LOOP_MODES = ("off", "track", "playlist")


class MusicService:
    """Letolti a zenet, majd ha belefer a kovetkezo csengetesig, lejatssza.

    force=True: akkor is elindul, ha nem fer bele a szunetbe.
    now_play=True: az elso szam megszakitja a most szolo hangot.
    """

    def __init__(
        self,
        url: list[str] | None,
        player: MixerPlayer,
        time_src: TimeSource,
        force: bool = False,
        now_play: bool = False,
        files: list[str] | None = None,
    ) -> None:
        self.url = url or []
        self.player = player
        self.time_src = time_src
        self.force = force
        self.now_play = now_play
        self.filename: list[str] = list(files or [])
        self.status = "pending"  # pending|downloading|playing|skipped|done|stopped|error
        self.error: str | None = None
        self.current: str | None = None  # a most szolo szam fajlneve
        self.progress: dict | None = None  # letoltes alatt: {percent, title, item}
        self.index: int | None = None  # a most szolo szam indexe a filename-ben
        self.loop_mode: str = "off"  # off|track|playlist
        self._cancel = threading.Event()

    def stop(self) -> None:
        self._cancel.set()
        self.current = None
        self.index = None
        self.player.stop()
        self.player.set_loop(False)
        self.status = "stopped"

    def set_loop(self, mode: str) -> None:
        if mode not in LOOP_MODES:
            raise ValueError(f"Ismeretlen ismetlesi mod: {mode}")
        self.loop_mode = mode
        if self.status == "playing":
            self.player.set_loop(mode == "track")

    def seek(self, seconds: float) -> float | None:
        """Tekeres a most szolo szamon belul; az uj pozicio, None ha nem szol zene."""
        if self.status != "playing":
            return None
        return self.player.seek(seconds)

    def seek_by(self, delta_s: float) -> float | None:
        """Relativ tekeres: aktualis pozicio + delta; None, ha nincs pozicio."""
        if self.status != "playing":
            return None
        pos = self.player.position()
        if pos is None:
            return None
        return self.seek(pos[0] + delta_s)

    @property
    def next_track(self) -> str | None:
        """A kovetkezo szam utvonala, None ha a sor vegen nincs tovabb."""
        if self.index is None or not self.filename:
            return None
        if self.loop_mode == "track":
            return self.filename[self.index]
        if self.index + 1 < len(self.filename):
            return self.filename[self.index + 1]
        if self.loop_mode == "playlist":
            return self.filename[0]
        return None

    def run(self) -> None:
        """Hatterszalbol hivando: allapotot vezet, kivetelt nem dob."""
        try:
            if not self.filename:
                self.status = "downloading"
                self.prepare()
            if self._cancel.is_set():
                return
            if not self():
                self.status = "skipped"
        except Exception as exc:
            logger.error("Zene hiba: %s", exc)
            self.error = str(exc)
            self.status = "error"

    def prepare(self) -> list[str]:
        finished = 0

        def on_progress(event: dict) -> None:
            nonlocal finished
            if event["status"] == "finished":
                finished += 1
            self.progress = {"percent": event["percent"], "title": event["title"], "item": finished + 1}

        self.progress = {"percent": None, "title": None, "item": 1}
        try:
            self.filename = MusicDownload(self.url, progress_cb=on_progress)()
        finally:
            self.progress = None
        return self.filename

    def __call__(self) -> bool:
        """True, ha a lejatszas elindult."""
        if not self.filename:
            self.prepare()
        if not self.force:
            seconds_left = self.get_break()
            total = self.get_music_length()
        if not self.force and seconds_left is not None and total > seconds_left:
            logger.info(
                "Zene nem fer bele a szunetbe (%.0f mp > %d mp), kihagyva.", total, seconds_left
            )
            return False
        self._start_music()
        return True

    def get_break(self) -> Optional[int]:
        """Masodpercek a kovetkezo csengetesig, None ha mar nincs tobb esemeny."""
        now = self.time_src.get_now()
        now_sec = now.hour * 3600 + now.minute * 60 + now.second
        rend = load_obj(config_store.config.ACTIVE_SCHEDULE)
        return min(
            (
                t - now_sec
                for ev in rend.events
                if (t := ev.time.hours * 3600 + ev.time.minute * 60) > now_sec
            ),
            default=None,
        )

    def _start_music(self) -> None:
        """Egymas utan jatssza le a szamokat egy hatterszalon."""

        def _run() -> None:
            index, first, fails = 0, True, 0
            while not self._cancel.is_set():
                path = self.filename[index]
                self.index, self.current = index, path
                self.player.set_loop(self.loop_mode == "track")
                result: dict = {}
                self.player.play(path, blocking=True, force=self.now_play if first else False,
                                 on_done=lambda err, r=result: r.update(err=err))
                first = False
                if self._cancel.is_set():
                    return
                if result.get("err"):
                    logger.error("Zene lejatszasi hiba (%s): %s", path, result["err"])
                fails = fails + 1 if result.get("err") else 0
                if fails >= len(self.filename):
                    self.error = result["err"]; self.current = None; self.index = None
                    self.player.set_loop(False); self.status = "error"
                    return
                index += 1
                if index >= len(self.filename):
                    if self.loop_mode != "playlist":
                        break
                    index = 0
            self.current = None; self.index = None
            self.player.set_loop(False)
            if not self._cancel.is_set():
                self.status = "done"

        self.status = "playing"
        threading.Thread(target=_run, daemon=True).start()

    def get_music_length(self) -> float:
        """Az osszes szam hossza masodpercben. Olvashatatlan fajl eseten kivetel."""
        total = 0.0
        for path in self.filename:
            duration = track_duration(path)
            if duration is None:
                raise RuntimeError(f"A zene hossza nem olvashato ({path})")
            total += duration
        return total
