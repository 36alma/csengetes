import contextlib
import json
import logging
import os
import struct
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve as sync_serve

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import wait_until
from micserver.server import MicServer, MicState
from remote_mic_helpers import Captures, RunningMicServer, derive, mac_hex, read_state, write_state
from services.remote_mic.client import RemoteMicClient, RotationUnavailable
from services.remote_mic.jitter import JitterBuffer
from services.remote_mic.store import RemoteMicStore

DAY = 86400


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class Env:
    def __init__(self, tmp_path):
        self.clock = Clock()
        self.state = str(tmp_path / "state.json")
        self.store = RemoteMicStore(path=str(tmp_path / "remote_mics.json"), clock=self.clock)
        self.captures = Captures()
        self.finished = []
        self.server = None
        self.client = None
        self.buffer = JitterBuffer()

    def start_server(self, port=0):
        self.server = RunningMicServer(
            MicServer(self.state, self.captures, host="127.0.0.1", port=port, announce=lambda text: None)
        )
        return self.server.port

    def add_source(self, port):
        self.source, self.token = self.store.create("Igazgatói", "127.0.0.1", port)
        return self.source

    def make_client(self, backoff_max=0.1):
        self.client = RemoteMicClient(
            self.source.id, self.store, self.buffer,
            on_finished=lambda client, final: self.finished.append(final),
            backoff_start=0.05, backoff_max=backoff_max,
        )
        return self.client

    def connect(self, token_age=0):
        """Felvett forrás + futó gép + kapcsolódó kliens; a token token_age másodperces."""
        self.add_source(self.start_server())
        write_state(self.state, self.token)
        self.clock.now += token_age
        client = self.make_client()
        client.start()
        assert wait_until(lambda: client.state == "on_air"), client.state
        return client

    def current(self):
        return self.store.get(self.source.id).token


@pytest.fixture
def env(tmp_path):
    instance = Env(tmp_path)
    yield instance
    if instance.client is not None:
        instance.client.stop()
    if instance.server is not None:
        instance.server.close()


@contextlib.contextmanager
def scripted(script):
    """Kézzel irányított mikrofongép: a script maga írja le a protokollt."""
    connections = []

    def handler(ws):
        connections.append(ws)
        with contextlib.suppress(ConnectionClosed):
            script(ws, len(connections))

    with sync_serve(handler, "127.0.0.1", 0, compression=None) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server.socket.getsockname()[1], connections
        server.shutdown()
    thread.join(3)


def script_handshake(ws, credential, stream=None, version=1):
    """A gép oldala: hello, auth átvétel, helyes welcome. A nonce-okat adja vissza."""
    nonce = "a" * 32
    ws.send(json.dumps({"type": "hello", "v": version, "name": "Hamis", "nonce": nonce}))
    auth = json.loads(ws.recv(timeout=3))
    good_stream = {"codec": "pcm_s16le", "sample_rate": 44100, "channels": 1, "frame_samples": 1152}
    ws.send(json.dumps({
        "type": "welcome",
        "proof": mac_hex(credential, f"s|{nonce}|{auth['nonce']}"),
        "stream": good_stream if stream is None else stream,
    }))


FRAME = struct.pack("<h", 16384) * 1152


def wait_forever(ws):
    while True:
        ws.recv()


def test_receives_decoded_audio(env):
    env.connect()
    assert wait_until(lambda: env.buffer.read_block() is not None)
    block = env.buffer.read_block()
    while block is None:
        block = env.buffer.read_block()
    assert block.dtype == np.float32 and len(block) == 1152
    assert block[0] == pytest.approx(0.5)


def test_records_last_seen(env):
    env.connect()
    assert env.store.get(env.source.id).last_seen == 1_000_000


def test_wrong_credential_stops_without_retrying(env):
    env.add_source(env.start_server())
    write_state(env.state, "B" * 43)
    client = env.make_client()
    client.start()
    assert wait_until(lambda: env.finished == ["auth_failed"])
    assert client.state == "auth_failed"
    assert env.captures.items == []


def test_server_without_the_credential_cannot_inject_audio(env):
    def script(ws, index):
        script_handshake(ws, "B" * 43)
        for _ in range(8):
            ws.send(FRAME)
        wait_forever(ws)

    with scripted(script) as (port, connections):
        env.add_source(port)
        client = env.make_client()
        client.start()
        assert wait_until(lambda: env.finished == ["auth_failed"])
    assert client.state == "auth_failed"
    assert env.buffer.read_block() is None and len(connections) == 1


@pytest.mark.parametrize("hello", [
    '{"type":"hello","v":99,"name":"x","nonce":"' + "a" * 32 + '"}',
    '{"type":"hello","v":1,"name":"x","nonce":"rövid"}',
    '{"type":"other"}',
    "nem json",
])
def test_bad_hello_is_a_protocol_error(env, hello):
    def script(ws, index):
        ws.send(hello)
        wait_forever(ws)

    with scripted(script) as (port, _):
        env.add_source(port)
        env.make_client().start()
        assert wait_until(lambda: env.finished == ["protocol_error"])


