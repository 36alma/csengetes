"""Regisztralt eszkozok (devices.json): memoriaban tartott lista, irasnal fajlba mentve."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid as uuid_lib
from typing import Callable

from model import Device

from .audit import log_event

logger = logging.getLogger("csengetes")

DEVICES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "devices.json")

_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")  # vezerlokarakter a naploba kerulne
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_UPDATABLE = {"name", "ip_address", "enabled", "mac_address", "hostname", "version"}
_OPTIONAL = ("mac_address", "hostname", "version")


class DeviceError(Exception):
    pass


class DeviceNotFound(DeviceError):
    pass


class DeviceConflict(DeviceError):
    pass


class DeviceInvalid(DeviceError):
    pass


def normalize_ip(value: object) -> str | None:
    """Kanonikus IP-szoveg, vagy None, ha nem hasznalhato eszkoz-cimnek."""
    if not isinstance(value, str):
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_multicast:
        return None
    return str(ip)


def _is_uuid5(value: str) -> bool:
    try:
        parsed = uuid_lib.UUID(value)
    except ValueError:
        return False
    return parsed.version == 5 and parsed.variant == uuid_lib.RFC_4122 and str(parsed) == value


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_clean_text(value: str) -> bool:
    # az isprintable() a sortoreseket (U+0085, U+2028/9), a format-karaktereket es a surrogate-eket is kiszuri
    return bool(_TEXT_RE.match(value)) and value.isprintable()


def _loaded_problem(device: Device) -> str | None:
    """Betoltott bejegyzes ellenorzese ugyanazokkal a szabalyokkal, mint a create/update."""
    if not _HASH_RE.match(device.token_hash):
        return "token_hash"
    if not _is_clean_text(device.name):
        return "name"
    if normalize_ip(device.ip_address) != device.ip_address:
        return "ip_address"
    if device.uuid is not None and not _is_uuid5(device.uuid):
        return "uuid"
    for field in _OPTIONAL:
        value = getattr(device, field)
        if value is not None and not _is_clean_text(value):
            return field
    return None


def _clean_text(value: object, field: str, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise DeviceInvalid(f"Ervenytelen '{field}' mezo.")
    value = value.strip()
    if not value and not required:
        return None
    if not _is_clean_text(value):
        raise DeviceInvalid(f"Ervenytelen '{field}' mezo (1-64 karakter, vezerlokarakter nelkul).")
    return value


class DeviceStore:
    def __init__(self, path: str = DEVICES_PATH, token_days: int = 90, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._token_days = token_days
        self._clock = clock
        self._lock = threading.RLock()
        self.load_warnings: list[str] = []  # a naplozas beallitasa elotti figyelmeztetesek (lasd replay_load_warnings)
        self._needs_backup = False  # a betoltes nem a teljes fajlt vette at: iras elott mentes kell
        self._devices: list[Device] = self._load()

    # ---------- betoltes / mentes ----------

    def _warn(self, message: str) -> None:
        self.load_warnings.append(message)
        logger.warning(message)

    def replay_load_warnings(self) -> None:
        """A betoltes figyelmeztetesei ujra a naploba (a szerver a naplo beallitasa utan hivja)."""
        for message in self.load_warnings:
            logger.warning(message)

    def _load(self) -> list[Device]:
        if not os.path.isfile(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):  # ValueError: JSONDecodeError es UnicodeDecodeError is
            raw = None
        if not isinstance(raw, list):
            self._needs_backup = True
            self._warn("A devices.json serult vagy nem lista: nincs engedelyezett eszkoz.")
            return []
        devices: list[Device] = []
        for item in raw:
            try:
                device = Device.model_validate(item)
            except Exception:
                device, problem = None, "mezok"
            else:
                problem = _loaded_problem(device)
                if problem is None:
                    if any(d.id == device.id for d in devices):
                        problem = "id (duplikalt)"
                    elif any(d.ip_address == device.ip_address for d in devices):
                        problem = "ip_address (duplikalt)"
                    elif device.uuid is not None and any(d.uuid == device.uuid for d in devices):
                        problem = "uuid (duplikalt)"
                    elif any(d.token_hash == device.token_hash for d in devices):
                        problem = "token_hash (duplikalt)"
            if problem is not None:
                self._needs_backup = True
                self._warn(f"A devices.json egy bejegyzese ervenytelen ({problem}), kihagyva.")
                continue
            devices.append(device)
        return devices

    def _backup_original(self) -> None:
        """A serult/reszben betoltott fajlt az elso iras elott megorizzuk (kulonben az iras eltorolne)."""
        if not self._needs_backup or not os.path.isfile(self._path):
            return
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.now()))
        backup = f"{self._path}.corrupt-{stamp}"
        shutil.copy2(self._path, backup)  # ha ez hibazik, az iras sem tortenik meg
        self._needs_backup = False
        logger.warning("A betoltetlen devices.json tartalma elmentve: %s", backup)

    def _save(self, devices: list[Device]) -> None:
        self._backup_original()
        data = [d.model_dump() for d in devices]
        directory = os.path.dirname(self._path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".devices-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def _commit(self, devices: list[Device]) -> None:
        """Eloszor mentes, csak siker eseten csereli a memoriabeli listat (hiba eseten nincs felig-allapot)."""
        self._save(devices)
        self._devices = devices

    def _replace(self, updated: Device) -> list[Device]:
        return [updated if d.id == updated.id else d for d in self._devices]

    # ---------- olvasas ----------

    def now(self) -> int:
        return int(self._clock())

    def list(self) -> list[Device]:
        with self._lock:
            return [d.model_copy() for d in self._devices]

    def get(self, device_id: str) -> Device | None:
        with self._lock:
            device = self._find(device_id)
            return device.model_copy() if device else None

    def _find(self, device_id: str) -> Device | None:
        return next((d for d in self._devices if d.id == device_id), None)

    def _require(self, device_id: str) -> Device:
        device = self._find(device_id)
        if device is None:
            raise DeviceNotFound("Ismeretlen eszkoz.")
        return device

    def find_by_uuid(self, uuid: str) -> Device | None:
        with self._lock:
            device = next((d for d in self._devices if d.uuid == uuid), None)
            return device.model_copy() if device else None

    def find_by_token(self, token: str) -> Device | None:
        if not isinstance(token, str) or not token:
            return None
        digest = hash_token(token)
        with self._lock:
            match = None
            for device in self._devices:  # vegigmegy mindenen: az idozites ne arulkodjon
                if hmac.compare_digest(device.token_hash.encode(), digest.encode()):
                    match = device
            return match.model_copy() if match else None

    def is_current(self, device_id: str, token_hash: str) -> bool:
        with self._lock:
            device = self._find(device_id)
            return (
                device is not None
                and device.enabled
                and hmac.compare_digest(device.token_hash.encode(), token_hash.encode())
                and self.now() < device.token_expires_at
            )

    def expiring(self, within_days: int = 14) -> list[Device]:
        limit = self.now() + within_days * 86400
        with self._lock:
            return [d.model_copy() for d in self._devices if d.enabled and d.token_expires_at <= limit]

    # ---------- iras ----------

    def _ip_or_raise(self, value: object, own_id: str | None = None) -> str:
        ip = normalize_ip(value)
        if ip is None:
            raise DeviceInvalid("Ervenytelen IP-cim.")
        if any(d.ip_address == ip and d.id != own_id for d in self._devices):
            raise DeviceConflict("Ezzel az IP-cimmel mar van eszkoz.")
        return ip

    def _new_token(self) -> tuple[str, str, int]:
        token = secrets.token_urlsafe(32)
        return token, hash_token(token), self.now() + self._token_days * 86400

    def create(self, name: str, ip_address: str, *, mac_address=None, hostname=None, version=None, uuid=None) -> tuple[Device, str]:
        name = _clean_text(name, "name", required=True)
        optional = {
            "mac_address": _clean_text(mac_address, "mac_address", False),
            "hostname": _clean_text(hostname, "hostname", False),
            "version": _clean_text(version, "version", False),
        }
        if uuid is not None and not (isinstance(uuid, str) and _is_uuid5(uuid)):
            raise DeviceInvalid("Ervenytelen 'uuid' mezo (v5 UUID kell).")
        with self._lock:
            ip = self._ip_or_raise(ip_address)
            if uuid is not None and any(d.uuid == uuid for d in self._devices):
                raise DeviceConflict("Ezzel az UUID-val mar van eszkoz.")
            token, digest, expires = self._new_token()
            device = Device(
                id="d_" + secrets.token_hex(4),
                name=name,
                ip_address=ip,
                token_hash=digest,
                token_expires_at=expires,
                created_at=self.now(),
                uuid=uuid,
                **optional,
            )
            self._commit(self._devices + [device])
            log_event(device.name, "created", ip=device.ip_address)
            return device.model_copy(), token

    def update(self, device_id: str, **fields) -> Device:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise DeviceInvalid(f"Nem modosithato mezo: {', '.join(sorted(unknown))}")
        with self._lock:
            device = self._require(device_id)
            changes: dict = {}
            if "name" in fields:
                changes["name"] = _clean_text(fields["name"], "name", required=True)
            if "ip_address" in fields:
                changes["ip_address"] = self._ip_or_raise(fields["ip_address"], own_id=device.id)
            if "enabled" in fields:
                if not isinstance(fields["enabled"], bool):
                    raise DeviceInvalid("Az 'enabled' mezo true/false kell legyen.")
                changes["enabled"] = fields["enabled"]
            for key in _OPTIONAL:
                if key in fields:
                    changes[key] = _clean_text(fields[key], key, False)
            updated = device.model_copy(update=changes)
            self._commit(self._replace(updated))
            if "enabled" in changes and changes["enabled"] != device.enabled:
                log_event(updated.name, "enabled" if updated.enabled else "disabled")
            other = sorted(k for k in changes if k != "enabled")
            if other:
                log_event(updated.name, "updated", fields=",".join(other))
            return updated.model_copy()

    def rotate(self, device_id: str) -> tuple[Device, str]:
        with self._lock:
            device = self._require(device_id)
            token, digest, expires = self._new_token()
            updated = device.model_copy(update={"token_hash": digest, "token_expires_at": expires})
            self._commit(self._replace(updated))
            log_event(updated.name, "rotated")
            return updated.model_copy(), token

    def delete(self, device_id: str) -> Device:
        with self._lock:
            device = self._require(device_id)
            self._commit([d for d in self._devices if d.id != device.id])
            log_event(device.name, "deleted")
            return device

    def touch(self, device_id: str) -> None:
        with self._lock:
            device = self._find(device_id)
            if device is None:
                return
            try:
                self._commit(self._replace(device.model_copy(update={"last_seen": self.now()})))
            except Exception as exc:  # az utolso kapcsolat csak tajekoztato: ne buktasson kapcsolatot
                logger.warning("A last_seen nem menthet: %s", exc)
