import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import mixer as api_mixer
from fakes import FakeMic, wait_until
from model import AudioPacket, InputDevice
from services.mixer import Mixer
from services.mixer.mixing import BLOCK_SIZE, SAMPLE_RATE


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mixer, "MUSIC_DIR", str(tmp_path))
    mixer = Mixer(mic=FakeMic())
    state = {"authorized": True}
    ctx = SimpleNamespace(get_mixer=lambda: mixer, ws_authorized=lambda ws: state["authorized"])
    app = FastAPI()
    app.include_router(api_mixer.create_router(ctx))
    yield SimpleNamespace(client=TestClient(app), mixer=mixer, state=state, music_dir=tmp_path)
    mixer.stop()


def test_state_idle(env):
    data = env.client.get("/api/mixer").json()
    assert data["running"] is False
    assert data["status"] == 0 and data["flags"] == []
    assert data["level"] == 0.0
    assert data["volume"] == {"master": 1.0, "music": 1.0, "mic": 1.0}
    assert data["stream"]["codec"] == "mp3"


def test_start_stop(env):
    assert env.client.post("/api/mixer/start").json() == {"ok": True}
    assert env.client.get("/api/mixer").json()["running"] is True
    env.client.post("/api/mixer/stop")
    assert env.client.get("/api/mixer").json()["running"] is False


def test_state_shows_signal_while_running(env):
    import time
    env.client.post("/api/mixer/start")
    env.mixer.set_mic_live(True)
    deadline = time.time() + 3
    data = env.client.get("/api/mixer").json()
    while data["level"] == 0.0 and time.time() < deadline:
        time.sleep(0.05)
        data = env.client.get("/api/mixer").json()
    assert data["level"] > 0.0
    assert "PLAYING_MIC" in data["flags"]


def test_volume_partial_and_validation(env):
    r = env.client.post("/api/mixer/volume", json={"master": 1.5, "mic": 0.5})
    assert r.status_code == 200
    assert r.json()["volume"] == {"master": 1.5, "music": 1.0, "mic": 0.5}
    assert env.client.post("/api/mixer/volume", json={"music": 3}).status_code == 400
    assert env.client.post("/api/mixer/volume", json={"master": "hang"}).status_code == 400
    assert env.client.post("/api/mixer/volume", json={}).status_code == 400
    assert env.mixer.master_volume == 1.5  # a hibas keres nem valtoztatott


def test_inputs_and_switch(env):
    inputs = env.client.get("/api/mixer/inputs").json()
    assert [d["index"] for d in inputs["inputs"]] == [0, 3]
    assert env.client.post("/api/mixer/input", json={"index": 3}).status_code == 200
    assert env.client.get("/api/mixer").json()["input"]["index"] == 3
    assert env.client.post("/api/mixer/input", json={"index": 99}).status_code == 404


def test_music_path_protection_and_playback(env):
    sf.write(env.music_dir / "dal.wav", 0.2 * np.sin(np.linspace(0, 500, SAMPLE_RATE)), SAMPLE_RATE)
    assert env.client.post("/api/mixer/music", json={"name": "nincs.mp3"}).status_code == 404
    assert env.client.post("/api/mixer/music", json={"name": "../server.py"}).status_code == 404
    assert env.client.post("/api/mixer/music", json={"name": "dal.wav"}).status_code == 200
    assert env.mixer.file_playing
    assert env.client.delete("/api/mixer/music").status_code == 200
    assert not env.mixer.file_playing


def test_unreadable_music_is_400(env):
    (env.music_dir / "rossz.mp3").write_bytes(b"nem hang")
    assert env.client.post("/api/mixer/music", json={"name": "rossz.mp3"}).status_code == 400


def test_websocket_streams_numbered_packets(env):
    env.client.post("/api/mixer/start")
    with env.client.websocket_connect("/api/mixer/stream") as ws:
        info = ws.receive_json()
        assert info["codec"] == "mp3"
        packets = [AudioPacket.from_bytes(ws.receive_bytes()) for _ in range(8)]
    seqs = [p.seq for p in packets]
    assert seqs == list(range(seqs[0], seqs[0] + 8))
    assert all(p.payload[0] == 0xFF for p in packets)  # MP3 keret szinkron bajt


def test_websocket_origin_check(env):
    same = {"origin": "http://testserver"}
    with env.client.websocket_connect("/api/mixer/stream", headers=same) as ws:
        assert ws.receive_json()["codec"] == "mp3"
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/api/mixer/stream", headers={"origin": "http://evil.example"}):
            pass


def test_websocket_rejects_unauthorized(env):
    env.state["authorized"] = False
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/api/mixer/stream"):
            pass
