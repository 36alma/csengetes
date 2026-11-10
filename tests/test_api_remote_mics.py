import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes import FakeMic, wait_until
from api import mixer as api_mixer
from api import remote_mics as api_remote_mics
from micserver.server import MicServer, MicState
from remote_mic_helpers import Captures, RunningMicServer, write_state
from services.mixer import Mixer
from services.music import MusicSession
from services.remote_mic.manager import RemoteMicManager
from services.remote_mic.store import RemoteMicStore


@pytest.fixture
def env(tmp_path):
    mixer = Mixer(mic=FakeMic())
    mixer.start()
    store = RemoteMicStore(path=str(tmp_path / "remote_mics.json"))
    manager = RemoteMicManager(store, mixer, backoff_start=0.05, backoff_max=0.1, rotate_wait=2.0)
    ctx = SimpleNamespace(remote_mics=manager, mixer=mixer, music_session=MusicSession())
    app = FastAPI()
    app.include_router(api_remote_mics.create_router(ctx))
    app.include_router(api_mixer.create_router(ctx))
    instance = SimpleNamespace(client=TestClient(app), manager=manager, store=store, tmp_path=tmp_path, servers=[])
    yield instance
    manager.shutdown()
    for running in instance.servers:
        running.close()
    mixer.stop()


