"""A felvett mikrofongépek (remote_mics.json): memóriában tartott lista, íráskor fájlba mentve."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
from typing import Callable

from micserver.protocol import DEFAULT_PORT, atomic_write_json, is_valid_credential, new_credential
from model import RemoteMicSource
from services.devices.store import _is_clean_text, normalize_ip

logger = logging.getLogger("csengetes")

REMOTE_MICS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "remote_mics.json")
MAX_SOURCES = 8

_HOSTNAME_RE = re.compile(r"(?=.{1,253}\Z)(?=.*[A-Za-z])[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
_UPDATABLE = {"name", "host", "port"}


class RemoteMicError(Exception):
    pass


class RemoteMicNotFound(RemoteMicError):
    pass


class RemoteMicConflict(RemoteMicError):
    pass


class RemoteMicInvalid(RemoteMicError):
    pass


def public_view(source: RemoteMicSource) -> dict:
    """A forrás kifelé adható alakja: a titok soha nem szerepel benne."""
    return source.model_dump(exclude={"token"})


def _valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def _valid_volume(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _clean_name(value: object) -> str:
    if isinstance(value, str) and _is_clean_text(value.strip()):
        return value.strip()
    raise RemoteMicInvalid("Érvénytelen név (1-64 karakter, vezérlőkarakter nélkül).")


def _clean_host(value: object) -> str:
    if isinstance(value, str):
        value = value.strip()
        ip = normalize_ip(value)
        if ip is not None:
            return ip
        if _HOSTNAME_RE.fullmatch(value):
            return value
    raise RemoteMicInvalid("Érvénytelen cím (IP-cím vagy gépnév kell).")


def _clean_port(value: object) -> int:
    if _valid_port(value):
        return value
    raise RemoteMicInvalid("Érvénytelen port (1-65535).")


def _loaded_problem(source: RemoteMicSource) -> str | None:
    """Betöltött bejegyzés ellenőrzése ugyanazokkal a szabályokkal, mint a felvételnél."""
    if not is_valid_credential(source.token):
        return "token"
    if not _is_clean_text(source.name):
        return "name"
    try:
        if _clean_host(source.host) != source.host:
            return "host"
    except RemoteMicInvalid:
        return "host"
    if not _valid_port(source.port):
        return "port"
    if not _valid_volume(source.volume):
        return "volume"
    return None


class RemoteMicStore:
    def __init__(self, path: str = REMOTE_MICS_PATH, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self.load_warnings: list[str] = []  # a naplózás beállítása előtti figyelmeztetések
        self._needs_backup = False  # a betöltés nem a teljes fájlt vette át: írás előtt mentés kell
        self._sources: list[RemoteMicSource] = self._load()

    # ---------- betöltés / mentés ----------

    def _warn(self, message: str) -> None:
        self.load_warnings.append(message)
        logger.warning(message)

    def replay_load_warnings(self) -> None:
        for message in self.load_warnings:
            logger.warning(message)

    def _load(self) -> list[RemoteMicSource]:
        if not os.path.isfile(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = None
        if not isinstance(raw, list):
            self._needs_backup = True
            self._warn("A remote_mics.json sérült vagy nem lista: nincs felvett mikrofongép.")
            return []
        sources: list[RemoteMicSource] = []
        for item in raw:
            try:
                source = RemoteMicSource.model_validate(item)
            except Exception:
                source, problem = None, "mezők"
            else:
                problem = _loaded_problem(source)
                if problem is None:
                    if any(s.id == source.id for s in sources):
                        problem = "id (duplikált)"
                    elif any((s.host, s.port) == (source.host, source.port) for s in sources):
                        problem = "cím (duplikált)"
            if problem is not None:
                self._needs_backup = True
                self._warn(f"A remote_mics.json egy bejegyzése érvénytelen ({problem}), kihagyva.")
                continue
            sources.append(source)
        return sources

    def _backup_original(self) -> None:
        """A sérült fájlt az első írás előtt megőrizzük, különben az írás eltörölné."""
        if not self._needs_backup or not os.path.isfile(self._path):
            return
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.now()))
        backup = f"{self._path}.corrupt-{stamp}"
        shutil.copy2(self._path, backup)
        self._needs_backup = False
        logger.warning("A betöltetlen remote_mics.json tartalma elmentve: %s", backup)

    def _commit(self, sources: list[RemoteMicSource]) -> None:
        """Először mentés, csak siker esetén cseréli a memóriabeli listát."""
        self._backup_original()
        atomic_write_json(self._path, [s.model_dump() for s in sources], prefix=".remote-mics-")
        self._sources = sources

    def _replace(self, updated: RemoteMicSource) -> list[RemoteMicSource]:
        return [updated if s.id == updated.id else s for s in self._sources]

    # ---------- olvasás ----------

    def now(self) -> int:
        return int(self._clock())

    def list(self) -> list[RemoteMicSource]:
        with self._lock:
            return [s.model_copy() for s in self._sources]

    def get(self, source_id: str) -> RemoteMicSource | None:
        with self._lock:
            source = self._find(source_id)
            return source.model_copy() if source else None

    def _find(self, source_id: str) -> RemoteMicSource | None:
        return next((s for s in self._sources if s.id == source_id), None)

    def _require(self, source_id: str) -> RemoteMicSource:
        source = self._find(source_id)
        if source is None:
            raise RemoteMicNotFound("Ismeretlen mikrofongép.")
        return source

    def _check_address_free(self, host: str, port: int, own_id: str | None = None) -> None:
        if any((s.host, s.port) == (host, port) and s.id != own_id for s in self._sources):
            raise RemoteMicConflict("Ezen a címen már van mikrofongép.")

    # ---------- írás ----------

    def create(self, name: object, host: object, port: object = DEFAULT_PORT) -> tuple[RemoteMicSource, str]:
        clean_name = _clean_name(name)
        clean_host = _clean_host(host)
        clean_port = _clean_port(port)
        with self._lock:
            if len(self._sources) >= MAX_SOURCES:
                raise RemoteMicConflict(f"Legfeljebb {MAX_SOURCES} mikrofongép vehető fel.")
            self._check_address_free(clean_host, clean_port)
            token = new_credential()
            now = self.now()
            source = RemoteMicSource(
                id="rm_" + secrets.token_hex(4),
                name=clean_name,
                host=clean_host,
                port=clean_port,
                token=token,
                created_at=now,
                rotated_at=now,
            )
            self._commit(self._sources + [source])
            return source.model_copy(), token

    def update(self, source_id: str, /, **fields) -> RemoteMicSource:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise RemoteMicInvalid(f"Nem módosítható mező: {', '.join(sorted(unknown))}")
        with self._lock:
            source = self._require(source_id)
            changes: dict = {}
            if "name" in fields:
                changes["name"] = _clean_name(fields["name"])
            if "host" in fields:
                changes["host"] = _clean_host(fields["host"])
            if "port" in fields:
                changes["port"] = _clean_port(fields["port"])
            updated = source.model_copy(update=changes)
            self._check_address_free(updated.host, updated.port, own_id=source.id)
            self._commit(self._replace(updated))
            return updated.model_copy()

    def delete(self, source_id: str) -> RemoteMicSource:
        with self._lock:
            source = self._require(source_id)
            self._commit([s for s in self._sources if s.id != source.id])
            return source

    def set_volume(self, source_id: str, volume: object) -> RemoteMicSource:
        if not _valid_volume(volume):
            raise RemoteMicInvalid("A hangerő 0 és 1 közötti szám lehet.")
        with self._lock:
            updated = self._require(source_id).model_copy(update={"volume": float(volume)})
            self._commit(self._replace(updated))
            return updated.model_copy()

    def set_credential(self, source_id: str, credential: str) -> RemoteMicSource:
        """Sikeres rotáció után: csak akkor tér vissza, ha az új érték tartósan a lemezen van."""
        if not is_valid_credential(credential):
            raise RemoteMicInvalid("Érvénytelen token.")
        with self._lock:
            updated = self._require(source_id).model_copy(update={"token": credential, "rotated_at": self.now()})
            self._commit(self._replace(updated))
            return updated.model_copy()

    def reissue(self, source_id: str) -> tuple[RemoteMicSource, str]:
        token = new_credential()
        return self.set_credential(source_id, token), token

    def touch(self, source_id: str) -> None:
        with self._lock:
            source = self._find(source_id)
            if source is None:
                return
            try:
                self._commit(self._replace(source.model_copy(update={"last_seen": self.now()})))
            except Exception as exc:  # az utolsó kapcsolat csak tájékoztató: ne buktasson kapcsolatot
                logger.warning("A last_seen nem menthető: %s", exc)