def test_unexpected_stream_format_is_a_protocol_error(env):
    env.add_source(1)
    token = env.token

    def script(ws, index):
        script_handshake(ws, token, stream={"codec": "opus"})
        wait_forever(ws)

    with scripted(script) as (port, _):
        env.store.update(env.source.id, port=port)
        env.make_client().start()
        assert wait_until(lambda: env.finished == ["protocol_error"])


def test_wrong_sized_frames_are_ignored(env):
    env.add_source(1)
    token = env.token

    def script(ws, index):
        script_handshake(ws, token)
        ws.send(b"\x00" * 100)
        for _ in range(3):
            ws.send(FRAME)
        wait_forever(ws)

    with scripted(script) as (port, _):
        env.store.update(env.source.id, port=port)
        env.make_client().start()
        assert wait_until(lambda: env.buffer.read_block() is not None)


def test_no_audio_state_without_frames(env):
    env.add_source(1)
    token = env.token

    def script(ws, index):
        script_handshake(ws, token)
        wait_forever(ws)

    with scripted(script) as (port, _):
        env.store.update(env.source.id, port=port)
        client = env.make_client()
        client.start()
        assert wait_until(lambda: client.state == "no_audio")


def test_rotates_at_connect_when_due(env):
    client = env.connect(token_age=DAY)
    assert wait_until(lambda: env.current() != env.token)
    assert read_state(env.state) == {"current": env.current(), "previous": env.token}
    assert env.store.get(env.source.id).rotated_at == 1_000_000 + DAY
    assert client.state == "on_air" and len(env.captures.items) == 1


def test_does_not_rotate_before_due(env):
    env.connect(token_age=DAY - 120)
    time.sleep(0.3)
    assert env.current() == env.token


def test_rotates_during_a_session_without_reconnecting(env):
    client = env.connect()
    env.clock.now += DAY
    assert wait_until(lambda: env.current() != env.token)
    assert client.state == "on_air" and len(env.captures.items) == 1


def test_manual_rotations_chain_on_one_connection(env):
    client = env.connect()
    assert client.request_rotation().result(3) is True
    first = env.current()
    assert client.request_rotation().result(3) is True
    second = env.current()
    assert first != env.token and second != first
    assert read_state(env.state) == {"current": second, "previous": first}
    assert len(env.captures.items) == 1


def test_rotated_credential_works_on_the_next_connection(env):
    client = env.connect()
    assert client.request_rotation().result(3) is True
    client.stop()
    assert wait_until(lambda: len(env.finished) == 1)
    assert wait_until(lambda: env.captures.items[0].stopped.is_set())
    second = env.make_client()
    second.start()
    assert wait_until(lambda: second.state == "on_air")


def test_rotation_request_needs_a_live_connection(env):
    env.add_source(1)
    with pytest.raises(RotationUnavailable):
        env.make_client().request_rotation()


def test_resumes_after_the_machine_saved_but_the_reply_was_lost(env):
    env.add_source(env.start_server())
    write_state(env.state, derive(env.token, "e" * 32), env.token)
    env.clock.now += DAY
    client = env.make_client()
    client.start()
    assert wait_until(lambda: env.current() != env.token)
    state = read_state(env.state)
    assert state == {"current": env.current(), "previous": env.token}


def test_failed_rotation_keeps_the_connection_and_retries_after_an_hour(env, monkeypatch):
    attempts = []
    real_save = MicState.save

    def fail(self, current, previous):
        attempts.append(1)
        raise OSError("tele a lemez")

    monkeypatch.setattr(MicState, "save", fail)
    client = env.connect(token_age=DAY)
    assert wait_until(lambda: len(attempts) == 1)
    time.sleep(0.3)
    assert len(attempts) == 1 and client.state == "on_air" and len(env.captures.items) == 1
    monkeypatch.setattr(MicState, "save", real_save)
    env.clock.now += 3599
    time.sleep(0.3)
    assert env.current() == env.token
    env.clock.now += 1
    assert wait_until(lambda: env.current() != env.token)


def test_mixer_save_failure_reconnects_with_the_saved_credential(env, monkeypatch):
    real_set = RemoteMicStore.set_credential
    calls = []

    def fail_once(self, source_id, credential):
        calls.append(credential)
        if len(calls) == 1:
            raise OSError("tele a lemez")
        return real_set(self, source_id, credential)

    monkeypatch.setattr(RemoteMicStore, "set_credential", fail_once)
    env.connect(token_age=DAY)
    assert wait_until(lambda: env.current() != env.token)
    assert len(env.captures.items) == 2
    assert read_state(env.state) == {"current": env.current(), "previous": env.token}


