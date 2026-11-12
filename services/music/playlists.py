"""Zenei playlistek (playlists.json): memoriaban tartott allapot, minden modositasnal atomi mentes."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import uuid

logger = logging.getLogger("csengetes")

PLAYLISTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "playlists.json")
NAME_MAX = 60


def _view(playlist: dict) -> dict:
    return {"id": playlist["id"], "name": playlist["name"], "tracks": list(playlist["tracks"])}


def _valid_playlist(item: object) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("tracks"), list)
        and all(isinstance(t, str) for t in item["tracks"])
    )


class PlaylistStore:
    def __init__(self, path: str = PLAYLISTS_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._playlists: list[dict] = []
        self._active: str | None = None
        self._load()

    # ---------- betoltes / mentes ----------

    def _load(self) -> None:
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):  # ValueError: JSONDecodeError es UnicodeDecodeError is
            raw = None
        if not isinstance(raw, dict) or not isinstance(raw.get("playlists"), list):
            logger.warning("A playlists.json serult vagy nem megfelelo alaku: ures playlist-lista indul.")
            return
        playlists = [item for item in raw["playlists"] if _valid_playlist(item)]
        if len(playlists) != len(raw["playlists"]):
            logger.warning("A playlists.json egy vagy tobb ervenytelen bejegyzese kihagyva.")
        self._playlists = playlists
        active = raw.get("active")
        self._active = active if any(p["id"] == active for p in playlists) else None

    def _commit(self, playlists: list[dict], active: str | None) -> None:
        """Eloszor mentes, csak siker eseten cserelodik a memoriabeli allapot."""
        data = {"active": active, "playlists": playlists}
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            logger.error("A playlists.json mentese sikertelen: %s", self._path)
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self._playlists = playlists
        self._active = active

    # ---------- segedek (lock alatt hivandok) ----------

    def _require(self, playlists: list[dict], pid: str) -> dict:
        playlist = next((p for p in playlists if p["id"] == pid), None)
        if playlist is None:
            raise KeyError(pid)
        return playlist

    @staticmethod
    def _clean_name(name: object, playlists: list[dict], own_id: str | None = None) -> str:
        if not isinstance(name, str):
            raise ValueError("Ervenytelen playlist-nev.")
        name = name.strip()
        if not 1 <= len(name) <= NAME_MAX:
            raise ValueError("A playlist neve 1-60 karakter legyen.")
        if any(p["id"] != own_id and p["name"].casefold() == name.casefold() for p in playlists):
            raise ValueError("Mar van ilyen nevu playlist.")
        return name

    @staticmethod
    def _clean_track(name: object) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Ervenytelen szamnev.")
        return name

    @staticmethod
    def _clean_index(value: object, length: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < length:
            raise ValueError("Ervenytelen sorszam.")
        return value

    # ---------- olvasas ----------

    @property
    def active(self) -> str | None:
        with self._lock:
            return self._active

    def list(self) -> list[dict]:
        with self._lock:
            return [_view(p) for p in self._playlists]

    def get(self, pid: str) -> dict:
        with self._lock:
            return _view(self._require(self._playlists, pid))

    def playlists_containing(self, name: str) -> list[str]:
        with self._lock:
            return [p["name"] for p in self._playlists if name in p["tracks"]]

    # ---------- iras ----------

    def create(self, name: str) -> dict:
        with self._lock:
            playlists = copy.deepcopy(self._playlists)
            name = self._clean_name(name, playlists)
            ids = {p["id"] for p in playlists}
            pid = uuid.uuid4().hex[:8]
            while pid in ids:
                pid = uuid.uuid4().hex[:8]
            playlist = {"id": pid, "name": name, "tracks": []}
            playlists.append(playlist)
            active = self._active if self._active is not None else pid
            self._commit(playlists, active)
            return _view(playlist)

    def rename(self, pid: str, name: str) -> dict:
        with self._lock:
            playlists = copy.deepcopy(self._playlists)
            playlist = self._require(playlists, pid)
            playlist["name"] = self._clean_name(name, playlists, own_id=pid)
            self._commit(playlists, self._active)
            return _view(playlist)

    def delete(self, pid: str) -> None:
        with self._lock:
            playlists = copy.deepcopy(self._playlists)
            self._require(playlists, pid)
            playlists = [p for p in playlists if p["id"] != pid]
            active = None if self._active == pid else self._active
            self._commit(playlists, active)

    def add_tracks(self, pid: str, names: list[str]) -> dict:
        if not isinstance(names, list):
            raise ValueError("Ervenytelen szamlista.")
        with self._lock:
            playlists = copy.deepcopy(self._playlists)
            playlist = self._require(playlists, pid)
            for name in names:
                name = self._clean_track(name)
                if name not in playlist["tracks"]:
                    playlist["tracks"].append(name)
            self._commit(playlists, self._active)
            return _view(playlist)

    def remove_track(self, pid: str, name: str) -> dict:
        with self._lock:
            playlists = copy.deepcopy(self._playlists)
            playlist = self._require(playlists, pid)
            if name in playlist["tracks"]:
                playlist["tracks"].remove(name)
                self._commit(playlists, self._active)
            return _view(playlist)

    def move_track(self, pid: str, from_index: int, to_index: int) -> dict:
        with self._lock:
            playlists = copy.deepcopy(self._playlists)
            playlist = self._require(playlists, pid)
            length = len(playlist["tracks"])
            src = self._clean_index(from_index, length)
            dst = self._clean_index(to_index, length)
            playlist["tracks"].insert(dst, playlist["tracks"].pop(src))
            self._commit(playlists, self._active)
            return _view(playlist)

    def remove_track_everywhere(self, name: str) -> None:
        with self._lock:
            if not any(name in p["tracks"] for p in self._playlists):
                return
            playlists = copy.deepcopy(self._playlists)
            for playlist in playlists:
                playlist["tracks"] = [t for t in playlist["tracks"] if t != name]
            self._commit(playlists, self._active)

    def set_active(self, pid: str | None) -> None:
        with self._lock:
            if pid is not None:
                self._require(self._playlists, pid)
            self._commit(copy.deepcopy(self._playlists), pid)
