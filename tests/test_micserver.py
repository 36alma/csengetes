import contextlib
import json
import logging
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import wait_until
from micserver.server import MicServer, MicState
from remote_mic_helpers import (
    Captures,
    RunningMicServer,
    close_code,
    derive,
    mac_hex,
    next_text,
    open_ws,
    read_state,
    send_auth,
    write_state,
)

T0 = "A" * 43
T1 = "B" * 43
NR = "c" * 32
NR2 = "d" * 32


@pytest.fixture
def env(tmp_path):
    state = str(tmp_path / "state.json")
    write_state(state, T0)
    captures = Captures()
    announced = []
    server = MicServer(state, captures, name="Igazgatói", host="127.0.0.1", port=0,
                       handshake_timeout=0.3, announce=announced.append)
    running = RunningMicServer(server)
    yield SimpleNamespace(state=state, captures=captures, announced=announced, port=running.port)
    running.close()


@contextlib.contextmanager
def authed(env, credential=T0):
    """Élő, hitelesített kapcsolat; a welcome üzenettel együtt."""
    with open_ws(env.port) as ws:
        send_auth(ws, credential)
        yield ws, json.loads(ws.recv(timeout=3))


def test_hello_comes_first_and_the_mic_stays_closed(env):
    with open_ws(env.port) as ws:
        hello = json.loads(ws.recv(timeout=3))
    assert hello["type"] == "hello" and hello["v"] == 1 and hello["name"] == "Igazgatói"
    assert len(hello["nonce"]) == 32
    assert env.captures.items == []


def test_wrong_credential_is_refused_without_opening_the_mic(env):
    with open_ws(env.port) as ws:
        send_auth(ws, T1)
        assert close_code(ws) == 1008
    assert env.captures.items == [] and env.announced == []


def test_unpaired_machine_refuses_everyone(env):
    os.remove(env.state)
    with open_ws(env.port) as ws:
        send_auth(ws, T0)
        assert close_code(ws) == 1008


def test_silent_client_is_dropped_after_the_timeout(env):
    with open_ws(env.port) as ws:
        ws.recv(timeout=3)
        assert close_code(ws) == 1008


@pytest.mark.parametrize("payload", [
    "nem json",
    json.dumps({"type": "other"}),
    json.dumps({"type": "auth", "nonce": "rövid", "proof": "x"}),
    json.dumps({"type": "auth", "nonce": "b" * 32}),
])
def test_malformed_auth_is_refused(env, payload):
    with open_ws(env.port) as ws:
        ws.recv(timeout=3)
        ws.send(payload)
        assert close_code(ws) == 1008
    assert env.captures.items == []


def test_good_credential_gets_mutual_proof_and_audio(env):
    with open_ws(env.port) as ws:
        hello = send_auth(ws, T0)
        welcome = json.loads(ws.recv(timeout=3))
        assert welcome["type"] == "welcome"
        assert welcome["proof"] == mac_hex(T0, f"s|{hello['nonce']}|{'b' * 32}")
        assert welcome["stream"] == {"codec": "pcm_s16le", "sample_rate": 44100, "channels": 1, "frame_samples": 1152}
        frame = ws.recv(timeout=3)
        assert isinstance(frame, bytes) and len(frame) == 2304
        assert env.captures.items[0].started.is_set()
        assert env.announced == ["ADÁSBAN"]
    assert wait_until(lambda: env.captures.items[0].stopped.is_set())
    assert wait_until(lambda: env.announced == ["ADÁSBAN", "KI"])


def test_second_client_is_busy_and_the_first_keeps_streaming(env):
    with authed(env) as (first, _):
        with open_ws(env.port) as second:
            send_auth(second, T0)
            assert close_code(second) == 1013
        assert isinstance(first.recv(timeout=3), bytes)
    assert len(env.captures.items) == 1


def test_machine_is_free_again_after_the_client_leaves(env):
    with authed(env):
        pass
    assert wait_until(lambda: env.announced == ["ADÁSBAN", "KI"])
    with authed(env) as (_, welcome):
        assert welcome["type"] == "welcome"


def test_mic_failure_is_reported_and_closes(tmp_path):
    state = str(tmp_path / "state.json")
    write_state(state, T0)
    announced = []
    server = MicServer(state, Captures(fail=True), host="127.0.0.1", port=0, announce=announced.append)
    running = RunningMicServer(server)
    try:
        with open_ws(running.port) as ws:
            send_auth(ws, T0)
            assert json.loads(ws.recv(timeout=3))["type"] == "welcome"
            assert json.loads(ws.recv(timeout=3)) == {"type": "error", "code": "mic_unavailable"}
            assert close_code(ws) == 1011
    finally:
        running.close()
    assert announced == []