def test_bad_rotation_proof_keeps_the_saved_credential(env):
    env.add_source(1)
    token = env.token

    def script(ws, index):
        script_handshake(ws, token)
        if index == 1:
            json.loads(ws.recv(timeout=3))
            ws.send(json.dumps({"type": "rotated", "proof": "0" * 64}))
        wait_forever(ws)

    with scripted(script) as (port, connections):
        env.store.update(env.source.id, port=port)
        env.clock.now += DAY
        env.make_client().start()
        assert wait_until(lambda: len(connections) >= 2)
    assert env.current() == token


def test_pending_manual_rotation_resolves_false_when_the_connection_drops(env):
    env.add_source(1)
    token = env.token
    got_rotate = threading.Event()

    def script(ws, index):
        script_handshake(ws, token)
        if index == 1:
            ws.send(FRAME)
            json.loads(ws.recv(timeout=3))
            got_rotate.set()
            time.sleep(0.05)
            ws.close()
            return
        wait_forever(ws)

    with scripted(script) as (port, _):
        env.store.update(env.source.id, port=port)
        client = env.make_client()
        client.start()
        assert wait_until(lambda: client.state in ("on_air", "no_audio"))
        future = client.request_rotation()
        assert got_rotate.wait(3)
        assert future.result(3) is False
    assert env.current() == token


def test_second_rotation_request_while_one_is_pending_is_refused(env):
    env.add_source(1)
    token = env.token
    release = threading.Event()

    def script(ws, index):
        script_handshake(ws, token)
        release.wait(3)
        wait_forever(ws)

    with scripted(script) as (port, _):
        env.store.update(env.source.id, port=port)
        client = env.make_client()
        client.start()
        assert wait_until(lambda: client.state == "no_audio")
        client.request_rotation()
        with pytest.raises(RotationUnavailable):
            client.request_rotation()
        release.set()


def test_reconnects_after_the_machine_comes_back(env):
    client = env.connect()
    port = env.server.port
    env.server.close()
    assert wait_until(lambda: client.state == "unreachable")
    env.start_server(port)
    assert wait_until(lambda: client.state == "on_air")
    assert env.finished == []


def test_unreachable_machine_is_retried_until_stopped(env):
    env.add_source(1)
    client = env.make_client()
    client.start()
    assert wait_until(lambda: client.state == "unreachable")
    client.stop()
    assert env.finished == ["off"] and client.state == "off"


def test_mic_failure_is_reported_and_retried(env):
    env.captures.fail = True
    env.add_source(env.start_server())
    write_state(env.state, env.token)
    client = env.make_client()
    client.start()
    assert wait_until(lambda: client.state == "mic_error")
    env.captures.fail = False
    assert wait_until(lambda: client.state == "on_air")


def test_stop_ends_the_stream_quickly(env):
    client = env.connect()
    started = time.time()
    client.stop()
    assert time.time() - started < 2.0
    assert client.state == "off" and env.finished == ["off"]
    assert wait_until(lambda: env.captures.items[0].stopped.is_set())


def test_secrets_never_reach_the_log(env, caplog):
    with caplog.at_level(logging.INFO, logger="csengetes"):
        env.connect(token_age=DAY)
        assert wait_until(lambda: env.current() != env.token)
    assert env.token not in caplog.text and env.current() not in caplog.text


def test_uses_no_system_proxy(env, monkeypatch):
    # a rendszer proxyja nem állhat a mixer és a gép közé
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    env.connect()


def test_backoff_restarts_after_a_session_that_delivered_audio(env):
    env.add_source(1)
    token = env.token
    attempts = []

    def script(ws, index):
        attempts.append(index)
        script_handshake(ws, token)
        ws.send(FRAME)
        time.sleep(0.02)  # a kliens még átveszi a hangot, utána szakad a kapcsolat

    with scripted(script) as (port, _):
        env.store.update(env.source.id, port=port)
        env.make_client(backoff_max=5.0).start()
        # visszaállítás nélkül a 8. próba több mint 6 mp múlva jönne
        assert wait_until(lambda: len(attempts) >= 8, timeout=3.0), attempts


def test_backoff_keeps_growing_while_the_mic_keeps_failing(env):
    env.captures.fail = True
    env.add_source(env.start_server())
    write_state(env.state, env.token)
    env.make_client(backoff_max=5.0).start()
    time.sleep(1.0)
    assert 2 <= len(env.captures.items) <= 6, len(env.captures.items)


def test_no_new_rotation_is_accepted_once_the_session_is_ending(env):
    client = env.connect()
    accepted = []
    real_finish = client._finish_manual

    def spy(result):
        try:
            client.request_rotation()
            accepted.append(True)
        except RotationUnavailable:
            accepted.append(False)
        real_finish(result)

    client._finish_manual = spy
    env.server.close()
    env.server = None  # már leállt, a fixture ne zárja be újra
    assert wait_until(lambda: accepted)
    assert accepted == [False]
