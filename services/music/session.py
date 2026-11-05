"""A zene ful es a mixer ful kozos zenei allapota: egyszerre egy zene fut."""

from __future__ import annotations

import os
import threading

from .main import MusicService


class MusicSession:
    """A futo MusicService-t tartja; a zene es a mixer API ugyanezt hasznalja."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: MusicService | None = None

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
        threading.Thread(target=service.run, daemon=True).start()
        return True

    def stop(self, player) -> None:
        if self._current is not None:
            self._current.stop()
        else:
            player.stop()

    def status(self) -> dict:
        svc = self._current
        if svc is None:
            return {"status": "idle", "files": [], "current": None, "error": None}
        return {
            "status": svc.status,
            "files": [os.path.basename(f) for f in svc.filename],
            "current": os.path.basename(svc.current) if svc.current else None,
            "error": svc.error,
        }
