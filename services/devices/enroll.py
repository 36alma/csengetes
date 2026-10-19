"""Eszkoz-jelentkezes (enrollment): az ESP32 UDP-datagramot kuld, a szerver csak hallgat.

A datagram JSON: {"uuid": "<v5 UUID>", "ver": "<firmware>", "ts": <epoch mp>, "sig": "<hex>"},
ahol sig = HMAC-SHA256(enroll_key, "<uuid>|<ver>|<ts>"). Az alairas bizonyitja, hogy a kuldo ismeri a
firmware-be egetett kulcsot; a UUID-t a kliens adja, a szerver csak a formatumat ellenorzi.
A jelentkezok csak a memoriaban, jovahagyasra varnak: az eszkoz felvetele (es a token) admin dontes.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
import uuid as uuid_lib
from typing import Callable

from .audit import log_event
from .store import DeviceConflict, DeviceNotFound, DeviceStore, normalize_ip, _is_clean_text, _is_uuid5

logger = logging.getLogger("csengetes")

MIN_KEY_LENGTH = 16
MAX_DATAGRAM = 512
MAX_SKEW_SECONDS = 60
PENDING_TTL_SECONDS = 600
MAX_PENDING = 64
DENIED_LOG_INTERVAL = 60


def parse_uuid5(value: object) -> str | None:
    """Kanonikus (kisbetus) UUID-szoveg, ha az ertek v5, RFC 4122 variansu UUID; kulonben None."""
    return value if isinstance(value, str) and _is_uuid5(value) else None


def sign(key: str, uuid: str, version: str, ts: int) -> str:
    message = f"{uuid}|{version}|{ts}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify(datagram: bytes, key: str, now: float) -> tuple[dict | None, str]:
    """(jelentkezes, "") ervenyes datagramra, kulonben (None, ok)."""
    if len(datagram) > MAX_DATAGRAM:
        return None, "too_large"
    try:
        data = json.loads(datagram.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "malformed"
    if not isinstance(data, dict):
        return None, "malformed"
    uuid, version, ts, sig = data.get("uuid"), data.get("ver", ""), data.get("ts"), data.get("sig")
    if isinstance(ts, bool) or not isinstance(ts, int) or not isinstance(sig, str) or not isinstance(version, str):
        return None, "malformed"
    if version and not _is_clean_text(version):
        return None, "malformed"
    clean_uuid = parse_uuid5(uuid)
    if clean_uuid is None:
        return None, "bad_uuid"
    if not hmac.compare_digest(sign(key, clean_uuid, version, ts).encode(), sig.encode("utf-8", "replace")):
        return None, "bad_signature"
    if abs(now - ts) > MAX_SKEW_SECONDS:
        return None, "stale"
    return {"uuid": clean_uuid, "version": version or None, "sig": sig}, ""


class Enrollment:
    """A jovahagyasra varo jelentkezok es a jelentkezes elbiralasa."""

    def __init__(self, store: DeviceStore, key: str, cidr: str = "", clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._key = key
        self._clock = clock
        self._lock = threading.Lock()
        self._network = ipaddress.ip_network(cidr, strict=False) if cidr else None
        self._pending: dict[str, dict] = {}
        self._seen_sigs: dict[str, float] = {}
        self._denied_logged: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return len(self._key) >= MIN_KEY_LENGTH

    def _source_allowed(self, ip: str) -> bool:
        address = ipaddress.ip_address(ip)
        if self._network is not None:
            return address in self._network
        return address.is_private  # alapertelmezes: csak helyi halozatrol fogadunk jelentkezest

    def _deny(self, ip: str, reason: str, now: float) -> None:
        if now - self._denied_logged.get(ip, 0) >= DENIED_LOG_INTERVAL:
            if len(self._denied_logged) > 256:
                self._denied_logged = {k: v for k, v in self._denied_logged.items() if now - v < DENIED_LOG_INTERVAL}
            self._denied_logged[ip] = now
            log_event(None, "enroll_denied", logging.WARNING, reason=reason, ip=ip)

    def handle(self, datagram: bytes, source_ip: str) -> bool:
        """Egy beerkezo datagram feldolgozasa; True, ha ervenyes jelentkezes volt."""
        if not self.enabled:
            return False
        ip = normalize_ip(source_ip)
        now = self._clock()
        with self._lock:
            if ip is None or not self._source_allowed(ip):
                self._deny(str(source_ip), "source_not_allowed", now)
                return False
            beacon, reason = verify(datagram, self._key, now)
            if beacon is None:
                self._deny(ip, reason, now)
                return False
            self._seen_sigs = {s: t for s, t in self._seen_sigs.items() if now - t <= MAX_SKEW_SECONDS}
            if beacon["sig"] in self._seen_sigs:  # ugyanaz a datagram masodszor: visszajatszas
                self._deny(ip, "replay", now)
                return False
            self._seen_sigs[beacon["sig"]] = now
            self._observe(beacon["uuid"], ip, beacon["version"], now)
        return True

    def _observe(self, uuid: str, ip: str, version: str | None, now: float) -> None:
        known = self._store.find_by_uuid(uuid)
        if known is not None and known.ip_address == ip:
            self._pending.pop(uuid, None)  # mar felvett, rendben jelentkezo eszkoz
            return
        entry = self._pending.get(uuid)
        if entry is None:
            self._prune(now)
            if len(self._pending) >= MAX_PENDING:
                self._deny(ip, "pending_full", now)
                return
            self._pending[uuid] = {"uuid": uuid, "ip_address": ip, "version": version, "first_seen": int(now),
                                   "last_seen": int(now), "ip_changed": False}
            log_event(None, "enroll_pending", uuid=uuid, ip=ip)
            return
        entry["last_seen"] = int(now)
        entry["version"] = version
        if entry["ip_address"] != ip and not entry["ip_changed"]:
            entry["ip_changed"] = True  # ugyanaz az UUID mas cimrol: a jovahagyas elott az adminnak kell dontenie
            log_event(None, "enroll_conflict", logging.WARNING, uuid=uuid, ip=ip, first_ip=entry["ip_address"])

    def _prune(self, now: float) -> None:
        self._pending = {u: e for u, e in self._pending.items() if now - e["last_seen"] <= PENDING_TTL_SECONDS}

    def _conflict(self, entry: dict) -> str | None:
        if self._store.find_by_uuid(entry["uuid"]) is not None:
            return "uuid_known"  # mas IP-rol jelentkezik, mint a felvett eszkoz
        if entry["ip_changed"]:
            return "ip_changed"
        if any(d.ip_address == entry["ip_address"] for d in self._store.list()):
            return "ip_taken"
        return None

    def list(self) -> list[dict]:
        with self._lock:
            self._prune(self._clock())
            return [dict(e, conflict=self._conflict(e)) for e in self._pending.values()]

    def dismiss(self, uuid: str) -> None:
        with self._lock:
            if self._pending.pop(uuid, None) is None:
                raise DeviceNotFound("Ismeretlen jelentkezo.")

    def approve(self, uuid: str, name: str):
        with self._lock:
            self._prune(self._clock())
            entry = self._pending.get(uuid)
            if entry is None:
                raise DeviceNotFound("Ismeretlen jelentkezo.")
            if self._conflict(entry) is not None:
                raise DeviceConflict("Utkozes miatt nem hagyhato jova, elobb vesd el a jelentkezest.")
            device, token = self._store.create(name, entry["ip_address"], version=entry["version"], uuid=uuid)
            del self._pending[uuid]
            return device, token


class EnrollListener:
    """UDP-hallgato szal: a datagramokat az Enrollment-nek adja. Kulcs nelkul nem indul el."""

    def __init__(self, enrollment: Enrollment, host: str, port: int) -> None:
        self._enrollment = enrollment
        self._host = host
        self._port = port
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not self._enrollment.enabled:
            logger.info("Az eszkoz-jelentkezes ki van kapcsolva (enroll_key nincs megadva, min. %d karakter).", MIN_KEY_LENGTH)
            return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self._host, self._port))
            sock.settimeout(0.5)
        except OSError as exc:
            logger.error("Az eszkoz-jelentkezes nem indult el a %s:%s cimen: %s", self._host, self._port, exc)
            return False
        self._sock = sock
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        sock = self._sock
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(MAX_DATAGRAM + 1)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._enrollment.handle(data, addr[0])
            except Exception:
                logger.exception("Hiba a jelentkezes feldolgozasakor.")

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
