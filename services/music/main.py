"""Zene lejatszasa a csengetesi rend szuneteiben."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from mutagen import MutagenError
from mutagen.mp3 import MP3

import config_store
from audio_player import AudioPlayer
from schedule_store import load_obj
from time_source import TimeSource

from .download import MusicDownload

logger = logging.getLogger("csengetes")


class MusicService:
    """Letolti a zenet, majd ha belefer a kovetkezo csengetesig, lejatssza.

    force=True: akkor is elindul, ha nem fer bele a szunetbe.
    now_play=True: az elso szam megszakitja a most szolo hangot.
    """

    def __init__(
        self,
        url: list[str] | None,
        player: AudioPlayer,
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
        self._cancel = threading.Event()

    def stop(self) -> None:
        self._cancel.set()
        self.current = None
        self.player.stop()
        self.status = "stopped"

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
        self.filename = MusicDownload(self.url)()
        return self.filename

    def __call__(self) -> bool:
        """True, ha a lejatszas elindult."""
        if not self.filename:
            self.prepare()
        seconds_left = self.get_break()
        total = self.get_music_length()

        if seconds_left is not None and not self.force and total > seconds_left:
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
            for index, filepath in enumerate(self.filename):
                if self._cancel.is_set():
                    return
                self.current = filepath
                self.player.play(
                    filepath,
                    blocking=True,
                    force=self.now_play if index == 0 else False,
                    on_done=lambda err, p=filepath: err
                    and logger.error("Zene lejatszasi hiba (%s): %s", p, err),
                )
            self.current = None
            if not self._cancel.is_set():
                self.status = "done"

        self.status = "playing"
        threading.Thread(target=_run, daemon=True).start()

    def get_music_length(self) -> float:
        """Az osszes szam hossza masodpercben. Olvashatatlan fajl eseten kivetel."""
        total = 0.0
        for path in self.filename:
            try:
                total += MP3(path).info.length
            except (MutagenError, OSError) as exc:
                raise RuntimeError(f"A zene hossza nem olvashato ({path}): {exc}") from exc
        return total
