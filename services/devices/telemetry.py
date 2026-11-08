"""Eszkoz-telemetria: az ESP32 buffer-allapota (utolso ertek) es eszkozonkenti esemeny-naplo.

A naplo memoriaban (korlatos) es `logs/devices/<id>.jsonl` fajlban el; ujrainditas utan is megmarad.
Az erkezesi idot a szerver adja; az ESP sajat `ts` mezoje csak informacio (az ESP ora pontatlan lehet).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from typing import Callable

DEVICE_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "devices"
)

MAX_EVENTS = 1000  # eszkozonkent
MAX_BATCH = 50  # egy kerestben
MAX_BODY_BYTES = 16 * 1024
RATE_LIMIT = 120  # kerest / perc / eszkoz (a ket POST egyutt)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TYPE_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_MSG_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,200}$")
_BUFFER_FIELDS = ("fill_ms", "capacity_ms", "underruns", "drops")
_INT_MAX = 10_000_000


class TelemetryInvalid(ValueError):
    pass


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _INT_MAX


def parse_buffer(body: object) -> dict:
    """A buffer-jelentes ellenorzott alakja; ismeretlen mezo eldobva, `fill_ms` kotelezo."""
    if not isinstance(body, dict):
        raise TelemetryInvalid("Ervenytelen kerestartalom.")
    if "fill_ms" not in body:
        raise TelemetryInvalid("A fill_ms megadasa kotelezo.")
    data = {}
    for key in _BUFFER_FIELDS:
        if key in body:
            if not _is_int(body[key]):
                raise TelemetryInvalid(f"Ervenytelen ertek: {key}.")
            data[key] = body[key]
    return data


def parse_events(body: object) -> list[dict]:
    """Az ESP naplo-esemenyei ellenorizve: {type, msg?, ts?}; max MAX_BATCH db."""
    if not isinstance(body, dict) or not isinstance(body.get("events"), list):
        raise TelemetryInvalid("Ervenytelen kerestartalom.")
    raw = body["events"]
    if not raw or len(raw) > MAX_BATCH:
        raise TelemetryInvalid(f"Az events lista 1-{MAX_BATCH} elemu lehet.")
    events = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str) or not _TYPE_RE.match(item["type"]):
            raise TelemetryInvalid("Ervenytelen esemenytipus.")
        event = {"type": item["type"]}
        msg = item.get("msg")
        if msg is not None:
            if not isinstance(msg, str) or not _MSG_RE.match(msg):
                raise TelemetryInvalid("Ervenytelen uzenet.")
            event["msg"] = msg
        ts = item.get("ts")
        if ts is not None:
            if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not 0 <= ts <= 4_102_444_800:
                raise TelemetryInvalid("Ervenytelen ts.")
            event["device_ts"] = ts
        events.append(event)
    return events


class DeviceTelemetry:
    def __init__(
        self,
        directory: str | None = DEVICE_LOG_DIR,
        clock: Callable[[], float] = time.time,
        max_events: int = MAX_EVENTS,
    ) -> None:
        self._dir = directory  # None: csak memoria
        self._clock = clock
        self._max = max_events
        self._lock = threading.Lock()
        self._buffers: dict[str, dict] = {}
        self._events: dict[str, deque] = {}
        self._appended: dict[str, int] = {}
        self._hits: dict[str, deque] = {}

    # --- sebessegkorlat ---

    def allow(self, device_id: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(device_id, deque())
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= RATE_LIMIT:
                return False
            hits.append(now)
            return True

    # --- buffer ---

    def set_buffer(self, device_id: str, data: dict) -> dict:
        state = dict(data, received_at=int(self._clock()))
        with self._lock:
            self._buffers[device_id] = state
        return state

    def get_buffer(self, device_id: str) -> dict | None:
        with self._lock:
            state = self._buffers.get(device_id)
            return dict(state) if state else None

    # --- naplo ---

    def _path(self, device_id: str) -> str | None:
        if self._dir is None or not _ID_RE.match(device_id):
            return None
        return os.path.join(self._dir, device_id + ".jsonl")

    def _load(self, device_id: str) -> deque:
        """A hivo tartja a zarat."""
        events = self._events.get(device_id)
        if events is not None:
            return events
        events = deque(maxlen=self._max)
        path = self._path(device_id)
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            item = json.loads(line)
                        except ValueError:
                            continue  # serult sor (pl. megszakadt iras): kihagyjuk
                        if isinstance(item, dict):
                            events.append(item)
            except OSError:
                pass
        self._events[device_id] = events
        return events

    def _write(self, device_id: str, new: list[dict], events: deque) -> None:
        """A hivo tartja a zarat. Hozzafuz; ha sok gyult fel, a fajlt a korlatos allapotra irja ujra."""
        path = self._path(device_id)
        if path is None:
            return
        try:
            os.makedirs(self._dir, exist_ok=True)
            count = self._appended.get(device_id, 0) + len(new)
            if count > self._max:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
                os.replace(tmp, path)
                count = 0
            else:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in new)
            self._appended[device_id] = count
        except OSError:
            pass  # a telemetria nem dobhatja el a hang-streamet; a memoria-naplo megmarad

    def add(self, device_id: str, source: str, events: list[dict]) -> int:
        """Esemenyek hozzaadasa; `source`: "esp" vagy "server". Az erkezesi idot a szerver irja."""
        now = int(self._clock())
        stamped = [dict(event, ts=now, source=source) for event in events]
        with self._lock:
            log = self._load(device_id)
            log.extend(stamped)
            self._write(device_id, stamped, log)
        return len(stamped)

    def server_event(self, device_id: str, event_type: str, **fields) -> None:
        extra = {}
        if event_type == "connected":
            with self._lock:
                last = next(
                    (e for e in reversed(self._load(device_id)) if e.get("source") == "server" and e.get("type") == "disconnected"),
                    None,
                )
            if last is not None:
                extra["offline_seconds"] = max(0, int(self._clock()) - last["ts"])
        self.add(device_id, "server", [dict({"type": event_type}, **fields, **extra)])

    def read(self, device_id: str, event_type: str | None = None, since: float | None = None, limit: int = 200) -> list[dict]:
        """A legujabb `limit` esemeny, idorendben (regebbi elol)."""
        with self._lock:
            events = list(self._load(device_id))
        if event_type is not None:
            events = [e for e in events if e.get("type") == event_type]
        if since is not None:
            events = [e for e in events if e.get("ts", 0) >= since]
        return events[-limit:] if limit > 0 else []

    def delete(self, device_id: str) -> None:
        with self._lock:
            self._buffers.pop(device_id, None)
            self._events.pop(device_id, None)
            self._appended.pop(device_id, None)
            self._hits.pop(device_id, None)
            path = self._path(device_id)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
