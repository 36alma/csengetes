"""Mely eszkozoknek van eppen nyitott stream-kapcsolata (szalbiztos szamlalo)."""

from __future__ import annotations

import threading


class Presence:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}

    def enter(self, device_id: str) -> None:
        with self._lock:
            self._counts[device_id] = self._counts.get(device_id, 0) + 1

    def leave(self, device_id: str) -> None:
        with self._lock:
            count = self._counts.get(device_id, 0) - 1
            if count > 0:
                self._counts[device_id] = count
            else:
                self._counts.pop(device_id, None)

    def is_connected(self, device_id: str) -> bool:
        with self._lock:
            return self._counts.get(device_id, 0) > 0
