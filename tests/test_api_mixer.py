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

from fakes import FakeMic, wait_until
from api import mixer as api_mixer
from model import AudioPacket
from services.mixer import Mixer, MixerPlayer, PlayGate
from services.mixer.mixing import SAMPLE_RATE


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mixer, "MUSIC_DIR", str(tmp_path))
    mixer = Mixer(mic=FakeMic())
    mixer.start()
    music_player = MixerPlayer(mixer, "music", PlayGate(), allowed_roots=[str(tmp_path)])
    state = {"authorized": True}
    ctx = SimpleNamespace(mixer=mixer, music_player=music_player, ws_authorized=lambda ws: state["authorized"])
    app = FastAPI()
    app.include_router(api_mixer.create_router(ctx))
    yield SimpleNamespace(client=TestClient(app), mixer=mixer, state=state, music_dir=tmp_path)
    mixer.stop()


def test_state_idle(env):
    data = env.client.get("/api/mixer").json()
    assert data["running"] is True
    assert data["status"] == 0 and data["flags"] == []
    assert data["level"] == 0.0
    assert data["mic_live"] is False and data["output_ok"] is True
    assert data["volume"] == {"master": 1.0, "music": 1.0, "mic": 1.0}
    assert data["stream"]["codec"] == "mp3"


def test_state_without_microphone_device(env):
    env.mixer.mic.current_output_device = None
    assert env.client.get("/api/mixer").json()["input"] is None
    assert env.client.get("/api/mixer/inputs").json()["current"] is None


def test_mic_toggle_shows_signal_and_flag(env):
    assert env.client.post("/api/mixer/mic", json={"live": True}).json() == {"ok": True, "mic_live": True}
    assert wait_until(lambda: env.client.get("/api/mixer").json()["level"] > 0.0)
    data = env.client.get("/api/mixer").json()
    assert data["mic_live"] is True and "PLAYING_MIC" in data["flags"]
    assert env.client.post("/api/mixer/mic", json={"live": False}).json()["mic_live"] is False


@pytest.mark.parametrize("body", [{}, {"live": "yes"}, {"live": 1}])
def test_mic_rejects_non_boolean(env, body):
    assert env.client.post("/api/mixer/mic", json=body).status_code == 400


def test_mic_failure_is_503_and_stays_off(env):
    def broken(samplerate, blocksize):
        raise RuntimeError("nincs mikrofon")

    env.mixer.mic.start = broken
    assert env.client.post("/api/mixer/mic", json={"live": True}).status_code == 503
    assert env.client.get("/api/mixer").json()["mic_live"] is False


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
    sf.write(env.music_dir / "dal.wav", 0.2 * np.sin(np.linspace(0, 500, SAMPLE_RATE * 3)), SAMPLE_RATE)
    assert env.client.post("/api/mixer/music", json={"name": "nincs.mp3"}).status_code == 404
    assert env.client.post("/api/mixer/music", json={"name": "../server.py"}).status_code == 404
    assert env.client.post("/api/mixer/music", json={"name": "dal.wav"}).status_code == 200
    assert wait_until(lambda: env.mixer.file_playing)
    assert wait_until(lambda: "PLAYING_MUSIC" in env.client.get("/api/mixer").json()["flags"])
    assert env.client.delete("/api/mixer/music").status_code == 200
    assert wait_until(lambda: not env.mixer.file_playing)


def test_websocket_streams_numbered_packets(env):
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
