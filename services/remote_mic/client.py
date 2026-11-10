"""A mixer kliense egy mikrofongéphez: kapcsolódás, kölcsönös azonosítás, hang fogadása, rotáció."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from typing import Callable

import numpy as np
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from micserver.protocol import (
    CLOSE_POLICY,
    FRAME_BYTES,
    PROTOCOL_VERSION,
    STREAM_INFO,
    ProtocolError,
    client_proof,
    decode,
    derive_rotated,
    encode,
    is_valid_nonce,
    new_nonce,
    proofs_equal,
    rotated_proof,
    server_proof,
)
from model import RemoteMicSource
from services.devices.audit import log_event

from .jitter import JitterBuffer
from .store import RemoteMicStore

logger = logging.getLogger("csengetes")

ROTATE_AFTER_SECONDS = 24 * 3600
ROTATE_CHECK_SECONDS = 60
ROTATE_RETRY_SECONDS = 3600  # sikertelen rotáció után ennyi idő múlva próbálja újra
HANDSHAKE_SECONDS = 5.0
RECV_POLL_SECONDS = 0.25  # a leállítás és a rotáció ennyin belül érvényesül
NO_AUDIO_SECONDS = 1.0
MAX_MESSAGE_BYTES = 16384

_STREAMING = "streaming"  # belső állapot: kifelé on_air vagy no_audio


class RotationUnavailable(Exception):
    """Nincs élő kapcsolat, vagy már folyamatban van egy kézi rotáció."""


class _AuthFailed(Exception):
    pass


class _ProtocolFailure(Exception):
    pass


class _MicFailure(Exception):
    pass


class _Reconnect(Exception):
    """A rotáció eltérést okozott: újra be kell lépni a lemezen lévő tokennel."""


class _Session:
    def __init__(self, credential: str) -> None:
        self.credential = credential
        self.pending: tuple[str, str] | None = None  # (nonce, új érték) a válaszig
        self.pending_manual = False
        self.next_check = 0  # epoch másodperc: mikor nézzük meg újra, esedékes-e


def _uri(source: RemoteMicSource) -> str:
    host = f"[{source.host}]" if ":" in source.host else source.host
    return f"ws://{host}:{source.port}"


class RemoteMicClient:
    def __init__(
        self,
        source_id: str,
        store: RemoteMicStore,
        buffer: JitterBuffer,
        on_finished: Callable[["RemoteMicClient", str], None] | None = None,
        backoff_start: float = 1.0,
        backoff_max: float = 10.0,
    ) -> None:
        self.source_id = source_id
        self._store = store
        self._buffer = buffer
        self._on_finished = on_finished
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "off"
        self._last_frame = 0.0
        self._warned_frame = False
        self._had_audio = False
        self._lock = threading.Lock()
        self._rotation_request: Future | None = None

    @property
    def state(self) -> str:
        if self._state != _STREAMING:
            return self._state
        return "on_air" if time.monotonic() - self._last_frame < NO_AUDIO_SECONDS else "no_audio"

    def start(self) -> None:
        self._state = "connecting"
        self._thread = threading.Thread(target=self._run, name=f"remote-mic-{self.source_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                logger.warning("A mikrofongép-kliens nem állt le időben (%s).", self.source_id)

    def request_rotation(self) -> Future:
        """Kézi rotáció kérése; a Future igaz, ha az új token mindkét oldalon érvénybe lépett."""
        with self._lock:
            if self._state != _STREAMING or self._rotation_request is not None:
                raise RotationUnavailable
            self._rotation_request = Future()
            return self._rotation_request

    # ---------- kapcsolat ----------

    def _run(self) -> None:
        delay = self._backoff_start
        final = "off"
        try:
            while not self._stop.is_set():
                source = self._store.get(self.source_id)
                if source is None:
                    break
                self._state = "connecting"
                try:
                    self._connect(source)
                except _AuthFailed:
                    log_event(source.name, "mic_auth_failed", logging.WARNING, host=source.host)
                    final = "auth_failed"
                    break
                except _ProtocolFailure:
                    log_event(source.name, "mic_protocol_error", logging.WARNING, host=source.host)
                    final = "protocol_error"
                    break
                except _MicFailure:
                    self._state = "mic_error"
                except (OSError, EOFError, WebSocketException, _Reconnect):
                    self._state = "unreachable"
                except Exception:
                    logger.exception("Váratlan hiba a mikrofongép-kliensben (%s)", source.name)
                    self._state = "unreachable"
                if self._had_audio:  # működő kapcsolat után nem a korábbi hibák szerint várunk
                    self._had_audio = False
                    delay = self._backoff_start
                if self._stop.wait(delay):
                    break
                delay = min(delay * 2, self._backoff_max)
        finally:
            self._state = final
            if self._on_finished is not None:
                self._on_finished(self, final)

    def _connect(self, source: RemoteMicSource) -> None:
        with connect(
            _uri(source),
            proxy=None,
            compression=None,
            max_size=MAX_MESSAGE_BYTES,
            open_timeout=5,
            close_timeout=1,
            ping_interval=10,
            ping_timeout=10,
        ) as ws:
            self._handshake(ws, source.token)
            self._stream(ws, source)

    def _recv_control(self, ws) -> dict:
        try:
            raw = ws.recv(timeout=HANDSHAKE_SECONDS)
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == CLOSE_POLICY:
                raise _AuthFailed from exc
            raise
        try:
            return decode(raw)
        except ProtocolError as exc:
            raise _ProtocolFailure from exc

    def _handshake(self, ws, credential: str) -> None:
        hello = self._recv_control(ws)
        server_nonce = hello.get("nonce")
        if hello["type"] != "hello" or hello.get("v") != PROTOCOL_VERSION or not is_valid_nonce(server_nonce):
            raise _ProtocolFailure
        client_nonce = new_nonce()
        proof = client_proof(credential, server_nonce, client_nonce)
        ws.send(encode({"type": "auth", "nonce": client_nonce, "proof": proof}))
        welcome = self._recv_control(ws)
        if welcome["type"] != "welcome":
            raise _ProtocolFailure
        # e nélkül egy token nélküli hamis gép is hangot juttathatna az iskolába
        if not proofs_equal(welcome.get("proof"), server_proof(credential, server_nonce, client_nonce)):
            raise _AuthFailed
        if welcome.get("stream") != STREAM_INFO:
            raise _ProtocolFailure

    def _stream(self, ws, source: RemoteMicSource) -> None:
        session = _Session(source.token)
        self._buffer.clear()
        self._last_frame = 0.0
        self._warned_frame = False
        self._state = _STREAMING
        self._store.touch(self.source_id)
        log_event(source.name, "mic_connected", host=source.host)
        try:
            while not self._stop.is_set():
                self._maybe_rotate(ws, session)
                try:
                    message = ws.recv(timeout=RECV_POLL_SECONDS)
                except TimeoutError:
                    continue
                if isinstance(message, bytes):
                    self._on_frame(message, source)
                else:
                    self._on_control(message, session, source)
        finally:
            self._state = "connecting"  # előbb az állapot: innentől nem fogadunk új rotációt
            self._finish_manual(False)
            log_event(source.name, "mic_disconnected")

    def _on_frame(self, frame: bytes, source: RemoteMicSource) -> None:
        if len(frame) != FRAME_BYTES:
            if not self._warned_frame:
                self._warned_frame = True
                log_event(source.name, "mic_bad_frame", logging.WARNING, size=len(frame))
            return
        self._last_frame = time.monotonic()
        self._had_audio = True
        self._buffer.feed(np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0)

    # ---------- rotáció ----------

    def _finish_manual(self, result: bool) -> None:
        with self._lock:
            future, self._rotation_request = self._rotation_request, None
        if future is not None:
            future.set_result(result)

    def _maybe_rotate(self, ws, session: _Session) -> None:
        if session.pending is not None:
            return
        with self._lock:
            manual = self._rotation_request is not None
        if not manual:
            now = self._store.now()
            if now < session.next_check:
                return
            session.next_check = now + ROTATE_CHECK_SECONDS
            source = self._store.get(self.source_id)
            if source is None or now - source.rotated_at < ROTATE_AFTER_SECONDS:
                return
        nonce = new_nonce()
        session.pending = (nonce, derive_rotated(session.credential, nonce))
        session.pending_manual = manual
        ws.send(encode({"type": "rotate", "nonce": nonce}))

    def _on_control(self, raw: str, session: _Session, source: RemoteMicSource) -> None:
        try:
            message = decode(raw)
        except ProtocolError:
            return
        kind = message["type"]
        if kind == "rotated" and session.pending is not None:
            self._complete_rotation(message, session, source)
        elif kind == "rotate_failed" and session.pending is not None:
            log_event(source.name, "mic_rotate_failed", logging.WARNING)
            session.pending = None
            session.next_check = self._store.now() + ROTATE_RETRY_SECONDS
            if session.pending_manual:
                self._finish_manual(False)
        elif kind == "error" and message.get("code") == "mic_unavailable":
            raise _MicFailure

    def _complete_rotation(self, message: dict, session: _Session, source: RemoteMicSource) -> None:
        nonce, new_value = session.pending
        if not proofs_equal(message.get("proof"), rotated_proof(new_value, nonce)):
            log_event(source.name, "mic_rotate_mismatch", logging.WARNING)
            raise _Reconnect
        try:
            self._store.set_credential(self.source_id, new_value)
        except Exception as exc:
            # a lemezen a régi token maradt, a gép ezt még elfogadja: azzal lépünk be újra
            log_event(source.name, "mic_rotate_save_failed", logging.ERROR, error=type(exc).__name__)
            raise _Reconnect from exc
        session.credential = new_value
        session.pending = None
        session.next_check = self._store.now() + ROTATE_CHECK_SECONDS
        log_event(source.name, "mic_rotated")
        if session.pending_manual:
            self._finish_manual(True)