def test_rotation_is_durable_before_the_reply(env):
    new_value = derive(T0, NR)
    with authed(env) as (ws, _):
        ws.send(json.dumps({"type": "rotate", "nonce": NR}))
        reply = next_text(ws)
    assert reply == {"type": "rotated", "proof": mac_hex(new_value, f"r|{NR}")}
    assert read_state(env.state) == {"current": new_value, "previous": T0}


def test_second_rotation_chains_from_the_first_result(env):
    first = derive(T0, NR)
    second = derive(first, NR2)
    with authed(env) as (ws, _):
        ws.send(json.dumps({"type": "rotate", "nonce": NR}))
        next_text(ws)
        ws.send(json.dumps({"type": "rotate", "nonce": NR2}))
        reply = next_text(ws)
    assert reply["proof"] == mac_hex(second, f"r|{NR2}")
    assert read_state(env.state) == {"current": second, "previous": first}


def test_previous_credential_works_after_a_lost_reply(env):
    write_state(env.state, T1, T0)
    with authed(env, T0) as (_, welcome):
        assert welcome["type"] == "welcome"
    assert read_state(env.state) == {"current": T1, "previous": T0}


def test_rotating_from_the_previous_credential_derives_from_it(env):
    write_state(env.state, T1, T0)
    new_value = derive(T0, NR)
    with authed(env, T0) as (ws, _):
        ws.send(json.dumps({"type": "rotate", "nonce": NR}))
        next_text(ws)
    assert read_state(env.state) == {"current": new_value, "previous": T0}


def test_using_the_current_credential_forgets_the_previous(env):
    write_state(env.state, T1, T0)
    with authed(env, T1):
        pass
    assert read_state(env.state) == {"current": T1, "previous": None}
    assert wait_until(lambda: env.announced == ["ADÁSBAN", "KI"])
    with open_ws(env.port) as old:
        send_auth(old, T0)
        assert close_code(old) == 1008


def test_failed_write_keeps_streaming_and_the_old_credential(env, monkeypatch):
    def fail(self, current, previous):
        raise OSError("tele a lemez")

    real_save = MicState.save
    monkeypatch.setattr(MicState, "save", fail)
    with authed(env) as (ws, _):
        ws.send(json.dumps({"type": "rotate", "nonce": NR}))
        assert next_text(ws) == {"type": "rotate_failed"}
        assert isinstance(ws.recv(timeout=3), bytes)
        monkeypatch.setattr(MicState, "save", real_save)
        ws.send(json.dumps({"type": "rotate", "nonce": NR2}))
        reply = next_text(ws)
    # a sikertelen próba után is a régi titokból képződik az új
    assert reply["proof"] == mac_hex(derive(T0, NR2), f"r|{NR2}")


def test_invalid_rotation_nonce_changes_nothing(env):
    with authed(env) as (ws, _):
        ws.send(json.dumps({"type": "rotate", "nonce": "rövid"}))
        assert next_text(ws) == {"type": "rotate_failed"}
    assert read_state(env.state) == {"current": T0, "previous": None}


def test_audio_keeps_flowing_during_a_slow_rotation_write(env, monkeypatch):
    real_save = MicState.save

    def slow(self, current, previous):
        time.sleep(0.3)
        real_save(self, current, previous)

    monkeypatch.setattr(MicState, "save", slow)
    with authed(env) as (ws, _):
        ws.send(json.dumps({"type": "rotate", "nonce": NR}))
        frames = 0
        while True:
            message = ws.recv(timeout=3)
            if isinstance(message, str):
                break
            frames += 1
    assert frames >= 10


def test_state_is_reread_for_each_connection(env):
    write_state(env.state, T1)
    with authed(env, T1) as (_, welcome):
        assert welcome["type"] == "welcome"


def test_secrets_never_reach_the_log(env, caplog):
    with caplog.at_level(logging.DEBUG):
        with open_ws(env.port) as bad:
            send_auth(bad, T1)
            close_code(bad)
        with authed(env) as (ws, _):
            ws.send(json.dumps({"type": "rotate", "nonce": NR}))
            next_text(ws)
    for secret in (T0, T1, derive(T0, NR)):
        assert secret not in caplog.text
