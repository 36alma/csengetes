"""Közös segédek a hálózati mikrofon tesztjeihez: mikrofon-helyettes és háttérben futó szerver."""

import asyncio
import base64
import hashlib
import hmac
import json
import struct
import threading
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


def mac_hex(key, message):
    """A protokoll HMAC-je a tesztelt kódtól függetlenül számolva."""
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def derive(key, nonce):
    digest = hmac.new(key.encode(), f"rotate|{nonce}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def write_state(path, current, previous=None):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"current": current, "previous": previous}, fh)


def read_state(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class FakeCapture:
    """Mikrofon helyett: futás közben folyamatosan ad hangblokkokat."""

    def __init__(self, on_block, value, fail):
        self._on_block = on_block
        self._frame = struct.pack("<h", value) * 1152
        self._fail = fail
        self.started = threading.Event()
        self.stopped = threading.Event()
        self._thread = None

    def start(self):
        if self._fail:
            raise OSError("nincs mikrofon")
        self.started.set()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        while not self.stopped.is_set():
            self._on_block(self._frame)
            time.sleep(0.005)

    def stop(self):
        self.stopped.set()
        if self._thread is not None:
            self._thread.join(2)


class Captures:
    """Capture-gyár: megjegyzi, hányszor nyitották meg a mikrofont."""

    def __init__(self, value=16384, fail=False):
        self.items = []
        self.value = value
        self.fail = fail

    def __call__(self, on_block):
        capture = FakeCapture(on_block, self.value, self.fail)
        self.items.append(capture)
        return capture


class RunningMicServer:
    """A MicServer futtatása háttérszálon, saját eseményhurokkal."""

    def __init__(self, server):
        self.server = server
        self._ready = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "a MicServer nem indult el"

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        await self.server.run(self._stop, self._ready.set)

    @property
    def port(self):
        return self.server.port

    def close(self):
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(5)


def open_ws(port):
    return connect(f"ws://127.0.0.1:{port}", proxy=None, open_timeout=3, close_timeout=1)


def send_auth(ws, credential, client_nonce="b" * 32):
    """A hello átvétele és az auth elküldése; a hello üzenetet adja vissza."""
    hello = json.loads(ws.recv(timeout=3))
    proof = mac_hex(credential, f"c|{hello['nonce']}|{client_nonce}")
    ws.send(json.dumps({"type": "auth", "nonce": client_nonce, "proof": proof}))
    return hello


def next_text(ws, timeout=3.0):
    """A következő szöveges üzenet; a közben érkező hangblokkokat átugorja."""
    deadline = time.time() + timeout
    while True:
        message = ws.recv(timeout=max(0.01, deadline - time.time()))
        if isinstance(message, str):
            return json.loads(message)


def close_code(ws, timeout=3.0):
    """Olvas a kapcsolat lezárásáig, és a szerver lezárási kódját adja vissza."""
    deadline = time.time() + timeout
    try:
        while True:
            ws.recv(timeout=max(0.01, deadline - time.time()))
    except ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd is not None else None