def add(env, name="Igazgatói", host="10.0.0.5", **extra):
    response = env.client.post("/api/remote-mics", json={"name": name, "host": host, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def live_machine(env):
    """Futó mikrofongép, élő kapcsolattal."""
    state = str(env.tmp_path / "state.json")
    running = RunningMicServer(MicServer(state, Captures(), host="127.0.0.1", port=0, announce=lambda text: None))
    env.servers.append(running)
    created = add(env, host="127.0.0.1", port=running.port)
    write_state(state, created["token"])
    source_id = created["source"]["id"]
    assert env.client.post(f"/api/remote-mics/{source_id}/live", json={"live": True}).status_code == 200
    assert wait_until(lambda: env.client.get("/api/remote-mics").json()["sources"][0]["state"] == "on_air")
    return source_id, created["token"]


def test_create_shows_the_token_once_and_the_list_never(env):
    created = add(env)
    assert len(created["token"]) == 43 and "token" not in created["source"]
    assert created["source"]["port"] == 8770 and created["source"]["live"] is False
    listing = env.client.get("/api/remote-mics")
    assert created["token"] not in listing.text and '"token"' not in listing.text
    assert listing.json()["sources"][0]["id"] == created["source"]["id"]
    assert listing.json()["max"] == 8


@pytest.mark.parametrize("body", [
    {"name": "", "host": "10.0.0.5"},
    {"name": "A", "host": "rossz host"},
    {"name": "A", "host": "10.0.0.5", "port": 0},
    {"name": "A", "host": "10.0.0.5", "port": None},
    {"host": "10.0.0.5"},
    {"name": "A"},
    ["lista"],
])
def test_create_rejects_bad_input(env, body):
    assert env.client.post("/api/remote-mics", json=body).status_code == 400


def test_create_duplicate_address_conflicts(env):
    add(env)
    assert env.client.post("/api/remote-mics", json={"name": "B", "host": "10.0.0.5"}).status_code == 409


def test_patch_updates_and_never_returns_the_token(env):
    created = add(env)
    source_id = created["source"]["id"]
    response = env.client.patch(f"/api/remote-mics/{source_id}", json={"name": "Új", "host": "10.0.0.6", "port": 9000})
    assert response.status_code == 200
    assert (response.json()["source"]["name"], response.json()["source"]["host"], response.json()["source"]["port"]) == ("Új", "10.0.0.6", 9000)
    assert created["token"] not in response.text


@pytest.mark.parametrize("body,status", [
    ({"name": ""}, 400),
    ({"token": "A" * 43}, 400),
    ({"volume": 0.5}, 400),
    ({"source_id": "x"}, 400),
    ({"self": 1}, 400),
    (["lista"], 400),
])
def test_patch_rejects_bad_input(env, body, status):
    source_id = add(env)["source"]["id"]
    assert env.client.patch(f"/api/remote-mics/{source_id}", json=body).status_code == status


def test_delete_removes(env):
    source_id = add(env)["source"]["id"]
    assert env.client.delete(f"/api/remote-mics/{source_id}").json() == {"ok": True}
    assert env.client.get("/api/remote-mics").json()["sources"] == []


@pytest.mark.parametrize("method,path,body", [
    ("patch", "/api/remote-mics/nincs", {"name": "X"}),
    ("delete", "/api/remote-mics/nincs", None),
    ("post", "/api/remote-mics/nincs/live", {"live": True}),
    ("post", "/api/remote-mics/nincs/volume", {"volume": 0.5}),
    ("post", "/api/remote-mics/nincs/rotate", None),
    ("post", "/api/remote-mics/nincs/reissue", None),
])
def test_unknown_id_is_404(env, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(env.client, method)(path, **kwargs).status_code == 404


@pytest.mark.parametrize("body", [{"live": "true"}, {"live": 1}, {"live": None}, {}, ["x"]])
def test_live_needs_a_boolean(env, body):
    source_id = add(env)["source"]["id"]
    assert env.client.post(f"/api/remote-mics/{source_id}/live", json=body).status_code == 400


def test_live_switches_on_and_off(env):
    source_id, _ = live_machine(env)
    response = env.client.post(f"/api/remote-mics/{source_id}/live", json={"live": False})
    assert response.status_code == 200
    assert response.json()["source"]["live"] is False


def test_volume_is_stored(env):
    source_id = add(env)["source"]["id"]
    response = env.client.post(f"/api/remote-mics/{source_id}/volume", json={"volume": 0.25})
    assert response.status_code == 200 and response.json()["source"]["volume"] == 0.25
    assert env.store.get(source_id).volume == 0.25


@pytest.mark.parametrize("raw", ['{"volume": NaN}', '{"volume": Infinity}', '{"volume": 1.5}', '{"volume": -1}',
                                 '{"volume": "1"}', '{"volume": true}', "{}", "[1]"])
def test_volume_rejects_bad_values(env, raw):
    source_id = add(env)["source"]["id"]
    response = env.client.post(f"/api/remote-mics/{source_id}/volume", content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert env.store.get(source_id).volume == 1.0


def test_rotate_needs_a_live_connection(env):
    source_id = add(env)["source"]["id"]
    assert env.client.post(f"/api/remote-mics/{source_id}/rotate").status_code == 409


def test_rotate_on_a_live_connection_changes_the_credential(env):
    source_id, token = live_machine(env)
    response = env.client.post(f"/api/remote-mics/{source_id}/rotate")
    assert response.status_code == 200 and response.json()["ok"] is True
    new_value = env.store.get(source_id).token
    assert new_value != token and new_value not in response.text


def test_rotate_failure_is_reported(env, monkeypatch):
    source_id, token = live_machine(env)

    def fail(self, current, previous):
        raise OSError("tele a lemez")

    monkeypatch.setattr(MicState, "save", fail)
    response = env.client.post(f"/api/remote-mics/{source_id}/rotate")
    assert response.status_code == 502 and "error" in response.json()
    assert env.store.get(source_id).token == token


def test_reissue_shows_a_new_token_once(env):
    created = add(env)
    source_id = created["source"]["id"]
    response = env.client.post(f"/api/remote-mics/{source_id}/reissue")
    assert response.status_code == 200
    data = response.json()
    assert data["token"] != created["token"] and "token" not in data["source"]
    assert env.store.get(source_id).token == data["token"]


def test_mixer_state_lists_the_strips_without_secrets(env):
    created = add(env)
    response = env.client.get("/api/mixer")
    strips = response.json()["remote_mics"]
    assert created["token"] not in response.text
    assert strips == [{
        "id": created["source"]["id"], "name": "Igazgatói", "host": "10.0.0.5",
        "live": False, "state": "off", "volume": 1.0, "level": 0.0,
    }]
