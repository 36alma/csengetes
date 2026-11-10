"""A mikrofongép WebSocket-szervere: azonosítja a mixert, küldi a hangot, kezeli a token-rotációt."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .protocol import (
    CLOSE_BUSY,
    CLOSE_INTERNAL,
    CLOSE_POLICY,
    DEFAULT_PORT,
    PROTOCOL_VERSION,
    STREAM_INFO,
    ProtocolError,
    atomic_write_json,
    client_proof,
    decode,
    derive_rotated,
    encode,
    is_valid_credential,
    is_valid_nonce,
    new_nonce,
    proofs_equal,
    rotated_proof,
    server_proof,
)

logger = logging.getLogger("micserver")

MAX_INBOUND_BYTES = 4096  # a mixer csak rövid vezérlőüzeneteket küld
AUDIO_QUEUE_BLOCKS = 8


class MicState:
    """A gépen tárolt titkok: a jelenlegi és a rotáció alatt még elfogadott előző."""

    def __init__(self, path: str) -> None:
        self._path = path

    def load(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = None
        if not isinstance(raw, dict):
            raw = {}
        return {key: raw.get(key) if is_valid_credential(raw.get(key)) else None for key in ("current", "previous")}

    def save(self, current: str, previous: str | None) -> None:
        atomic_write_json(self._path, {"current": current, "previous": previous})


class _Session:
    """Egy kapcsolat állapota: rotáció után az új titok lesz az aktuális."""

    def __init__(self, credential: str) -> None:
        self.credential = credential


def _offer(queue: asyncio.Queue, block: bytes) -> None:
    """Lemaradó hálózatnál a legrégebbi blokk esik ki, a mikrofon nem várakozhat."""
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(block)


def _match(state: dict, proof: object, server_nonce: str, client_nonce: str) -> tuple[str | None, bool]:
    """Melyik tárolt titokkal készült a bizonyíték; (titok, jelenlegi-e)."""
    found, is_current = None, False
    for key in ("current", "previous"):
        value = state[key]
        if value and proofs_equal(proof, client_proof(value, server_nonce, client_nonce)) and found is None:
            found, is_current = value, key == "current"
    return found, is_current


class MicServer:
    def __init__(
        self,
        state_path: str,
        capture_factory: Callable,
        name: str = "Mikrofongép",
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        handshake_timeout: float = 5.0,
        announce: Callable[[str], None] = print,
    ) -> None:
        self._state = MicState(state_path)
        self._capture_factory = capture_factory
        self._name = name
        self._host = host
        self.port = port
        self._handshake_timeout = handshake_timeout
        self._announce = announce
        self._busy = False

    async def run(self, stop: asyncio.Event | None = None, on_ready: Callable[[], None] | None = None) -> None:
        async with serve(
            self._handle,
            self._host,
            self.port,
            compression=None,
            max_size=MAX_INBOUND_BYTES,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=1,
        ) as server:
            self.port = server.sockets[0].getsockname()[1]
            logger.info("Figyelés: %s:%s", self._host, self.port)
            if on_ready is not None:
                on_ready()
            await (stop.wait() if stop is not None else asyncio.Future())

    async def _handle(self, ws: ServerConnection) -> None:
        try:
            await self._serve_connection(ws)
        except ConnectionClosed:
            pass  # a mixer eltűnt: a lezárást a finally ágak elvégezték

    async def _read_auth(self, ws: ServerConnection) -> tuple[str, object] | None:
        try:
            message = decode(await asyncio.wait_for(ws.recv(), self._handshake_timeout))
        except (asyncio.TimeoutError, ProtocolError):  # 3.10-en még nem azonos a beépített TimeoutError-ral
            return None
        client_nonce = message.get("nonce")
        if message["type"] != "auth" or not is_valid_nonce(client_nonce):
            return None
        return client_nonce, message.get("proof")

    async def _serve_connection(self, ws: ServerConnection) -> None:
        server_nonce = new_nonce()
        await ws.send(encode({"type": "hello", "v": PROTOCOL_VERSION, "name": self._name, "nonce": server_nonce}))
        auth = await self._read_auth(ws)
        if auth is None:
            await ws.close(CLOSE_POLICY)
            return
        client_nonce, proof = auth
        state = await asyncio.to_thread(self._state.load)
        credential, is_current = _match(state, proof, server_nonce, client_nonce)
        if credential is None:
            logger.warning("Sikertelen azonosítás: %s", ws.remote_address)
            await ws.close(CLOSE_POLICY)
            return
        if self._busy:
            await ws.close(CLOSE_BUSY)
            return
        self._busy = True
        try:
            if is_current and state["previous"]:
                await self._forget_previous(credential)
            proof_back = server_proof(credential, server_nonce, client_nonce)
            await ws.send(encode({"type": "welcome", "proof": proof_back, "stream": STREAM_INFO}))
            await self._stream(ws, _Session(credential))
        finally:
            self._busy = False

    async def _forget_previous(self, credential: str) -> None:
        """A jelenlegi titok használata bizonyítja, hogy a mixer megkapta: az előző törölhető."""
        try:
            await asyncio.to_thread(self._state.save, credential, None)
        except Exception as exc:
            logger.warning("Az előző titok nem törölhető: %s", type(exc).__name__)

    async def _stream(self, ws: ServerConnection, session: _Session) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=AUDIO_QUEUE_BLOCKS)

        def on_block(block: bytes) -> None:
            try:
                loop.call_soon_threadsafe(_offer, queue, block)
            except RuntimeError:
                pass  # az eseményhurok már leállt

        try:
            capture = self._capture_factory(on_block)
            await asyncio.to_thread(capture.start)
        except Exception as exc:
            logger.error("A mikrofon nem nyitható meg: %s", exc)
            await ws.send(encode({"type": "error", "code": "mic_unavailable"}))
            await ws.close(CLOSE_INTERNAL)
            return

        self._announce("ADÁSBAN")
        sender = asyncio.create_task(self._send_audio(ws, queue))
        receiver = asyncio.create_task(self._receive(ws, session))
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
            await asyncio.to_thread(capture.stop)
            self._announce("KI")

    async def _send_audio(self, ws: ServerConnection, queue: asyncio.Queue) -> None:
        while True:
            await ws.send(await queue.get())

    async def _receive(self, ws: ServerConnection, session: _Session) -> None:
        async for raw in ws:
            try:
                message = decode(raw)
            except ProtocolError:
                continue
            if message["type"] == "rotate":
                await self._rotate(ws, session, message.get("nonce"))

    async def _rotate(self, ws: ServerConnection, session: _Session, nonce: object) -> None:
        if not is_valid_nonce(nonce):
            await ws.send(encode({"type": "rotate_failed"}))
            return
        new_value = derive_rotated(session.credential, nonce)
        try:
            # előbb tartósan lemezre, csak utána a válasz; külön szálon, hogy a hang ne álljon meg
            await asyncio.to_thread(self._state.save, new_value, session.credential)
        except Exception as exc:
            logger.error("A token-rotáció nem menthető: %s", type(exc).__name__)
            await ws.send(encode({"type": "rotate_failed"}))
            return
        session.credential = new_value
        await ws.send(encode({"type": "rotated", "proof": rotated_proof(new_value, nonce)}))
