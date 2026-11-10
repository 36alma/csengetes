"""A mikrofongépek kapcsolatainak kezelése és bekötése a mixerbe."""

from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass

from micserver.protocol import DEFAULT_PORT
from model import RemoteMicSource
from services.devices.audit import log_event

from .client import RemoteMicClient, RotationUnavailable
from .jitter import JitterBuffer
from .store import RemoteMicError, RemoteMicNotFound, RemoteMicStore, public_view


class RemoteMicNotLive(RemoteMicError):
    """A művelethez élő kapcsolat kell."""


class RemoteMicRotationFailed(RemoteMicError):
    """A gép nem erősítette meg az új tokent; a régi érvényes maradt."""


class _Channel:
    """A keverőszál ezt látja. A hangerő itt él, mert a store a lemezírás alatt is zárat tart."""

    def __init__(self, source_id: str, volume: float) -> None:
        self.id = source_id
        self.volume = volume
        self.buffer = JitterBuffer()

    def read_block(self):
        return self.buffer.read_block()


@dataclass
class _Link:
    channel: _Channel
    client: RemoteMicClient


class RemoteMicManager:
    def __init__(self, store: RemoteMicStore, mixer, backoff_start: float = 1.0, backoff_max: float = 10.0,
                 rotate_wait: float = 12.0) -> None:
        self._store = store
        self._mixer = mixer
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._rotate_wait = rotate_wait
        self._lock = threading.Lock()
        self._links: dict[str, _Link] = {}
        self._last_state: dict[str, str] = {}  # miért állt le magától (pl. hibás token)

    # ---------- nézetek ----------

    def _status(self, source_id: str) -> tuple[bool, str]:
        with self._lock:
            link = self._links.get(source_id)
            if link is not None:
                return True, link.client.state
            return False, self._last_state.get(source_id, "off")

    def _view(self, source: RemoteMicSource) -> dict:
        live, state = self._status(source.id)
        return {**public_view(source), "live": live, "state": state}

    def sources(self) -> list[dict]:
        return [self._view(source) for source in self._store.list()]

    def state(self) -> list[dict]:
        """A Mixer fül gyakran kérdezi le: csak a sávokhoz szükséges mezők."""
        levels = self._mixer.remote_levels
        result = []
        for source in self._store.list():
            live, state = self._status(source.id)
            result.append({
                "id": source.id,
                "name": source.name,
                "host": source.host,
                "live": live,
                "state": state,
                "volume": source.volume,
                "level": round(levels.get(source.id, 0.0), 3),
            })
        return result

    # ---------- belső ----------

    def _require(self, source_id: str) -> RemoteMicSource:
        source = self._store.get(source_id)
        if source is None:
            raise RemoteMicNotFound("Ismeretlen mikrofongép.")
        return source

    def _publish(self) -> None:
        self._mixer.remote_channels = tuple(link.channel for link in self._links.values())

    def _start(self, source: RemoteMicSource) -> None:
        with self._lock:
            if source.id in self._links:
                return
            channel = _Channel(source.id, source.volume)
            client = RemoteMicClient(
                source.id, self._store, channel.buffer, on_finished=self._on_finished,
                backoff_start=self._backoff_start, backoff_max=self._backoff_max,
            )
            self._links[source.id] = _Link(channel, client)
            self._last_state.pop(source.id, None)
            self._publish()
        client.start()

    def _stop(self, source_id: str) -> None:
        with self._lock:
            link = self._links.pop(source_id, None)
            self._last_state.pop(source_id, None)
            self._publish()
        if link is not None:
            link.client.stop()  # a záron kívül: a kliens szála az _on_finished-ben kéri a zárat

    def _on_finished(self, client: RemoteMicClient, final: str) -> None:
        """A kliens magától állt le (pl. hibás token): a kapcsoló kikapcsol, az ok látszik."""
        with self._lock:
            link = self._links.get(client.source_id)
            if link is None or link.client is not client:
                return
            del self._links[client.source_id]
            self._last_state[client.source_id] = final
            self._publish()

    # ---------- műveletek ----------

    def add(self, name: object, host: object, port: object = DEFAULT_PORT) -> tuple[dict, str]:
        source, token = self._store.create(name, host, port)
        log_event(source.name, "mic_created", host=source.host, port=source.port)
        return self._view(source), token

    def update(self, source_id: str, /, **fields) -> dict:
        before = self._require(source_id)
        updated = self._store.update(source_id, **fields)
        if (updated.host, updated.port) != (before.host, before.port):
            self._stop(source_id)  # a futó kapcsolat még a régi címre mutat
        return self._view(updated)

    def delete(self, source_id: str) -> None:
        source = self._require(source_id)
        self._stop(source_id)
        self._store.delete(source_id)
        log_event(source.name, "mic_deleted")

    def set_live(self, source_id: str, live: bool) -> dict:
        source = self._require(source_id)
        if live:
            self._start(source)
        else:
            self._stop(source_id)
        return self._view(source)

    def set_volume(self, source_id: str, volume: object) -> dict:
        updated = self._store.set_volume(source_id, volume)
        with self._lock:
            link = self._links.get(source_id)
            if link is not None:
                link.channel.volume = updated.volume
        return self._view(updated)

    def rotate_now(self, source_id: str) -> dict:
        source = self._require(source_id)
        with self._lock:
            link = self._links.get(source_id)
        if link is None:
            raise RemoteMicNotLive("Nincs élő kapcsolat a géppel.")
        try:
            future = link.client.request_rotation()
        except RotationUnavailable as exc:
            raise RemoteMicNotLive("Nincs élő kapcsolat, vagy már folyamatban van egy tokencsere.") from exc
        try:
            confirmed = future.result(timeout=self._rotate_wait)
        except concurrent.futures.TimeoutError:
            confirmed = False
        if not confirmed:
            raise RemoteMicRotationFailed("A gép nem erősítette meg az új tokent, a régi érvényes maradt.")
        return self._view(self._store.get(source.id))

    def reissue(self, source_id: str) -> tuple[dict, str]:
        source = self._require(source_id)
        # előbb a kapcsolat áll le, különben egy épp futó rotáció felülírná az új tokent
        self._stop(source_id)
        updated, token = self._store.reissue(source_id)
        log_event(source.name, "mic_reissued")
        return self._view(updated), token

    def shutdown(self) -> None:
        with self._lock:
            links = list(self._links.values())
            self._links.clear()
            self._publish()
        for link in links:
            link.client.stop()
