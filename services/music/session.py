"""A zene ful es a mixer ful kozos zenei allapota: egyszerre egy zene fut."""

from __future__ import annotations

import os
import threading

from .main import LOOP_MODES, MusicService


class MusicSession:
    """A futo MusicService-t tartja; a zene es a mixer API ugyanezt hasznalja."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: MusicService | None = None
        self.loop_mode: str = "off"

    @property
    def current(self) -> MusicService | None:
        return self._current

    def start(self, service: MusicService) -> bool:
        """Hatterszalon elinditja; False, ha mar fut egy zene."""
        with self._lock:
            current = self._current
            if current is not None and current.status in ("downloading", "playing"):
                return False
            self._current = service
            service.loop_mode = self.loop_mode
        threading.Thread(target=service.run, daemon=True).start()
        return True

    def set_loop(self, mode: str) -> None:
        if mode not in LOOP_MODES:
            raise ValueError(f"Ismeretlen ismetlesi mod: {mode}")
        self.loop_mode = mode
        svc = self._current
        if svc is not None and svc.status == "playing":
            svc.set_loop(mode)

    def seek(self, position_s: float | None = None, delta_s: float | None = None) -> float | None:
        svc = self._current
        if svc is None or svc.status != "playing":
            return None
        if position_s is not None:
            return svc.seek(position_s)
        if delta_s is not None:
            return svc.seek_by(delta_s)
        return None

    def stop(self, player) -> None:
        if self._current is not None:
            self._current.stop()
        else:
            player.stop()

    def status(self) -> dict:
        svc = self._current
        if svc is None:
            return {
                "status": "idle", "files": [], "queue": [], "index": None,
                "current": None, "next": None, "loop": self.loop_mode,
                "position_s": None, "duration_s": None, "progress": None, "error": None,
            }
        names = [os.path.basename(f) for f in svc.filename]
        position_s = duration_s = None
        if svc.status == "playing":
            pos = svc.player.position()
            if pos is not None:
                position_s, duration_s = pos
        nxt = svc.next_track
        return {
            "status": svc.status,
            "files": names,
            "queue": names,
            "index": svc.index,
            "current": os.path.basename(svc.current) if svc.current else None,
            "next": os.path.basename(nxt) if nxt else None,
            "loop": svc.loop_mode,
            "position_s": position_s,
            "duration_s": duration_s,
            "progress": svc.progress,
            "error": svc.error,
        }
