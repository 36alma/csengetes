import json
import os
import sys
import threading

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve as sync_serve

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, FakeSink, wait_until
from micserver.server import MicServer, MicState
from remote_mic_helpers import Captures, RunningMicServer, mac_hex, read_state, write_state
from services.mixer import Mixer
from services.remote_mic.manager import RemoteMicManager, RemoteMicNotLive, RemoteMicRotationFailed
from services.remote_mic.store import RemoteMicNotFound, RemoteMicStore


class Env:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.sink = FakeSink()
        self.mixer = Mixer(mic=FakeMic(), output_factory=lambda index: self.sink)
        self.mixer.start()
        self.store = RemoteMicStore(path=str(tmp_path / "remote_mics.json"))
        self.manager = RemoteMicManager(self.store, self.mixer, backoff_start=0.05, backoff_max=0.1, rotate_wait=0.5)
        self.servers = []
        self.machines = 0

    def machine(self, credential=None, captures=None):
        """Futó mikrofongép, felvett forrással; (forrás nézet, token, captures, állapotfájl)."""
        self.machines += 1
        captures = captures or Captures()
        state = str(self.tmp_path / f"state{self.machines}.json")
        running = RunningMicServer(MicServer(state, captures, host="127.0.0.1", port=0, announce=lambda text: None))
        self.servers.append(running)
        view, token = self.manager.add(f"Gép {self.machines}", "127.0.0.1", running.port)
        write_state(state, credential or token)
        return view, token, captures, state

    def info(self, source_id):
        return next(item for item in self.manager.state() if item["id"] == source_id)

    def wait_state(self, source_id, *states):
        assert wait_until(lambda: self.info(source_id)["state"] in states), self.info(source_id)

    def close(self):
        self.manager.shutdown()
        for running in self.servers:
            running.close()
        self.mixer.stop()


@pytest.fixture
def env(tmp_path):
    instance = Env(tmp_path)
    yield instance
    instance.close()


def near(value, target):
    return abs(int(value) - int(target * 32767)) <= 2


def test_new_machine_is_off_and_nothing_connects(env):
    view, _, captures, _ = env.machine()
    info = env.info(view["id"])
    assert info["live"] is False and info["state"] == "off" and info["level"] == 0.0
    assert captures.items == [] and env.mixer.remote_channels == ()


def test_machine_audio_reaches_the_mixer_output(env):
    view, _, _, _ = env.machine()
    env.manager.set_live(view["id"], True)
    assert wait_until(lambda: env.sink.blocks and near(env.sink.blocks[-1][0], 0.5))
    assert env.info(view["id"])["live"] is True
    env.wait_state(view["id"], "on_air")
    assert wait_until(lambda: env.info(view["id"])["level"] == pytest.approx(0.5, abs=0.02))


def test_two_machines_are_mixed(env):
    first = env.machine(captures=Captures(value=8192))[0]
    second = env.machine(captures=Captures(value=4096))[0]
    env.manager.set_live(first["id"], True)
    env.manager.set_live(second["id"], True)
    assert wait_until(lambda: env.sink.blocks and near(env.sink.blocks[-1][0], 0.375))


def test_volume_applies_live_and_survives_a_restart(env):
    view, _, _, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.manager.set_volume(view["id"], 0.5)
    assert wait_until(lambda: env.sink.blocks and near(env.sink.blocks[-1][0], 0.25))
    assert RemoteMicStore(path=env.store._path).get(view["id"]).volume == 0.5


def test_saved_volume_is_used_when_going_live(env):
    view, _, _, _ = env.machine()
    env.manager.set_volume(view["id"], 0.25)
    env.manager.set_live(view["id"], True)
    assert wait_until(lambda: env.sink.blocks and near(env.sink.blocks[-1][0], 0.125))


def test_bad_volume_changes_nothing(env):
    from services.remote_mic.store import RemoteMicInvalid

    view, _, _, _ = env.machine()
    env.manager.set_live(view["id"], True)
    with pytest.raises(RemoteMicInvalid):
        env.manager.set_volume(view["id"], 2.0)
    assert env.info(view["id"])["volume"] == 1.0


def test_going_off_disconnects_and_removes_the_channel(env):
    view, _, captures, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    env.manager.set_live(view["id"], False)
    info = env.info(view["id"])
    assert info["live"] is False and info["state"] == "off"
    assert env.mixer.remote_channels == ()
    assert wait_until(lambda: captures.items[0].stopped.is_set())


def test_going_live_twice_keeps_one_connection(env):
    view, _, captures, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    env.manager.set_live(view["id"], True)
    assert len(env.mixer.remote_channels) == 1 and len(captures.items) == 1


def test_auth_failure_switches_off_and_is_shown(env):
    view, _, _, _ = env.machine(credential="B" * 43)
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "auth_failed")
    assert env.info(view["id"])["live"] is False
    assert env.mixer.remote_channels == ()


