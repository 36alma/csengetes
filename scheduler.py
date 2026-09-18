"""Hattersal, amely figyeli az idot es inditja a csengetest az aktiv rend szerint."""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("csengetes")

MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")


class Scheduler:
    def __init__(self, time_source, audio_player):
        self._time_source = time_source
        self._audio_player = audio_player
        self._active_schedule: dict | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_fired_key: str | None = None  # "HH:MM" - az utoljara inditott esemeny kulcsa
        self._lock = threading.Lock()

    def set_active_schedule(self, schedule: dict | None) -> None:
        with self._lock:
            self._active_schedule = schedule
            self._last_fired_key = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Utemezo elindult.")

    def stop(self) -> None:
        self._running = False
        logger.info("Utemezo leallitva.")

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                logger.error("Hiba az utemezo ciklusban: %s", exc)
            time.sleep(1)

    def _tick(self) -> None:
        with self._lock:
            schedule = self._active_schedule
        if not schedule:
            return

        now = self._time_source.get_now()
        current_key = now.strftime("%H:%M")

        with self._lock:
            if current_key == self._last_fired_key:
                return

        for event in schedule.get("events", []):
            if event["time"] == current_key:
                with self._lock:
                    self._last_fired_key = current_key
                filepath = os.path.join(MEDIA_DIR, event["file"])
                if not os.path.isfile(filepath):
                    logger.error("Hianyzo mediafajl az utemezett esemenyhez: %s", filepath)
                    return
                logger.info("Utemezett csengetes inditasa: %s -> %s", current_key, event["file"])
                self._audio_player.play(filepath)
                return