def test_going_live_again_clears_a_previous_failure(env):
    view, token, _, state = env.machine(credential="B" * 43)
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "auth_failed")
    write_state(state, token)
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")


def test_rotate_now_needs_a_live_connection(env):
    view, _, _, _ = env.machine()
    with pytest.raises(RemoteMicNotLive):
        env.manager.rotate_now(view["id"])


def test_rotate_now_replaces_the_credential_on_both_sides(env):
    view, token, captures, state = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    env.manager.rotate_now(view["id"])
    new_value = env.store.get(view["id"]).token
    assert new_value != token
    assert read_state(state) == {"current": new_value, "previous": token}
    assert len(captures.items) == 1


def test_rotate_now_reports_a_machine_side_failure_and_stays_connected(env, monkeypatch):
    def fail(self, current, previous):
        raise OSError("tele a lemez")

    view, token, _, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    monkeypatch.setattr(MicState, "save", fail)
    with pytest.raises(RemoteMicRotationFailed):
        env.manager.rotate_now(view["id"])
    assert env.store.get(view["id"]).token == token
    env.wait_state(view["id"], "on_air")


def test_rotate_now_times_out_without_disconnecting(env):
    def handler(ws):
        try:
            nonce = "a" * 32
            ws.send(json.dumps({"type": "hello", "v": 1, "name": "x", "nonce": nonce}))
            auth = json.loads(ws.recv(timeout=3))
            token = env.store.list()[0].token
            ws.send(json.dumps({
                "type": "welcome",
                "proof": mac_hex(token, f"s|{nonce}|{auth['nonce']}"),
                "stream": {"codec": "pcm_s16le", "sample_rate": 44100, "channels": 1, "frame_samples": 1152},
            }))
            connections.append(ws)
            while True:
                ws.recv()
        except ConnectionClosed:
            pass

    connections = []
    with sync_serve(handler, "127.0.0.1", 0, compression=None) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        view, _ = env.manager.add("Néma", "127.0.0.1", server.socket.getsockname()[1])
        env.manager.set_live(view["id"], True)
        env.wait_state(view["id"], "no_audio")
        with pytest.raises(RemoteMicRotationFailed):
            env.manager.rotate_now(view["id"])
        assert env.info(view["id"])["state"] == "no_audio" and len(connections) == 1
        env.manager.set_live(view["id"], False)
        server.shutdown()
    thread.join(3)


def test_reissue_gives_a_new_credential_and_stops_the_connection(env):
    view, token, captures, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    _, fresh = env.manager.reissue(view["id"])
    assert fresh != token and env.store.get(view["id"]).token == fresh
    assert env.info(view["id"])["live"] is False
    assert wait_until(lambda: captures.items[0].stopped.is_set())


def test_changing_the_address_stops_the_connection(env):
    view, _, _, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    env.manager.update(view["id"], port=view["port"] + 1)
    assert env.info(view["id"])["live"] is False


def test_renaming_keeps_the_connection(env):
    view, _, captures, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    assert env.manager.update(view["id"], name="Új név")["name"] == "Új név"
    assert env.info(view["id"])["name"] == "Új név" and env.info(view["id"])["live"] is True
    assert len(captures.items) == 1


def test_delete_stops_and_forgets(env):
    view, _, captures, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    env.manager.delete(view["id"])
    assert env.manager.state() == [] and env.mixer.remote_channels == ()
    assert wait_until(lambda: captures.items[0].stopped.is_set())


@pytest.mark.parametrize("call", [
    lambda m: m.set_live("nincs", True),
    lambda m: m.set_volume("nincs", 0.5),
    lambda m: m.rotate_now("nincs"),
    lambda m: m.reissue("nincs"),
    lambda m: m.delete("nincs"),
    lambda m: m.update("nincs", name="x"),
])
def test_unknown_id_is_not_found(env, call):
    with pytest.raises(RemoteMicNotFound):
        call(env.manager)


def test_shutdown_stops_every_connection(env):
    first = env.machine()
    second = env.machine()
    env.manager.set_live(first[0]["id"], True)
    env.manager.set_live(second[0]["id"], True)
    env.wait_state(first[0]["id"], "on_air")
    env.wait_state(second[0]["id"], "on_air")
    env.manager.shutdown()
    assert env.mixer.remote_channels == ()
    assert wait_until(lambda: first[2].items[0].stopped.is_set() and second[2].items[0].stopped.is_set())


def test_views_never_contain_the_credential(env):
    view, token, _, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    text = json.dumps([view, env.manager.state(), env.manager.sources()])
    assert token not in text and '"token"' not in text


def test_sources_list_management_fields(env):
    view, _, _, _ = env.machine()
    env.manager.set_live(view["id"], True)
    env.wait_state(view["id"], "on_air")
    item = env.manager.sources()[0]
    assert item["id"] == view["id"] and item["host"] == "127.0.0.1" and item["port"] == view["port"]
    assert item["live"] is True and item["state"] == "on_air" and item["last_seen"] is not None
